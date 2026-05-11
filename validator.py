"""
validator.py — Compares Python model outputs against Excel outputs.

Reads the Excel workbook's prediction sheets and compares predicted scores
for every game against Python-generated predictions.
"""

import openpyxl
import pandas as pd
import numpy as np
from pathlib import Path

from data_loader import (
    load_master_data, load_team_glossary, load_schedule,
    get_weekid_range, filter_master_data, build_abbr_to_name,
)
from features import build_feature_table
from predictor import predict_week


def extract_excel_predictions(excel_path: str, sheet_name: str) -> pd.DataFrame:
    """Extract predicted points from an Excel prediction sheet.

    Args:
        excel_path: Path to the Excel workbook.
        sheet_name: Name of the sheet (e.g., '20251' for Week 1 2025).

    Returns:
        DataFrame with columns: team, excel_predicted_pts.
    """
    wb = openpyxl.load_workbook(excel_path, data_only=True)
    ws = wb[sheet_name]

    co_col = openpyxl.utils.column_index_from_string('CO')
    df_col = openpyxl.utils.column_index_from_string('DF')

    results = []
    for r in range(4, 40):
        team = ws.cell(r, co_col).value
        pts = ws.cell(r, df_col).value
        if team and pts is not None:
            results.append({
                'team': team.strip(),
                'excel_predicted_pts': float(pts),
                'row': r,
            })

    wb.close()
    return pd.DataFrame(results)


def extract_excel_schedule(excel_path: str, sheet_name: str) -> pd.DataFrame:
    """Extract schedule data from an Excel prediction sheet.

    Args:
        excel_path: Path to the Excel workbook.
        sheet_name: Sheet name.

    Returns:
        Schedule DataFrame in standard format.
    """
    wb = openpyxl.load_workbook(excel_path, data_only=True)
    ws = wb[sheet_name]

    col_map = {
        'BZ': 'game_number', 'CA': 'week', 'CB': 'away_team',
        'CC': 'away_ml', 'CD': 'away_spread', 'CE': 'away_result',
        'CF': 'home_team', 'CG': 'home_ml', 'CH': 'home_spread',
        'CI': 'home_result', 'CJ': 'total_line', 'CK': 'total_result',
    }

    rows = []
    for r in range(4, 22):
        row = {}
        for col_letter, col_name in col_map.items():
            col_idx = openpyxl.utils.column_index_from_string(col_letter)
            row[col_name] = ws.cell(r, col_idx).value
        if row['away_team'] is not None:
            # Strip whitespace from team abbreviations
            row['away_team'] = str(row['away_team']).strip()
            row['home_team'] = str(row['home_team']).strip()
            rows.append(row)

    wb.close()
    return pd.DataFrame(rows)


def validate_week(excel_path: str, sheet_name: str,
                  year: int, week: int,
                  master_data_path: str = "master_data.csv",
                  tolerance: float = 0.01) -> pd.DataFrame:
    """Validate Python predictions against Excel for a specific week.

    Args:
        excel_path: Path to the Excel workbook.
        sheet_name: Sheet name in the workbook.
        year: NFL season year.
        week: Week number.
        master_data_path: Path to master data CSV.
        tolerance: Maximum allowed difference in predicted points.

    Returns:
        Comparison DataFrame with match/fail flags.
    """
    # Get Excel predictions
    excel_preds = extract_excel_predictions(excel_path, sheet_name)

    # Get schedule from Excel
    schedule = extract_excel_schedule(excel_path, sheet_name)

    # Run Python model
    master = load_master_data(master_data_path)
    glossary = load_team_glossary()
    abbr_to_name = build_abbr_to_name(glossary)

    weekids = get_weekid_range(year, week)
    filtered = filter_master_data(master, weekids)
    feature_table, turnover_slopes, avg_sow = build_feature_table(master, filtered)

    rng = np.random.default_rng(42)
    py_preds = predict_week(schedule, feature_table, turnover_slopes, avg_sow,
                            abbr_to_name, rng=rng)

    # Build Python lookup
    py_lookup = {}
    for _, row in py_preds.iterrows():
        py_lookup[row['away_team']] = row['away_predicted_pts']
        py_lookup[row['home_team']] = row['home_predicted_pts']

    # Compare
    excel_preds['python_predicted_pts'] = excel_preds['team'].map(py_lookup)
    excel_preds['difference'] = abs(
        excel_preds['excel_predicted_pts'] - excel_preds['python_predicted_pts']
    )
    excel_preds['match'] = excel_preds['difference'] < tolerance

    return excel_preds


def validate_all_weeks(excel_path: str,
                       master_data_path: str = "master_data.csv",
                       tolerance: float = 0.01) -> pd.DataFrame:
    """Validate all available weeks in the Excel workbook.

    Discovers prediction sheets by naming convention (e.g., '20251' = 2025 Week 1).

    Args:
        excel_path: Path to the Excel workbook.
        master_data_path: Path to master data CSV.
        tolerance: Maximum allowed difference.

    Returns:
        Combined comparison DataFrame across all weeks.
    """
    wb = openpyxl.load_workbook(excel_path, read_only=True)
    sheet_names = wb.sheetnames
    wb.close()

    all_results = []

    for sheet in sheet_names:
        # Match pattern like '20251' (year + week number)
        import re
        match = re.match(r'^(\d{4})(\d+)$', sheet)
        if not match:
            continue

        year = int(match.group(1))
        week = int(match.group(2))

        # Skip template sheets (e.g., '2025X')
        if not match.group(2).isdigit():
            continue

        print(f"\nValidating {year} Week {week} (sheet: {sheet})...")
        try:
            result = validate_week(excel_path, sheet, year, week,
                                   master_data_path, tolerance)
            result['year'] = year
            result['week'] = week
            result['sheet'] = sheet
            all_results.append(result)

            n_match = result['match'].sum()
            n_total = len(result)
            max_diff = result['difference'].max()
            print(f"  {n_match}/{n_total} predictions match "
                  f"(max diff: {max_diff:.10f})")
        except Exception as e:
            print(f"  Error: {e}")

    if all_results:
        combined = pd.concat(all_results, ignore_index=True)
        return combined

    return pd.DataFrame()


if __name__ == "__main__":
    import sys

    excel_path = "NFL Model Sample.xlsx"
    if not Path(excel_path).exists():
        print(f"Excel file not found: {excel_path}")
        sys.exit(1)

    print("Validating Python model against Excel workbook...")
    results = validate_all_weeks(excel_path)

    if results.empty:
        print("\nNo prediction sheets found to validate.")
        sys.exit(1)

    # Summary
    n_total = len(results)
    n_match = results['match'].sum()
    max_diff = results['difference'].max()

    print(f"\n{'=' * 60}")
    print(f"VALIDATION SUMMARY")
    print(f"{'=' * 60}")
    print(f"Total predictions: {n_total}")
    print(f"Matching (< 0.01): {n_match} ({n_match/n_total:.1%})")
    print(f"Failing:           {n_total - n_match}")
    print(f"Max difference:    {max_diff:.10f}")

    if n_match == n_total:
        print("\nALL PREDICTIONS MATCH.")
    else:
        print("\nFAILURES:")
        failures = results[~results['match']]
        print(failures[['year', 'week', 'team', 'excel_predicted_pts',
                        'python_predicted_pts', 'difference']].to_string(index=False))

    # Save comparison
    results.to_csv("output/validation_results.csv", index=False)
    print(f"\nSaved: output/validation_results.csv")
