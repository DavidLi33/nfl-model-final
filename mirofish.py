"""
mirofish.py — Integration with MiroFish swarm intelligence prediction engine.

MiroFish simulates thousands of AI agents with different personas (sharp bettors,
casual fans, journalists, contrarians) debating each game based on seed material
(injury reports, stats, news). The resulting sentiment consensus is used to
filter our model's bet recommendations.

Flow:
    1. Gather seed material (injuries, team stats, model predictions)
    2. Send to MiroFish API for multi-agent simulation
    3. Parse report for agree/disagree verdict on each bet
    4. Auto-select only bets where MiroFish agrees with the model

Requires MiroFish running locally (default: http://localhost:5001).
See: https://github.com/666ghj/MiroFish
"""

import ssl
import os
import json
import time
import requests
import warnings
from typing import Dict, List, Optional, Tuple
from pathlib import Path

# SSL fix for macOS
try:
    import certifi
    os.environ.setdefault('SSL_CERT_FILE', certifi.where())
except ImportError:
    pass

warnings.filterwarnings('ignore')

MIROFISH_URL = os.environ.get("MIROFISH_URL", "http://localhost:5001")
MIROFISH_TIMEOUT = int(os.environ.get("MIROFISH_TIMEOUT", "120"))


def is_available(base_url: str = MIROFISH_URL) -> bool:
    """Check if MiroFish is running and reachable."""
    try:
        r = requests.get(f"{base_url}/api/health", timeout=5)
        return r.status_code == 200
    except Exception:
        # Try root endpoint as fallback
        try:
            r = requests.get(base_url, timeout=5)
            return r.status_code in (200, 404)  # 404 = server up but no root route
        except Exception:
            return False


def fetch_injuries(season: int, week: int) -> Dict[str, List[dict]]:
    """Fetch injury reports from nflverse, grouped by team abbreviation.

    Returns: {team_abbr: [{name, position, status, injury}, ...]}
    """
    try:
        ssl._create_default_https_context = ssl._create_unverified_context
        import nfl_data_py as nfl
        from scraper import _fix_abbr

        inj = nfl.import_injuries([season])
        week_inj = inj[inj['week'] == week].copy()

        # Only meaningful statuses
        week_inj = week_inj[week_inj['report_status'].isin([
            'Questionable', 'Doubtful', 'Out', 'Injured Reserve',
        ])]

        by_team = {}
        for _, row in week_inj.iterrows():
            team = _fix_abbr(row['team'])
            if team not in by_team:
                by_team[team] = []
            by_team[team].append({
                'name': row['full_name'],
                'position': row['position'],
                'status': row['report_status'],
                'injury': row.get('report_primary_injury', ''),
            })

        return by_team

    except Exception as e:
        print(f"Could not fetch injuries: {e}")
        return {}


def build_seed_material(game: dict, team_stats: dict,
                        injuries: Dict[str, List[dict]]) -> str:
    """Build seed text for MiroFish to analyze a single game.

    Args:
        game: Dict with game_number, away_team, home_team, predictions, bets.
        team_stats: Dict of {team_abbr: stats_dict}.
        injuries: Dict of {team_abbr: [injury_dicts]}.

    Returns:
        Formatted seed text string.
    """
    away = game['away_team']
    home = game['home_team']
    away_pts = game.get('away_predicted_pts', 0)
    home_pts = game.get('home_predicted_pts', 0)
    winner = game.get('predicted_winner', '')
    prob = game.get('winner_prob', 0)
    spread = game.get('predicted_spread', 0)

    lines = [
        f"# NFL Game Analysis: {away} @ {home}",
        "",
        f"## Model Prediction",
        f"- Predicted score: {away} {away_pts:.1f} - {home} {home_pts:.1f}",
        f"- Predicted winner: {winner} ({prob*100:.1f}% probability)",
        f"- Predicted spread: {spread:.1f} points",
        "",
    ]

    # Bet recommendations
    bets = game.get('bets', [])
    rec_bets = [b for b in bets if b.get('recommended')]
    if rec_bets:
        lines.append("## Recommended Bets")
        for b in rec_bets:
            lines.append(
                f"- {b['type'].upper()}: {b['pick']} "
                f"(odds {b['odds']:+d}, {b['value']*100:.1f}% EV, "
                f"{b['prob']*100:.1f}% model probability)"
            )
        lines.append("")

    # Team stats
    for team_abbr, label in [(away, "Away"), (home, "Home")]:
        stats = team_stats.get(team_abbr, {})
        if stats:
            lines.append(f"## {label} Team: {team_abbr}")
            lines.append(f"- Offense: {stats.get('offense_points', 0):.1f} pts/game, "
                         f"{stats.get('offense_yards_total', 0):.0f} yds/game "
                         f"({stats.get('offense_pass_yards', 0):.0f} pass, "
                         f"{stats.get('offense_rush_yards', 0):.0f} rush)")
            lines.append(f"- Defense: {stats.get('defense_points', 0):.1f} pts allowed/game, "
                         f"{stats.get('defense_yards_total', 0):.0f} yds allowed/game")
            lines.append(f"- Win rate: {stats.get('win_rate', 0)*100:.0f}%, "
                         f"SOW: {stats.get('sow', 0):.3f}")
            lines.append("")

    # Injury reports
    for team_abbr, label in [(away, "Away"), (home, "Home")]:
        team_inj = injuries.get(team_abbr, [])
        if team_inj:
            lines.append(f"## {label} Injuries: {team_abbr}")
            # Sort by importance: key positions first
            pos_priority = {'QB': 0, 'RB': 1, 'WR': 2, 'TE': 3, 'OT': 4,
                            'OG': 5, 'C': 5, 'DE': 6, 'DT': 6, 'LB': 7,
                            'CB': 8, 'S': 9}
            team_inj.sort(key=lambda x: pos_priority.get(x['position'], 10))
            for inj in team_inj:
                injury_str = f" ({inj['injury']})" if inj.get('injury') else ""
                lines.append(f"- {inj['name']} ({inj['position']}) - "
                             f"**{inj['status']}**{injury_str}")
            lines.append("")

    lines.extend([
        "## Analysis Request",
        "Based on the injury reports, team performance trends, public sentiment, "
        "and any factors that could make the statistical model inaccurate, evaluate "
        "each recommended bet. Consider:",
        "- How injuries affect the projected outcome",
        "- Whether the model may be overvaluing or undervaluing either team",
        "- Public betting trends and potential line value",
        "- Any matchup-specific factors the model might miss",
        "",
        "For each recommended bet, provide a clear AGREE or DISAGREE verdict "
        "with a confidence level (high/medium/low) and brief reasoning.",
    ])

    return "\n".join(lines)


