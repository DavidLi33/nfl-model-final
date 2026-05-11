"""
scraper.py — Pulls NFL game data from nflverse (via nfl_data_py).

Outputs data in the exact same format as the master_data.csv so it
plugs directly into the prediction model.

nflverse provides publicly hosted parquet files with play-by-play and
schedule data sourced from NFL/PFR — no web scraping required.

Usage:
    from scraper import scrape_season, scrape_weeks, update_master_data

    # Full season
    df = scrape_season(2024)
    df.to_csv('master_data.csv', index=False)

    # Specific weeks
    df = scrape_weeks(2025, [1, 2, 3])
"""

import ssl
import os
import warnings

# Fix SSL certificate issues common on macOS Python installs
try:
    import certifi
    os.environ.setdefault('SSL_CERT_FILE', certifi.where())
    os.environ.setdefault('REQUESTS_CA_BUNDLE', certifi.where())
except ImportError:
    pass
ssl._create_default_https_context = ssl._create_unverified_context

import nfl_data_py as nfl
import pandas as pd
import numpy as np
from typing import List, Optional, Dict
from pathlib import Path

from data_loader import compute_weekid, load_team_glossary, build_abbr_to_name

warnings.filterwarnings('ignore', category=FutureWarning)

# nflverse uses different abbreviations than our model in some cases
NFLVERSE_ABBR_MAP = {
    'LA': 'LAR',
    'OAK': 'LV',
    'SD': 'LAC',
    'STL': 'LAR',
}


def _fix_abbr(abbr: str) -> str:
    """Convert nflverse abbreviation to our model's abbreviation."""
    if pd.isna(abbr):
        return ''
    return NFLVERSE_ABBR_MAP.get(abbr, abbr)


