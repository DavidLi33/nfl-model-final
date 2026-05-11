"""
predictor.py — Takes a week's matchups and outputs predicted scores and bet decisions.

Implements Sections H-N from the spec:
  H: Offensive/defensive point projections per component
  I: O/D weighting and final predicted score
  J: Poisson distribution grid (win probability)
  K: Monte Carlo simulation (spread/total probabilities)
  L: Prediction output (winner, spread, total, value)
  M: Bet decision logic
"""

import pandas as pd
import numpy as np
from scipy.stats import poisson, norm
from typing import Dict, Tuple, Optional
from data_loader import (
    DEFAULT_SPREAD_ODDS, DEFAULT_TOTAL_ODDS,
    POISSON_GRID_SIZE, MONTE_CARLO_SIMS,
    build_abbr_to_name, build_name_to_abbr,
)


def _lookup_team_stats(team_abbr: str, feature_table: pd.DataFrame,
                       abbr_to_name: Dict[str, str]) -> pd.Series:
    """Look up a team's row in the feature table by abbreviation."""
    full_name = abbr_to_name[team_abbr]
    row = feature_table[feature_table['team'] == full_name]
    if row.empty:
        raise ValueError(f"Team not found: {team_abbr} ({full_name})")
    return row.iloc[0]


def compute_component_points(team: pd.Series,
                             turnover_slopes: Dict[str, float]) -> Dict[str, float]:
    """Steps 17-24: Compute offensive and defensive point components for a team.

    Args:
        team: Row from feature table with all averaged stats.
        turnover_slopes: Turnover regression slopes.

    Returns:
        Dict with keys: off_pass_pts, off_rush_pts, off_fumble_cost, off_int_cost,
                        def_pass_pts_allowed, def_rush_pts_allowed,
                        def_fumble_gain, def_int_gain.
    """
    total_off_yards = max(team['offense_yards_total'], 0.001)
    total_def_yards = max(team['defense_yards_total'], 0.001)

    return {
        # Offensive components (Steps 17-20)
        'off_pass_pts': team['adj_off_points'] * (team['offense_pass_yards'] / total_off_yards),
        'off_rush_pts': team['adj_off_points'] * (team['offense_rush_yards'] / total_off_yards),
        'off_fumble_cost': team['offense_fumbles'] * turnover_slopes['pts_per_fumble_offense_loss'],
        'off_int_cost': team['offense_interceptions'] * turnover_slopes['pts_per_int_offense_loss'],
        # Defensive components (Steps 21-24)
        'def_pass_pts_allowed': team['adj_def_points'] * (team['defense_pass_yards'] / total_def_yards),
        'def_rush_pts_allowed': team['adj_def_points'] * (team['defense_rush_yards'] / total_def_yards),
        'def_fumble_gain': team['defense_fumbles'] * turnover_slopes['pts_per_fumble_defense_gain'],
        'def_int_gain': team['defense_interceptions'] * turnover_slopes['pts_per_int_defense_gain'],
    }


def compute_od_weight(away_comp: Dict, home_comp: Dict) -> float:
    """Step 25: Calculate offense/defense weighting for a game.

    Returns the away team's CX weight (0-1). Home weight = 1 - away_weight.
    """
    off_signal_away = (away_comp['off_pass_pts'] + away_comp['off_rush_pts']
                       - away_comp['off_fumble_cost'] - away_comp['off_int_cost'])
    off_signal_home = (home_comp['off_pass_pts'] + home_comp['off_rush_pts']
                       - home_comp['off_fumble_cost'] - home_comp['off_int_cost'])

    def_signal_away = (away_comp['def_pass_pts_allowed'] + away_comp['def_rush_pts_allowed']
                       + away_comp['def_fumble_gain'] + away_comp['def_int_gain'])
    def_signal_home = (home_comp['def_pass_pts_allowed'] + home_comp['def_rush_pts_allowed']
                       + home_comp['def_fumble_gain'] + home_comp['def_int_gain'])

    off_diff = abs(off_signal_away - off_signal_home)
    def_diff = abs(def_signal_away - def_signal_home)

    total = off_diff + def_diff
    if total == 0:
        return 0.5

    return off_diff / total


