# nfl-model-final
# NFL Model Final

A Python-based NFL prediction and betting workflow that:
- builds team-level features from historical game data,
- predicts weekly outcomes (winner, spread, and totals signals),
- sizes bets from bankroll rules,
- and tracks season results through a Flask dashboard.

## What this repository includes

- **Model pipeline (`main.py`)**: orchestrates loading data, feature engineering, prediction, and output exports.
- **Feature engineering (`features.py`)**: creates model inputs used by weekly predictions.
- **Prediction/bet logic (`predictor.py`)**: computes game-level forecasts and bet sizing recommendations.
- **Data utilities (`data_loader.py`, `scraper.py`)**: load local CSVs and optionally scrape/update data.
- **Web dashboard (`dashboard.py`)**: interactive Flask app to run weeks, select/lock bets, grade outcomes, and review P&L.
- **Season tracker (`tracker.py`)**: persists week/bet states as JSON files under `tracker/`.

## Quick start (local)

### 1) Install dependencies
```bash
pip install -r requirements.txt
```

### 2) Run a weekly prediction from CLI
```bash
python main.py --year 2025 --week 1 --bankroll 1000
```

Optional:
- add `--scrape` to update data before running,
- pass `--schedule schedule_week1_2025.csv` for a specific schedule file.

### 3) Run the dashboard
```bash
python dashboard.py
```
Then open `http://localhost:5050`.

## Output artifacts

Running the pipeline writes files to `output/`, including:
- week predictions,
- feature tables,
- run parameters,
- and bet sheets.

Live site:
https://nfl-model-final.onrender.com/