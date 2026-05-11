"""
features.py — Computes all derived stats and features exactly as the Excel model does.

Sections A-F from the spec:
  A: Data filtering (handled by data_loader)
  B: Team averages over rolling window
  C: Sorted averages by offense points
  D: Turnover point value regressions
  E: Points adjustment (remove turnover noise)
  F: Win rate, SOW, standard deviation
"""

import pandas as pd
import numpy as np
from scipy import stats
from typing import Dict, Tuple


def compute_team_averages(filtered_df: pd.DataFrame) -> pd.DataFrame:
    """Step 4-5: Compute average stats per team over the filtered window.

    For each unique team in the filtered data, compute simple arithmetic
    averages of all offensive and defensive stats.

    Args:
        filtered_df: Master data filtered to the rolling window.

    Returns:
        DataFrame with one row per team and averaged stat columns.
    """
    agg_cols = {
        'offense_points': 'mean',
        'offense_yards_total': 'mean',
        'offense_pass_yards': 'mean',
        'offense_rush_yards': 'mean',
        'offense_interceptions': 'mean',
        'offense_fumbles': 'mean',
        'offense_sacks': 'mean',
        'defense_points': 'mean',
        'defense_yards_total': 'mean',
        'defense_pass_yards': 'mean',
        'defense_rush_yards': 'mean',
        'defense_interceptions': 'mean',
        'defense_fumbles': 'mean',
        'defense_sacks': 'mean',
    }

    team_avgs = filtered_df.groupby('offense').agg(agg_cols).reset_index()
    team_avgs = team_avgs.rename(columns={'offense': 'team'})

    return team_avgs


def sort_by_offense_points(team_avgs: pd.DataFrame) -> pd.DataFrame:
    """Step 6: Sort teams by average offense points descending.

    Adds a rank column (1 = highest offense points).

    Args:
        team_avgs: DataFrame from compute_team_averages.

    Returns:
        Sorted DataFrame with rank column added.
    """
    sorted_df = team_avgs.sort_values('offense_points', ascending=False).reset_index(drop=True)
    sorted_df.insert(0, 'rank', range(1, len(sorted_df) + 1))
    return sorted_df


def _slope_with_duplicate_first(x_vals: np.ndarray, y_vals: np.ndarray) -> float:
    """Compute linear regression slope matching Excel's SLOPE over a range
    that includes a duplicate of the first data point.

    The Excel model's UNIQUE output occupies a fixed range (BC42:BC53),
    and the first unique value appears twice in that range. SLOPE operates
    on the full range, so the first data point is double-weighted.

    Args:
        x_vals: Array of unique X values (e.g. turnover counts).
        y_vals: Array of corresponding Y values (averages).

    Returns:
        Regression slope with the duplicate-first-point behavior.
    """
    # Duplicate the first point to match Excel layout
    x_with_dup = np.concatenate([[x_vals[0]], x_vals])
    y_with_dup = np.concatenate([[y_vals[0]], y_vals])
    slope, _, _, _, _ = stats.linregress(x_with_dup, y_with_dup)
    return slope


def compute_turnover_regression(filtered_df: pd.DataFrame) -> Dict[str, float]:
    """Steps 7-8: Compute turnover point value regression slopes.

    For interceptions and fumbles, compute:
    - offense_loss: points lost per turnover committed
    - defense_gain: points gained per turnover forced

    Uses linear regression (SLOPE) on the relationship between
    turnover counts and average points scored. The Excel model's SLOPE
    range includes a duplicate of the first UNIQUE value, which we replicate.

    Args:
        filtered_df: Master data filtered to the rolling window.

    Returns:
        Dict with keys: pts_per_int_offense_loss, pts_per_int_defense_gain,
                        pts_per_fumble_offense_loss, pts_per_fumble_defense_gain
    """
    result = {}

    # --- Interceptions ---
    int_off = filtered_df.groupby('offense_interceptions')['offense_points'].mean()
    if len(int_off) >= 2:
        slope = _slope_with_duplicate_first(int_off.index.values, int_off.values)
        result['pts_per_int_offense_loss'] = slope * -1
    else:
        result['pts_per_int_offense_loss'] = 0.0

    int_def = filtered_df.groupby('defense_interceptions')['offense_points'].mean()
    if len(int_def) >= 2:
        slope = _slope_with_duplicate_first(int_def.index.values, int_def.values)
        result['pts_per_int_defense_gain'] = slope
    else:
        result['pts_per_int_defense_gain'] = 0.0

    # --- Fumbles ---
    fum_off = filtered_df.groupby('offense_fumbles')['offense_points'].mean()
    if len(fum_off) >= 2:
        slope = _slope_with_duplicate_first(fum_off.index.values, fum_off.values)
        result['pts_per_fumble_offense_loss'] = slope * -1
    else:
        result['pts_per_fumble_offense_loss'] = 0.0

    fum_def = filtered_df.groupby('defense_fumbles')['offense_points'].mean()
    if len(fum_def) >= 2:
        slope = _slope_with_duplicate_first(fum_def.index.values, fum_def.values)
        result['pts_per_fumble_defense_gain'] = slope
    else:
        result['pts_per_fumble_defense_gain'] = 0.0

    return result