def _aggregate_game_stats(pbp: pd.DataFrame, season: int) -> pd.DataFrame:
    """Aggregate play-by-play data into per-team game-level stats.

    Produces two rows per game (one per team) matching the master data schema.

    Args:
        pbp: Play-by-play DataFrame from nfl_data_py.
        season: NFL season year.

    Returns:
        DataFrame in master_data.csv format.
    """
    # Filter to actual plays (exclude timeouts, penalties without plays, etc.)
    plays = pbp[pbp['play_type'].notna()].copy()
    plays = plays[~plays['play_type'].isin(['no_play', 'qb_kneel', 'qb_spike'])]

    # Get schedule for game-level metadata
    sched = nfl.import_schedules([season])

    glossary = load_team_glossary()
    abbr_to_name = build_abbr_to_name(glossary)

    master_rows = []

    # Group by game
    for game_id, game_plays in plays.groupby('game_id'):
        # Get schedule info
        sched_row = sched[sched['game_id'] == game_id]
        if sched_row.empty:
            continue
        sched_row = sched_row.iloc[0]

        week = int(sched_row['week'])
        away_team = _fix_abbr(sched_row['away_team'])
        home_team = _fix_abbr(sched_row['home_team'])
        game_date = str(sched_row['gameday'])
        away_score = int(sched_row['away_score']) if pd.notna(sched_row['away_score']) else 0
        home_score = int(sched_row['home_score']) if pd.notna(sched_row['home_score']) else 0

        weekid = compute_weekid(season, week)

        # Aggregate stats per team (offensive perspective)
        for team, opp, is_home in [(away_team, home_team, False),
                                    (home_team, away_team, True)]:
            team_plays = game_plays[game_plays['posteam'] == sched_row[
                'home_team' if is_home else 'away_team']]
            opp_plays = game_plays[game_plays['posteam'] == sched_row[
                'away_team' if is_home else 'home_team']]

            team_score = home_score if is_home else away_score
            opp_score = away_score if is_home else home_score

            # Offensive stats
            off_pass_yards = int(team_plays['passing_yards'].sum())
            off_pass_attempts = int(team_plays['pass_attempt'].sum())
            off_rush_yards = int(team_plays['rushing_yards'].sum())
            off_rush_attempts = int(team_plays['rush_attempt'].sum())
            off_yards_total = off_pass_yards + off_rush_yards
            off_interceptions = int(team_plays['interception'].sum())
            off_fumbles = int(team_plays['fumble_lost'].sum())
            off_sacks = int(team_plays['sack'].sum())
            off_touchdowns = int(team_plays['touchdown'].sum())

            # Defensive stats (opponent's offensive plays = our defensive perspective)
            def_pass_yards = int(opp_plays['passing_yards'].sum())
            def_pass_attempts = int(opp_plays['pass_attempt'].sum())
            def_rush_yards = int(opp_plays['rushing_yards'].sum())
            def_rush_attempts = int(opp_plays['rush_attempt'].sum())
            def_yards_total = def_pass_yards + def_rush_yards
            def_interceptions = int(opp_plays['interception'].sum())
            def_fumbles = int(opp_plays['fumble_lost'].sum())
            def_sacks = int(opp_plays['sack'].sum())
            def_touchdowns = int(opp_plays['touchdown'].sum())

            team_name = abbr_to_name.get(team, team)

            master_rows.append({
                'weekid': weekid,
                'season': season,
                'date': game_date,
                'week': week,
                'away': away_team,
                'vs': '@',
                'home': home_team,
                'win': 1 if team_score > opp_score else 0,
                'offense': team_name,
                'offense_points': team_score,
                'offense_yards_total': off_yards_total,
                'offense_pass_yards': off_pass_yards,
                'offense_pass_attempts': off_pass_attempts,
                'offense_rush_yards': off_rush_yards,
                'offense_rush_attempts': off_rush_attempts,
                'offense_interceptions': off_interceptions,
                'offense_fumbles': off_fumbles,
                'offense_sacks': off_sacks,
                'offense_touchdowns': off_touchdowns,
                'defense_points': opp_score,
                'defense_yards_total': def_yards_total,
                'defense_pass_yards': def_pass_yards,
                'defense_pass_attempts': def_pass_attempts,
                'defense_rush_yards': def_rush_yards,
                'defense_rush_attempts': def_rush_attempts,
                'defense_interceptions': def_interceptions,
                'defense_fumbles': def_fumbles,
                'defense_sacks': def_sacks,
                'defense_touchdowns': def_touchdowns,
            })

    df = pd.DataFrame(master_rows)
    # Sort by weekid then by game order (away team pairs)
    df = df.sort_values(['weekid', 'away', 'offense']).reset_index(drop=True)
    return df


def scrape_season(season: int, max_week: int = 18) -> pd.DataFrame:
    """Pull all game data for a full NFL season.

    Args:
        season: NFL season year (e.g. 2024).
        max_week: Last week to include (default 18 for full regular season).

    Returns:
        DataFrame in master_data.csv format.
    """
    print(f"Downloading {season} play-by-play data...")
    pbp = nfl.import_pbp_data([season])
    print(f"  {len(pbp)} plays loaded")

    # Filter to regular season weeks
    pbp = pbp[pbp['week'] <= max_week]

    print("Aggregating to game-level stats...")
    df = _aggregate_game_stats(pbp, season)
    print(f"  {len(df)} rows ({len(df)//2} games)")

    return df


def scrape_weeks(season: int, weeks: List[int]) -> pd.DataFrame:
    """Pull game data for specific weeks of a season.

    Args:
        season: NFL season year.
        weeks: List of week numbers to include.

    Returns:
        DataFrame in master_data.csv format.
    """
    print(f"Downloading {season} play-by-play data...")
    pbp = nfl.import_pbp_data([season])

    # Filter to requested weeks
    pbp = pbp[pbp['week'].isin(weeks)]
    print(f"  Filtered to weeks {weeks}: {len(pbp)} plays")

    df = _aggregate_game_stats(pbp, season)
    print(f"  {len(df)} rows ({len(df)//2} games)")

    return df


