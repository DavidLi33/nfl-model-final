"""
backtest.py — Walk-forward backtesting framework for the NFL model.

Runs the model week-by-week using only data available before each game,
compares predictions to actual results, and outputs accuracy metrics.
"""

import pandas as pd
import numpy as np
from typing import List, Optional, Tuple
from pathlib import Path

from data_loader import (
    load_master_data, load_team_glossary, load_schedule,
    get_weekid_range, filter_master_data, build_abbr_to_name,
    compute_weekid, BACK_TEST_RANGE,
)
from features import build_feature_table
from predictor import predict_week, compute_bet_sizing


def backtest_season(master_data_path: str,
                    schedule_dir: str,
                    season: int,
                    weeks: List[int],
                    bankroll: float = 1000.0,
                    seed: int = 42,
                    output_dir: str = "output") -> pd.DataFrame:
    """Run walk-forward backtest across multiple weeks.

    For each week, uses only data available before that week to make predictions,
    then compares to actual results.

    Args:
        master_data_path: Path to master data CSV.
        schedule_dir: Directory containing schedule CSVs.
        season: NFL season year.
        weeks: List of weeks to backtest.
        bankroll: Starting bankroll.
        seed: Random seed.
        output_dir: Output directory.

    Returns:
        DataFrame with all predictions and actual results.
    """
    Path(output_dir).mkdir(exist_ok=True)

    master = load_master_data(master_data_path)
    glossary = load_team_glossary()
    abbr_to_name = build_abbr_to_name(glossary)
    name_to_abbr = {v: k for k, v in abbr_to_name.items()}

    all_results = []
    running_bankroll = bankroll

    for week in weeks:
        print(f"\n{'=' * 60}")
        print(f"Backtesting {season} Week {week} (bankroll: ${running_bankroll:,.2f})")
        print(f"{'=' * 60}")

        # Load schedule for this week
        schedule_path = Path(schedule_dir) / f"schedule_week{week}_{season}.csv"
        if not schedule_path.exists():
            print(f"  Schedule not found: {schedule_path}, skipping")
            continue

        schedule = load_schedule(str(schedule_path))

        # Filter master data to only include weeks BEFORE this one
        weekids = get_weekid_range(season, week)
        filtered = filter_master_data(master, weekids)

        if len(filtered) == 0:
            print(f"  No historical data available, skipping")
            continue

        # Build features and predict
        feature_table, turnover_slopes, avg_sow = build_feature_table(master, filtered)
        rng = np.random.default_rng(seed + week)
        preds = predict_week(schedule, feature_table, turnover_slopes, avg_sow,
                             abbr_to_name, rng=rng)
        preds = compute_bet_sizing(preds, running_bankroll)

        # Add actual results
        preds = _add_actual_results(preds, schedule, master, season, week, name_to_abbr)

        preds['season'] = season
        preds['week'] = week
        preds['bankroll_before'] = running_bankroll

        # Calculate P&L
        week_pnl = _calculate_week_pnl(preds)
        running_bankroll += week_pnl
        preds['week_pnl'] = week_pnl
        preds['bankroll_after'] = running_bankroll

        all_results.append(preds)

        # Print summary
        _print_week_summary(preds, week_pnl)

    if not all_results:
        print("No weeks backtested.")
        return pd.DataFrame()

    combined = pd.concat(all_results, ignore_index=True)

    # Print and save overall summary
    _print_backtest_summary(combined, bankroll)
    combined.to_csv(f"{output_dir}/backtest_{season}.csv", index=False)
    print(f"\nSaved: {output_dir}/backtest_{season}.csv")

    return combined