def compute_points_adjustment(sorted_avgs: pd.DataFrame,
                              turnover_slopes: Dict[str, float]) -> pd.DataFrame:
    """Step 9: Decompose points into turnover and non-turnover components.

    For each team:
    - BR = points allowed by offense turnovers
    - BS = points scored by defense turnovers
    - BQ = adjusted defense points (defense_points - BR)
    - BT = adjusted offense points (offense_points - BS)

    Args:
        sorted_avgs: Sorted team averages DataFrame.
        turnover_slopes: Dict of turnover regression slopes.

    Returns:
        DataFrame with added columns: pts_off_turnover_cost, pts_def_turnover_gain,
        adj_def_points, adj_off_points.
    """
    df = sorted_avgs.copy()

    # BR: Points allowed by offense turnovers
    df['pts_off_turnover_cost'] = (
        df['offense_interceptions'] * turnover_slopes['pts_per_int_offense_loss'] +
        df['offense_fumbles'] * turnover_slopes['pts_per_fumble_offense_loss']
    )

    # BS: Points scored by defense turnovers
    df['pts_def_turnover_gain'] = (
        df['defense_interceptions'] * turnover_slopes['pts_per_int_defense_gain'] +
        df['defense_fumbles'] * turnover_slopes['pts_per_fumble_defense_gain']
    )

    # BQ: Adjusted defense points allowed = defense_points - BR
    df['adj_def_points'] = df['defense_points'] - df['pts_off_turnover_cost']

    # BT: Adjusted offense points scored = offense_points - BS
    df['adj_off_points'] = df['offense_points'] - df['pts_def_turnover_gain']

    return df


def compute_win_rates(filtered_df: pd.DataFrame,
                      sorted_avgs: pd.DataFrame) -> pd.DataFrame:
    """Steps 10-14: Compute win rate, opponent win rate, SOW, and stdev.

    Args:
        filtered_df: Master data filtered to the rolling window.
        sorted_avgs: Sorted team averages (will be augmented in-place).

    Returns:
        DataFrame with added columns: win_rate, opp_win_rate, sow, pts_stdev.
    """
    df = sorted_avgs.copy()

    # Step 10: Win rate per team
    win_rates = filtered_df.groupby('offense')['win'].mean()

    # Build team win rate lookup
    win_rate_lookup = win_rates.to_dict()

    # Step 3 (dependency): For each row in filtered data, compute opponent win rate
    # Each game is a pair of consecutive rows sharing the same weekid+date
    filt = filtered_df.copy()

    # Assign opponent for each row
    # Rows come in pairs: row i and row i+1 are the same game
    opp_win_rates = []
    for idx in range(0, len(filt), 2):
        if idx + 1 < len(filt):
            team1 = filt.iloc[idx]['offense']
            team2 = filt.iloc[idx + 1]['offense']
            opp_win_rates.append(win_rate_lookup.get(team2, 0))
            opp_win_rates.append(win_rate_lookup.get(team1, 0))
        else:
            opp_win_rates.append(0)

    filt['opp_win_rate'] = opp_win_rates

    # Step 11: Average opponent win rate per team
    opp_wr_by_team = filt.groupby('offense')['opp_win_rate'].mean()

    # Step 12: Strength of Wins (SOW) - avg opponent win rate of games WON
    wins_only = filt[filt['win'] == 1]
    sow_by_team = wins_only.groupby('offense')['opp_win_rate'].mean()

    # Step 13: Points standard deviation per team
    pts_stdev = filt.groupby('offense')['offense_points'].std(ddof=1)

    # Merge into sorted_avgs
    df['win_rate'] = df['team'].map(win_rate_lookup).fillna(0)
    df['opp_win_rate'] = df['team'].map(opp_wr_by_team).fillna(0)
    df['sow'] = df['team'].map(sow_by_team).fillna(0)
    df['pts_stdev'] = df['team'].map(pts_stdev).fillna(0)

    return df