def submit_to_mirofish(seed_text: str, game_label: str,
                       base_url: str = MIROFISH_URL,
                       timeout: int = MIROFISH_TIMEOUT) -> Optional[dict]:
    """Submit seed material to MiroFish and get the analysis report.

    Args:
        seed_text: Formatted seed material.
        game_label: Label for this analysis (e.g., "DAL @ PHI").
        base_url: MiroFish API URL.
        timeout: Max seconds to wait for simulation.

    Returns:
        Report dict from MiroFish, or None on failure.
    """
    try:
        # Step 1: Create prediction session and upload seed material
        create_resp = requests.post(
            f"{base_url}/api/graph/build",
            json={
                "title": f"NFL Analysis: {game_label}",
                "content": seed_text,
                "source_type": "text",
            },
            timeout=30,
        )
        if create_resp.status_code not in (200, 201):
            print(f"  MiroFish graph build failed: {create_resp.status_code}")
            return None

        session_data = create_resp.json()
        session_id = session_data.get("session_id") or session_data.get("id")

        # Step 2: Start simulation
        sim_resp = requests.post(
            f"{base_url}/api/simulation/start",
            json={"session_id": session_id},
            timeout=30,
        )
        if sim_resp.status_code not in (200, 201):
            print(f"  MiroFish simulation start failed: {sim_resp.status_code}")
            return None

        # Step 3: Poll for completion
        start_time = time.time()
        while time.time() - start_time < timeout:
            status_resp = requests.get(
                f"{base_url}/api/simulation/status",
                params={"session_id": session_id},
                timeout=10,
            )
            if status_resp.status_code == 200:
                status_data = status_resp.json()
                if status_data.get("status") in ("completed", "done", "finished"):
                    break
            time.sleep(3)
        else:
            print(f"  MiroFish simulation timed out after {timeout}s")
            return None

        # Step 4: Get report
        report_resp = requests.post(
            f"{base_url}/api/report/generate",
            json={"session_id": session_id},
            timeout=60,
        )
        if report_resp.status_code != 200:
            print(f"  MiroFish report generation failed: {report_resp.status_code}")
            return None

        return report_resp.json()

    except requests.ConnectionError:
        print(f"  MiroFish not reachable at {base_url}")
        return None
    except Exception as e:
        print(f"  MiroFish error: {e}")
        return None