def _add_actual_results(preds: pd.DataFrame, schedule: pd.DataFrame,
                        master: pd.DataFrame, season: int, week: int,
                        name_to_abbr: dict) -> pd.DataFrame:
    """Add actual game results to predictions from schedule or master data.

    Args:
        preds: Predictions DataFrame.
        schedule: Schedule DataFrame (may have away_result, home_result).
        master: Full master data.
        season: Season year.
        week: Week number.
        name_to_abbr: Team name to abbreviation mapping.

    Returns:
        Predictions with actual result columns added.
    """
    df = preds.copy()

    # Try to get results from schedule
    schedule_results = {}
    for _, row in schedule.iterrows():
        gn = row['game_number']
        away_result = row.get('away_result')
        home_result = row.get('home_result')
        if pd.notna(away_result) and pd.notna(home_result):
            schedule_results[gn] = {
                'away_score': int(away_result),
                'home_score': int(home_result),
            }

    # Fallback: try master data
    weekid = compute_weekid(season, week)
    week_games = master[master['weekid'] == weekid]
    master_results = {}
    for i in range(0, len(week_games), 2):
        if i + 1 < len(week_games):
            row1 = week_games.iloc[i]
            row2 = week_games.iloc[i + 1]
            away_abbr = name_to_abbr.get(row1['offense'], row1['offense'][:3])
            home_abbr = name_to_abbr.get(row2['offense'], row2['offense'][:3])
            master_results[(away_abbr, home_abbr)] = {
                'away_score': row1['offense_points'],
                'home_score': row2['offense_points'],
            }

    # Merge results
    actual_away = []
    actual_home = []
    actual_winner = []
    ml_correct = []
    spread_correct = []
    total_correct = []

    for _, row in df.iterrows():
        gn = row['game_number']
        away = row['away_team']
        home = row['home_team']

        result = schedule_results.get(gn)
        if result is None:
            result = master_results.get((away, home))

        if result:
            a_score = result['away_score']
            h_score = result['home_score']
            actual_away.append(a_score)
            actual_home.append(h_score)

            # ML correctness
            if a_score > h_score:
                winner = away
            else:
                winner = home
            actual_winner.append(winner)
            ml_correct.append('yes' if row['predicted_winner'] == winner else 'no')

            # Spread correctness
            actual_spread = abs(a_score - h_score)
            if row['spread_side'] == '-':
                # Bet favorite covers
                if actual_spread > row['book_spread'] and winner == row['predicted_winner']:
                    spread_correct.append('yes')
                else:
                    spread_correct.append('no')
            else:
                # Bet underdog covers
                if actual_spread < row['book_spread']:
                    spread_correct.append('yes')
                else:
                    spread_correct.append('no')

            # Total correctness
            actual_total = a_score + h_score
            if row['ou_side'] == 'over':
                total_correct.append('yes' if actual_total > row['total_line'] else 'no')
            else:
                total_correct.append('yes' if actual_total < row['total_line'] else 'no')
        else:
            actual_away.append(np.nan)
            actual_home.append(np.nan)
            actual_winner.append('')
            ml_correct.append('')
            spread_correct.append('')
            total_correct.append('')

    df['actual_away_score'] = actual_away
    df['actual_home_score'] = actual_home
    df['actual_winner'] = actual_winner
    df['ml_correct'] = ml_correct
    df['spread_correct'] = spread_correct
    df['total_correct'] = total_correct

    return df


def _calculate_week_pnl(preds: pd.DataFrame) -> float:
    """Calculate profit/loss for a week's bets."""
    pnl = 0.0

    for _, row in preds.iterrows():
        # ML bet P&L
        if row['ml_bet'] == 'bet' and row['ml_correct']:
            amt = row['ml_bet_amount']
            ml = row['winner_ml']
            if row['ml_correct'] == 'yes':
                if ml < 0:
                    pnl += amt * (100 / abs(ml))
                else:
                    pnl += amt * (ml / 100)
            else:
                pnl -= amt

        # Spread bet P&L
        if row['spread_bet'] == 'bet' and row['spread_correct']:
            amt = row['spread_bet_amount']
            if row['spread_correct'] == 'yes':
                pnl += amt * (100 / 110)  # -110 standard
            else:
                pnl -= amt

        # Total bet P&L
        if row['total_bet'] == 'bet' and row['total_correct']:
            amt = row['total_bet_amount']
            if row['total_correct'] == 'yes':
                pnl += amt * (100 / 110)
            else:
                pnl -= amt

    return pnl


def _print_week_summary(preds: pd.DataFrame, week_pnl: float):
    """Print a summary of the week's backtest results."""
    has_results = preds['actual_winner'].str.len().gt(0)
    if not has_results.any():
        print("  No actual results available for this week.")
        return

    # ML accuracy
    ml_bets = preds[(preds['ml_bet'] == 'bet') & (preds['ml_correct'] != '')]
    if len(ml_bets) > 0:
        ml_hits = (ml_bets['ml_correct'] == 'yes').sum()
        print(f"  ML: {ml_hits}/{len(ml_bets)} correct "
              f"({ml_hits/len(ml_bets):.1%})")

    # Overall ML accuracy (all games, not just bets)
    all_ml = preds[preds['ml_correct'] != '']
    if len(all_ml) > 0:
        all_hits = (all_ml['ml_correct'] == 'yes').sum()
        print(f"  Overall winner picks: {all_hits}/{len(all_ml)} "
              f"({all_hits/len(all_ml):.1%})")

    # Spread accuracy
    spread_bets = preds[(preds['spread_bet'] == 'bet') & (preds['spread_correct'] != '')]
    if len(spread_bets) > 0:
        spread_hits = (spread_bets['spread_correct'] == 'yes').sum()
        print(f"  Spread: {spread_hits}/{len(spread_bets)} correct "
              f"({spread_hits/len(spread_bets):.1%})")

    # Total accuracy
    total_bets = preds[(preds['total_bet'] == 'bet') & (preds['total_correct'] != '')]
    if len(total_bets) > 0:
        total_hits = (total_bets['total_correct'] == 'yes').sum()
        print(f"  Total: {total_hits}/{len(total_bets)} correct "
              f"({total_hits/len(total_bets):.1%})")

    # MAE
    valid = preds[preds['actual_away_score'].notna()]
    if len(valid) > 0:
        away_mae = abs(valid['away_predicted_pts'] - valid['actual_away_score']).mean()
        home_mae = abs(valid['home_predicted_pts'] - valid['actual_home_score']).mean()
        print(f"  MAE: away {away_mae:.2f}, home {home_mae:.2f}, "
              f"avg {(away_mae + home_mae) / 2:.2f}")

    print(f"  Week P&L: ${week_pnl:+,.2f}")


