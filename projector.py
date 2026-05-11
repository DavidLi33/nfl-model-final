"""
projector.py — Projects rest-of-season results using the same predict_game()
pipeline the weekly model uses.

For each remaining game in the schedule, we compute the win probability
(p_away_wins / p_home_wins from the Poisson grid) and sum them per team to get
expected total wins. Then we derive divisional seeding for playoffs.

Entry point:
    project_season(year, current_week, team_stats, turnover_slopes, avg_sow,
                   abbr_to_name) -> dict
"""

import os
import ssl
import warnings
import numpy as np
import pandas as pd
from typing import Dict, Optional

# SSL fix for nfl_data_py on macOS (same as scraper.py)
try:
    import certifi
    os.environ.setdefault('SSL_CERT_FILE', certifi.where())
    os.environ.setdefault('REQUESTS_CA_BUNDLE', certifi.where())
except ImportError:
    pass
ssl._create_default_https_context = ssl._create_unverified_context

warnings.filterwarnings('ignore', category=FutureWarning)


# NFL division structure (AFC/NFC East/North/South/West)
DIVISIONS: Dict[str, tuple] = {
    # AFC East
    'BUF': ('AFC', 'East'), 'MIA': ('AFC', 'East'),
    'NE':  ('AFC', 'East'), 'NYJ': ('AFC', 'East'),
    # AFC North
    'BAL': ('AFC', 'North'), 'CIN': ('AFC', 'North'),
    'CLE': ('AFC', 'North'), 'PIT': ('AFC', 'North'),
    # AFC South
    'HOU': ('AFC', 'South'), 'IND': ('AFC', 'South'),
    'JAX': ('AFC', 'South'), 'TEN': ('AFC', 'South'),
    # AFC West
    'DEN': ('AFC', 'West'), 'KC':  ('AFC', 'West'),
    'LV':  ('AFC', 'West'), 'LAC': ('AFC', 'West'),
    # NFC East
    'DAL': ('NFC', 'East'), 'NYG': ('NFC', 'East'),
    'PHI': ('NFC', 'East'), 'WAS': ('NFC', 'East'),
    # NFC North
    'CHI': ('NFC', 'North'), 'DET': ('NFC', 'North'),
    'GB':  ('NFC', 'North'), 'MIN': ('NFC', 'North'),
    # NFC South
    'ATL': ('NFC', 'South'), 'CAR': ('NFC', 'South'),
    'NO':  ('NFC', 'South'), 'TB':  ('NFC', 'South'),
    # NFC West
    'ARI': ('NFC', 'West'), 'LAR': ('NFC', 'West'),
    'SF':  ('NFC', 'West'), 'SEA': ('NFC', 'West'),
}

NFLVERSE_ABBR_MAP = {'LA': 'LAR', 'OAK': 'LV', 'SD': 'LAC', 'STL': 'LAR'}


def _fix_abbr(abbr) -> str:
    if pd.isna(abbr):
        return ''
    s = str(abbr)
    return NFLVERSE_ABBR_MAP.get(s, s)


def fetch_full_schedule(season: int) -> pd.DataFrame:
    """Pull the full regular-season schedule for a year (includes played + future games).

    If the requested season has no schedule yet (common for future years before
    the league releases the next slate), fall back to the prior year as a proxy
    so we can still produce a season projection. The returned DataFrame has an
    attribute `_source_year` indicating which year the schedule actually came
    from. Odds/spread columns may be all-null for future years — predict_game
    handles zero odds gracefully.
    """
    import nfl_data_py as nfl

    def _load(yr: int) -> pd.DataFrame:
        df = nfl.import_schedules([yr])
        df = df[df['game_type'] == 'REG'].copy()
        df['away_team'] = df['away_team'].apply(_fix_abbr)
        df['home_team'] = df['home_team'].apply(_fix_abbr)
        return df

    sched = _load(season)
    source_year = season

    # Fall back to prior year if API returned nothing for the requested season.
    if len(sched) == 0:
        try:
            sched = _load(season - 1)
            source_year = season - 1
            # Strip any actual scores from the proxy — they're from a different
            # year and would mis-populate "completed games" records.
            if 'away_score' in sched.columns:
                sched['away_score'] = np.nan
            if 'home_score' in sched.columns:
                sched['home_score'] = np.nan
        except Exception:
            pass

    sched.attrs['source_year'] = source_year
    sched.attrs['requested_year'] = season
    return sched