def scrape_schedule(season: int, week: int) -> pd.DataFrame:
    """Pull schedule data (odds, spreads, totals) for a specific week.

    Returns data in the schedule CSV format used by the model.

    Args:
        season: NFL season year.
        week: Week number.

    Returns:
        DataFrame with columns matching schedule_weekN_YEAR.csv format.
    """
    sched = nfl.import_schedules([season])
    week_games = sched[sched['week'] == week].copy()
    week_games = week_games[week_games['game_type'] == 'REG']

    rows = []
    for i, (_, game) in enumerate(week_games.iterrows(), 1):
        away = _fix_abbr(game['away_team'])
        home = _fix_abbr(game['home_team'])

        # Spread is from home team perspective in nflverse (negative = home favored)
        spread_line = game.get('spread_line', 0)
        if pd.isna(spread_line):
            spread_line = 0
        away_spread = -spread_line  # Convert: our format has away perspective
        home_spread = spread_line

        away_ml = int(game['away_moneyline']) if pd.notna(game.get('away_moneyline')) else 0
        home_ml = int(game['home_moneyline']) if pd.notna(game.get('home_moneyline')) else 0

        total_line = game.get('total_line', 0)
        if pd.isna(total_line):
            total_line = 0

        away_score = int(game['away_score']) if pd.notna(game.get('away_score')) else ''
        home_score = int(game['home_score']) if pd.notna(game.get('home_score')) else ''
        total_result = (away_score + home_score) if away_score != '' and home_score != '' else ''

        rows.append({
            'game_number': i,
            'week': week,
            'away_team': away,
            'away_ml': away_ml,
            'away_spread': away_spread,
            'away_result': away_score,
            'home_team': home,
            'home_ml': home_ml,
            'home_spread': home_spread,
            'home_result': home_score,
            'total_line': total_line,
            'total_result': total_result,
        })

    return pd.DataFrame(rows)


def update_master_data(master_path: str, seasons: List[int],
                       max_week: int = 18) -> pd.DataFrame:
    """Build or update the master data CSV from nflverse data.

    Scrapes any seasons not already present in the master data.

    Args:
        master_path: Path to master_data.csv.
        seasons: List of seasons to include.
        max_week: Max week per season.

    Returns:
        Updated DataFrame.
    """
    try:
        from data_loader import load_master_data
        existing = load_master_data(master_path)
        existing_seasons = set(existing['season'].unique())
    except FileNotFoundError:
        existing = pd.DataFrame()
        existing_seasons = set()

    new_dfs = []
    for season in seasons:
        if season in existing_seasons:
            print(f"{season}: already in master data, skipping")
            continue
        print(f"\n{season}: scraping...")
        df = scrape_season(season, max_week)
        new_dfs.append(df)

    if new_dfs:
        new_data = pd.concat(new_dfs, ignore_index=True)
        if not existing.empty:
            combined = pd.concat([existing, new_data], ignore_index=True)
        else:
            combined = new_data
        combined = combined.sort_values(['weekid', 'away', 'offense']).reset_index(drop=True)
        combined.to_csv(master_path, index=False)
        print(f"\nSaved {len(combined)} rows to {master_path}")
        return combined

    return existing


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NFL Data Scraper (nflverse)")
    parser.add_argument('--season', type=int, required=True, help="Season year")
    parser.add_argument('--week', type=int, default=None,
                        help="Specific week (omit for full season)")
    parser.add_argument('--schedule', action='store_true',
                        help="Also generate schedule CSV with odds/spreads")
    parser.add_argument('--output', default=None, help="Output CSV path")

    args = parser.parse_args()

    if args.week:
        print(f"Scraping {args.season} Week {args.week}...")
        df = scrape_weeks(args.season, [args.week])
        out_path = args.output or f"scraped_{args.season}_week{args.week}.csv"

        if args.schedule:
            sched = scrape_schedule(args.season, args.week)
            sched_path = f"schedule_week{args.week}_{args.season}.csv"
            sched.to_csv(sched_path, index=False)
            print(f"Schedule saved: {sched_path}")
    else:
        print(f"Scraping full {args.season} season...")
        df = scrape_season(args.season)
        out_path = args.output or f"scraped_{args.season}.csv"

    df.to_csv(out_path, index=False)
    print(f"\nGame data saved: {out_path} ({len(df)} rows, {len(df)//2} games)")
