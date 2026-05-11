"""
main.py — Orchestrates the NFL prediction model pipeline.

Usage:
    python main.py --year 2025 --week 1 --bankroll 1000
    python main.py --year 2025 --week 1 --bankroll 1000 --scrape
    python main.py --year 2025 --week 1 --bankroll 1000 --schedule schedule_week1_2025.csv
"""

import argparse
import sys
import numpy as np
import pandas as pd
from pathlib import Path

from data_loader import (
    load_master_data, load_team_glossary, load_schedule, load_spread_to_ml,
    get_weekid_range, filter_master_data, build_abbr_to_name, build_name_to_abbr,
    BACK_TEST_RANGE,
)
from features import build_feature_table
from predictor import predict_week, compute_bet_sizing


def run_prediction(year: int, week: int, bankroll: float,
                   master_data_path: str = "master_data.csv",
                   schedule_path: str = None,
                   scrape: bool = False,
                   seed: int = None,
                   output_dir: str = "output",
                   bet_pct: int = 10) -> pd.DataFrame:
    """Run the full prediction pipeline for a given week.

    Args:
        year: NFL season year.
        week: Week number to predict.
        bankroll: Total bankroll amount.
        master_data_path: Path to master data CSV.
        schedule_path: Path to schedule CSV (or None to auto-detect).
        scrape: Whether to scrape latest data from PFR.
        seed: Random seed for reproducibility.
        output_dir: Directory for output CSV files.

    Returns:
        Predictions DataFrame.
    """
    Path(output_dir).mkdir(exist_ok=True)

    # Step 1: Load or scrape data
    print(f"=== NFL Model v5 — {year} Week {week} ===")
    print(f"Bankroll: ${bankroll:,.2f}")

    if scrape:
        from scraper import update_master_data
        # Determine which weeks we need data for
        weekids_needed = get_weekid_range(year, week)
        # Find the seasons and weeks we need
        weeks_to_check = []
        for wid in weekids_needed:
            # Reverse-engineer season/week from weekid
            for y in range(2022, year + 1):
                from data_loader import YEAR_INDEX_MAP
                idx = YEAR_INDEX_MAP.get(y, 0)
                for w in range(1, 19):
                    if idx * 18 - (18 - w) == wid:
                        weeks_to_check.append((y, w))

        print(f"Scraping data for weeks: {weeks_to_check}")
        for s, w in weeks_to_check:
            update_master_data(master_data_path, s, [w])

    master = load_master_data(master_data_path)
    glossary = load_team_glossary()
    abbr_to_name = build_abbr_to_name(glossary)

    # Step 2: Load schedule
    if schedule_path is None:
        schedule_path = f"schedule_week{week}_{year}.csv"
    if not Path(schedule_path).exists():
        print(f"\nSchedule file not found: {schedule_path}, attempting to scrape...")
        try:
            from scraper import scrape_schedule
            schedule = scrape_schedule(year, week)
        except Exception as e:
            print(f"\nERROR: Schedule file not found and auto-scrape failed: {e}")
            print("Please provide a schedule CSV with columns:")
            print("  game_number, week, away_team, away_ml, away_spread,")
            print("  away_result, home_team, home_ml, home_spread,")
            print("  home_result, total_line, total_result")
            sys.exit(1)
    else:
        schedule = load_schedule(schedule_path)
    print(f"\nGames this week: {len(schedule)}")

    # Step 3: Filter historical data
    weekids = get_weekid_range(year, week)
    filtered = filter_master_data(master, weekids)
    print(f"Historical window: {BACK_TEST_RANGE} weeks ({len(filtered)} game-rows)")

    if len(filtered) == 0:
        print("ERROR: No historical data in the lookback window!")
        sys.exit(1)

    # Step 4: Build features
    feature_table, turnover_slopes, avg_sow = build_feature_table(master, filtered)

    print(f"\nTurnover slopes:")
    for k, v in turnover_slopes.items():
        print(f"  {k}: {v:.4f}")
    print(f"League avg SOW: {avg_sow:.4f}")

    # Step 5: Run predictions
    rng = np.random.default_rng(seed) if seed else np.random.default_rng()
    preds = predict_week(schedule, feature_table, turnover_slopes, avg_sow,
                         abbr_to_name, rng=rng)

    # Step 6: Bet sizing
    preds = compute_bet_sizing(preds, bankroll, bet_fraction=bet_pct / 100)

    # Step 7: Output results
    _print_predictions(preds)
    _save_outputs(preds, feature_table, turnover_slopes, avg_sow, year, week, output_dir)

    return preds