def compute_weighted_points(team_comp: Dict, opp_comp: Dict,
                            cx_away: float, cx_home: float) -> Dict[str, float]:
    """Step 26: Blend offensive and defensive projections with O/D weights.

    Per the spec, BOTH teams (away and home) use the same weight pattern:
      CY4 = CP4 * CX4 + CT5 * CX5  (away)
      CY5 = CP5 * CX4 + CT4 * CX5  (home)

    Both offenses are weighted by CX_away, both defenses by CX_home.

    For turnovers the pattern is reversed:
      DA4 = CV4 * CX5 + CR5 * CX4  (away)
      DA5 = CV5 * CX5 + CR4 * CX4  (home)

    Both defense gains weighted by CX_home, both offense costs by CX_away.

    Args:
        team_comp: This team's component points.
        opp_comp: Opponent's component points.
        cx_away: The away team's CX weight (offense dominance).
        cx_home: The home team's CX weight (= 1 - cx_away).

    Returns:
        Dict with weighted point components.
    """
    # Pass/Rush: all offenses weighted by CX_away, all defenses by CX_home
    passing = team_comp['off_pass_pts'] * cx_away + opp_comp['def_pass_pts_allowed'] * cx_home
    rushing = team_comp['off_rush_pts'] * cx_away + opp_comp['def_rush_pts_allowed'] * cx_home

    # Turnovers: defense gains weighted by CX_home, offense costs by CX_away
    fumbles = team_comp['def_fumble_gain'] * cx_home + opp_comp['off_fumble_cost'] * cx_away
    ints = team_comp['def_int_gain'] * cx_home + opp_comp['off_int_cost'] * cx_away

    return {
        'passing': passing,
        'rushing': rushing,
        'fumbles': fumbles,
        'ints': ints,
        'raw_total': passing + rushing + fumbles + ints,
    }


def compute_strength_adjustment(team_sow: float, opp_win_rate: float,
                                avg_sow: float) -> float:
    """Step 28: Compute strength adjustment multiplier.

    Bonus (>1) if: (SOW >= avg AND opp_wr >= 0.5) OR (SOW < avg AND opp_wr < 0.5)
    Penalty (<1) otherwise.
    """
    bonus = ((team_sow >= avg_sow and opp_win_rate >= 0.5) or
             (team_sow < avg_sow and opp_win_rate < 0.5))

    if bonus:
        return abs(team_sow - avg_sow) + 1
    else:
        return 1 - abs(team_sow - avg_sow)


def compute_poisson_win_probs(away_lambda: float, home_lambda: float,
                              grid_size: int = POISSON_GRID_SIZE) -> Tuple[float, float]:
    """Steps 30-32: Build Poisson grid and compute win probabilities.

    Args:
        away_lambda: Predicted points for away team.
        home_lambda: Predicted points for home team.
        grid_size: Maximum score to consider (0 to grid_size-1).

    Returns:
        Tuple of (P(away wins), P(home wins)).
    """
    scores = np.arange(grid_size)
    away_pmf = poisson.pmf(scores, away_lambda)
    home_pmf = poisson.pmf(scores, home_lambda)

    # Joint probability grid: grid[i][j] = P(away=i) * P(home=j)
    grid = np.outer(away_pmf, home_pmf)

    # P(away wins) = sum where i > j
    p_away = np.sum(np.tril(grid, k=-1))  # lower triangle (away > home)
    # Wait - np.tril with k=-1 gives elements where row >= col+1, i.e., i > j
    # Actually np.tril(grid, -1) gives lower triangle excluding diagonal
    # For grid[i][j], tril keeps i >= j+1, meaning i > j → away wins

    # P(home wins) = sum where j > i (upper triangle)
    p_home = np.sum(np.triu(grid, k=1))

    return p_away, p_home


def run_monte_carlo(fav_predicted: float, fav_stdev: float,
                    dog_predicted: float, dog_stdev: float,
                    n_sims: int = MONTE_CARLO_SIMS,
                    rng: Optional[np.random.Generator] = None) -> pd.DataFrame:
    """Steps 33-35: Run Monte Carlo simulation for spread/total probabilities.

    Args:
        fav_predicted: Favorite's predicted points (mean).
        fav_stdev: Favorite's historical points std dev.
        dog_predicted: Underdog's predicted points (mean).
        dog_stdev: Underdog's historical points std dev.
        n_sims: Number of simulations.
        rng: Random number generator (for reproducibility).

    Returns:
        DataFrame with columns: fav_score, dog_score, spread, total.
    """
    if rng is None:
        rng = np.random.default_rng()

    fav_scores = np.maximum(0, rng.normal(fav_predicted, fav_stdev, n_sims))
    dog_scores = np.maximum(0, rng.normal(dog_predicted, dog_stdev, n_sims))

    return pd.DataFrame({
        'fav_score': fav_scores,
        'dog_score': dog_scores,
        'spread': fav_scores - dog_scores,
        'total': fav_scores + dog_scores,
    })