def compute_current_records(schedule: pd.DataFrame, current_week: int) -> Dict[str, dict]:
    """Compute records from games played BEFORE `current_week`.

    Only counts games strictly before the projection's starting week — those are
    "in the books." Games at or after `current_week` are re-predicted by the
    model regardless of any actual score on file (this matters for retrospective
    testing on a completed season; we don't want to double-count a game as both
    a recorded win AND a predicted win).
    """
    records: Dict[str, dict] = {}

    for abbr in DIVISIONS.keys():
        records[abbr] = {'wins': 0, 'losses': 0, 'ties': 0, 'pf': 0, 'pa': 0}

    for _, game in schedule.iterrows():
        wk = int(game['week']) if pd.notna(game.get('week')) else 0
        if wk >= current_week:
            continue  # this game is in the projection window

        away = game['away_team']
        home = game['home_team']
        a_score = game.get('away_score')
        h_score = game.get('home_score')

        if pd.isna(a_score) or pd.isna(h_score):
            continue  # game not yet played
        if away not in records or home not in records:
            continue

        a_score = int(a_score)
        h_score = int(h_score)

        records[away]['pf'] += a_score
        records[away]['pa'] += h_score
        records[home]['pf'] += h_score
        records[home]['pa'] += a_score

        if a_score > h_score:
            records[away]['wins'] += 1
            records[home]['losses'] += 1
        elif h_score > a_score:
            records[home]['wins'] += 1
            records[away]['losses'] += 1
        else:
            records[away]['ties'] += 1
            records[home]['ties'] += 1

    return records


def reconstruct_feature_table(team_stats: Dict[str, dict],
                              abbr_to_name: Dict[str, str]) -> pd.DataFrame:
    """Rebuild a feature_table DataFrame (one row per team) from the dict
    stored in the tracker.

    The predict_game() pipeline looks up teams by full_name, so we make sure
    every row has a 'team' column with the full name.
    """
    rows = []
    for abbr, stats in team_stats.items():
        row = dict(stats)
        if 'team' not in row or not row['team']:
            row['team'] = abbr_to_name.get(abbr, abbr)
        rows.append(row)
    return pd.DataFrame(rows)