def parse_verdict(report: dict, bet_type: str, bet_pick: str) -> dict:
    """Parse a MiroFish report to extract agree/disagree verdict for a specific bet.

    Scopes signal detection to sentences near the bet type keyword to avoid
    cross-contamination when a report discusses both ML and spread bets.

    Args:
        report: Raw report from MiroFish.
        bet_type: "ml" or "spread".
        bet_pick: The pick string (e.g., "PHI" or "PHI -7.0").

    Returns:
        {verdict: "agree"/"disagree", confidence: "high"/"medium"/"low",
         reasoning: str}
    """
    # Extract report text from various possible response formats
    report_text = ""
    if isinstance(report, dict):
        report_text = (report.get("report", "") or
                       report.get("content", "") or
                       report.get("analysis", "") or
                       report.get("text", "") or
                       json.dumps(report))
    elif isinstance(report, str):
        report_text = report

    report_lower = report_text.lower()

    # Keywords that identify sections about this bet type
    if bet_type == "ml":
        type_keys = ["moneyline", "ml bet", "ml ", "money line", "to win"]
    else:
        type_keys = ["spread", "ats", "against the spread", "cover", "points"]

    # Try to find the section of text most relevant to this bet type.
    # Extract a window of ~200 chars around the first mention.
    scoped_text = report_lower  # fallback: full report
    for key in type_keys:
        idx = report_lower.find(key)
        if idx >= 0:
            start = max(0, idx - 80)
            end = min(len(report_lower), idx + 200)
            scoped_text = report_lower[start:end]
            break

    agree_signals = ["agree", "recommend", "take the bet", "take this",
                     "good value", "favorable", "strong play",
                     "like this bet", "confirmed"]
    disagree_signals = ["disagree", "avoid", "skip", "pass on", "too risky",
                        "overvalued", "not recommended", "fade",
                        "stay away", "not worth"]

    agree_count = sum(1 for s in agree_signals if s in scoped_text)
    disagree_count = sum(1 for s in disagree_signals if s in scoped_text)

    # Confidence from language (scan full report)
    high_conf = ["strongly", "clearly", "definitely", "high confidence",
                 "very confident", "overwhelmingly"]
    low_conf = ["slight", "marginal", "barely", "uncertain", "close call",
                "coin flip", "low confidence"]

    has_high = any(w in scoped_text for w in high_conf)
    has_low = any(w in scoped_text for w in low_conf)
    confidence = "high" if has_high else ("low" if has_low else "medium")

    # Determine verdict
    if agree_count > disagree_count:
        verdict = "agree"
    elif disagree_count > agree_count:
        verdict = "disagree"
    elif agree_count == 0 and disagree_count == 0:
        # No signals found — fallback: agree (no red flags)
        verdict = "agree"
        confidence = "low"
    else:
        # Tied — default to agree if pick team mentioned positively in scope
        pick_lower = bet_pick.lower().split()[0]
        verdict = "agree" if pick_lower in scoped_text else "disagree"

    # Extract reasoning: the scoped section from the original (not lowered) text
    for key in type_keys:
        idx = report_text.lower().find(key)
        if idx >= 0:
            start = max(0, idx - 40)
            end = min(len(report_text), idx + 260)
            reasoning = report_text[start:end].strip()
            if start > 0:
                reasoning = "..." + reasoning
            if end < len(report_text):
                reasoning += "..."
            break
    else:
        reasoning = report_text[:300].strip()
        if len(report_text) > 300:
            reasoning += "..."

    return {
        "verdict": verdict,
        "confidence": confidence,
        "reasoning": reasoning,
    }


def analyze_week(games: list, team_stats: dict, season: int, week: int,
                 base_url: str = MIROFISH_URL) -> Dict[str, dict]:
    """Analyze all games for a week through MiroFish.

    Args:
        games: List of game dicts (from _build_games_list).
        team_stats: Dict of {team_abbr: stats_dict}.
        season: NFL season year.
        week: Week number.
        base_url: MiroFish API URL.

    Returns:
        Dict of {bet_id: {verdict, confidence, reasoning}}
    """
    print(f"MiroFish: Analyzing {len(games)} games for {season} Week {week}...")

    # Fetch injury reports
    injuries = fetch_injuries(season, week)
    inj_count = sum(len(v) for v in injuries.values())
    print(f"  Loaded {inj_count} injury reports across {len(injuries)} teams")

    verdicts = {}

    for game in games:
        away = game['away_team']
        home = game['home_team']
        label = f"{away} @ {home}"
        print(f"  Analyzing: {label}...")

        # Build seed material
        seed = build_seed_material(game, team_stats, injuries)

        # Submit to MiroFish
        report = submit_to_mirofish(seed, label, base_url)

        if report is None:
            print(f"    No report — skipping")
            continue

        # Parse verdicts for each bet in this game
        for bet in game.get('bets', []):
            if not bet.get('recommended'):
                continue

            v = parse_verdict(report, bet['type'], bet['pick'])
            verdicts[bet['id']] = v
            tag = "AGREE" if v['verdict'] == 'agree' else "DISAGREE"
            print(f"    {bet['type'].upper()} {bet['pick']}: "
                  f"{tag} ({v['confidence']} confidence)")

    print(f"MiroFish: Done — {len(verdicts)} bets analyzed")
    return verdicts


def get_seed_material_for_week(games: list, team_stats: dict,
                               season: int, week: int) -> Dict[int, str]:
    """Generate seed material for all games without calling MiroFish.
    Useful for inspection or manual submission.

    Returns: {game_number: seed_text}
    """
    injuries = fetch_injuries(season, week)
    seeds = {}
    for game in games:
        seed = build_seed_material(game, team_stats, injuries)
        seeds[game['game_number']] = seed
    return seeds
