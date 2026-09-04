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

## Render deployment

Use these commands for the Render Web Service:

```bash
pip install -r requirements.txt
```

```bash
gunicorn dashboard:app
```

Set these environment variables:

```text
APP_PASSWORD=<your write-action password>
SECRET_KEY=<long random string>
```

Optional cross-device last-screen restore uses Upstash Redis. Create a free
Upstash Redis database and add:

```text
UPSTASH_REDIS_REST_URL=<your Upstash REST URL>
UPSTASH_REDIS_REST_TOKEN=<your Upstash REST token>
```

When a user unlocks write mode, they enter the shared password plus their name.
The app stores that user's last dashboard screen under a Redis key like
`last_screen:david`, so the same user can restore it from another device.

To preserve tracker state across Render restarts and redeploys, add a persistent
disk mounted at `/var/data`, then set:

```text
TRACKER_DIR=/var/data/tracker
```

Without `TRACKER_DIR`, the app uses the local repository `tracker/` folder.

Live site:
https://nfl-model-final.onrender.com/