def _safe_num(value, default=0):
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def project_season(year: int, current_week: int,
                   team_stats: Dict[str, dict],
                   turnover_slopes: Dict[str, float],
                   avg_sow: float,
                   abbr_to_name: Dict[str, str]) -> dict:
    """Run projection for the rest of the NFL season.

    Args:
        year: Season year.
        current_week: Week we're currently predicting (inclusive — its games are
            treated as unplayed for projection purposes).
        team_stats: Dict of {abbr: feature_table_row_dict} from tracker.
        turnover_slopes: Turnover regression slopes from the current week's build.
        avg_sow: League avg SOW from the current week's build.
        abbr_to_name: Abbreviation -> full-name map.

    Returns:
        {
          'standings': {abbr: {current_w, current_l, proj_w, expected_wins,
                               upcoming: [...]}},
          'seeding': {'AFC': [...], 'NFC': [...]},
          'current_week': int,
        }
    """
    from predictor import predict_game

    schedule = fetch_full_schedule(year)
    source_year = schedule.attrs.get('source_year', year)
    proxy_schedule = (source_year != year)
    current_records = compute_current_records(schedule, current_week)
    feature_table = reconstruct_feature_table(team_stats, abbr_to_name)

    # Which teams do we actually have features for?
    teams_with_features = set()
    for _, row in feature_table.iterrows():
        teams_with_features.add(row['team'])

    # Initialize projections
    projections: Dict[str, dict] = {}
    for abbr in DIVISIONS.keys():
        rec = current_records.get(abbr, {'wins': 0, 'losses': 0, 'ties': 0})
        projections[abbr] = {
            'team': abbr,
            'full_name': abbr_to_name.get(abbr, abbr),
            'conference': DIVISIONS[abbr][0],
            'division': DIVISIONS[abbr][1],
            'current_w': rec['wins'],
            'current_l': rec['losses'],
            'current_t': rec['ties'],
            'proj_w': 0.0,           # sum of win probabilities on remaining games
            'proj_games': 0,
            'upcoming': [],
        }

    rng = np.random.default_rng(42)
    remaining = schedule[schedule['week'] >= current_week].copy()

    for _, game in remaining.iterrows():
        wk = int(game['week']) if pd.notna(game.get('week')) else 0
        away = game['away_team']
        home = game['home_team']

        if away not in projections or home not in projections:
            continue

        away_name = abbr_to_name.get(away)
        home_name = abbr_to_name.get(home)
        if away_name not in teams_with_features or home_name not in teams_with_features:
            continue

        sched_row = pd.Series({
            'away_team': away,
            'home_team': home,
            'game_number': 0,
            'away_ml': _safe_num(game.get('away_moneyline')),
            'home_ml': _safe_num(game.get('home_moneyline')),
            'away_spread': -_safe_num(game.get('spread_line')),
            'home_spread': _safe_num(game.get('spread_line')),
            'total_line': _safe_num(game.get('total_line')),
        })

        try:
            pred = predict_game(away, home, 0, sched_row, feature_table,
                                turnover_slopes, avg_sow, abbr_to_name, rng=rng)
        except Exception:
            continue

        p_away = float(pred['p_away_wins'])
        p_home = float(pred['p_home_wins'])

        projections[away]['proj_w'] += p_away
        projections[away]['proj_games'] += 1
        projections[home]['proj_w'] += p_home
        projections[home]['proj_games'] += 1

        away_pts = float(pred['away_predicted_pts'])
        home_pts = float(pred['home_predicted_pts'])
        winner = pred['predicted_winner']

        projections[away]['upcoming'].append({
            'week': wk,
            'opponent': home,
            'home_away': 'away',
            'win_prob': round(p_away, 4),
            'predicted_win': winner == away,
            'team_pts': round(away_pts, 1),
            'opp_pts': round(home_pts, 1),
        })
        projections[home]['upcoming'].append({
            'week': wk,
            'opponent': away,
            'home_away': 'home',
            'win_prob': round(p_home, 4),
            'predicted_win': winner == home,
            'team_pts': round(home_pts, 1),
            'opp_pts': round(away_pts, 1),
        })

    # Finalize totals
    for abbr, proj in projections.items():
        proj['expected_wins'] = round(proj['current_w'] + proj['proj_w'], 2)
        proj['upcoming'].sort(key=lambda x: x['week'])
        proj['proj_w'] = round(proj['proj_w'], 2)
        # predicted_wins: count of upcoming games we expect to WIN (binary),
        # plus games already won. Matches the "WIN" labels shown in the expanded
        # schedule view so the big number == sum of WINs visible to the user.
        predicted_upcoming_wins = sum(
            1 for g in proj['upcoming'] if g.get('predicted_win')
        )
        proj['predicted_wins'] = proj['current_w'] + predicted_upcoming_wins
        proj['predicted_losses'] = (
            proj['current_l']
            + sum(1 for g in proj['upcoming'] if not g.get('predicted_win'))
        )

    seeding = compute_seeding(projections)
    bracket = simulate_playoff_bracket(seeding, team_stats, turnover_slopes,
                                       avg_sow, abbr_to_name)

    return {
        'standings': projections,
        'seeding': seeding,
        'bracket': bracket,
        'current_week': current_week,
        'year': year,
        'schedule_source_year': source_year,
        'proxy_schedule': proxy_schedule,
    }


def _predict_playoff_game(higher_seed: str, lower_seed: str,
                          feature_table: pd.DataFrame,
                          turnover_slopes: Dict[str, float],
                          avg_sow: float,
                          abbr_to_name: Dict[str, str],
                          neutral: bool = False,
                          rng=None) -> dict:
    """Predict a single playoff game.

    The higher-seeded team hosts (so they're 'home') unless it's the Super Bowl.
    For neutral-site games we pick a coin-flip home assignment.
    """
    from predictor import predict_game

    if neutral:
        # Just put the higher seed as home (doesn't materially affect Poisson model)
        home, away = higher_seed, lower_seed
    else:
        home, away = higher_seed, lower_seed

    sched_row = pd.Series({
        'away_team': away, 'home_team': home, 'game_number': 0,
        'away_ml': 0, 'home_ml': 0,
        'away_spread': 0, 'home_spread': 0,
        'total_line': 0,
    })

    pred = predict_game(away, home, 0, sched_row, feature_table,
                        turnover_slopes, avg_sow, abbr_to_name, rng=rng)

    p_home = float(pred['p_home_wins'])
    p_away = float(pred['p_away_wins'])
    winner = home if p_home >= p_away else away
    win_prob = p_home if winner == home else p_away

    return {
        'home': home,
        'away': away,
        'home_pts': round(float(pred['home_predicted_pts']), 1),
        'away_pts': round(float(pred['away_predicted_pts']), 1),
        'winner': winner,
        'win_prob': round(win_prob, 4),
    }


