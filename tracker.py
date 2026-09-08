"""
tracker.py — Persistent season betting tracker.

Manages the full bet lifecycle: run model → select bets → lock → grade results.
State is stored in JSON files under tracker/ directory (one per season).
"""

import json
import os
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List

TRACKER_DIR = Path(os.environ.get("TRACKER_DIR", Path(__file__).parent / "tracker"))


class SeasonTracker:
    """Manages bet tracking state for an NFL season."""

    def __init__(self, season: int):
        self.season = season
        self.path = TRACKER_DIR / f"season_{season}.json"
        self.data = self._load()

    def _load(self) -> dict:
        TRACKER_DIR.mkdir(exist_ok=True)
        if self.path.exists():
            with open(self.path) as f:
                return json.load(f)
        return {"season": self.season, "starting_bankroll": 1000.0, "weeks": {}}

    def save(self):
        TRACKER_DIR.mkdir(exist_ok=True)
        with open(self.path, 'w') as f:
            json.dump(self.data, f, indent=2, default=str)

    @property
    def starting_bankroll(self) -> float:
        return self.data["starting_bankroll"]

    def set_starting_bankroll(self, amount: float) -> bool:
        """Set starting bankroll. Only allowed if no weeks are locked/graded."""
        for wk_data in self.data["weeks"].values():
            if wk_data["status"] in ("locked", "graded"):
                return False
        self.data["starting_bankroll"] = amount
        self.save()
        return True

    def get_current_bankroll(self) -> float:
        """Get the running bankroll after all graded weeks."""
        bankroll = self.data["starting_bankroll"]
        for wk in sorted(self.data["weeks"].keys(), key=int):
            if self.data["weeks"][wk]["status"] == "graded":
                bankroll = self.data["weeks"][wk]["bankroll_after"]
        return bankroll

    def get_week(self, week: int) -> Optional[dict]:
        return self.data["weeks"].get(str(week))

    def week_status(self, week: int) -> str:
        wk = self.get_week(week)
        return wk["status"] if wk else "new"

    def has_any_locked(self) -> bool:
        return any(w["status"] in ("locked", "graded")
                   for w in self.data["weeks"].values())

    def all_week_statuses(self) -> Dict[int, str]:
        """Return status for all 18 weeks."""
        return {w: self.week_status(w) for w in range(1, 19)}

    def init_week(self, week: int, predictions: list, team_stats: dict,
                  bet_pct: int = 100,
                  turnover_slopes: Optional[dict] = None,
                  avg_sow: Optional[float] = None) -> bool:
        """Initialize a week with model predictions. Only works for new/pending/projection-only weeks."""
        wk_str = str(week)
        existing = self.data["weeks"].get(wk_str)
        if existing and existing["status"] not in ("pending", "projection_only"):
            return False

        bankroll = self.get_current_bankroll()
        bets = []

        for pred in predictions:
            # ML bet option
            bets.append({
                "id": f"{pred['game_number']}_ml",
                "game_number": pred["game_number"],
                "away_team": pred["away_team"],
                "home_team": pred["home_team"],
                "type": "ml",
                "pick": pred["predicted_winner"],
                "odds": int(pred["winner_ml"]),
                "amount": round(float(pred["ml_bet_amount"]), 2),
                "recommended": pred["ml_bet"] == "bet",
                "selected": pred["ml_bet"] == "bet",
                "prob": round(float(pred["winner_prob"]), 4),
                "value": round(float(pred["ml_value"]), 4),
                "result": None,
                "pnl": 0.0,
            })

            # Spread bet option
            if pred["spread_side"] == "-":
                spread_team = pred["predicted_winner"]
                spread_points = -float(pred["book_spread"])
            else:
                spread_team = (pred["home_team"]
                               if pred["predicted_winner"] == pred["away_team"]
                               else pred["away_team"])
                spread_points = float(pred["book_spread"])

            bets.append({
                "id": f"{pred['game_number']}_spread",
                "game_number": pred["game_number"],
                "away_team": pred["away_team"],
                "home_team": pred["home_team"],
                "type": "spread",
                "pick": f"{spread_team} {pred['spread_side']}{pred['book_spread']}",
                "spread_team": spread_team,
                "spread_points": spread_points,
                "odds": -110,
                "amount": round(float(pred["spread_bet_amount"]), 2),
                "recommended": pred["spread_bet"] == "bet",
                "selected": pred["spread_bet"] == "bet",
                "prob": round(float(pred["spread_prob"]), 4),
                "value": round(float(pred["spread_value"]), 4),
                "result": None,
                "pnl": 0.0,
            })

            total_side = str(pred.get("ou_side", "")).lower()
            total_line = float(pred.get("total_line", 0) or 0)
            bets.append({
                "id": f"{pred['game_number']}_total",
                "game_number": pred["game_number"],
                "away_team": pred["away_team"],
                "home_team": pred["home_team"],
                "type": "total",
                "pick": f"{total_side.upper()} {total_line:g}",
                "total_side": total_side,
                "total_line": total_line,
                "odds": int(pred.get("total_odds", -110) or -110),
                "amount": round(float(pred.get("total_bet_amount", 0) or 0), 2),
                "recommended": pred.get("total_bet") == "bet",
                "selected": pred.get("total_bet") == "bet",
                "prob": round(float(pred.get("total_prob", 0) or 0), 4),
                "value": round(float(pred.get("total_value", 0) or 0), 4),
                "result": None,
                "pnl": 0.0,
            })

        # Clean predictions for JSON serialization
        clean_preds = []
        for p in predictions:
            clean = {}
            for k, v in p.items():
                try:
                    if hasattr(v, '__float__') and not isinstance(v, (bool, str)):
                        clean[k] = float(v)
                    else:
                        clean[k] = v
                except (TypeError, ValueError):
                    clean[k] = str(v)
            clean_preds.append(clean)

        # Clean team stats
        clean_stats = {}
        for abbr, stats in team_stats.items():
            clean_stats[abbr] = {}
            for k, v in stats.items():
                try:
                    if hasattr(v, '__float__') and not isinstance(v, (bool, str)):
                        clean_stats[abbr][k] = float(v)
                    else:
                        clean_stats[abbr][k] = v
                except (TypeError, ValueError):
                    clean_stats[abbr][k] = str(v)

        # Clean turnover slopes
        clean_slopes = {}
        if turnover_slopes:
            for k, v in turnover_slopes.items():
                try:
                    clean_slopes[k] = float(v)
                except (TypeError, ValueError):
                    clean_slopes[k] = 0.0

        try:
            clean_avg_sow = float(avg_sow) if avg_sow is not None else None
        except (TypeError, ValueError):
            clean_avg_sow = None

        self.data["weeks"][wk_str] = {
            "status": "pending",
            "bet_pct": bet_pct,
            "bankroll_before": bankroll,
            "bankroll_after": bankroll,
            "bets": bets,
            "predictions": clean_preds,
            "team_stats": clean_stats,
            "turnover_slopes": clean_slopes,
            "avg_sow": clean_avg_sow,
            "season_projection": None,
            "created_at": datetime.now().isoformat(),
            "locked_at": None,
            "graded_at": None,
        }
        self.save()
        return True

    def toggle_bet(self, week: int, bet_id: str) -> bool:
        """Toggle a bet on/off. Only works for pending weeks."""
        wk_str = str(week)
        wk_data = self.data["weeks"].get(wk_str)
        if not wk_data or wk_data["status"] != "pending":
            return False

        for bet in wk_data["bets"]:
            if bet["id"] == bet_id:
                bet["selected"] = not bet["selected"]
                break

        self._recalculate_amounts(wk_str)
        self.save()
        return True

    def set_selected_bets(self, week: int, selected_ids: List[str]) -> bool:
        """Replace pending-week selections with an exact list of selected bet ids."""
        wk_str = str(week)
        wk_data = self.data["weeks"].get(wk_str)
        if not wk_data or wk_data["status"] != "pending":
            return False

        selected = set(selected_ids)
        valid_ids = {b["id"] for b in wk_data["bets"]}
        if not selected.issubset(valid_ids):
            return False

        for bet in wk_data["bets"]:
            bet["selected"] = bet["id"] in selected

        self._recalculate_amounts(wk_str)
        self.save()
        return True

    def _recalculate_amounts(self, wk_str: str):
        """Redistribute bankroll among selected bets proportionally by value."""
        wk_data = self.data["weeks"][wk_str]
        pool = wk_data["bankroll_before"] * wk_data.get("bet_pct", 100) / 100
        selected = [b for b in wk_data["bets"] if b["selected"]]
        total_value = sum(max(0, b["value"]) for b in selected)

        for b in wk_data["bets"]:
            if b["selected"] and total_value > 0:
                b["amount"] = round(max(0, b["value"]) / total_value * pool, 2)
            else:
                b["amount"] = 0.0

    def apply_mirofish(self, week: int, verdicts: Dict[str, dict]) -> bool:
        """Apply MiroFish verdicts to a pending week.

        Bets where MiroFish disagrees are deselected. Amounts are recalculated.

        Args:
            week: Week number.
            verdicts: {bet_id: {verdict, confidence, reasoning}}
        """
        wk_str = str(week)
        wk_data = self.data["weeks"].get(wk_str)
        if not wk_data or wk_data["status"] != "pending":
            return False

        for bet in wk_data["bets"]:
            bid = bet["id"]
            if bid in verdicts:
                v = verdicts[bid]
                bet["mirofish_verdict"] = v["verdict"]
                bet["mirofish_confidence"] = v["confidence"]
                bet["mirofish_reasoning"] = v["reasoning"]

                # Deselect bets MiroFish disagrees with
                if v["verdict"] == "disagree" and bet["selected"]:
                    bet["selected"] = False
            else:
                # No verdict = no MiroFish opinion, keep as-is
                bet["mirofish_verdict"] = None

        wk_data["mirofish_run"] = True
        self._recalculate_amounts(wk_str)
        self.save()
        return True

    def lock_bets(self, week: int) -> bool:
        """Lock bets for a week."""
        wk_str = str(week)
        wk_data = self.data["weeks"].get(wk_str)
        if not wk_data or wk_data["status"] != "pending":
            return False

        for bet in wk_data["bets"]:
            if not bet["selected"]:
                bet["amount"] = 0.0

        wk_data["status"] = "locked"
        wk_data["locked_at"] = datetime.now().isoformat()
        self.save()
        return True

    def unlock_bets(self, week: int) -> bool:
        """Move a locked week back to pending so selections can be edited."""
        wk_str = str(week)
        wk_data = self.data["weeks"].get(wk_str)
        if not wk_data or wk_data["status"] != "locked":
            return False

        wk_data["status"] = "pending"
        wk_data["locked_at"] = None
        wk_data["unlocked_at"] = datetime.now().isoformat()
        self._recalculate_amounts(wk_str)
        self.save()
        return True

    @staticmethod
    def win_profit(amount: float, odds: int) -> float:
        """Return profit, excluding returned stake, for a winning bet."""
        if odds < 0:
            return amount * (100 / abs(odds))
        return amount * (odds / 100)

    @staticmethod
    def grade_bet_result(bet: dict, game_result: dict) -> str:
        """Return win/loss/push for one bet against one completed game."""
        a_score = game_result["away_score"]
        h_score = game_result["home_score"]

        if bet["type"] == "ml":
            if a_score == h_score:
                return "push"
            actual_winner = bet["away_team"] if a_score > h_score else bet["home_team"]
            return "win" if bet["pick"] == actual_winner else "loss"

        if bet["type"] == "spread":
            st = bet["spread_team"]
            sp = bet["spread_points"]
            t_score = a_score if st == bet["away_team"] else h_score
            o_score = h_score if st == bet["away_team"] else a_score
            margin = t_score - o_score + sp
            if margin > 0:
                return "win"
            if margin == 0:
                return "push"
            return "loss"

        if bet["type"] == "total":
            total_points = a_score + h_score
            line = float(bet.get("total_line", 0) or 0)
            if total_points == line:
                return "push"
            side = str(bet.get("total_side", "")).lower()
            if side == "over":
                return "win" if total_points > line else "loss"
            return "win" if total_points < line else "loss"

        return "loss"

    @classmethod
    def pnl_for_bet(cls, bet: dict, result: str, projected: bool = False) -> float:
        """Return P&L for one bet using actual result or projected-win mode."""
        amount = float(bet.get("amount", 0) or 0)
        odds = int(bet.get("odds", -110) or -110)
        if projected or result == "win":
            return round(cls.win_profit(amount, odds), 2)
        if result == "push":
            return 0.0
        if result == "loss":
            return round(-amount, 2)
        return 0.0

    def grade_week(self, week: int, game_results: Dict[int, dict]) -> bool:
        """Grade a week's bets with actual results.

        game_results: {game_number: {away_team, home_team, away_score, home_score}}
        Can be called on locked or graded weeks (to re-grade with updated scores).
        """
        wk_str = str(week)
        wk_data = self.data["weeks"].get(wk_str)
        if not wk_data or wk_data["status"] not in ("locked", "graded"):
            return False

        total_pnl = 0.0
        graded_any = False

        for bet in wk_data["bets"]:
            if not bet["selected"] or bet["amount"] == 0:
                bet["result"] = "skip"
                continue

            result = game_results.get(bet["game_number"])
            if not result:
                continue

            graded_any = True
            bet["result"] = self.grade_bet_result(bet, result)
            bet["pnl"] = self.pnl_for_bet(bet, bet["result"])

            total_pnl += bet["pnl"]

        if graded_any:
            wk_data["bankroll_after"] = round(
                wk_data["bankroll_before"] + total_pnl, 2)
            wk_data["status"] = "graded"
            wk_data["graded_at"] = datetime.now().isoformat()
            self.save()
            return True
        return False

    def get_season_summary(self) -> dict:
        """Get aggregate season statistics."""
        starting = self.data["starting_bankroll"]
        current = self.get_current_bankroll()
        wins = losses = pushes = 0
        ml_w = ml_l = sp_w = sp_l = tot_w = tot_l = 0
        total_wagered = 0.0

        for wk_data in self.data["weeks"].values():
            if wk_data["status"] != "graded":
                continue
            for b in wk_data["bets"]:
                if b["result"] == "win":
                    wins += 1
                    total_wagered += b["amount"]
                    if b["type"] == "ml":
                        ml_w += 1
                    elif b["type"] == "spread":
                        sp_w += 1
                    else:
                        tot_w += 1
                elif b["result"] == "loss":
                    losses += 1
                    total_wagered += b["amount"]
                    if b["type"] == "ml":
                        ml_l += 1
                    elif b["type"] == "spread":
                        sp_l += 1
                    else:
                        tot_l += 1
                elif b["result"] == "push":
                    pushes += 1

        return {
            "starting_bankroll": starting,
            "current_bankroll": current,
            "total_pnl": round(current - starting, 2),
            "roi": (round((current - starting) / starting * 100, 1)
                    if starting > 0 else 0),
            "record": f"{wins}-{losses}" + (f"-{pushes}" if pushes else ""),
            "ml_record": f"{ml_w}-{ml_l}",
            "spread_record": f"{sp_w}-{sp_l}",
            "total_record": f"{tot_w}-{tot_l}",
            "total_wagered": round(total_wagered, 2),
        }

    def save_projection(self, week: int, projection: dict) -> bool:
        """Cache the season projection on a week so we don't recompute on every page load.

        If the week doesn't exist yet (e.g. running a future-year projection
        without first initializing the week via the model), create a minimal
        stub so we have a place to attach the projection.
        """
        wk_str = str(week)
        wk_data = self.data["weeks"].get(wk_str)
        if not wk_data:
            wk_data = {
                "status": "projection_only",
                "predictions": [],
                "team_stats": {},
                "bets": [],
                "created_at": datetime.now().isoformat(),
                "season_projection": None,
            }
            self.data["weeks"][wk_str] = wk_data
        wk_data["season_projection"] = projection
        wk_data["projected_at"] = datetime.now().isoformat()
        self.save()
        return True

    def get_projection(self, week: int) -> Optional[dict]:
        wk = self.get_week(week)
        if not wk:
            return None
        return wk.get("season_projection")

    def reset_week(self, week: int) -> bool:
        """Delete a week's data entirely. Use with caution."""
        wk_str = str(week)
        if wk_str in self.data["weeks"]:
            del self.data["weeks"][wk_str]
            self.save()
            return True
        return False