def compute_ml_value(model_prob: float, ml_odds: int) -> float:
    """Step 39: Calculate expected value of a moneyline bet.

    Args:
        model_prob: Model's probability this team wins.
        ml_odds: American moneyline odds.

    Returns:
        Expected value as decimal (positive = +EV).
    """
    if ml_odds < 0:
        payout_ratio = 100 / abs(ml_odds)
    else:
        payout_ratio = ml_odds / 100

    return (model_prob * payout_ratio - (1 - model_prob)) / payout_ratio


def compute_spread_total_value(prob: float, odds: int = DEFAULT_SPREAD_ODDS) -> float:
    """Steps 43/47: Calculate expected value for spread or total bet."""
    if odds < 0:
        payout_ratio = 100 / abs(odds)
    else:
        payout_ratio = odds / 100
    return (prob * payout_ratio - (1 - prob)) / payout_ratio


def predict_game(away_abbr: str, home_abbr: str, game_num: int,
                 schedule_row: pd.Series,
                 feature_table: pd.DataFrame,
                 turnover_slopes: Dict[str, float],
                 avg_sow: float,
                 abbr_to_name: Dict[str, str],
                 rng: Optional[np.random.Generator] = None) -> Dict:
    """Run the full prediction pipeline for a single game.

    Args:
        away_abbr: Away team 3-letter abbreviation.
        home_abbr: Home team 3-letter abbreviation.
        game_num: Game number in the schedule.
        schedule_row: Row from schedule DataFrame with odds/spreads.
        feature_table: Complete feature table from features.py.
        turnover_slopes: Turnover regression slopes.
        avg_sow: League-wide average SOW.
        abbr_to_name: Abbreviation to full name lookup.
        rng: Random number generator for Monte Carlo.

    Returns:
        Dict with all prediction outputs.
    """
    # Look up team stats
    away_stats = _lookup_team_stats(away_abbr, feature_table, abbr_to_name)
    home_stats = _lookup_team_stats(home_abbr, feature_table, abbr_to_name)

    # Steps 17-24: Component points
    away_comp = compute_component_points(away_stats, turnover_slopes)
    home_comp = compute_component_points(home_stats, turnover_slopes)

    # Step 25: O/D weighting
    cx_away = compute_od_weight(away_comp, home_comp)
    cx_home = 1 - cx_away

    # Step 26: Weighted point projections
    away_weighted = compute_weighted_points(away_comp, home_comp, cx_away, cx_home)
    home_weighted = compute_weighted_points(home_comp, away_comp, cx_away, cx_home)

    # Step 28: Strength adjustment
    de_away = compute_strength_adjustment(away_stats['sow'], home_stats['win_rate'], avg_sow)
    de_home = compute_strength_adjustment(home_stats['sow'], away_stats['win_rate'], avg_sow)

    # Step 29: Final predicted points
    away_predicted = away_weighted['raw_total'] * de_away
    home_predicted = home_weighted['raw_total'] * de_home

    # Steps 30-32: Poisson win probabilities
    p_away_wins, p_home_wins = compute_poisson_win_probs(away_predicted, home_predicted)

    # Determine winner
    if away_predicted > home_predicted:
        winner = away_abbr
        winner_prob = p_away_wins
    else:
        winner = home_abbr
        winner_prob = p_home_wins

    # Step 33-35: Monte Carlo simulation
    away_ml = schedule_row.get('away_ml', 0)
    home_ml = schedule_row.get('home_ml', 0)

    if away_ml != 0 and home_ml != 0 and away_ml < home_ml:
        fav_abbr, dog_abbr = away_abbr, home_abbr
        fav_predicted, dog_predicted = away_predicted, home_predicted
        fav_stdev, dog_stdev = away_stats['pts_stdev'], home_stats['pts_stdev']
    else:
        fav_abbr, dog_abbr = home_abbr, away_abbr
        fav_predicted, dog_predicted = home_predicted, away_predicted
        fav_stdev, dog_stdev = home_stats['pts_stdev'], away_stats['pts_stdev']

    mc_results = run_monte_carlo(fav_predicted, fav_stdev,
                                 dog_predicted, dog_stdev, rng=rng)

    # Predicted spread and total
    predicted_spread = abs(away_predicted - home_predicted)
    predicted_total = away_predicted + home_predicted

    # Schedule data
    book_spread_away = schedule_row.get('away_spread', 0)
    book_spread = abs(book_spread_away) if book_spread_away else 0
    total_line = schedule_row.get('total_line', 0)

    # Step 41: Spread side
    # Determine who is the book favorite
    if book_spread_away < 0:
        book_fav = away_abbr
    else:
        book_fav = home_abbr

    if predicted_spread > book_spread and winner == book_fav:
        spread_side = "-"
    else:
        spread_side = "+"

    # Step 42: Spread probability from simulation
    if spread_side == "+":
        spread_prob = np.mean(mc_results['spread'] < book_spread)
    else:
        spread_prob = np.mean(mc_results['spread'] > book_spread)

    # Step 45: Over/under
    if predicted_total > total_line:
        ou_side = "over"
    else:
        ou_side = "under"

    # Step 46: Total probability from simulation
    if ou_side == "over":
        total_prob = np.mean(mc_results['total'] > total_line)
    else:
        total_prob = np.mean(mc_results['total'] < total_line)

    # Value calculations
    # ML value for winner
    if winner == away_abbr:
        winner_ml = away_ml
    else:
        winner_ml = home_ml

    ml_value = compute_ml_value(winner_prob, winner_ml) if winner_ml != 0 else 0
    spread_value = compute_spread_total_value(spread_prob, DEFAULT_SPREAD_ODDS)
    total_value = compute_spread_total_value(total_prob, DEFAULT_TOTAL_ODDS)

    # Line deltas
    spread_line_delta = abs(predicted_spread - book_spread)
    total_line_delta = abs(predicted_total - total_line) if total_line else 0

    return {
        'game_number': game_num,
        'away_team': away_abbr,
        'home_team': home_abbr,
        'away_predicted_pts': away_predicted,
        'home_predicted_pts': home_predicted,
        'predicted_winner': winner,
        'predicted_spread': predicted_spread,
        'predicted_total': predicted_total,
        'p_away_wins': p_away_wins,
        'p_home_wins': p_home_wins,
        'winner_prob': winner_prob,
        'winner_ml': winner_ml,
        'ml_value': ml_value,
        'book_spread': book_spread,
        'spread_side': spread_side,
        'spread_prob': spread_prob,
        'spread_value': spread_value,
        'spread_line_delta': spread_line_delta,
        'total_line': total_line,
        'ou_side': ou_side,
        'total_prob': total_prob,
        'total_value': total_value,
        'total_line_delta': total_line_delta,
        'cx_away': cx_away,
        'de_away': de_away,
        'de_home': de_home,
        'away_stdev': away_stats['pts_stdev'],
        'home_stdev': home_stats['pts_stdev'],
    }


