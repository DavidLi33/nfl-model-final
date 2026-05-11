"""
data_loader.py — Reads and validates source data for the NFL prediction model.

Loads master game data, team glossary, control parameters, spread-to-moneyline
lookup, and weekly schedule data from CSV files.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, Tuple, Optional


# Year-to-index mapping (fixed per spec)
YEAR_INDEX_MAP: Dict[int, int] = {2022: 1, 2023: 2, 2024: 3, 2025: 4, 2026: 5, 2027: 6}

# Fixed model parameters
BACK_TEST_RANGE: int = 6
DEFAULT_SPREAD_ODDS: int = -110
DEFAULT_TOTAL_ODDS: int = -110
POISSON_GRID_SIZE: int = 80
MONTE_CARLO_SIMS: int = 1000
RISK_FREE_RATE: float = 0.000769


def load_master_data(path: str = "master_data.csv") -> pd.DataFrame:
    """Load the master game-level dataset.

    Args:
        path: Path to the master data CSV file.

    Returns:
        DataFrame with cleaned column names and proper types.
    """
    df = pd.read_csv(path)
    df.columns = df.columns.str.strip().str.lower().str.replace(" ", "_")

    # Ensure numeric columns
    numeric_cols = [
        'weekid', 'season', 'week', 'win', 'offense_points',
        'offense_yards_total', 'offense_pass_yards', 'offense_pass_attempts',
        'offense_rush_yards', 'offense_rush_attempts', 'offense_interceptions',
        'offense_fumbles', 'offense_sacks', 'offense_touchdowns',
        'defense_points', 'defense_yards_total', 'defense_pass_yards',
        'defense_pass_attempts', 'defense_rush_yards', 'defense_rush_attempts',
        'defense_interceptions', 'defense_fumbles', 'defense_sacks',
        'defense_touchdowns'
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df['offense'] = df['offense'].str.strip()

    return df


def load_team_glossary(path: str = "glossary_teams.csv") -> pd.DataFrame:
    """Load the 32-team glossary with name mappings.

    Returns:
        DataFrame with columns: full_name, city, abbr, nickname.
    """
    df = pd.read_csv(path)
    df.columns = df.columns.str.strip().str.lower().str.replace(" ", "_")
    df['full_name'] = df['full_name'].str.strip()
    df['abbr'] = df['abbr'].str.strip()
    return df


def load_spread_to_ml(path: str = "glossary_spread_to_ml.csv") -> pd.DataFrame:
    """Load the spread-to-moneyline lookup table.

    Returns:
        DataFrame with columns: game_count, spread, avg_moneyline, etc.
    """
    df = pd.read_csv(path)
    df.columns = df.columns.str.strip().str.lower().str.replace(" ", "_")
    return df


def load_schedule(path: str = "schedule_week1_2025.csv") -> pd.DataFrame:
    """Load a weekly schedule file with matchups and odds.

    Returns:
        DataFrame with game_number, teams, odds, spreads, results.
    """
    df = pd.read_csv(path)
    df.columns = df.columns.str.strip().str.lower().str.replace(" ", "_")
    # Strip whitespace from team abbreviations
    for col in ['away_team', 'home_team']:
        if col in df.columns:
            df[col] = df[col].str.strip()
    return df


def build_abbr_to_name(glossary: pd.DataFrame) -> Dict[str, str]:
    """Build abbreviation -> full team name lookup dict."""
    return dict(zip(glossary['abbr'], glossary['full_name']))


def build_name_to_abbr(glossary: pd.DataFrame) -> Dict[str, str]:
    """Build full team name -> abbreviation lookup dict."""
    return dict(zip(glossary['full_name'], glossary['abbr']))


def compute_weekid(year: int, week: int) -> int:
    """Compute the Weekid for a given year and week.

    Formula: year_index * 18 - (18 - week)
    Simplifies to: (year_index - 1) * 18 + week

    Args:
        year: NFL season year (e.g. 2025).
        week: Week number (1-18).

    Returns:
        Integer weekid.
    """
    year_index = YEAR_INDEX_MAP[year]
    return year_index * 18 - (18 - week)


def get_weekid_range(current_year: int, current_week: int,
                     back_test_range: int = BACK_TEST_RANGE) -> list:
    """Get the list of weekids in the rolling lookback window.

    The window includes `back_test_range` weeks ending at current_week - 1
    (i.e., only data available BEFORE the current week for prediction).

    Actually per the spec, the window includes weeks from
    (current_week - back_test_range) to (current_week - 1) for the prediction
    context. But the spec example shows Week 1 with back_test_range=6 going
    back into the previous season. Let me re-read...

    Per spec: for weeks in range (current_week - back_test_range + 1) to current_week
    But for prediction, we use data BEFORE current week, so the range should be
    the back_test_range weeks ending at current_week - 1 (the most recent
    completed week).

    Wait - the spec says the 20251 tab (Week 1 predictions) uses a 6-week window.
    For Week 1 2025, this would include the last 6 weeks of 2024 (weeks 13-18).

    Let me compute this correctly: the weekid for prediction week W is computed
    from weeks that have ALREADY been played. So for predicting Week 1 2025,
    we use weeks 13-18 of 2024.

    Re-reading the spec more carefully:
    - For Week 1, back 6 weeks includes weeks from previous season
    - weekid for 2024 week 13 = 3*18 - (18-13) = 54-5 = 49
    - weekid for 2024 week 18 = 3*18 - (18-18) = 54

    So the range is: last `back_test_range` completed weeks before current week.

    Args:
        current_year: Season year.
        current_week: Week number to predict.
        back_test_range: Number of weeks in lookback window.

    Returns:
        List of weekid integers.
    """
    # The current week's weekid
    current_weekid = compute_weekid(current_year, current_week)

    # We want the back_test_range weeks BEFORE the current week
    # These are weekids from (current_weekid - back_test_range) to (current_weekid - 1)
    weekids = list(range(current_weekid - back_test_range, current_weekid))

    return weekids


def filter_master_data(master_df: pd.DataFrame, weekids: list) -> pd.DataFrame:
    """Filter master data to only include rows with weekids in the given list.

    Args:
        master_df: Full master data DataFrame.
        weekids: List of weekid values to include.

    Returns:
        Filtered DataFrame.
    """
    return master_df[master_df['weekid'].isin(weekids)].copy().reset_index(drop=True)


if __name__ == "__main__":
    # Quick validation
    master = load_master_data()
    glossary = load_team_glossary()
    spread_ml = load_spread_to_ml()

    print(f"Master data: {len(master)} rows, {len(master.columns)} columns")
    print(f"Teams: {len(glossary)} teams")
    print(f"Spread-to-ML: {len(spread_ml)} entries")
    print(f"Weekid for 2025 Week 1: {compute_weekid(2025, 1)}")
    print(f"Weekid range for 2025 Week 1 (6-week lookback): {get_weekid_range(2025, 1)}")

    # Show what data we'd use for Week 1 2025 predictions
    weekids = get_weekid_range(2025, 1)
    filtered = filter_master_data(master, weekids)
    print(f"Filtered data for Week 1 2025: {len(filtered)} rows")
    print(f"Seasons in window: {filtered['season'].unique()}")
    print(f"Weeks in window: {sorted(filtered['week'].unique())}")