def simulate_playoff_bracket(seeding: dict, team_stats: dict,
                             turnover_slopes: Dict[str, float],
                             avg_sow: float,
                             abbr_to_name: Dict[str, str]) -> dict:
    """Simulate the playoff bracket using the same model.

    NFL format (per conference, 7 teams):
      Wild Card: #1 bye; #2v#7, #3v#6, #4v#5
      Divisional: re-seed survivors; #1 hosts lowest, second-highest hosts other
      Conference: 2 survivors meet
      Super Bowl: AFC winner vs NFC winner

    Returns:
      {
        'AFC': {'wc': [...], 'div': [...], 'champ': {...}},
        'NFC': {...},
        'super_bowl': {...},
        'champion': 'KC',
      }
    """
    feature_table = reconstruct_feature_table(team_stats, abbr_to_name)
    rng = np.random.default_rng(42)

    bracket = {'AFC': {}, 'NFC': {}}

    for conf in ('AFC', 'NFC'):
        seeds = {s['seed']: s['team'] for s in seeding[conf]}
        if len(seeds) < 7:
            bracket[conf] = {'wc': [], 'div': [], 'champ': None, 'error': 'Not enough seeds'}
            continue

        # Wild Card Round
        wc_matchups = [(2, 7), (3, 6), (4, 5)]
        wc_results = []
        for hi, lo in wc_matchups:
            r = _predict_playoff_game(seeds[hi], seeds[lo], feature_table,
                                      turnover_slopes, avg_sow, abbr_to_name, rng=rng)
            r['higher_seed'] = hi
            r['lower_seed'] = lo
            r['winner_seed'] = hi if r['winner'] == seeds[hi] else lo
            wc_results.append(r)
        bracket[conf]['wc'] = wc_results

        # Divisional Round — #1 plus 3 wild-card winners, re-seeded
        survivors = [1] + [r['winner_seed'] for r in wc_results]
        survivors_sorted = sorted(survivors)  # by seed ascending (1 is best)
        # Top seed plays lowest-seeded survivor; the other two play
        s_top = survivors_sorted[0]
        s_low = survivors_sorted[-1]
        s_mid_a = survivors_sorted[1]
        s_mid_b = survivors_sorted[2]

        div_matchups = [(s_top, s_low), (s_mid_a, s_mid_b)]
        div_results = []
        for hi, lo in div_matchups:
            r = _predict_playoff_game(seeds[hi], seeds[lo], feature_table,
                                      turnover_slopes, avg_sow, abbr_to_name, rng=rng)
            r['higher_seed'] = hi
            r['lower_seed'] = lo
            r['winner_seed'] = hi if r['winner'] == seeds[hi] else lo
            div_results.append(r)
        bracket[conf]['div'] = div_results

        # Conference Championship
        champ_seeds = sorted(r['winner_seed'] for r in div_results)
        hi, lo = champ_seeds[0], champ_seeds[1]
        champ_game = _predict_playoff_game(seeds[hi], seeds[lo], feature_table,
                                           turnover_slopes, avg_sow,
                                           abbr_to_name, rng=rng)
        champ_game['higher_seed'] = hi
        champ_game['lower_seed'] = lo
        champ_game['winner_seed'] = hi if champ_game['winner'] == seeds[hi] else lo
        bracket[conf]['champ'] = champ_game

    # Super Bowl (neutral site)
    afc_winner = bracket['AFC'].get('champ', {}).get('winner')
    nfc_winner = bracket['NFC'].get('champ', {}).get('winner')

    super_bowl = None
    champion = None
    if afc_winner and nfc_winner:
        # AFC nominally home; doesn't materially change result
        sb = _predict_playoff_game(afc_winner, nfc_winner, feature_table,
                                   turnover_slopes, avg_sow, abbr_to_name,
                                   neutral=True, rng=rng)
        sb['afc_team'] = afc_winner
        sb['nfc_team'] = nfc_winner
        super_bowl = sb
        champion = sb['winner']

    return {
        **bracket,
        'super_bowl': super_bowl,
        'champion': champion,
    }