def compute_avg_sow(sorted_avgs: pd.DataFrame) -> float:
    """Step 14: League-wide average SOW."""
    return sorted_avgs['sow'].mean()


def build_feature_table(master_df: pd.DataFrame,
                        filtered_df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, float], float]:
    """Build the complete feature table from filtered data.

    Runs Steps 4-14 in sequence.

    Args:
        master_df: Full master data (not currently used, reserved).
        filtered_df: Data filtered to the rolling window.

    Returns:
        Tuple of (feature_table, turnover_slopes, avg_sow).
    """
    # Steps 4-5: Team averages
    team_avgs = compute_team_averages(filtered_df)

    # Step 6: Sort by offense points
    sorted_avgs = sort_by_offense_points(team_avgs)

    # Steps 7-8: Turnover regressions
    turnover_slopes = compute_turnover_regression(filtered_df)

    # Step 9: Points adjustment
    sorted_avgs = compute_points_adjustment(sorted_avgs, turnover_slopes)

    # Steps 10-14: Win rates, SOW, stdev
    sorted_avgs = compute_win_rates(filtered_df, sorted_avgs)

    # Step 14: Average SOW
    avg_sow = compute_avg_sow(sorted_avgs)

    return sorted_avgs, turnover_slopes, avg_sow


if __name__ == "__main__":
    from data_loader import (load_master_data, load_team_glossary,
                             get_weekid_range, filter_master_data)

    master = load_master_data()
    glossary = load_team_glossary()

    weekids = get_weekid_range(2025, 1)
    filtered = filter_master_data(master, weekids)

    feature_table, turnover_slopes, avg_sow = build_feature_table(master, filtered)

    print("=== Turnover Slopes ===")
    for k, v in turnover_slopes.items():
        print(f"  {k}: {v:.4f}")

    print(f"\nAvg SOW: {avg_sow:.4f}")

    print("\n=== Feature Table (top 5 by offense points) ===")
    cols = ['team', 'offense_points', 'adj_off_points', 'adj_def_points',
            'win_rate', 'sow', 'pts_stdev']
    print(feature_table[cols].head(10).to_string(index=False))

    # Validate against spec test case: Dallas Cowboys
    dal = feature_table[feature_table['team'] == 'Dallas Cowboys'].iloc[0]
    print("\n=== Dallas Cowboys Validation ===")
    print(f"  adj_off_points (BT): {dal['adj_off_points']:.3f} (spec: 17.918)")
    print(f"  adj_def_points (BQ): {dal['adj_def_points']:.3f} (spec: 22.558)")
    print(f"  win_rate: {dal['win_rate']:.3f} (spec: 0.500)")
    print(f"  sow: {dal['sow']:.4f} (spec: 0.4444)")
    print(f"  pts_stdev: {dal['pts_stdev']:.3f} (spec: 8.264)")

    phi = feature_table[feature_table['team'] == 'Philadelphia Eagles'].iloc[0]
    print("\n=== Philadelphia Eagles Validation ===")
    print(f"  adj_off_points (BT): {phi['adj_off_points']:.3f} (spec: 23.480)")
    print(f"  adj_def_points (BQ): {phi['adj_def_points']:.3f} (spec: 15.334)")
    print(f"  win_rate: {phi['win_rate']:.3f} (spec: 0.833)")
    print(f"  sow: {phi['sow']:.4f} (spec: 0.4267)")