def predict_week(schedule: pd.DataFrame,
                 feature_table: pd.DataFrame,
                 turnover_slopes: Dict[str, float],
                 avg_sow: float,
                 abbr_to_name: Dict[str, str],
                 rng: Optional[np.random.Generator] = None) -> pd.DataFrame:
    """Run predictions for all games in a weekly schedule.

    Args:
        schedule: DataFrame with game matchups and odds.
        feature_table: Complete feature table.
        turnover_slopes: Turnover regression slopes.
        avg_sow: League average SOW.
        abbr_to_name: Team abbreviation to full name mapping.
        rng: Random number generator.

    Returns:
        DataFrame with one row per game, all prediction outputs.
    """
    predictions = []

    for _, row in schedule.iterrows():
        pred = predict_game(
            away_abbr=row['away_team'],
            home_abbr=row['home_team'],
            game_num=row['game_number'],
            schedule_row=row,
            feature_table=feature_table,
            turnover_slopes=turnover_slopes,
            avg_sow=avg_sow,
            abbr_to_name=abbr_to_name,
            rng=rng,
        )
        predictions.append(pred)

    preds_df = pd.DataFrame(predictions)

    # Steps 48-50: Bet decision logic
    preds_df = apply_bet_decisions(preds_df)

    return preds_df


def apply_bet_decisions(preds_df: pd.DataFrame) -> pd.DataFrame:
    """Steps 48-50: Apply bet decision thresholds.

    ML bet: predicted_spread > average predicted spread
    Spread bet: spread_line_delta > average line delta AND side == "-"
    Total bet: total_line_delta > average total line delta
    """
    df = preds_df.copy()

    # Step 48: ML bet threshold
    avg_spread = df['predicted_spread'].mean()
    df['ml_bet'] = df['predicted_spread'].apply(
        lambda x: 'bet' if x > avg_spread else 'no'
    )

    # Step 49: Spread bet threshold
    avg_line_delta = df['spread_line_delta'].mean()
    df['spread_bet'] = df.apply(
        lambda r: 'bet' if r['spread_line_delta'] > avg_line_delta and r['spread_side'] == '-'
        else 'no', axis=1
    )

    # Step 50: Total bet threshold
    avg_total_delta = df['total_line_delta'].mean()
    df['total_bet'] = df['total_line_delta'].apply(
        lambda x: 'bet' if x > avg_total_delta else 'no'
    )

    # Store thresholds
    df.attrs['ml_threshold'] = avg_spread
    df.attrs['spread_threshold'] = avg_line_delta
    df.attrs['total_threshold'] = avg_total_delta

    return df