def compute_seeding(projections: Dict[str, dict]) -> dict:
    """Simplified NFL playoff seeding based on expected wins.

    - 4 division winners per conference get seeds 1-4 (by expected wins)
    - 3 wild cards per conference get seeds 5-7 (highest remaining by expected wins)
    Tiebreaker: point differential (pf - pa); ties broken alphabetically.
    """
    div_groups: Dict[tuple, list] = {}
    for abbr, proj in projections.items():
        key = (proj['conference'], proj['division'])
        div_groups.setdefault(key, []).append(proj)

    div_winners = {'AFC': [], 'NFC': []}
    remaining_pool = {'AFC': [], 'NFC': []}

    def sort_key(p):
        # Sort by predicted_wins (binary count, matches what user sees) then
        # expected_wins as tiebreak, then team name.
        return (-p.get('predicted_wins', 0), -p['expected_wins'], p['team'])

    for (conf, div), teams in div_groups.items():
        sorted_teams = sorted(teams, key=sort_key)
        if sorted_teams:
            div_winners[conf].append(sorted_teams[0])
            remaining_pool[conf].extend(sorted_teams[1:])

    seeding = {'AFC': [], 'NFC': []}
    for conf in ('AFC', 'NFC'):
        div_ws = sorted(div_winners[conf], key=sort_key)
        wcs = sorted(remaining_pool[conf], key=sort_key)[:3]

        seed_num = 1
        for p in div_ws:
            seeding[conf].append({
                'seed': seed_num,
                'team': p['team'],
                'full_name': p['full_name'],
                'expected_wins': p['expected_wins'],
                'predicted_wins': p.get('predicted_wins', 0),
                'current_record': f"{p['current_w']}-{p['current_l']}",
                'division': p['division'],
                'div_winner': True,
            })
            seed_num += 1
        seed_num = 5
        for p in wcs:
            seeding[conf].append({
                'seed': seed_num,
                'team': p['team'],
                'full_name': p['full_name'],
                'expected_wins': p['expected_wins'],
                'predicted_wins': p.get('predicted_wins', 0),
                'current_record': f"{p['current_w']}-{p['current_l']}",
                'division': p['division'],
                'div_winner': False,
            })
            seed_num += 1

    return seeding


if __name__ == "__main__":
    # Quick test
    from data_loader import load_master_data, load_team_glossary, build_abbr_to_name
    from data_loader import get_weekid_range, filter_master_data
    from features import build_feature_table

    master = load_master_data()
    glossary = load_team_glossary()
    abbr_to_name = build_abbr_to_name(glossary)
    name_to_abbr = {v: k for k, v in abbr_to_name.items()}

    weekids = get_weekid_range(2025, 6)
    filtered = filter_master_data(master, weekids)
    feature_table, turnover_slopes, avg_sow = build_feature_table(master, filtered)

    team_stats = {}
    for _, row in feature_table.iterrows():
        abbr = name_to_abbr.get(row['team'], '')
        if abbr:
            team_stats[abbr] = row.to_dict()

    result = project_season(2025, 6, team_stats, turnover_slopes, avg_sow, abbr_to_name)
    print("=== Top 5 by expected wins ===")
    standings = sorted(result['standings'].values(), key=lambda p: -p['expected_wins'])
    for p in standings[:5]:
        print(f"  {p['team']:4s}  {p['current_w']}-{p['current_l']}  "
              f"proj W: {p['proj_w']:.1f}  total: {p['expected_wins']:.1f}")
    print("\n=== AFC Seeding ===")
    for s in result['seeding']['AFC']:
        print(f"  #{s['seed']} {s['team']:4s} "
              f"{'(div)' if s['div_winner'] else '(wc) '} "
              f"exp wins: {s['expected_wins']}")