def _print_backtest_summary(results: pd.DataFrame, starting_bankroll: float):
    """Print overall backtest summary statistics."""
    print(f"\n{'=' * 60}")
    print("BACKTEST SUMMARY")
    print(f"{'=' * 60}")

    has_results = results['actual_winner'].str.len().gt(0)
    valid = results[has_results]

    if valid.empty:
        print("No actual results available for comparison.")
        return

    # Overall ML accuracy
    all_ml = valid[valid['ml_correct'] != '']
    if len(all_ml) > 0:
        hits = (all_ml['ml_correct'] == 'yes').sum()
        print(f"\nWinner prediction accuracy: {hits}/{len(all_ml)} "
              f"({hits/len(all_ml):.1%})")

    # ML bet accuracy
    ml_bets = valid[(valid['ml_bet'] == 'bet') & (valid['ml_correct'] != '')]
    if len(ml_bets) > 0:
        hits = (ml_bets['ml_correct'] == 'yes').sum()
        print(f"ML bet accuracy: {hits}/{len(ml_bets)} ({hits/len(ml_bets):.1%})")

    # ATS record
    spread_bets = valid[(valid['spread_bet'] == 'bet') & (valid['spread_correct'] != '')]
    if len(spread_bets) > 0:
        hits = (spread_bets['spread_correct'] == 'yes').sum()
        print(f"ATS record: {hits}-{len(spread_bets)-hits} "
              f"({hits/len(spread_bets):.1%})")

    # Total accuracy
    total_bets = valid[(valid['total_bet'] == 'bet') & (valid['total_correct'] != '')]
    if len(total_bets) > 0:
        hits = (total_bets['total_correct'] == 'yes').sum()
        print(f"Total O/U record: {hits}-{len(total_bets)-hits} "
              f"({hits/len(total_bets):.1%})")

    # MAE
    mae_away = abs(valid['away_predicted_pts'] - valid['actual_away_score']).mean()
    mae_home = abs(valid['home_predicted_pts'] - valid['actual_home_score']).mean()
    avg_mae = (mae_away + mae_home) / 2
    print(f"\nMAE on predicted scores: {avg_mae:.2f} points")
    print(f"  Away team MAE: {mae_away:.2f}")
    print(f"  Home team MAE: {mae_home:.2f}")

    # Calibration: are close games actually close?
    pred_spread = valid['predicted_spread']
    actual_spread = abs(valid['actual_away_score'] - valid['actual_home_score'])

    close_pred = valid[pred_spread < 5]
    if len(close_pred) > 0:
        close_actual = abs(close_pred['actual_away_score'] - close_pred['actual_home_score'])
        print(f"\nCalibration:")
        print(f"  Predicted close (<5 pts): {len(close_pred)} games, "
              f"actual avg spread: {close_actual.mean():.1f}")

    blowout_pred = valid[pred_spread > 14]
    if len(blowout_pred) > 0:
        blowout_actual = abs(blowout_pred['actual_away_score'] - blowout_pred['actual_home_score'])
        print(f"  Predicted blowout (>14 pts): {len(blowout_pred)} games, "
              f"actual avg spread: {blowout_actual.mean():.1f}")

    # P&L
    final_bankroll = valid.iloc[-1]['bankroll_after'] if 'bankroll_after' in valid.columns else starting_bankroll
    total_pnl = final_bankroll - starting_bankroll
    roi = total_pnl / starting_bankroll * 100

    print(f"\nP&L:")
    print(f"  Starting bankroll: ${starting_bankroll:,.2f}")
    print(f"  Final bankroll: ${final_bankroll:,.2f}")
    print(f"  Total P&L: ${total_pnl:+,.2f} ({roi:+.1f}%)")


if __name__ == "__main__":
    import sys

    # Example: backtest Week 1 of 2025
    print("NFL Model Backtest")
    print("=" * 60)

    # Check if schedule files exist
    schedule_dir = "."
    if not Path("schedule_week1_2025.csv").exists():
        print("No schedule files found. Create schedule CSVs to run backtests.")
        print("Format: schedule_weekN_YEAR.csv")
        sys.exit(0)

    results = backtest_season(
        master_data_path="master_data.csv",
        schedule_dir=schedule_dir,
        season=2025,
        weeks=[1],  # Add more weeks as schedules become available
        bankroll=1000.0,
        seed=42,
    )