def compute_bet_sizing(preds_df: pd.DataFrame, bankroll: float,
                       bet_fraction: float = 0.1) -> pd.DataFrame:
    """Step 44 (Bet Sizing): Allocate bankroll proportionally to value.

    Args:
        preds_df: Predictions DataFrame with bet decisions.
        bankroll: Total bankroll amount.
        bet_fraction: Fraction of bankroll to risk per week.

    Returns:
        DataFrame with bet amounts added.
    """
    df = preds_df.copy()
    total_pool = bankroll * bet_fraction

    # Collect all positive-value bets (ML and spread only)
    ml_bets = df[df['ml_bet'] == 'bet']['ml_value'].clip(lower=0)
    spread_bets = df[df['spread_bet'] == 'bet']['spread_value'].clip(lower=0)

    total_value = ml_bets.sum() + spread_bets.sum()

    if total_value > 0:
        df['ml_bet_amount'] = df.apply(
            lambda r: max(0, r['ml_value']) / total_value * total_pool
            if r['ml_bet'] == 'bet' else 0, axis=1
        )
        df['spread_bet_amount'] = df.apply(
            lambda r: max(0, r['spread_value']) / total_value * total_pool
            if r['spread_bet'] == 'bet' else 0, axis=1
        )
    else:
        df['ml_bet_amount'] = 0
        df['spread_bet_amount'] = 0

    df['total_bet_amount'] = 0

    return df


if __name__ == "__main__":
    from data_loader import (load_master_data, load_team_glossary, load_schedule,
                             get_weekid_range, filter_master_data, build_abbr_to_name)
    from features import build_feature_table

    master = load_master_data()
    glossary = load_team_glossary()
    schedule = load_schedule("schedule_week1_2025.csv")
    abbr_to_name = build_abbr_to_name(glossary)

    weekids = get_weekid_range(2025, 1)
    filtered = filter_master_data(master, weekids)
    feature_table, turnover_slopes, avg_sow = build_feature_table(master, filtered)

    # Use fixed seed for reproducibility
    rng = np.random.default_rng(42)
    preds = predict_week(schedule, feature_table, turnover_slopes, avg_sow, abbr_to_name, rng=rng)

    print("=== Week 1 2025 Predictions ===")
    cols = ['away_team', 'home_team', 'away_predicted_pts', 'home_predicted_pts',
            'predicted_winner', 'predicted_spread', 'predicted_total', 'ml_bet']
    print(preds[cols].to_string(index=False))

    # Validate test case 1: DAL @ PHI
    dal_phi = preds[preds['game_number'] == 1].iloc[0]
    print(f"\n=== DAL @ PHI Validation ===")
    print(f"  DAL predicted: {dal_phi['away_predicted_pts']:.3f} (spec: 21.760)")
    print(f"  PHI predicted: {dal_phi['home_predicted_pts']:.3f} (spec: 29.113)")
    print(f"  Spread: {dal_phi['predicted_spread']:.3f} (spec: 7.353)")
    print(f"  Total: {dal_phi['predicted_total']:.3f} (spec: 50.873)")
    print(f"  Winner: {dal_phi['predicted_winner']} (spec: PHI)")
    print(f"  CX_away: {dal_phi['cx_away']:.3f} (spec: 0.475)")

    # Validate test case 2: KC @ LAC
    kc_lac = preds[preds['game_number'] == 2].iloc[0]
    print(f"\n=== KC @ LAC Validation ===")
    print(f"  KC predicted: {kc_lac['away_predicted_pts']:.3f} (spec: 18.466)")
    print(f"  LAC predicted: {kc_lac['home_predicted_pts']:.3f} (spec: 25.854)")

    # Validate test case 3: CIN @ CLE
    cin_cle = preds[preds['game_number'] == 6].iloc[0]
    print(f"\n=== CIN @ CLE Validation ===")
    print(f"  CIN predicted: {cin_cle['away_predicted_pts']:.3f} (spec: 30.643)")
    print(f"  CLE predicted: {cin_cle['home_predicted_pts']:.3f} (spec: 9.675)")