def _print_predictions(preds: pd.DataFrame):
    """Print a formatted prediction summary to console."""
    print("\n" + "=" * 80)
    print("PREDICTIONS")
    print("=" * 80)

    for _, g in preds.iterrows():
        matchup = f"{g['away_team']} @ {g['home_team']}"
        print(f"\n{'─' * 60}")
        print(f"Game {g['game_number']}: {matchup}")
        print(f"  Predicted: {g['away_team']} {g['away_predicted_pts']:.1f} - "
              f"{g['home_team']} {g['home_predicted_pts']:.1f}")
        print(f"  Winner: {g['predicted_winner']} "
              f"(prob: {g['winner_prob']:.1%})")
        print(f"  Spread: {g['predicted_spread']:.1f}")
        print(f"  Total: {g['predicted_total']:.1f}")

        # ML bet
        if g['ml_bet'] == 'bet':
            print(f"  ** ML BET: {g['predicted_winner']} "
                  f"(ML {g['winner_ml']:+d}, value {g['ml_value']:.1%}, "
                  f"${g['ml_bet_amount']:.2f})")

        # Spread bet
        if g['spread_bet'] == 'bet':
            print(f"  ** SPREAD BET: {g['spread_side']} {g['book_spread']} "
                  f"(prob {g['spread_prob']:.1%}, value {g['spread_value']:.1%}, "
                  f"${g['spread_bet_amount']:.2f})")


    # Summary
    print(f"\n{'=' * 80}")
    print("BET SUMMARY")
    print(f"{'=' * 80}")
    ml_bets = preds[preds['ml_bet'] == 'bet']
    spread_bets = preds[preds['spread_bet'] == 'bet']

    print(f"  ML bets: {len(ml_bets)} games")
    print(f"  Spread bets: {len(spread_bets)} games")
    total_wagered = (preds['ml_bet_amount'].sum() +
                     preds['spread_bet_amount'].sum())
    print(f"  Total wagered: ${total_wagered:.2f}")


def _save_outputs(preds: pd.DataFrame, feature_table: pd.DataFrame,
                  turnover_slopes: dict, avg_sow: float,
                  year: int, week: int, output_dir: str):
    """Save all outputs to CSV files."""
    prefix = f"{output_dir}/{year}_week{week}"

    # Predictions
    preds.to_csv(f"{prefix}_predictions.csv", index=False)
    print(f"\nSaved: {prefix}_predictions.csv")

    # Feature table
    feature_table.to_csv(f"{prefix}_features.csv", index=False)
    print(f"Saved: {prefix}_features.csv")

    # Parameters
    params = pd.DataFrame([{
        'year': year,
        'week': week,
        'back_test_range': BACK_TEST_RANGE,
        'avg_sow': avg_sow,
        **turnover_slopes,
    }])
    params.to_csv(f"{prefix}_params.csv", index=False)
    print(f"Saved: {prefix}_params.csv")

    # Bet sheet (easy to read)
    bet_rows = []
    for _, g in preds.iterrows():
        matchup = f"{g['away_team']} @ {g['home_team']}"
        if g['ml_bet'] == 'bet':
            bet_rows.append({
                'game': matchup,
                'type': 'ML',
                'pick': g['predicted_winner'],
                'odds': g['winner_ml'],
                'value': g['ml_value'],
                'prob': g['winner_prob'],
                'amount': g['ml_bet_amount'],
            })
        if g['spread_bet'] == 'bet':
            bet_rows.append({
                'game': matchup,
                'type': 'Spread',
                'pick': f"{g['spread_side']}{g['book_spread']}",
                'odds': -110,
                'value': g['spread_value'],
                'prob': g['spread_prob'],
                'amount': g['spread_bet_amount'],
            })
    if bet_rows:
        bets_df = pd.DataFrame(bet_rows)
        bets_df.to_csv(f"{prefix}_bets.csv", index=False)
        print(f"Saved: {prefix}_bets.csv")


def main():
    parser = argparse.ArgumentParser(description="NFL Model v5 — Prediction Pipeline")
    parser.add_argument('--year', type=int, required=True, help="NFL season year")
    parser.add_argument('--week', type=int, required=True, help="Week number to predict")
    parser.add_argument('--bankroll', type=float, required=True, help="Total bankroll amount")
    parser.add_argument('--master-data', default="master_data.csv",
                        help="Path to master data CSV")
    parser.add_argument('--schedule', default=None,
                        help="Path to schedule CSV (default: schedule_weekN_YEAR.csv)")
    parser.add_argument('--scrape', action='store_true',
                        help="Scrape latest data from Pro Football Reference")
    parser.add_argument('--seed', type=int, default=None,
                        help="Random seed for Monte Carlo (for reproducibility)")
    parser.add_argument('--output-dir', default="output",
                        help="Directory for output CSV files")
    parser.add_argument('--bet-pct', type=int, default=100,
                        help="Percent of bankroll to wager per week (default: 100)")

    args = parser.parse_args()

    run_prediction(
        year=args.year,
        week=args.week,
        bankroll=args.bankroll,
        master_data_path=args.master_data,
        schedule_path=args.schedule,
        scrape=args.scrape,
        seed=args.seed,
        output_dir=args.output_dir,
        bet_pct=args.bet_pct,
    )


if __name__ == "__main__":
    main()
