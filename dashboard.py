"""
dashboard.py — Flask web dashboard for the NFL betting pipeline.

Full workflow: Run Model → Select Bets → Lock → Grade Results → Track P&L.
State persists between sessions via tracker.py (JSON files).

Usage:
    python dashboard.py
    # Then open http://localhost:5050
"""

import json
import os
import re
import numpy as np
import pandas as pd
from pathlib import Path
from urllib.parse import urlencode
from flask import Flask, render_template_string, request, jsonify, redirect, session
import requests

from data_loader import (
    load_master_data, load_team_glossary, load_schedule,
    get_weekid_range, filter_master_data, build_abbr_to_name, compute_weekid,
    BACK_TEST_RANGE, YEAR_INDEX_MAP,
)
from features import build_feature_table
from predictor import predict_week, compute_bet_sizing
from tracker import SeasonTracker

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-nfl-dashboard-secret")

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# ─── HTML Template ───────────────────────────────────────────────────────────

TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NFL Betting Tracker</title>
<style>
  :root {
    --bg: #0f1117; --surface: #1a1d27; --surface2: #22252f; --border: #2a2d3a;
    --text: #e0e0e0; --muted: #888; --accent: #4fc3f7;
    --green: #66bb6a; --red: #ef5350; --yellow: #ffd54f; --blue: #42a5f5;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, 'Segoe UI', Roboto, monospace;
         background: var(--bg); color: var(--text); }
  .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
  h1 { font-size: 1.8rem; margin-bottom: 4px; color: var(--accent); }
  h2 { font-size: 1.2rem; margin: 20px 0 12px; color: var(--accent);
       border-bottom: 1px solid var(--border); padding-bottom: 6px; }
  .subtitle { color: var(--muted); font-size: 0.9rem; margin-bottom: 20px; }

  /* Season Overview Cards */
  .season-bar { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
                gap: 10px; margin-bottom: 20px; }
  .stat-card { background: var(--surface); padding: 14px; border-radius: 8px;
               border: 1px solid var(--border); }
  .stat-card .label { font-size: 0.7rem; color: var(--muted); text-transform: uppercase;
                      letter-spacing: 0.5px; margin-bottom: 3px; }
  .stat-card .val { font-size: 1.4rem; font-weight: 700; }
  .green { color: var(--green); } .red { color: var(--red); }
  .yellow { color: var(--yellow); } .blue { color: var(--blue); }

  /* Week Timeline */
  .week-bar { display: flex; gap: 4px; margin-bottom: 20px; flex-wrap: wrap; }
  .week-btn { width: 48px; height: 38px; border-radius: 6px; border: 2px solid var(--border);
              background: var(--bg); color: var(--muted); font-size: 0.8rem; font-weight: 600;
              cursor: pointer; display: flex; align-items: center; justify-content: center;
              text-decoration: none; transition: all 0.15s; }
  .week-btn:hover { border-color: var(--accent); color: var(--text); }
  .week-btn.current { border-color: var(--accent); color: var(--accent); background: var(--surface); }
  .week-btn.st-pending { border-color: var(--yellow); color: var(--yellow); }
  .week-btn.st-locked { border-color: var(--blue); color: var(--blue); }
  .week-btn.st-graded { border-color: var(--green); color: var(--green); }
  .week-btn.st-graded.neg { border-color: var(--red); color: var(--red); }

  /* Controls */
  .controls { display: flex; gap: 14px; align-items: flex-end; flex-wrap: wrap;
              margin-bottom: 20px; background: var(--surface); padding: 16px;
              border-radius: 8px; }
  .ctrl { display: flex; flex-direction: column; gap: 4px; }
  .ctrl label { font-size: 0.7rem; color: var(--muted); text-transform: uppercase;
                letter-spacing: 0.5px; }
  input, select { background: var(--bg); border: 1px solid var(--border);
                  color: var(--text); padding: 8px 12px; border-radius: 4px;
                  font-size: 0.95rem; }
  input:focus, select:focus { outline: none; border-color: var(--accent); }
  input:disabled, select:disabled { opacity: 0.5; cursor: not-allowed; }
  button, .btn { background: var(--accent); color: #000; border: none;
                 padding: 8px 20px; border-radius: 4px; font-weight: 600;
                 cursor: pointer; font-size: 0.9rem; display: inline-block;
                 text-decoration: none; text-align: center; }
  button:hover, .btn:hover { opacity: 0.85; }
  .btn-green { background: var(--green); }
  .btn-blue { background: var(--blue); }
  .btn-red { background: var(--red); color: #fff; }
  .btn-muted { background: var(--border); color: var(--text); }
  .btn-sm { padding: 6px 14px; font-size: 0.8rem; }
  .icon-btn { width: 34px; height: 34px; padding: 0; display:inline-flex;
              align-items:center; justify-content:center; border-radius:6px; }
  .auth-pill { display:inline-flex; align-items:center; gap:8px; padding:7px 10px;
               border:1px solid var(--border); border-radius:6px; background:var(--surface);
               color:var(--muted); font-size:0.78rem; }
  .auth-pill.unlocked { color:var(--green); border-color:rgba(102,187,106,0.45); }
  .filter-btn.active { background: var(--accent); color: #0f1117; }
  .tabs { display:flex; gap:8px; margin: 18px 0 20px; border-bottom:1px solid var(--border); }
  .tab { color:var(--muted); text-decoration:none; padding:10px 12px; border-bottom:2px solid transparent;
         font-size:0.85rem; font-weight:700; }
  .tab.active { color:var(--accent); border-color:var(--accent); }
  .toolbar { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:14px; }
  .copy-note { color:var(--green); font-size:0.8rem; opacity:0; transition:opacity 0.15s; }
  .copy-note.show { opacity:1; }

  /* Status badge */
  .badge { display: inline-block; padding: 3px 10px; border-radius: 4px;
           font-size: 0.72rem; font-weight: 700; text-transform: uppercase;
           letter-spacing: 0.5px; }
  .badge-new { background: var(--border); color: var(--muted); }
  .badge-pending { background: rgba(255,213,79,0.15); color: var(--yellow); }
  .badge-locked { background: rgba(66,165,245,0.15); color: var(--blue); }
  .badge-graded { background: rgba(102,187,106,0.15); color: var(--green); }

  /* MiroFish verdict badges */
  .mf-badge { display: inline-block; padding: 2px 7px; border-radius: 3px;
              font-size: 0.65rem; font-weight: 700; text-transform: uppercase;
              letter-spacing: 0.3px; margin-left: 6px; vertical-align: middle; }
  .mf-badge.agree { background: rgba(102,187,106,0.18); color: var(--green); }
  .mf-badge.disagree { background: rgba(239,83,80,0.18); color: var(--red); }
  .mf-badge.pending { background: rgba(255,213,79,0.15); color: var(--yellow); }
  .mf-reasoning { font-size: 0.72rem; color: var(--muted); margin-top: 2px;
                  padding-left: 38px; line-height: 1.3; max-height: 0;
                  overflow: hidden; transition: max-height 0.2s; cursor: pointer; }
  .mf-reasoning.show { max-height: 100px; }
  .mf-conf { font-size: 0.6rem; opacity: 0.7; margin-left: 3px; }

  /* Game cards */
  .game-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
               gap: 14px; margin-bottom: 20px; }
  .game-card { background: var(--surface); border: 1px solid var(--border);
               border-radius: 8px; padding: 16px; }
  .game-card .matchup { font-size: 1.05rem; font-weight: 700; margin-bottom: 8px; }
  .game-card .scores { display: flex; justify-content: space-between; margin-bottom: 10px; }
  .game-card .team-score { text-align: center; flex: 1; }
  .game-card .team-name { font-size: 0.85rem; color: var(--muted); }
  .game-card .team-pts { font-size: 1.5rem; font-weight: 700; }
  .game-card .pred-info { font-size: 0.82rem; margin-bottom: 10px; color: var(--muted); }
  .game-card .pred-info .winner { color: var(--yellow); font-weight: 700; }

  /* Bet rows inside game cards */
  .bet-row { display: flex; align-items: center; gap: 10px; padding: 8px 10px;
             border-radius: 6px; margin-bottom: 4px; background: var(--bg);
             font-size: 0.82rem; transition: background 0.15s; }
  .bet-row.selected { background: rgba(102,187,106,0.08); }
  .bet-row.locked { background: rgba(66,165,245,0.08); }

  .bet-check { width: 28px; height: 28px; border-radius: 6px; border: 2px solid var(--border);
               display: flex; align-items: center; justify-content: center;
               cursor: pointer; font-size: 0.9rem; flex-shrink: 0;
               transition: all 0.15s; user-select: none; }
  .bet-check.on { border-color: var(--green); background: var(--green); color: #000; }
  .bet-check:hover { border-color: var(--accent); }
  .bet-check.disabled { cursor: default; opacity: 0.6; }
  .bet-check.disabled:hover { border-color: var(--border); }

  .bet-info { flex: 1; }
  .bet-type { font-weight: 700; color: var(--accent); margin-right: 6px; }
  .bet-pick { color: var(--text); }
  .bet-odds { color: var(--muted); margin-left: 4px; }
  .bet-ev { font-size: 0.75rem; margin-left: 4px; }
  .bet-ev.pos { color: var(--green); }
  .bet-ev.neg { color: var(--red); }
  .bet-amt { font-weight: 700; font-variant-numeric: tabular-nums; min-width: 80px;
             text-align: right; }
  .bet-rec { font-size: 0.65rem; color: var(--green); margin-left: 4px;
             text-transform: uppercase; }

  /* Graded bet results */
  .result-badge { display: inline-block; padding: 2px 8px; border-radius: 3px;
                  font-size: 0.72rem; font-weight: 700; text-transform: uppercase;
                  min-width: 40px; text-align: center; }
  .result-badge.win { background: var(--green); color: #000; }
  .result-badge.loss { background: var(--red); color: #fff; }
  .result-badge.push { background: var(--yellow); color: #000; }
  .result-badge.skip { background: var(--border); color: var(--muted); }
  .bet-pnl { font-weight: 700; min-width: 80px; text-align: right;
             font-variant-numeric: tabular-nums; }
  .bet-pnl.pos { color: var(--green); }
  .bet-pnl.neg { color: var(--red); }

  /* Actual scores overlay */
  .actual-scores { font-size: 0.75rem; color: var(--muted); text-align: center;
                   margin-top: -6px; margin-bottom: 8px; }
  .actual-scores span { color: var(--text); font-weight: 600; }

  /* Stats comparison (expandable) */
  .stats-toggle { font-size: 0.75rem; color: var(--muted); text-align: center;
                  margin-top: 10px; cursor: pointer; user-select: none;
                  padding: 4px; border-radius: 4px; }
  .stats-toggle:hover { background: var(--bg); }
  .stats-toggle .arrow { display: inline-block; transition: transform 0.2s; font-size: 0.6rem; }
  .stats-toggle .arrow.open { transform: rotate(180deg); }
  .stats-compare { display: none; margin-top: 8px; }
  .stats-compare.open { display: block; }
  .stats-compare table { width: 100%; font-size: 0.76rem; border-collapse: collapse; }
  .stats-compare th { color: var(--muted); font-size: 0.68rem; padding: 3px 6px;
                      border-bottom: 1px solid var(--border); background: transparent; }
  .stats-compare td { padding: 3px 6px; text-align: center;
                      border-bottom: 1px solid rgba(42,45,58,0.4); }
  .stats-compare td:first-child { text-align: right; }
  .stats-compare td:last-child { text-align: left; }
  .stats-compare .better { color: var(--green); font-weight: 600; }

  /* Action bar */
  .action-bar { display: flex; gap: 12px; align-items: center; margin: 20px 0;
                padding: 16px; background: var(--surface); border-radius: 8px;
                border: 1px solid var(--border); }
  .action-bar .spacer { flex: 1; }
  .action-bar .wager-total { font-size: 0.9rem; }
  .action-bar .wager-total strong { color: var(--accent); }

  /* Week P&L summary */
  .pnl-summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
                 gap: 10px; margin-bottom: 20px; }
  .pl-grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap:10px; margin-bottom:18px; }
  .pl-section { background:var(--surface); border:1px solid var(--border); border-radius:8px;
                padding:16px; margin-bottom:18px; }
  .pl-section-title { color:var(--accent); font-weight:700; font-size:0.85rem;
                      text-transform:uppercase; letter-spacing:0.5px; margin-bottom:10px; }
  .metric-row { display:flex; justify-content:space-between; gap:14px; padding:7px 0;
                border-bottom:1px solid rgba(42,45,58,0.65); font-size:0.85rem; }
  .metric-row:last-child { border-bottom:none; }
  .bars { display:flex; align-items:flex-end; gap:7px; height:180px; padding:12px 4px 0; border-bottom:1px solid var(--border); }
  .bar-wrap { flex:1; min-width:20px; display:flex; flex-direction:column; align-items:center; gap:6px; height:100%; justify-content:flex-end; }
  .bar { width:100%; max-width:34px; min-height:2px; border-radius:4px 4px 0 0; }
  .bar.neg { border-radius:0 0 4px 4px; }
  .bar-label { color:var(--muted); font-size:0.68rem; }
  .line-chart { width:100%; height:220px; }

  /* Error/info messages */
  .msg { padding: 12px 16px; border-radius: 6px; margin-bottom: 16px; font-size: 0.9rem; }
  .msg-error { background: rgba(239,83,80,0.12); color: var(--red); }
  .msg-info { background: rgba(79,195,247,0.12); color: var(--accent); }
  .msg-success { background: rgba(102,187,106,0.12); color: var(--green); }

  /* Season history table */
  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  th { background: var(--surface2); color: var(--accent); text-align: left;
       padding: 10px 12px; font-weight: 600; font-size: 0.75rem;
       text-transform: uppercase; letter-spacing: 0.5px;
       border-bottom: 2px solid var(--border); }
  td { padding: 10px 12px; border-bottom: 1px solid var(--border); }
  tr:hover td { background: rgba(79,195,247,0.04); }
  .num { text-align: right; font-variant-numeric: tabular-nums; }

  /* Season Projection */
  .proj-container { background: var(--surface); border:1px solid var(--border);
                    border-radius:8px; padding:16px; margin-bottom:24px; }
  .proj-subtitle { color: var(--muted); font-size:0.8rem; margin-bottom:12px; }
  .proj-grid { display:grid; grid-template-columns:1fr 1fr; gap:20px; }
  @media (max-width: 900px) { .proj-grid { grid-template-columns:1fr; } }
  .conf-block { background: var(--surface2); border-radius:6px; overflow:hidden; }
  .conf-head { background: var(--bg); padding: 8px 12px; font-weight:600;
               color: var(--accent); font-size:0.9rem; letter-spacing:0.5px;
               border-bottom:1px solid var(--border); }
  .team-row { padding:8px 12px; border-bottom:1px solid var(--border);
              cursor:pointer; transition:background 0.15s;
              display:grid; grid-template-columns: 30px 50px 1fr 60px 70px 60px;
              gap:8px; align-items:center; }
  .team-row:hover { background: rgba(79,195,247,0.06); }
  .team-row.expanded { background: rgba(79,195,247,0.08); }
  .team-row.seed-in { border-left: 3px solid var(--green); }
  .team-row.seed-div { border-left: 3px solid var(--yellow); }
  .team-row.seed-out { border-left: 3px solid transparent; }
  .team-seed { font-weight:700; color:var(--muted); text-align:center;
               font-size:0.8rem; }
  .team-seed.in { color:var(--green); }
  .team-seed.div { color:var(--yellow); }
  .team-abbr { font-weight:700; font-size:0.9rem; }
  .team-name { color: var(--muted); font-size:0.75rem; }
  .team-rec { text-align:right; font-variant-numeric:tabular-nums;
              font-size:0.85rem; color: var(--muted); }
  .team-exp { text-align:right; font-variant-numeric:tabular-nums;
              font-weight:700; font-size:0.95rem; color:var(--text); }
  .team-div { text-align:right; font-size:0.7rem; color:var(--muted);
              text-transform:uppercase; }
  .team-detail { grid-column: 1 / -1; margin-top:8px; padding:10px;
                 background: var(--bg); border-radius:4px;
                 display:none; }
  .team-row.expanded + .team-detail { display:block; }
  .sched-row { display:grid; grid-template-columns: 40px 50px 80px 60px 1fr 60px;
               gap:8px; padding:4px 6px; font-size:0.8rem;
               border-bottom:1px solid var(--surface2);
               align-items:center; }
  .sched-row:last-child { border-bottom:none; }
  .sched-week { color:var(--muted); font-variant-numeric:tabular-nums; }
  .sched-ha { color:var(--muted); font-size:0.7rem; text-transform:uppercase; }
  .sched-result { font-weight:600; font-size:0.75rem; text-transform:uppercase; }
  .sched-result.win { color:var(--green); }
  .sched-result.loss { color:var(--red); }
  .sched-prob { text-align:right; font-variant-numeric:tabular-nums;
                color:var(--muted); font-size:0.75rem; }

  .proj-legend { margin-top:10px; display:flex; gap:14px; flex-wrap:wrap;
                 font-size:0.72rem; color:var(--muted); }
  .proj-legend span.dot { display:inline-block; width:8px; height:8px;
                          border-radius:50%; margin-right:4px;
                          vertical-align:middle; }

  /* Playoff bracket */
  .bracket { background: var(--surface); border:1px solid var(--border);
             border-radius:8px; padding:16px; margin-bottom:24px; }
  .bracket-grid { display:grid;
                  grid-template-columns: 1.2fr 1.2fr 1fr 1fr 1.2fr 1.2fr;
                  gap:10px; align-items:center; margin-top:14px; }
  .bracket-col-head { text-transform:uppercase; letter-spacing:0.5px;
                      font-size:0.7rem; color:var(--muted);
                      text-align:center; padding-bottom:6px;
                      border-bottom:1px solid var(--border); margin-bottom:8px; }
  .bracket-col { display:flex; flex-direction:column; gap:10px; }
  .matchup { background: var(--surface2); border:1px solid var(--border);
             border-radius:6px; padding:8px 10px; font-size:0.78rem; }
  .matchup .row { display:flex; justify-content:space-between;
                  align-items:center; padding:2px 0; }
  .matchup .team { display:flex; gap:6px; align-items:center; }
  .matchup .team.win { color: var(--green); font-weight:600; }
  .matchup .team.loss { color: var(--muted); }
  .matchup .seed { display:inline-block; min-width:18px; color:var(--muted);
                   font-size:0.7rem; text-align:center; }
  .matchup .pts { font-variant-numeric:tabular-nums;
                  color:var(--muted); font-size:0.72rem; }
  .matchup .prob { display:block; text-align:right; font-size:0.7rem;
                   color:var(--muted); margin-top:4px; padding-top:4px;
                   border-top:1px dashed var(--border); }
  .sb-trophy { background: linear-gradient(135deg, #ffd54f 0%, #f9a825 100%);
               color:#0f1117; font-weight:700; padding:10px;
               border-radius:6px; text-align:center; margin-top:10px;
               font-size:0.95rem; letter-spacing:0.5px; }
  .conf-label { font-size:0.7rem; color:var(--muted);
                text-transform:uppercase; letter-spacing:0.5px;
                margin-bottom:4px; }
</style>
</head>
<body>
<div class="container">
  <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:16px; flex-wrap:wrap;">
    <div>
      <h1>NFL Betting Tracker</h1>
      <p class="subtitle">{{ year }} Season &mdash; Model v5</p>
    </div>
    <button type="button" id="auth-pill" class="auth-pill" onclick="unlockWrites()" title="Sign in">
      <span id="auth-icon">SIGN IN</span><span id="auth-text">Guest</span>
    </button>
  </div>

  <div class="tabs">
    <a class="tab {{ 'active' if tab == 'week' }}" href="/?year={{ year }}&week={{ week }}&tab=week">Weekly Tracker</a>
    <a class="tab {{ 'active' if tab == 'pnl' }}" href="/?year={{ year }}&week={{ week }}&tab=pnl&pnl_mode={{ pnl_mode }}">P&amp;L Dashboard</a>
  </div>

  {% if tab == 'week' %}
  <!-- Season Overview -->
  <div class="season-bar">
    <div class="stat-card">
      <div class="label">Starting Bankroll</div>
      <div class="val">${{ "%.2f"|format(summary.starting_bankroll) }}</div>
    </div>
    <div class="stat-card">
      <div class="label">Current Bankroll</div>
      <div class="val {{ 'green' if summary.current_bankroll >= summary.starting_bankroll else 'red' }}">
        ${{ "%.2f"|format(summary.current_bankroll) }}</div>
    </div>
    <div class="stat-card">
      <div class="label">P&amp;L</div>
      <div class="val {{ 'green' if summary.total_pnl >= 0 else 'red' }}">
        {{ "%+.2f"|format(summary.total_pnl) }}</div>
    </div>
    <div class="stat-card">
      <div class="label">ROI</div>
      <div class="val {{ 'green' if summary.roi >= 0 else 'red' }}">
        {{ "%+.1f"|format(summary.roi) }}%</div>
    </div>
    <div class="stat-card">
      <div class="label">Record</div>
      <div class="val">{{ summary.record }}</div>
    </div>
    <div class="stat-card">
      <div class="label">ML / Spread / Total</div>
      <div class="val" style="font-size:1rem;">{{ summary.ml_record }} / {{ summary.spread_record }} / {{ summary.total_record }}</div>
    </div>
  </div>

  <!-- Year selector + Week Timeline -->
  <div style="display:flex; gap:14px; align-items:center; margin-bottom:6px;">
    <form method="GET" style="display:flex; gap:8px; align-items:center;">
      <select name="year" onchange="this.form.submit()" style="padding:6px 10px;">
        {% for y in years %}<option value="{{y}}" {{'selected' if y==year}}>{{y}}</option>{% endfor %}
      </select>
      <input type="hidden" name="week" value="{{ week }}">
    </form>
    <span style="color:var(--muted); font-size:0.8rem;">WEEK:</span>
  </div>
  <div class="week-bar">
    {% for w in range(1, 19) %}
      {% set ws = week_statuses.get(w, 'new') %}
      <a href="/?year={{ year }}&week={{ w }}"
         class="week-btn {{ 'current' if w == week }} st-{{ ws }}">
        {{ w }}
      </a>
    {% endfor %}
  </div>

  <!-- Messages -->
  {% if error %}<div class="msg msg-error">{{ error }}</div>{% endif %}
  {% if success %}<div class="msg msg-success">{{ success }}</div>{% endif %}

  <!-- Week Header -->
  <div style="display:flex; align-items:center; gap:12px; margin-bottom:16px;">
    <h2 style="margin:0; border:none; padding:0;">Week {{ week }}</h2>
    <span class="badge badge-{{ status }}">{{ status }}</span>
  </div>

  <!-- ═══ STATUS: NEW ═══ -->
  {% if status in ('new', 'projection_only') %}
  <div class="msg msg-info">
    Run the model to generate predictions and bet recommendations for this week.
  </div>
  <form method="POST" action="/run">
    <input type="hidden" name="year" value="{{ year }}">
    <input type="hidden" name="week" value="{{ week }}">
    <div class="controls">
      <div class="ctrl">
        <label>Bankroll ($)</label>
        <input type="number" name="bankroll" value="{{ "%.0f"|format(summary.current_bankroll) }}"
               step="100" min="100" {{ 'disabled' if bankroll_locked }}>
      </div>
      <div class="ctrl">
        <label>Bet %</label>
        <input type="number" name="bet_pct" value="100" step="1" min="1" max="100" style="width:70px;">
      </div>
      <div class="ctrl">
        <label>&nbsp;</label>
        <button type="submit">Run Model</button>
      </div>
    </div>
  </form>

  <!-- ═══ STATUS: PENDING ═══ -->
  {% elif status == 'pending' %}
  <div class="msg msg-info" style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
    <span>Select which bets to take, then lock them in. Amounts auto-adjust based on your selections.</span>
    <div style="display:flex; gap:6px;">
      <form method="POST" action="/mirofish" style="margin:0;">
        <input type="hidden" name="year" value="{{ year }}">
        <input type="hidden" name="week" value="{{ week }}">
        <button type="submit" class="btn-sm" style="background:#9c27b0;">
          {{ 'Re-run MiroFish' if week_data.get('mirofish_run') else 'Analyze with MiroFish' }}
        </button>
      </form>
      <form method="POST" action="/run" style="margin:0;">
        <input type="hidden" name="year" value="{{ year }}">
        <input type="hidden" name="week" value="{{ week }}">
        {% if week == 1 and not bankroll_locked %}
        <input type="number" name="bankroll" value="{{ "%.0f"|format(summary.starting_bankroll) }}"
               step="100" min="100" style="width:95px;">
        {% else %}
        <input type="hidden" name="bankroll" value="{{ "%.0f"|format(summary.starting_bankroll) }}">
        {% endif %}
        <input type="hidden" name="bet_pct" value="{{ week_data.bet_pct }}">
        <button type="submit" class="btn-sm btn-muted">Re-run Model</button>
      </form>
    </div>
  </div>
  {% if week_data.get('mirofish_run') %}
  <div class="msg msg-success" style="font-size:0.82rem;">
    MiroFish analysis complete — bets where MiroFish disagrees have been deselected.
    You can still manually toggle any bet.
  </div>
  {% endif %}

  <div class="toolbar">
    <div class="stat-card" style="min-width:260px;">
      <div class="label">Projected Profit (if all selected hit)</div>
      <div class="val green" id="projected-profit">${{ "%.2f"|format(projected_profit) }}</div>
    </div>
    <button type="button" id="selected-only" class="btn-sm btn-muted filter-btn"
            data-active="false" onclick="toggleSelectedOnly()">Selected Only</button>
    <button type="button" class="btn-sm btn-muted" onclick="selectRecommendedBets()">Select Recommended</button>
    <button type="button" class="btn-sm btn-muted" onclick="copySelectedBets()">Copy Selected Bets</button>
    <span id="copy-note" class="copy-note">Copied!</span>
  </div>

  <div class="game-grid">
    {% for g in games %}
    <div class="game-card" data-game-card>
      <div class="matchup">Game {{ g.game_number }}: {{ g.away_team }} @ {{ g.home_team }}</div>
      <div class="scores">
        <div class="team-score">
          <div class="team-name">{{ g.away_team }}</div>
          <div class="team-pts">{{ "%.1f"|format(g.away_predicted_pts) }}</div>
        </div>
        <div style="color:var(--muted); align-self:center;">vs</div>
        <div class="team-score">
          <div class="team-name">{{ g.home_team }}</div>
          <div class="team-pts">{{ "%.1f"|format(g.home_predicted_pts) }}</div>
        </div>
      </div>
      <div class="pred-info">
        Winner: <span class="winner">{{ g.predicted_winner }}</span>
        ({{ "%.1f"|format(g.winner_prob * 100) }}%)
        &nbsp;|&nbsp; Spread: {{ "%.1f"|format(g.predicted_spread) }}
      </div>
      {% for b in g.bets %}
      <div class="bet-row {{ 'selected' if b.selected }}" id="row-{{ b.id }}"
           data-bet-row data-bet-id="{{ b.id }}" data-selected="{{ 'true' if b.selected else 'false' }}"
           data-recommended="{{ 'true' if b.recommended else 'false' }}"
           data-team="{{ b.pick }}" data-type="{{ b.type }}" data-line="{{ b.get('spread_points', b.get('total_line', '')) }}"
           data-odds="{{ b.odds }}" data-amount="{{ b.amount }}" data-pick="{{ b.pick }}">
        <div class="bet-check {{ 'on' if b.selected }}" id="check-{{ b.id }}"
             onclick="toggleBet('{{ b.id }}')">{{ 'X' if b.selected else '' }}</div>
        <div class="bet-info">
          <span class="bet-type">{{ b.type|upper }}</span>
          <span class="bet-pick">{{ b.pick }}</span>
          <span class="bet-odds">({{ "%+d"|format(b.odds) }})</span>
          <span class="bet-ev {{ 'pos' if b.value > 0 else 'neg' }}">
            {{ "%+.1f"|format(b.value * 100) }}%</span>
          {% if b.recommended %}<span class="bet-rec">REC</span>{% endif %}
          {% if b.get('mirofish_verdict') == 'agree' %}
            <span class="mf-badge agree">MF AGREE<span class="mf-conf">{{ b.get('mirofish_confidence', '') }}</span></span>
          {% elif b.get('mirofish_verdict') == 'disagree' %}
            <span class="mf-badge disagree">MF DISAGREE<span class="mf-conf">{{ b.get('mirofish_confidence', '') }}</span></span>
          {% endif %}
        </div>
        <div class="bet-amt" id="amt-{{ b.id }}">${{ "%.2f"|format(b.amount) }}</div>
      </div>
      {% if b.get('mirofish_reasoning') %}
      <div class="mf-reasoning" id="mfr-{{ b.id }}" onclick="this.classList.toggle('show')">
        {{ b.mirofish_reasoning }}
      </div>
      {% endif %}
      {% endfor %}
      <!-- Expandable stats -->
      <div class="stats-toggle" onclick="toggleStats(event, 'stats-{{ g.game_number }}')">
        <span class="arrow" id="arrow-{{ g.game_number }}">v</span> Team Stats
      </div>
      <div class="stats-compare" id="stats-{{ g.game_number }}">
        {% set a = team_stats.get(g.away_team, {}) %}
        {% set h = team_stats.get(g.home_team, {}) %}
        <table>
          <thead><tr><th>{{ g.away_team }}</th><th>Stat</th><th>{{ g.home_team }}</th></tr></thead>
          <tbody>
            <tr>
              <td class="{{ 'better' if a.get('offense_points',0) > h.get('offense_points',0) }}">{{ "%.1f"|format(a.get('offense_points',0)) }}</td>
              <td style="color:var(--muted)">Off Pts</td>
              <td class="{{ 'better' if h.get('offense_points',0) > a.get('offense_points',0) }}">{{ "%.1f"|format(h.get('offense_points',0)) }}</td>
            </tr>
            <tr>
              <td class="{{ 'better' if a.get('defense_points',0) < h.get('defense_points',0) }}">{{ "%.1f"|format(a.get('defense_points',0)) }}</td>
              <td style="color:var(--muted)">Def Pts</td>
              <td class="{{ 'better' if h.get('defense_points',0) < a.get('defense_points',0) }}">{{ "%.1f"|format(h.get('defense_points',0)) }}</td>
            </tr>
            <tr>
              <td class="{{ 'better' if a.get('offense_yards_total',0) > h.get('offense_yards_total',0) }}">{{ "%.0f"|format(a.get('offense_yards_total',0)) }}</td>
              <td style="color:var(--muted)">Off Yds</td>
              <td class="{{ 'better' if h.get('offense_yards_total',0) > a.get('offense_yards_total',0) }}">{{ "%.0f"|format(h.get('offense_yards_total',0)) }}</td>
            </tr>
            <tr>
              <td class="{{ 'better' if a.get('offense_pass_yards',0) > h.get('offense_pass_yards',0) }}">{{ "%.0f"|format(a.get('offense_pass_yards',0)) }}</td>
              <td style="color:var(--muted)">Pass Yds</td>
              <td class="{{ 'better' if h.get('offense_pass_yards',0) > a.get('offense_pass_yards',0) }}">{{ "%.0f"|format(h.get('offense_pass_yards',0)) }}</td>
            </tr>
            <tr>
              <td class="{{ 'better' if a.get('offense_rush_yards',0) > h.get('offense_rush_yards',0) }}">{{ "%.0f"|format(a.get('offense_rush_yards',0)) }}</td>
              <td style="color:var(--muted)">Rush Yds</td>
              <td class="{{ 'better' if h.get('offense_rush_yards',0) > a.get('offense_rush_yards',0) }}">{{ "%.0f"|format(h.get('offense_rush_yards',0)) }}</td>
            </tr>
            <tr>
              <td class="{{ 'better' if a.get('defense_yards_total',0) < h.get('defense_yards_total',0) }}">{{ "%.0f"|format(a.get('defense_yards_total',0)) }}</td>
              <td style="color:var(--muted)">Def Yds</td>
              <td class="{{ 'better' if h.get('defense_yards_total',0) < a.get('defense_yards_total',0) }}">{{ "%.0f"|format(h.get('defense_yards_total',0)) }}</td>
            </tr>
            <tr>
              <td class="{{ 'better' if a.get('win_rate',0) > h.get('win_rate',0) }}">{{ "%.0f"|format(a.get('win_rate',0) * 100) }}%</td>
              <td style="color:var(--muted)">Win Rate</td>
              <td class="{{ 'better' if h.get('win_rate',0) > a.get('win_rate',0) }}">{{ "%.0f"|format(h.get('win_rate',0) * 100) }}%</td>
            </tr>
            <tr>
              <td class="{{ 'better' if a.get('sow',0) > h.get('sow',0) }}">{{ "%.3f"|format(a.get('sow',0)) }}</td>
              <td style="color:var(--muted)">SOW</td>
              <td class="{{ 'better' if h.get('sow',0) > a.get('sow',0) }}">{{ "%.3f"|format(h.get('sow',0)) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    {% endfor %}
  </div>

  <!-- Lock action bar -->
  <div class="action-bar">
    <div class="wager-total">
      Selected: <strong id="sel-count">{{ selected_count }}</strong> bets
      &nbsp;&mdash;&nbsp;
      Total wager: <strong id="total-wagered">${{ "%.2f"|format(total_wagered) }}</strong>
      of ${{ "%.2f"|format(week_data.bankroll_before) }}
    </div>
    <div class="spacer"></div>
    <form method="POST" action="/reset-week" style="margin:0;">
      <input type="hidden" name="year" value="{{ year }}">
      <input type="hidden" name="week" value="{{ week }}">
      <button type="submit" class="btn-red"
              onclick="return confirm('Reset this week and clear all saved bets/results?')">
        Reset Week
      </button>
    </form>
    <form method="POST" action="/lock" style="margin:0;">
      <input type="hidden" name="year" value="{{ year }}">
      <input type="hidden" name="week" value="{{ week }}">
      <button type="submit" class="btn-green"
              onclick="return confirm('Lock these bets? You can unlock them later if changes are needed.')">
        Lock Bets
      </button>
    </form>
  </div>

  <!-- ═══ STATUS: LOCKED ═══ -->
  {% elif status == 'locked' %}
  <div class="msg msg-info" style="display:flex; justify-content:space-between; align-items:center;">
    <span>Bets are locked. Unlock to edit selections, or grade results after games finish.</span>
    <span style="color:var(--muted); font-size:0.8rem;">
      Locked {{ week_data.locked_at[:16] if week_data.locked_at else '' }}
    </span>
  </div>
  <div class="toolbar">
    <div class="stat-card" style="min-width:260px;">
      <div class="label">Projected Profit (if all selected hit)</div>
      <div class="val green" id="projected-profit">${{ "%.2f"|format(projected_profit) }}</div>
    </div>
    <button type="button" class="btn-sm btn-muted" onclick="copySelectedBets()">Copy Selected Bets</button>
    <span id="copy-note" class="copy-note">Copied!</span>
  </div>

  <div class="game-grid">
    {% for g in games %}
    <div class="game-card">
      <div class="matchup">Game {{ g.game_number }}: {{ g.away_team }} @ {{ g.home_team }}</div>
      <div class="scores">
        <div class="team-score">
          <div class="team-name">{{ g.away_team }}</div>
          <div class="team-pts">{{ "%.1f"|format(g.away_predicted_pts) }}</div>
        </div>
        <div style="color:var(--muted); align-self:center;">vs</div>
        <div class="team-score">
          <div class="team-name">{{ g.home_team }}</div>
          <div class="team-pts">{{ "%.1f"|format(g.home_predicted_pts) }}</div>
        </div>
      </div>
      {% for b in g.bets %}
        {% if b.selected %}
        <div class="bet-row locked selected" data-bet-row data-bet-id="{{ b.id }}" data-selected="true"
             data-team="{{ b.pick }}" data-type="{{ b.type }}" data-line="{{ b.get('spread_points', b.get('total_line', '')) }}"
             data-odds="{{ b.odds }}" data-amount="{{ b.amount }}" data-pick="{{ b.pick }}">
          <div style="color:var(--blue); font-size:0.9rem; width:28px; text-align:center;">[L]</div>
          <div class="bet-info">
            <span class="bet-type">{{ b.type|upper }}</span>
            <span class="bet-pick">{{ b.pick }}</span>
            <span class="bet-odds">({{ "%+d"|format(b.odds) }})</span>
          </div>
          <div class="bet-amt">${{ "%.2f"|format(b.amount) }}</div>
        </div>
        {% endif %}
      {% endfor %}
    </div>
    {% endfor %}
  </div>

  <div class="action-bar">
    <div class="wager-total">
      Locked: <strong>{{ selected_count }}</strong> bets
      &nbsp;&mdash;&nbsp;
      Total wagered: <strong>${{ "%.2f"|format(total_wagered) }}</strong>
    </div>
    <div class="spacer"></div>
    <form method="POST" action="/reset-week" style="margin:0;">
      <input type="hidden" name="year" value="{{ year }}">
      <input type="hidden" name="week" value="{{ week }}">
      <button type="submit" class="btn-red"
              onclick="return confirm('Reset this week and clear all saved bets/results?')">
        Reset Week
      </button>
    </form>
    <form method="POST" action="/unlock" style="margin:0;">
      <input type="hidden" name="year" value="{{ year }}">
      <input type="hidden" name="week" value="{{ week }}">
      <button type="submit" class="btn-muted"
              onclick="return confirm('Unlock this week and edit bets?')">
        Unlock Bets
      </button>
    </form>
    <form method="POST" action="/grade" style="margin:0;">
      <input type="hidden" name="year" value="{{ year }}">
      <input type="hidden" name="week" value="{{ week }}">
      <button type="submit" class="btn-blue">Grade Results</button>
    </form>
  </div>

  <!-- ═══ STATUS: GRADED ═══ -->
  {% elif status == 'graded' %}

  <!-- Week P&L summary -->
  {% set week_pnl = week_data.bankroll_after - week_data.bankroll_before %}
  <div class="pnl-summary">
    <div class="stat-card">
      <div class="label">Week P&amp;L</div>
      <div class="val {{ 'green' if week_pnl >= 0 else 'red' }}">
        {{ "%+.2f"|format(week_pnl) }}</div>
    </div>
    <div class="stat-card">
      <div class="label">Bankroll Before</div>
      <div class="val">${{ "%.2f"|format(week_data.bankroll_before) }}</div>
    </div>
    <div class="stat-card">
      <div class="label">Bankroll After</div>
      <div class="val {{ 'green' if week_pnl >= 0 else 'red' }}">
        ${{ "%.2f"|format(week_data.bankroll_after) }}</div>
    </div>
    <div class="stat-card">
      <div class="label">Week Record</div>
      <div class="val">{{ week_record }}</div>
    </div>
  </div>
  <div class="toolbar">
    <button type="button" class="btn-sm btn-muted" onclick="copySelectedBets()">Copy Selected Bets</button>
    <span id="copy-note" class="copy-note">Copied!</span>
  </div>

  <div class="game-grid">
    {% for g in games %}
    <div class="game-card">
      <div class="matchup">Game {{ g.game_number }}: {{ g.away_team }} @ {{ g.home_team }}</div>
      <div class="scores">
        <div class="team-score">
          <div class="team-name">{{ g.away_team }}</div>
          <div class="team-pts">{{ "%.1f"|format(g.away_predicted_pts) }}</div>
        </div>
        <div style="color:var(--muted); align-self:center;">vs</div>
        <div class="team-score">
          <div class="team-name">{{ g.home_team }}</div>
          <div class="team-pts">{{ "%.1f"|format(g.home_predicted_pts) }}</div>
        </div>
      </div>
      {% if g.actual_away is not none %}
      <div class="actual-scores">
        Actual: <span>{{ g.actual_away }}</span> &ndash; <span>{{ g.actual_home }}</span>
      </div>
      {% endif %}
      {% for b in g.bets %}
        {% if b.selected %}
        <div class="bet-row selected" data-bet-row data-bet-id="{{ b.id }}" data-selected="true"
             data-team="{{ b.pick }}" data-type="{{ b.type }}" data-line="{{ b.get('spread_points', b.get('total_line', '')) }}"
             data-odds="{{ b.odds }}" data-amount="{{ b.amount }}" data-pick="{{ b.pick }}">
          <span class="result-badge {{ b.result or 'skip' }}">{{ b.result or '?' }}</span>
          <div class="bet-info">
            <span class="bet-type">{{ b.type|upper }}</span>
            <span class="bet-pick">{{ b.pick }}</span>
            <span class="bet-odds">({{ "%+d"|format(b.odds) }})</span>
          </div>
          <div class="bet-amt">${{ "%.2f"|format(b.amount) }}</div>
          <div class="bet-pnl {{ 'pos' if b.pnl > 0 else 'neg' if b.pnl < 0 else '' }}">
            {{ "%+.2f"|format(b.pnl) }}</div>
        </div>
        {% endif %}
      {% endfor %}
    </div>
    {% endfor %}
  </div>

  <!-- Re-grade option -->
  <div class="action-bar">
    <span style="color:var(--muted); font-size:0.8rem;">
      Graded {{ week_data.graded_at[:16] if week_data.graded_at else '' }}
    </span>
    <div class="spacer"></div>
    <form method="POST" action="/reset-week" style="margin:0;">
      <input type="hidden" name="year" value="{{ year }}">
      <input type="hidden" name="week" value="{{ week }}">
      <button type="submit" class="btn-sm btn-red"
              onclick="return confirm('Reset this week and clear all saved bets/results?')">
        Reset Week
      </button>
    </form>
    <form method="POST" action="/grade" style="margin:0;">
      <input type="hidden" name="year" value="{{ year }}">
      <input type="hidden" name="week" value="{{ week }}">
      <button type="submit" class="btn-sm btn-muted">Re-grade</button>
    </form>
  </div>

  {% endif %}

  <!-- Season Projection (rest-of-season) -->
  <h2>Rest-of-Season Projection</h2>
  <div class="proj-container">
    {% if projection and projection.get('proxy_schedule') %}
    <div class="msg msg-info" style="margin-bottom:10px;">
      <strong>Note:</strong> The {{ year }} schedule isn't published yet, so this projection
      uses the <strong>{{ projection.schedule_source_year }}</strong> schedule as a proxy.
      Matchups will differ from the real {{ year }} slate, but team strength rankings still reflect
      the model's view of each roster based on the most recent {{ projection.schedule_source_year }} data.
    </div>
    {% endif %}
    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px; margin-bottom:10px;">
      <div class="proj-subtitle">
        {% if projection %}
          Projected from Week {{ projection.current_week }}'s 6-week rolling stats.
          Teams sorted by projected wins (count of predicted WINs). Click a team to see remaining schedule; click again to collapse.
        {% elif week_data and week_data.get('team_stats') %}
          Click below to project the rest of the season using this week's model inputs.
        {% else %}
          Click below to project the {{ year }} season using the most recent available data.
          No spreads/odds are required — works even if the {{ year }} schedule isn't published yet.
        {% endif %}
      </div>
      <form method="POST" action="/project" style="margin:0;">
        <input type="hidden" name="year" value="{{ year }}">
        <input type="hidden" name="week" value="{{ week }}">
        <button type="submit" class="btn-sm" style="background:#4fc3f7; color:#0f1117; font-weight:600;">
          {{ 'Re-run Projection' if projection else 'Run Season Projection' }}
        </button>
      </form>
    </div>

    {% if projection %}
    <div class="proj-grid">
      {% for conf in ['AFC', 'NFC'] %}
      {% set conf_teams = projection.standings.values()|selectattr('conference', 'equalto', conf)|list %}
      {% set sorted_teams = conf_teams|sort(attribute='predicted_wins', reverse=true) %}
      {% set seed_map = {} %}
      {% for s in projection.seeding[conf] %}{% set _ = seed_map.update({s.team: s}) %}{% endfor %}

      <div class="conf-block">
        <div class="conf-head">{{ conf }}</div>
        {% for t in sorted_teams %}
          {% set seed = seed_map.get(t.team) %}
          {% set seed_cls = 'seed-out' %}
          {% if seed and seed.div_winner %}{% set seed_cls = 'seed-div' %}
          {% elif seed %}{% set seed_cls = 'seed-in' %}{% endif %}
        <div class="team-row {{ seed_cls }}" onclick="toggleTeam(this)">
          <div class="team-seed {% if seed and seed.div_winner %}div{% elif seed %}in{% endif %}">
            {{ '#' ~ seed.seed if seed else '' }}
          </div>
          <div class="team-abbr">{{ t.team }}</div>
          <div class="team-name">{{ t.division }}</div>
          <div class="team-rec">{{ t.current_w }}-{{ t.current_l }}{% if t.current_t %}-{{ t.current_t }}{% endif %}</div>
          <div class="team-exp">{{ t.predicted_wins }}-{{ t.predicted_losses }}</div>
          <div class="team-div" title="Expected wins (sum of win probabilities)">{{ "%.1f"|format(t.expected_wins) }} exp</div>
        </div>
        <div class="team-detail" id="detail-{{ conf }}-{{ t.team }}">
          {% if t.upcoming %}
            {% for g in t.upcoming %}
            <div class="sched-row">
              <div class="sched-week">W{{ g.week }}</div>
              <div class="sched-ha">{{ '@' if g.home_away == 'away' else 'vs' }}</div>
              <div class="team-abbr" style="font-size:0.85rem;">{{ g.opponent }}</div>
              <div class="sched-result {{ 'win' if g.predicted_win else 'loss' }}">
                {{ 'WIN' if g.predicted_win else 'LOSS' }}
              </div>
              <div style="color:var(--muted); font-size:0.75rem;">
                {{ g.team_pts }} - {{ g.opp_pts }}
              </div>
              <div class="sched-prob">{{ "%.0f"|format(g.win_prob * 100) }}%</div>
            </div>
            {% endfor %}
          {% else %}
            <div style="color:var(--muted); font-size:0.8rem; padding:6px;">
              No remaining games.
            </div>
          {% endif %}
        </div>
        {% endfor %}
      </div>
      {% endfor %}
    </div>
    <div class="proj-legend">
      <span><span class="dot" style="background:var(--yellow);"></span>Division leader (seeds 1-4)</span>
      <span><span class="dot" style="background:var(--green);"></span>Wild card (seeds 5-7)</span>
      <span>Big number = projected final W-L (count of WIN labels). "exp" = sum of win probabilities.</span>
    </div>
    {% endif %}
  </div>

  {% if projection and projection.bracket %}
  {% set br = projection.bracket %}
  <h2>Playoff Bracket Prediction</h2>
  <div class="bracket">
    <div style="color:var(--muted); font-size:0.8rem; margin-bottom:6px;">
      Each round simulated with the same model. Higher seed hosts (Super Bowl is neutral).
    </div>

    <div class="bracket-grid">
      <div>
        <div class="bracket-col-head">AFC Wild Card</div>
        <div class="bracket-col">
          {% for m in br.AFC.wc %}
          <div class="matchup">
            <div class="row"><div class="team {{ 'win' if m.winner == m.away else 'loss' }}">
              <span class="seed">#{{ m.lower_seed }}</span>{{ m.away }}
            </div><div class="pts">{{ m.away_pts }}</div></div>
            <div class="row"><div class="team {{ 'win' if m.winner == m.home else 'loss' }}">
              <span class="seed">#{{ m.higher_seed }}</span>{{ m.home }}
            </div><div class="pts">{{ m.home_pts }}</div></div>
            <div class="prob">{{ m.winner }} {{ "%.0f"|format(m.win_prob*100) }}%</div>
          </div>
          {% endfor %}
          <div class="matchup" style="opacity:0.6;">
            <div style="text-align:center; color:var(--muted); font-size:0.75rem;">
              #1 {{ projection.seeding.AFC[0].team }} (BYE)
            </div>
          </div>
        </div>
      </div>

      <div>
        <div class="bracket-col-head">AFC Divisional</div>
        <div class="bracket-col">
          {% for m in br.AFC.div %}
          <div class="matchup">
            <div class="row"><div class="team {{ 'win' if m.winner == m.away else 'loss' }}">
              <span class="seed">#{{ m.lower_seed }}</span>{{ m.away }}
            </div><div class="pts">{{ m.away_pts }}</div></div>
            <div class="row"><div class="team {{ 'win' if m.winner == m.home else 'loss' }}">
              <span class="seed">#{{ m.higher_seed }}</span>{{ m.home }}
            </div><div class="pts">{{ m.home_pts }}</div></div>
            <div class="prob">{{ m.winner }} {{ "%.0f"|format(m.win_prob*100) }}%</div>
          </div>
          {% endfor %}
        </div>
      </div>

      <div>
        <div class="bracket-col-head">AFC Champ</div>
        <div class="bracket-col">
          {% if br.AFC.champ %}
          {% set m = br.AFC.champ %}
          <div class="matchup">
            <div class="row"><div class="team {{ 'win' if m.winner == m.away else 'loss' }}">
              <span class="seed">#{{ m.lower_seed }}</span>{{ m.away }}
            </div><div class="pts">{{ m.away_pts }}</div></div>
            <div class="row"><div class="team {{ 'win' if m.winner == m.home else 'loss' }}">
              <span class="seed">#{{ m.higher_seed }}</span>{{ m.home }}
            </div><div class="pts">{{ m.home_pts }}</div></div>
            <div class="prob">{{ m.winner }} {{ "%.0f"|format(m.win_prob*100) }}%</div>
          </div>
          {% endif %}
        </div>
      </div>

      <div>
        <div class="bracket-col-head">NFC Champ</div>
        <div class="bracket-col">
          {% if br.NFC.champ %}
          {% set m = br.NFC.champ %}
          <div class="matchup">
            <div class="row"><div class="team {{ 'win' if m.winner == m.away else 'loss' }}">
              <span class="seed">#{{ m.lower_seed }}</span>{{ m.away }}
            </div><div class="pts">{{ m.away_pts }}</div></div>
            <div class="row"><div class="team {{ 'win' if m.winner == m.home else 'loss' }}">
              <span class="seed">#{{ m.higher_seed }}</span>{{ m.home }}
            </div><div class="pts">{{ m.home_pts }}</div></div>
            <div class="prob">{{ m.winner }} {{ "%.0f"|format(m.win_prob*100) }}%</div>
          </div>
          {% endif %}
        </div>
      </div>

      <div>
        <div class="bracket-col-head">NFC Divisional</div>
        <div class="bracket-col">
          {% for m in br.NFC.div %}
          <div class="matchup">
            <div class="row"><div class="team {{ 'win' if m.winner == m.away else 'loss' }}">
              <span class="seed">#{{ m.lower_seed }}</span>{{ m.away }}
            </div><div class="pts">{{ m.away_pts }}</div></div>
            <div class="row"><div class="team {{ 'win' if m.winner == m.home else 'loss' }}">
              <span class="seed">#{{ m.higher_seed }}</span>{{ m.home }}
            </div><div class="pts">{{ m.home_pts }}</div></div>
            <div class="prob">{{ m.winner }} {{ "%.0f"|format(m.win_prob*100) }}%</div>
          </div>
          {% endfor %}
        </div>
      </div>

      <div>
        <div class="bracket-col-head">NFC Wild Card</div>
        <div class="bracket-col">
          {% for m in br.NFC.wc %}
          <div class="matchup">
            <div class="row"><div class="team {{ 'win' if m.winner == m.away else 'loss' }}">
              <span class="seed">#{{ m.lower_seed }}</span>{{ m.away }}
            </div><div class="pts">{{ m.away_pts }}</div></div>
            <div class="row"><div class="team {{ 'win' if m.winner == m.home else 'loss' }}">
              <span class="seed">#{{ m.higher_seed }}</span>{{ m.home }}
            </div><div class="pts">{{ m.home_pts }}</div></div>
            <div class="prob">{{ m.winner }} {{ "%.0f"|format(m.win_prob*100) }}%</div>
          </div>
          {% endfor %}
          <div class="matchup" style="opacity:0.6;">
            <div style="text-align:center; color:var(--muted); font-size:0.75rem;">
              #1 {{ projection.seeding.NFC[0].team }} (BYE)
            </div>
          </div>
        </div>
      </div>
    </div>

    {% if br.super_bowl %}
    <div style="margin-top:18px; max-width:500px; margin-left:auto; margin-right:auto;">
      <div class="bracket-col-head">Super Bowl</div>
      <div class="matchup" style="border:2px solid var(--yellow);">
        <div class="row">
          <div class="team {{ 'win' if br.super_bowl.winner == br.super_bowl.away else 'loss' }}">
            <span class="conf-label">NFC</span>{{ br.super_bowl.nfc_team }}
          </div>
          <div class="pts">{{ br.super_bowl.away_pts if br.super_bowl.away == br.super_bowl.nfc_team else br.super_bowl.home_pts }}</div>
        </div>
        <div class="row">
          <div class="team {{ 'win' if br.super_bowl.winner == br.super_bowl.home else 'loss' }}">
            <span class="conf-label">AFC</span>{{ br.super_bowl.afc_team }}
          </div>
          <div class="pts">{{ br.super_bowl.home_pts if br.super_bowl.home == br.super_bowl.afc_team else br.super_bowl.away_pts }}</div>
        </div>
        <div class="prob">{{ br.super_bowl.winner }} {{ "%.0f"|format(br.super_bowl.win_prob*100) }}%</div>
      </div>
      {% if br.champion %}
      <div class="sb-trophy">PROJECTED CHAMPION: {{ br.champion }}</div>
      {% endif %}
    </div>
    {% endif %}
  </div>
  {% endif %}

  <!-- Season History (always visible if any weeks graded) -->
  {% if graded_weeks %}
  <h2>Season History</h2>

  <!-- Charts -->
  <div style="display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:24px;">
    <div style="background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:16px;">
      <div style="font-size:0.75rem; color:var(--muted); text-transform:uppercase; letter-spacing:0.5px; margin-bottom:10px;">Bankroll Over Time</div>
      <canvas id="bankrollChart" height="200"></canvas>
    </div>
    <div style="background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:16px;">
      <div style="font-size:0.75rem; color:var(--muted); text-transform:uppercase; letter-spacing:0.5px; margin-bottom:10px;">Weekly P&amp;L</div>
      <canvas id="pnlChart" height="200"></canvas>
    </div>
  </div>

  <table>
    <thead>
      <tr>
        <th>Week</th><th>Status</th><th class="num">Wagered</th>
        <th class="num">Record</th><th class="num">P&amp;L</th><th class="num">Bankroll</th>
      </tr>
    </thead>
    <tbody>
      {% for gw in graded_weeks %}
      <tr>
        <td><a href="/?year={{ year }}&week={{ gw.week }}" style="color:var(--accent);">Week {{ gw.week }}</a></td>
        <td><span class="badge badge-{{ gw.status }}">{{ gw.status }}</span></td>
        <td class="num">${{ "%.2f"|format(gw.wagered) }}</td>
        <td class="num">{{ gw.record }}</td>
        <td class="num {{ 'green' if gw.pnl >= 0 else 'red' }}">{{ "%+.2f"|format(gw.pnl) }}</td>
        <td class="num">${{ "%.2f"|format(gw.bankroll_after) }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% endif %}

  {% else %}
  <div style="display:flex; justify-content:space-between; gap:12px; align-items:center; flex-wrap:wrap; margin-bottom:16px;">
    <h2 style="margin:0; border:none; padding:0;">P&amp;L Dashboard</h2>
    <form method="GET" class="toolbar" style="margin:0;">
      <input type="hidden" name="year" value="{{ year }}">
      <input type="hidden" name="week" value="{{ week }}">
      <input type="hidden" name="tab" value="pnl">
      <label class="ctrl" style="margin:0;">
        <span style="font-size:0.7rem; color:var(--muted); text-transform:uppercase;">Profit Calc Mode</span>
        <select name="pnl_mode" onchange="this.form.submit()">
          <option value="actual" {{ 'selected' if pnl_mode == 'actual' }}>Actual</option>
          <option value="projected" {{ 'selected' if pnl_mode == 'projected' }}>Projected</option>
        </select>
      </label>
    </form>
  </div>

  <div class="pl-grid">
    <div class="stat-card"><div class="label">Total P&amp;L</div><div class="val {{ 'green' if pnl_dashboard.summary.total_pnl >= 0 else 'red' }}">${{ "%+.2f"|format(pnl_dashboard.summary.total_pnl) }}</div></div>
    <div class="stat-card"><div class="label">All Recommended P&amp;L</div><div class="val {{ 'green' if pnl_dashboard.summary.all_recommended_pnl >= 0 else 'red' }}">${{ "%+.2f"|format(pnl_dashboard.summary.all_recommended_pnl) }}</div></div>
    <div class="stat-card"><div class="label">Profit Margin</div><div class="val {{ 'green' if pnl_dashboard.summary.profit_margin >= 0 else 'red' }}">{{ "%+.1f"|format(pnl_dashboard.summary.profit_margin * 100) }}%</div></div>
    <div class="stat-card"><div class="label">Season Sharpe</div><div class="val" title="Sharpe = (mean weekly margin - 0.000769) / stdev weekly margins. Not annualized.">{{ pnl_dashboard.summary.sharpe_label }}</div></div>
    <div class="stat-card"><div class="label">Bets Taken</div><div class="val">{{ pnl_dashboard.summary.total_bets_taken }}</div></div>
    <div class="stat-card"><div class="label">Amount Bet</div><div class="val">${{ "%.2f"|format(pnl_dashboard.summary.total_amount_bet) }}</div></div>
    <div class="stat-card"><div class="label">Avg Weekly Margin</div><div class="val {{ 'green' if pnl_dashboard.summary.average_weekly_margin >= 0 else 'red' }}">{{ "%+.1f"|format(pnl_dashboard.summary.average_weekly_margin * 100) }}%</div></div>
    <div class="stat-card"><div class="label">Projected Final</div><div class="val">${{ "%.2f"|format(pnl_dashboard.projection.projected_final) if pnl_dashboard.projection else "Insufficient data" }}</div></div>
  </div>

  {% if pnl_dashboard.projection %}
  <div class="msg msg-info">
    End-of-season projection based on current average returns:
    <strong>${{ "%.2f"|format(pnl_dashboard.projection.projected_final) }}</strong>
    with ±1 stdev band
    <strong>${{ "%.2f"|format(pnl_dashboard.projection.lower_band) }}</strong> to
    <strong>${{ "%.2f"|format(pnl_dashboard.projection.upper_band) }}</strong>.
  </div>
  {% endif %}

  <div style="display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:18px;">
    <div class="pl-section">
      <div class="pl-section-title">Week-over-Week Net Profit</div>
      <div class="bars">
        {% for row in pnl_dashboard.weeks %}
        <div class="bar-wrap" title="Week {{ row.week }}: {{ '%+.2f'|format(row.week_net_profit) }}">
          <div class="bar {{ 'neg' if row.week_net_profit < 0 }}" style="height:{{ row.bar_height }}%; background:{{ 'var(--green)' if row.week_net_profit >= 0 else 'var(--red)' }};"></div>
          <div class="bar-label">W{{ row.week }}</div>
        </div>
        {% endfor %}
      </div>
    </div>
    <div class="pl-section">
      <div class="pl-section-title">Profit By Bet Type</div>
      {% for t in ['ml', 'spread', 'total'] %}
      {% set m = pnl_dashboard.by_type[t] %}
      <div class="metric-row">
        <span>{{ t|upper }}</span>
        <strong class="{{ 'green' if m.profit >= 0 else 'red' }}">${{ "%+.2f"|format(m.profit) }}</strong>
      </div>
      <div class="metric-row">
        <span>Prediction hit / Bet hit v2</span>
        <span>{{ m.prediction_hit_label }} / {{ m.bet_hit_label }}</span>
      </div>
      {% endfor %}
    </div>
  </div>

  <div class="pl-section">
    <div class="pl-section-title">Cumulative Bankroll Curve</div>
    <canvas id="pnlDashboardBankroll" class="line-chart"></canvas>
  </div>

  <div class="pl-section">
    <div class="pl-section-title">Weekly P&amp;L Table</div>
    {% if pnl_dashboard.weeks %}
    <table>
      <thead>
        <tr>
          <th>Week</th><th class="num">Margin</th><th class="num">Net Profit</th>
          <th class="num">ML</th><th class="num">Spread</th><th class="num">Total</th>
          <th class="num">ML v2</th><th class="num">Spread v2</th><th class="num">Total v2</th><th class="num">Sharpe</th>
        </tr>
      </thead>
      <tbody>
        {% for row in pnl_dashboard.weeks %}
        <tr>
          <td>Week {{ row.week }}</td>
          <td class="num {{ 'green' if row.profit_margin >= 0 else 'red' }}">{{ "%+.1f"|format(row.profit_margin * 100) }}%</td>
          <td class="num {{ 'green' if row.week_net_profit >= 0 else 'red' }}">${{ "%+.2f"|format(row.week_net_profit) }}</td>
          <td class="num {{ 'green' if row.ml_profit >= 0 else 'red' }}">${{ "%+.2f"|format(row.ml_profit) }}</td>
          <td class="num {{ 'green' if row.spread_profit >= 0 else 'red' }}">${{ "%+.2f"|format(row.spread_profit) }}</td>
          <td class="num {{ 'green' if row.total_profit >= 0 else 'red' }}">${{ "%+.2f"|format(row.total_profit) }}</td>
          <td class="num">{{ row.ml_hit_rate_v2_label }}</td>
          <td class="num">{{ row.spread_hit_rate_v2_label }}</td>
          <td class="num">{{ row.total_hit_rate_v2_label }}</td>
          <td class="num">{{ row.sharpe_label }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}
    <div class="msg msg-info">No historical week data yet.</div>
    {% endif %}
  </div>
  {% endif %}

</div>

<script>
const YEAR = {{ year }};
const WEEK = {{ week }};
const STATUS = {{ status|tojson }};
const TRACKER_USER = {{ current_user|tojson }};
const SERVER_UNLOCKED = {{ 'true' if is_write_unlocked else 'false' }};
let CLIENT_UNLOCKED = SERVER_UNLOCKED;
const STORE_KEY = 'nfl-bets-' + TRACKER_USER + '-' + YEAR + '-week-' + WEEK;

function getSelectedIdsFromDom() {
    return Array.from(document.querySelectorAll('[data-bet-row]'))
        .filter(row => row.dataset.selected === 'true')
        .map(row => row.dataset.betId);
}

function persistWeekState() {
    var payload = {selected_ids: getSelectedIdsFromDom(), status: STATUS, locked: STATUS === 'locked' || STATUS === 'graded'};
    localStorage.setItem(STORE_KEY, JSON.stringify(payload));
}

function updateAuthUI() {
    var unlocked = CLIENT_UNLOCKED;
    var pill = document.getElementById('auth-pill');
    var icon = document.getElementById('auth-icon');
    var text = document.getElementById('auth-text');
    if (!pill) return;
    pill.classList.toggle('unlocked', unlocked);
    icon.textContent = unlocked ? 'SIGNED IN' : 'SIGN IN';
    text.textContent = unlocked ? TRACKER_USER : 'Guest';
}

function unlockWrites() {
    return ensureWriteAuth(true);
}

function ensureWriteAuth(forcePrompt) {
    if (!forcePrompt && CLIENT_UNLOCKED) {
        return Promise.resolve(true);
    }
    var pwd = window.prompt('Enter sign-in password');
    if (pwd === null) return Promise.resolve(false);
    var user = sessionStorage.getItem('nfl-screen-user') || '';
    if (forcePrompt || !user) {
        user = window.prompt('Enter your name', user);
        if (user === null) return Promise.resolve(false);
        user = user.trim();
    }
    return fetch('/auth', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({password: pwd, user: user})
    }).then(function(r) {
        if (!r.ok) throw new Error('Incorrect password');
        return r.json();
    }).then(function(data) {
        if (data.ok) {
            CLIENT_UNLOCKED = true;
            if (data.user) sessionStorage.setItem('nfl-screen-user', data.user);
            updateAuthUI();
            if (data.user && data.user !== TRACKER_USER) {
                window.location.href = data.last_screen || (window.location.pathname + window.location.search);
                return false;
            }
            if (forcePrompt && data.last_screen && data.last_screen !== window.location.pathname + window.location.search) {
                window.location.href = data.last_screen;
            }
            return true;
        }
        return false;
    }).catch(function(err) {
        alert(err.message || 'Could not sign in');
        CLIENT_UNLOCKED = false;
        updateAuthUI();
        return false;
    });
}

function bindProtectedForms() {
    document.querySelectorAll('form[method="POST"]').forEach(function(form) {
        form.addEventListener('submit', function(e) {
            if (form.dataset.authSubmitting === 'true') return;
            e.preventDefault();
            ensureWriteAuth(false).then(function(ok) {
                if (!ok) return;
                form.dataset.authSubmitting = 'true';
                form.submit();
            });
        });
    });
}

function profitFromOdds(amount, odds) {
    amount = Number(amount) || 0;
    odds = Number(odds) || -110;
    return odds < 0 ? amount * (100 / Math.abs(odds)) : amount * (odds / 100);
}

function recalcProjectedProfit() {
    var total = 0;
    document.querySelectorAll('[data-bet-row]').forEach(function(row) {
        if (row.dataset.selected === 'true') {
            total += profitFromOdds(row.dataset.amount, row.dataset.odds);
        }
    });
    var el = document.getElementById('projected-profit');
    if (el) el.textContent = '$' + total.toFixed(2);
}

function applySelectedOnlyFilter() {
    var selectedOnly = document.getElementById('selected-only');
    selectedOnly = selectedOnly && selectedOnly.dataset.active === 'true';
    document.querySelectorAll('[data-bet-row]').forEach(function(row) {
        row.style.display = (!selectedOnly || row.dataset.selected === 'true') ? '' : 'none';
    });
    document.querySelectorAll('[data-game-card]').forEach(function(card) {
        var visibleRows = Array.from(card.querySelectorAll('[data-bet-row]'))
            .filter(function(row) { return row.style.display !== 'none'; });
        card.style.display = (!selectedOnly || visibleRows.length > 0) ? '' : 'none';
    });
}

function toggleSelectedOnly() {
    var btn = document.getElementById('selected-only');
    if (!btn) return;
    var active = btn.dataset.active === 'true';
    btn.dataset.active = active ? 'false' : 'true';
    btn.classList.toggle('active', !active);
    applySelectedOnlyFilter();
}

function copySelectedBets() {
    var lines = Array.from(document.querySelectorAll('[data-bet-row]'))
        .filter(function(row) { return row.dataset.selected === 'true'; })
        .map(function(row) {
            var type = row.dataset.type === 'spread' ? 'spread' : (row.dataset.type === 'total' ? 'total' : 'ML');
            var line = row.dataset.line ? ' ' + row.dataset.line : '';
            return row.dataset.pick + ' | ' + type + line + ' | odds ' + signedOdds(row.dataset.odds);
        });
    if (!lines.length) {
        alert('No selected bets to copy.');
        return;
    }
    navigator.clipboard.writeText(lines.join('\\n')).then(function() {
        var note = document.getElementById('copy-note');
        if (note) {
            note.classList.add('show');
            setTimeout(function() { note.classList.remove('show'); }, 1300);
        }
    });
}

function selectRecommendedBets() {
    var recommendedIds = Array.from(document.querySelectorAll('[data-bet-row]'))
        .filter(function(row) { return row.dataset.recommended === 'true'; })
        .map(function(row) { return row.dataset.betId; });

    if (!recommendedIds.length) {
        alert('No recommended bets for this week.');
        return;
    }

    ensureWriteAuth(false).then(function(ok) {
        if (!ok) return;
        fetch('/sync-selection', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({year: YEAR, week: WEEK, selected_ids: recommendedIds})
        }).then(function(r) { return r.json(); }).then(function(data) {
            if (data.ok) applyBetState(data);
        });
    });
}

function signedOdds(odds) {
    odds = Number(odds) || 0;
    return odds > 0 ? '+' + odds : String(odds);
}

function rehydrateSelectionFromLocalStorage() {
    if (STATUS !== 'pending') {
        persistWeekState();
        return;
    }
    var savedRaw = localStorage.getItem(STORE_KEY);
    if (!savedRaw) {
        persistWeekState();
        return;
    }
    try {
        var saved = JSON.parse(savedRaw);
        if (!Array.isArray(saved.selected_ids)) return;
        var current = getSelectedIdsFromDom().sort().join('|');
        var stored = saved.selected_ids.slice().sort().join('|');
        if (current === stored) return;
        ensureWriteAuth(false).then(function(ok) {
            if (!ok) return;
            fetch('/sync-selection', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({year: YEAR, week: WEEK, selected_ids: saved.selected_ids})
            }).then(function(r) { return r.json(); }).then(function(data) {
                if (data.ok) applyBetState(data);
            });
        });
    } catch (e) {
        localStorage.removeItem(STORE_KEY);
    }
}

function applyBetState(data) {
    data.bets.forEach(b => {
        var check = document.getElementById('check-' + b.id);
        var row = document.getElementById('row-' + b.id);
        var amt = document.getElementById('amt-' + b.id);
        if (row) {
            row.dataset.selected = b.selected ? 'true' : 'false';
            row.dataset.amount = Number(b.amount || 0).toFixed(2);
            row.className = b.selected ? 'bet-row selected' : 'bet-row';
        }
        if (check) {
            check.className = b.selected ? 'bet-check on' : 'bet-check';
            check.innerHTML = b.selected ? 'X' : '';
        }
        if (amt) amt.textContent = '$' + Number(b.amount || 0).toFixed(2);
    });
    var wagered = document.getElementById('total-wagered');
    var count = document.getElementById('sel-count');
    if (wagered) wagered.textContent = '$' + data.total_wagered.toFixed(2);
    if (count) count.textContent = data.selected_count;
    if (typeof data.projected_profit === 'number') {
        var pp = document.getElementById('projected-profit');
        if (pp) pp.textContent = '$' + data.projected_profit.toFixed(2);
    } else {
        recalcProjectedProfit();
    }
    applySelectedOnlyFilter();
    persistWeekState();
}

function toggleBet(betId) {
    ensureWriteAuth(false).then(function(ok) {
        if (!ok) return;
    fetch('/toggle', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({year: YEAR, week: WEEK, bet_id: betId})
    })
    .then(r => {
        if (r.status === 401) throw new Error('Password required');
        return r.json();
    })
    .then(data => {
        if (!data.ok) return;
        applyBetState(data);
    }).catch(function(err) {
        alert(err.message || 'Could not update bet');
    });
    });
}

function toggleStats(e, statsId) {
    e.stopPropagation();
    var el = document.getElementById(statsId);
    var arrow = document.getElementById('arrow-' + statsId.replace('stats-', ''));
    el.classList.toggle('open');
    if (arrow) arrow.classList.toggle('open');
}

function toggleTeam(row) {
    row.classList.toggle('expanded');
}

document.addEventListener('DOMContentLoaded', function() {
    updateAuthUI();
    bindProtectedForms();
    rehydrateSelectionFromLocalStorage();
    recalcProjectedProfit();
    drawPnlDashboardCurve();
});

function drawPnlDashboardCurve() {
    var canvas = document.getElementById('pnlDashboardBankroll');
    if (!canvas) return;
    var pts = [
        {% for p in pnl_dashboard.bankroll_points %}
        {week: {{ p.week }}, bankroll: {{ p.bankroll }} },
        {% endfor %}
    ];
    if (!pts.length) return;
    var dpr = window.devicePixelRatio || 1;
    var rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    var ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    var W = rect.width, H = rect.height;
    var pad = {t: 12, r: 18, b: 32, l: 64};
    var cW = W - pad.l - pad.r, cH = H - pad.t - pad.b;
    var yMin = Math.min.apply(null, pts.map(function(p){return p.bankroll}));
    var yMax = Math.max.apply(null, pts.map(function(p){return p.bankroll}));
    var yPad = Math.max((yMax - yMin) * 0.15, 50);
    yMin -= yPad; yMax += yPad;
    if (yMin === yMax) yMax += 100;
    function xPos(i) { return pad.l + (pts.length === 1 ? 0 : i / (pts.length - 1)) * cW; }
    function yPos(v) { return pad.t + cH - ((v - yMin) / (yMax - yMin)) * cH; }
    ctx.clearRect(0, 0, W, H);
    ctx.strokeStyle = 'rgba(255,255,255,0.06)';
    ctx.fillStyle = '#888';
    ctx.font = '11px monospace';
    for (var i = 0; i <= 4; i++) {
        var y = pad.t + cH / 4 * i;
        var val = yMax - (yMax - yMin) / 4 * i;
        ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
        ctx.textAlign = 'right'; ctx.fillText('$' + Math.round(val), pad.l - 8, y + 4);
    }
    ctx.strokeStyle = '#4fc3f7';
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    pts.forEach(function(p, i) {
        var x = xPos(i), y = yPos(p.bankroll);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
    pts.forEach(function(p, i) {
        var x = xPos(i), y = yPos(p.bankroll);
        ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2);
        ctx.fillStyle = p.bankroll >= pts[0].bankroll ? '#66bb6a' : '#ef5350';
        ctx.fill();
        ctx.fillStyle = '#888'; ctx.textAlign = 'center'; ctx.font = '10px monospace';
        ctx.fillText(p.week === 0 ? 'Start' : 'W' + p.week, x, H - pad.b + 18);
    });
}

// ─── Charts ───
(function() {
    var bankrollCanvas = document.getElementById('bankrollChart');
    var pnlCanvas = document.getElementById('pnlChart');
    if (!bankrollCanvas || !pnlCanvas) return;

    var startingBR = {{ summary.starting_bankroll }};
    var weeks = [
        {% for gw in graded_weeks %}
        {week: {{ gw.week }}, pnl: {{ gw.pnl }}, bankroll: {{ gw.bankroll_after }} },
        {% endfor %}
    ];
    if (weeks.length === 0) return;

    var accent = '#4fc3f7', green = '#66bb6a', red = '#ef5350',
        muted = '#888', border = '#2a2d3a', gridColor = 'rgba(255,255,255,0.06)';

    function drawChart(canvas, drawFn) {
        var dpr = window.devicePixelRatio || 1;
        var rect = canvas.getBoundingClientRect();
        canvas.width = rect.width * dpr;
        canvas.height = rect.height * dpr;
        var ctx = canvas.getContext('2d');
        ctx.scale(dpr, dpr);
        ctx.clearRect(0, 0, rect.width, rect.height);
        drawFn(ctx, rect.width, rect.height);
    }

    // ── Bankroll line chart ──
    drawChart(bankrollCanvas, function(ctx, W, H) {
        var pad = {t: 10, r: 16, b: 30, l: 60};
        var cW = W - pad.l - pad.r, cH = H - pad.t - pad.b;

        // Data points: start + each week
        var pts = [{x: 0, y: startingBR, label: 'Start'}];
        weeks.forEach(function(w) { pts.push({x: w.week, y: w.bankroll, label: 'Wk ' + w.week}); });

        var yMin = Math.min.apply(null, pts.map(function(p){return p.y}));
        var yMax = Math.max.apply(null, pts.map(function(p){return p.y}));
        var yPad = Math.max((yMax - yMin) * 0.15, 50);
        yMin = Math.floor((yMin - yPad) / 50) * 50;
        yMax = Math.ceil((yMax + yPad) / 50) * 50;
        if (yMin === yMax) yMax = yMin + 100;

        function xPos(i) { return pad.l + (i / (pts.length - 1)) * cW; }
        function yPos(v) { return pad.t + cH - ((v - yMin) / (yMax - yMin)) * cH; }

        // Grid lines
        ctx.strokeStyle = gridColor; ctx.lineWidth = 1;
        var nGrid = 4;
        for (var i = 0; i <= nGrid; i++) {
            var gy = pad.t + (cH / nGrid) * i;
            ctx.beginPath(); ctx.moveTo(pad.l, gy); ctx.lineTo(W - pad.r, gy); ctx.stroke();
            var gVal = yMax - ((yMax - yMin) / nGrid) * i;
            ctx.fillStyle = muted; ctx.font = '11px monospace'; ctx.textAlign = 'right';
            ctx.fillText('$' + Math.round(gVal), pad.l - 8, gy + 4);
        }

        // Starting bankroll reference line
        ctx.strokeStyle = 'rgba(255,255,255,0.12)'; ctx.setLineDash([4,4]);
        ctx.beginPath(); ctx.moveTo(pad.l, yPos(startingBR)); ctx.lineTo(W - pad.r, yPos(startingBR)); ctx.stroke();
        ctx.setLineDash([]);

        // Line
        ctx.strokeStyle = accent; ctx.lineWidth = 2.5; ctx.lineJoin = 'round';
        ctx.beginPath();
        pts.forEach(function(p, i) {
            var x = xPos(i), y = yPos(p.y);
            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        });
        ctx.stroke();

        // Gradient fill under line
        var grad = ctx.createLinearGradient(0, pad.t, 0, H - pad.b);
        grad.addColorStop(0, 'rgba(79,195,247,0.18)'); grad.addColorStop(1, 'rgba(79,195,247,0)');
        ctx.fillStyle = grad;
        ctx.beginPath();
        pts.forEach(function(p, i) {
            var x = xPos(i), y = yPos(p.y);
            if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        });
        ctx.lineTo(xPos(pts.length - 1), H - pad.b);
        ctx.lineTo(xPos(0), H - pad.b);
        ctx.closePath(); ctx.fill();

        // Dots + labels
        pts.forEach(function(p, i) {
            var x = xPos(i), y = yPos(p.y);
            ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2);
            ctx.fillStyle = (p.y >= startingBR) ? green : red; ctx.fill();
            ctx.fillStyle = muted; ctx.font = '10px monospace'; ctx.textAlign = 'center';
            ctx.fillText(p.label, x, H - pad.b + 16);
        });
    });

    // ── Weekly P&L bar chart ──
    drawChart(pnlCanvas, function(ctx, W, H) {
        var pad = {t: 10, r: 16, b: 30, l: 60};
        var cW = W - pad.l - pad.r, cH = H - pad.t - pad.b;
        var n = weeks.length;
        if (n === 0) return;

        var pnls = weeks.map(function(w){return w.pnl});
        var maxAbs = Math.max.apply(null, pnls.map(function(v){return Math.abs(v)}));
        maxAbs = Math.max(maxAbs, 50);
        var yRange = Math.ceil(maxAbs / 50) * 50;

        function yPos(v) { return pad.t + cH / 2 - (v / yRange) * (cH / 2); }

        // Grid
        ctx.strokeStyle = gridColor; ctx.lineWidth = 1;
        [-yRange, -yRange/2, 0, yRange/2, yRange].forEach(function(v) {
            var y = yPos(v);
            ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
            ctx.fillStyle = muted; ctx.font = '11px monospace'; ctx.textAlign = 'right';
            ctx.fillText((v >= 0 ? '+' : '') + Math.round(v), pad.l - 8, y + 4);
        });

        // Zero line
        ctx.strokeStyle = 'rgba(255,255,255,0.2)'; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(pad.l, yPos(0)); ctx.lineTo(W - pad.r, yPos(0)); ctx.stroke();

        // Bars
        var barGap = 8;
        var barW = Math.min(40, (cW - barGap * (n + 1)) / n);
        var totalBarSpace = barW * n + barGap * (n - 1);
        var startX = pad.l + (cW - totalBarSpace) / 2;

        weeks.forEach(function(w, i) {
            var x = startX + i * (barW + barGap);
            var zeroY = yPos(0);
            var barY = yPos(w.pnl);
            var barH = Math.abs(barY - zeroY);

            // Bar
            var topY = w.pnl >= 0 ? barY : zeroY;
            ctx.fillStyle = w.pnl >= 0 ? green : red;
            ctx.beginPath();
            // Rounded top corners
            var r = Math.min(3, barH / 2);
            ctx.moveTo(x + r, topY);
            ctx.lineTo(x + barW - r, topY);
            ctx.quadraticCurveTo(x + barW, topY, x + barW, topY + r);
            ctx.lineTo(x + barW, topY + barH - r);
            ctx.quadraticCurveTo(x + barW, topY + barH, x + barW - r, topY + barH);
            ctx.lineTo(x + r, topY + barH);
            ctx.quadraticCurveTo(x, topY + barH, x, topY + barH - r);
            ctx.lineTo(x, topY + r);
            ctx.quadraticCurveTo(x, topY, x + r, topY);
            ctx.fill();

            // Value label above/below bar
            ctx.fillStyle = w.pnl >= 0 ? green : red;
            ctx.font = '10px monospace'; ctx.textAlign = 'center';
            var labelY = w.pnl >= 0 ? topY - 5 : topY + barH + 12;
            ctx.fillText((w.pnl >= 0 ? '+' : '') + Math.round(w.pnl), x + barW / 2, labelY);

            // Week label
            ctx.fillStyle = muted; ctx.font = '10px monospace';
            ctx.fillText('W' + w.week, x + barW / 2, H - pad.b + 16);
        });
    });
})();
</script>
</body>
</html>
"""


# ─── Helper functions ────────────────────────────────────────────────────────

def _run_model(year: int, week: int, bet_pct: int = 100):
    """Run the prediction model and return (predictions, team_stats, turnover_slopes, avg_sow, error)."""
    try:
        master = load_master_data(str(BASE_DIR / "master_data.csv"))
        glossary = load_team_glossary(str(BASE_DIR / "glossary_teams.csv"))
        abbr_to_name = build_abbr_to_name(glossary)
        name_to_abbr = {v: k for k, v in abbr_to_name.items()}

        # Load or scrape schedule
        schedule_path = BASE_DIR / f"schedule_week{week}_{year}.csv"
        if schedule_path.exists():
            schedule = load_schedule(str(schedule_path))
        else:
            from scraper import scrape_schedule
            schedule = scrape_schedule(year, week)

        weekids = get_weekid_range(year, week)
        filtered = filter_master_data(master, weekids)
        if len(filtered) == 0:
            return None, None, None, None, "No historical data in lookback window."

        feature_table, turnover_slopes, avg_sow = build_feature_table(master, filtered)

        rng = np.random.default_rng(42)
        preds_df = predict_week(schedule, feature_table, turnover_slopes,
                                avg_sow, abbr_to_name, rng=rng)
        preds_df = compute_bet_sizing(preds_df, 0, bet_fraction=1.0)  # placeholder, tracker recalcs

        predictions = preds_df.to_dict('records')

        # Build team stats dict keyed by abbreviation
        team_stats = {}
        for _, row in feature_table.iterrows():
            abbr = name_to_abbr.get(row['team'], '')
            if abbr:
                team_stats[abbr] = row.to_dict()

        return predictions, team_stats, turnover_slopes, float(avg_sow), None

    except Exception as e:
        import traceback
        traceback.print_exc()
        return None, None, None, None, f"Model error: {e}"


def _fetch_game_results(year: int, week: int) -> dict:
    """Fetch actual game results from nflverse. Returns {game_number: {away_team, home_team, away_score, home_score}}."""
    try:
        from scraper import scrape_schedule
        sched = scrape_schedule(year, week)
        results = {}
        for _, row in sched.iterrows():
            if pd.notna(row.get('away_result')) and pd.notna(row.get('home_result')):
                away_score = int(row['away_result'])
                home_score = int(row['home_result'])
                if away_score > 0 or home_score > 0:  # has actual results
                    results[row['game_number']] = {
                        'away_team': row['away_team'],
                        'home_team': row['home_team'],
                        'away_score': away_score,
                        'home_score': home_score,
                    }
        return results
    except Exception as e:
        print(f"Error fetching results: {e}")
        return {}


def _build_games_list(week_data: dict) -> list:
    """Build a list of game dicts with predictions and bets merged."""
    predictions = week_data.get("predictions", [])
    bets = week_data.get("bets", [])

    # Index bets by game_number
    bets_by_game = {}
    for b in bets:
        gn = b["game_number"]
        if gn not in bets_by_game:
            bets_by_game[gn] = []
        bets_by_game[gn].append(b)

    # Fetch actual scores if graded
    actual_scores = {}
    if week_data["status"] == "graded":
        for b in bets:
            if b.get("result") in ("win", "loss", "push"):
                # We can reconstruct actual scores from bet results and predictions
                pass  # handled below via game_results stored at grade time

    games = []
    for pred in predictions:
        gn = pred["game_number"]

        # Try to find actual scores from graded bets
        actual_away = None
        actual_home = None
        if week_data["status"] == "graded":
            # Look for actual scores in the stored data
            actual_away = pred.get("actual_away_score")
            actual_home = pred.get("actual_home_score")

        games.append({
            "game_number": gn,
            "away_team": pred["away_team"],
            "home_team": pred["home_team"],
            "away_predicted_pts": pred["away_predicted_pts"],
            "home_predicted_pts": pred["home_predicted_pts"],
            "predicted_winner": pred["predicted_winner"],
            "winner_prob": pred["winner_prob"],
            "predicted_spread": pred["predicted_spread"],
            "bets": bets_by_game.get(gn, []),
            "actual_away": actual_away,
            "actual_home": actual_home,
        })

    return games


def _get_graded_weeks(tracker: SeasonTracker) -> list:
    """Build summary of all graded (and locked) weeks for the season history table."""
    weeks = []
    for wk_str in sorted(tracker.data["weeks"].keys(), key=int):
        wk_data = tracker.data["weeks"][wk_str]
        if wk_data["status"] not in ("graded", "locked"):
            continue

        bets = wk_data["bets"]
        selected = [b for b in bets if b.get("selected")]
        wagered = sum(b["amount"] for b in selected)
        wins = sum(1 for b in selected if b.get("result") == "win")
        losses = sum(1 for b in selected if b.get("result") == "loss")
        pushes = sum(1 for b in selected if b.get("result") == "push")
        pnl = wk_data["bankroll_after"] - wk_data["bankroll_before"]

        weeks.append({
            "week": int(wk_str),
            "status": wk_data["status"],
            "wagered": wagered,
            "record": f"{wins}-{losses}" + (f"-{pushes}" if pushes else ""),
            "pnl": pnl,
            "bankroll_after": wk_data["bankroll_after"],
        })

    return weeks


def _configured_password() -> str:
    """Return the dashboard write password from deployment env."""
    return (
        os.environ.get("APP_PASSWORD")
        or os.environ.get("VITE_APP_PASSWORD")
        or os.environ.get("NEXT_PUBLIC_APP_PASSWORD")
        or ""
    )


def _is_write_unlocked() -> bool:
    password = _configured_password()
    return bool(session.get("screen_user")) and (not password or bool(session.get("write_unlocked")))


def _screen_user(raw_user: str) -> str:
    """Normalize a login name so it is safe to use as part of a Redis key."""
    user = (raw_user or "").strip().lower()
    user = re.sub(r"[^a-z0-9_.@-]+", "-", user)
    return user.strip("-")[:80]


def _current_tracker_user() -> str:
    return session.get("screen_user") or "guest"


def _season_tracker(year: int) -> SeasonTracker:
    return SeasonTracker(year, _current_tracker_user())


def _safe_screen_path(raw_path: str) -> str:
    """Only allow internal dashboard paths to be restored after login."""
    if not raw_path or not raw_path.startswith("/") or raw_path.startswith("//"):
        return "/"
    if raw_path.startswith("/auth"):
        return "/"
    return raw_path[:500]


def _current_screen_path() -> str:
    args = {
        key: value
        for key, value in request.args.items()
        if key not in ("error", "success")
    }
    return "/" + (f"?{urlencode(args)}" if args else "")


def _upstash_config():
    url = os.environ.get("UPSTASH_REDIS_REST_URL", "").rstrip("/")
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
    if not url or not token:
        return None
    return url, token


def _last_screen_key(user: str) -> str:
    return f"last_screen:{user}"


def _upstash_request(command: list):
    config = _upstash_config()
    if not config:
        return None
    url, token = config
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=command,
            timeout=2,
        )
        resp.raise_for_status()
        return resp.json().get("result")
    except requests.RequestException as err:
        app.logger.warning("Upstash request failed: %s", err)
        return None


def _save_last_screen(user: str, path: str) -> None:
    user = _screen_user(user)
    if not user:
        return
    payload = json.dumps({"path": _safe_screen_path(path)})
    _upstash_request(["SET", _last_screen_key(user), payload])


def _load_last_screen(user: str):
    user = _screen_user(user)
    if not user:
        return None
    raw = _upstash_request(["GET", _last_screen_key(user)])
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return _safe_screen_path(data.get("path"))


def _require_write_auth_json():
    if _is_write_unlocked():
        return None
    return jsonify({"ok": False, "auth_required": True, "error": "Password required"}), 401


def _require_write_auth_redirect(year: int, week: int):
    if _is_write_unlocked():
        return None
    return redirect(f"/?year={year}&week={week}&error=Password+required+for+write+actions")


def _moneyline_profit(amount: float, odds: int) -> float:
    return SeasonTracker.win_profit(amount, odds)


def _projected_profit(bets: list) -> float:
    return round(sum(
        _moneyline_profit(float(b.get("amount", 0) or 0), int(b.get("odds", -110) or -110))
        for b in bets if b.get("selected")
    ), 2)


def _label_pct(value):
    if value is None:
        return "N/A"
    return f"{value * 100:.1f}%"


def _label_num(value):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "Insufficient data"
    return f"{value:.2f}"


def _mean(values: list) -> float:
    return float(np.mean(values)) if values else 0.0


def _stdev(values: list):
    if len(values) < 2:
        return None
    return float(np.std(values, ddof=1))


def _game_results_from_week(week_data: dict) -> dict:
    results = {}
    for pred in week_data.get("predictions", []):
        if pred.get("actual_away_score") is None or pred.get("actual_home_score") is None:
            continue
        results[pred["game_number"]] = {
            "away_score": pred["actual_away_score"],
            "home_score": pred["actual_home_score"],
        }
    return results


def _allocate_bet_amounts(bets: list, selected_ids: set, bankroll: float, bet_pct: int) -> list:
    pool = bankroll * bet_pct / 100
    total_value = sum(max(0, float(b.get("value", 0) or 0)) for b in bets if b["id"] in selected_ids)
    allocated = []
    for bet in bets:
        b = dict(bet)
        b["selected"] = b["id"] in selected_ids
        if b["selected"] and total_value > 0:
            b["amount"] = round(max(0, float(b.get("value", 0) or 0)) / total_value * pool, 2)
        else:
            b["amount"] = 0.0
        allocated.append(b)
    return allocated


def _week_pnl_row(week_num: int, week_data: dict, week_bankroll: float, mode: str,
                  selected_ids: set = None) -> dict:
    selected_ids = selected_ids or {b["id"] for b in week_data.get("bets", []) if b.get("selected")}
    bets = _allocate_bet_amounts(
        week_data.get("bets", []),
        selected_ids,
        week_bankroll,
        int(week_data.get("bet_pct", 100) or 100),
    )
    game_results = _game_results_from_week(week_data)
    use_projected = mode == "projected"

    profits = {"ml": 0.0, "spread": 0.0, "total": 0.0}
    prediction_hits = {t: [0, 0] for t in profits}
    bet_hits = {t: [0, 0] for t in profits}
    amount_bet = 0.0
    bets_taken = 0

    for bet in bets:
        btype = bet.get("type")
        if btype not in profits:
            continue

        result = None
        game_result = game_results.get(bet["game_number"])
        if game_result:
            result = SeasonTracker.grade_bet_result(bet, game_result)
            prediction_hits[btype][1] += 1
            if result == "win":
                prediction_hits[btype][0] += 1

        if not bet.get("selected"):
            continue

        amount_bet += float(bet.get("amount", 0) or 0)
        bets_taken += 1
        if use_projected:
            pnl = SeasonTracker.pnl_for_bet(bet, "win", projected=True)
            result_for_hit = "win"
        elif not game_result:
            pnl = 0.0
            result_for_hit = None
        else:
            pnl = SeasonTracker.pnl_for_bet(bet, result or "loss")
            result_for_hit = result

        profits[btype] += pnl
        if result_for_hit in ("win", "loss", "push"):
            bet_hits[btype][1] += 1
            if result_for_hit == "win":
                bet_hits[btype][0] += 1

    week_net = sum(profits.values())
    amount_bet = round(amount_bet, 2)
    margin = week_net / amount_bet if amount_bet > 0 else 0.0

    row = {
        "week": week_num,
        "ml_profit": round(profits["ml"], 2),
        "spread_profit": round(profits["spread"], 2),
        "total_profit": round(profits["total"], 2),
        "week_net_profit": round(week_net, 2),
        "amount_bet": amount_bet,
        "profit_margin": margin,
        "bets_taken": bets_taken,
        "ending_bankroll": round(week_bankroll + week_net, 2),
    }

    for btype in ("ml", "spread", "total"):
        ph_w, ph_t = prediction_hits[btype]
        bh_w, bh_t = bet_hits[btype]
        row[f"{btype}_hit_rate"] = ph_w / ph_t if ph_t else None
        row[f"{btype}_hit_rate_v2"] = bh_w / bh_t if bh_t else None
        row[f"{btype}_hit_rate_v2_label"] = _label_pct(row[f"{btype}_hit_rate_v2"])

    return row


def _build_pnl_dashboard(tracker: SeasonTracker, mode: str) -> dict:
    starting = float(tracker.starting_bankroll)
    rows = []
    bankroll = starting
    all_rec_bankroll = starting
    all_rec_pnl_total = 0.0

    for wk_str in sorted(tracker.data["weeks"].keys(), key=int):
        week_data = tracker.data["weeks"][wk_str]
        if week_data.get("status") not in ("pending", "locked", "graded"):
            continue
        if mode == "actual" and week_data.get("status") != "graded":
            continue

        week_num = int(wk_str)
        calc_mode = "actual" if mode == "actual" and week_data.get("status") == "graded" else mode
        row = _week_pnl_row(week_num, week_data, bankroll, calc_mode)
        bankroll = row["ending_bankroll"]
        rows.append(row)

        rec_ids = {b["id"] for b in week_data.get("bets", [])
                   if b.get("recommended") and float(b.get("value", 0) or 0) > 0}
        if week_data.get("status") == "graded" and rec_ids:
            rec_row = _week_pnl_row(week_num, week_data, all_rec_bankroll, "actual", rec_ids)
            all_rec_bankroll = rec_row["ending_bankroll"]
            all_rec_pnl_total += rec_row["week_net_profit"]

    margins = [r["profit_margin"] for r in rows]
    total_pnl = round(sum(r["week_net_profit"] for r in rows), 2)
    total_amount = round(sum(r["amount_bet"] for r in rows), 2)
    stdev = _stdev(margins)
    sharpe = ((np.mean(margins) - 0.000769) / stdev) if stdev and stdev > 0 else None

    by_type = {}
    for btype in ("ml", "spread", "total"):
        weekly_pred = [r[f"{btype}_hit_rate"] for r in rows if r[f"{btype}_hit_rate"] is not None]
        weekly_bet = [r[f"{btype}_hit_rate_v2"] for r in rows if r[f"{btype}_hit_rate_v2"] is not None]
        by_type[btype] = {
            "profit": round(sum(r[f"{btype}_profit"] for r in rows), 2),
            "prediction_hit_label": _label_pct(_mean(weekly_pred) if weekly_pred else None),
            "bet_hit_label": _label_pct(_mean(weekly_bet) if weekly_bet else None),
        }

    expanding = []
    for idx, row in enumerate(rows, 1):
        window = margins[:idx]
        sd = _stdev(window)
        row["sharpe"] = ((np.mean(window) - 0.000769) / sd) if sd and sd > 0 else None
        row["sharpe_label"] = _label_num(row["sharpe"])
        expanding.append(row["sharpe"])

    max_abs = max([abs(r["week_net_profit"]) for r in rows] + [1])
    for row in rows:
        row["bar_height"] = max(3, abs(row["week_net_profit"]) / max_abs * 100)

    projection = None
    if margins and stdev is not None:
        completed = len(rows)
        remaining = max(0, 18 - completed)
        current_balance = starting + total_pnl
        expected_margin = _mean(margins)
        projection = {
            "completed_weeks": completed,
            "remaining_weeks": remaining,
            "current_balance": current_balance,
            "expected_margin": expected_margin,
            "margin_stdev": stdev,
            "projected_final": current_balance * ((1 + expected_margin) ** remaining),
            "upper_band": current_balance * ((1 + expected_margin + stdev) ** remaining),
            "lower_band": current_balance * ((1 + expected_margin - stdev) ** remaining),
        }

    return {
        "weeks": rows,
        "by_type": by_type,
        "bankroll_points": [{"week": 0, "bankroll": starting}]
        + [{"week": r["week"], "bankroll": r["ending_bankroll"]} for r in rows],
        "projection": projection,
        "summary": {
            "total_pnl": total_pnl,
            "profit_margin": total_pnl / starting if starting > 0 else 0.0,
            "total_bets_taken": sum(r["bets_taken"] for r in rows),
            "total_amount_bet": total_amount,
            "average_weekly_margin": _mean(margins),
            "sharpe": sharpe,
            "sharpe_label": _label_num(sharpe),
            "all_recommended_pnl": round(all_rec_pnl_total, 2),
        },
    }


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route("/auth", methods=["POST"])
def auth_write_actions():
    data = request.get_json(silent=True) or {}
    password = _configured_password()
    if not password or data.get("password") == password:
        screen_user = _screen_user(data.get("user"))
        if not screen_user:
            return jsonify({"ok": False, "error": "Name required"}), 400
        session["write_unlocked"] = True
        session["screen_user"] = screen_user
        last_screen = _load_last_screen(screen_user) if screen_user else None
        return jsonify({"ok": True, "user": screen_user, "last_screen": last_screen})
    return jsonify({"ok": False, "error": "Incorrect password"}), 401


@app.route("/")
def index():
    year = request.args.get("year", 2025, type=int)
    week = request.args.get("week", 1, type=int)
    tab = request.args.get("tab", "week")
    pnl_mode = request.args.get("pnl_mode", "actual")
    if tab not in ("week", "pnl"):
        tab = "week"
    if pnl_mode not in ("actual", "projected"):
        pnl_mode = "actual"
    if session.get("screen_user"):
        _save_last_screen(session["screen_user"], _current_screen_path())

    tracker = _season_tracker(year)
    status = tracker.week_status(week)
    week_data = tracker.get_week(week) or {}
    summary = tracker.get_season_summary()
    week_statuses = tracker.all_week_statuses()

    years = sorted(YEAR_INDEX_MAP.keys())
    games = _build_games_list(week_data) if week_data else []
    team_stats = week_data.get("team_stats", {})

    selected_count = sum(1 for b in week_data.get("bets", []) if b.get("selected"))
    total_wagered = sum(b["amount"] for b in week_data.get("bets", []) if b.get("selected"))
    projected_profit = _projected_profit(week_data.get("bets", []))

    # Week record for graded weeks
    week_record = ""
    if status == "graded":
        sel_bets = [b for b in week_data.get("bets", []) if b.get("selected")]
        w = sum(1 for b in sel_bets if b.get("result") == "win")
        l = sum(1 for b in sel_bets if b.get("result") == "loss")
        p = sum(1 for b in sel_bets if b.get("result") == "push")
        week_record = f"{w}-{l}" + (f"-{p}" if p else "")

    graded_weeks = _get_graded_weeks(tracker)
    projection = tracker.get_projection(week)
    pnl_dashboard = _build_pnl_dashboard(tracker, pnl_mode)

    error = request.args.get("error")
    success = request.args.get("success")

    return render_template_string(
        TEMPLATE,
        year=year, week=week, status=status,
        tab=tab, pnl_mode=pnl_mode,
        week_data=week_data, summary=summary,
        week_statuses=week_statuses, years=years,
        games=games, team_stats=team_stats,
        selected_count=selected_count, total_wagered=total_wagered,
        week_record=week_record, graded_weeks=graded_weeks,
        projected_profit=projected_profit,
        bankroll_locked=tracker.has_any_locked(),
        projection=projection,
        pnl_dashboard=pnl_dashboard,
        is_write_unlocked=_is_write_unlocked(),
        current_user=_current_tracker_user(),
        error=error, success=success,
    )


@app.route("/run", methods=["POST"])
def run_model():
    year = request.form.get("year", 2025, type=int)
    week = request.form.get("week", 1, type=int)
    bankroll = request.form.get("bankroll", 1000, type=float)
    bet_pct = request.form.get("bet_pct", 100, type=int)

    auth_redirect = _require_write_auth_redirect(year, week)
    if auth_redirect:
        return auth_redirect

    tracker = _season_tracker(year)

    # Set bankroll if not locked
    if not tracker.has_any_locked():
        tracker.set_starting_bankroll(bankroll)

    # Check if week can be initialized
    if tracker.week_status(week) not in ("new", "pending", "projection_only"):
        return redirect(f"/?year={year}&week={week}&error=Cannot+re-run+locked+or+graded+week")

    predictions, team_stats, turnover_slopes, avg_sow, error = _run_model(year, week, bet_pct)
    if error:
        return redirect(f"/?year={year}&week={week}&error={error}")

    # Recalculate bet amounts with actual bankroll
    current_bankroll = tracker.get_current_bankroll()
    pool = current_bankroll * bet_pct / 100

    # Get model recommendations
    ml_bets = [p for p in predictions if p['ml_bet'] == 'bet']
    spread_bets = [p for p in predictions if p['spread_bet'] == 'bet']
    total_bets = [p for p in predictions if p.get('total_bet') == 'bet']
    total_value = (sum(max(0, p['ml_value']) for p in ml_bets) +
                   sum(max(0, p['spread_value']) for p in spread_bets) +
                   sum(max(0, p.get('total_value', 0)) for p in total_bets))

    if total_value > 0:
        for p in predictions:
            if p['ml_bet'] == 'bet':
                p['ml_bet_amount'] = max(0, p['ml_value']) / total_value * pool
            else:
                p['ml_bet_amount'] = 0
            if p['spread_bet'] == 'bet':
                p['spread_bet_amount'] = max(0, p['spread_value']) / total_value * pool
            else:
                p['spread_bet_amount'] = 0
            if p.get('total_bet') == 'bet':
                p['total_bet_amount'] = max(0, p.get('total_value', 0)) / total_value * pool
            else:
                p['total_bet_amount'] = 0

    tracker.init_week(week, predictions, team_stats, bet_pct,
                      turnover_slopes=turnover_slopes, avg_sow=avg_sow)
    return redirect(f"/?year={year}&week={week}&success=Model+run+complete")


@app.route("/project", methods=["POST"])
def run_projection():
    """Project the rest of the season.

    Works in two modes:
      1. Normal: uses team_stats + turnover_slopes + avg_sow saved by a prior
         "Run Model" execution.
      2. Bootstrap (e.g. for a future year like 2026 where no week has been
         initialized and odds aren't available yet): builds team_stats from the
         most recent master_data window so the user can still see a full-season
         projection without spreads/odds.
    """
    year = request.form.get("year", 2025, type=int)
    week = request.form.get("week", 1, type=int)

    auth_redirect = _require_write_auth_redirect(year, week)
    if auth_redirect:
        return auth_redirect

    tracker = _season_tracker(year)
    week_data = tracker.get_week(week) or {}

    team_stats = week_data.get("team_stats", {})
    turnover_slopes = week_data.get("turnover_slopes")
    avg_sow = week_data.get("avg_sow")

    bootstrap = not team_stats
    if bootstrap or not turnover_slopes or avg_sow is None:
        # Build feature data from the most recent 6 weeks of master data.
        try:
            master = load_master_data(str(BASE_DIR / "master_data.csv"))
            glossary = load_team_glossary(str(BASE_DIR / "glossary_teams.csv"))
            abbr_to_name_local = build_abbr_to_name(glossary)
            name_to_abbr_local = {v: k for k, v in abbr_to_name_local.items()}

            weekids = get_weekid_range(year, week)
            available_weekids = sorted(master['weekid'].unique())
            # If the requested window has nothing (future year w/ no data yet),
            # fall back to the latest 6 available weekids.
            in_window = [w for w in weekids if w in available_weekids]
            if not in_window:
                in_window = available_weekids[-6:]

            filtered = filter_master_data(master, in_window)
            if len(filtered) == 0:
                return redirect(
                    f"/?year={year}&week={week}"
                    f"&error=No+master+data+available+to+build+features"
                )

            feature_table, ts_slopes, sow_avg = build_feature_table(master, filtered)

            if not turnover_slopes:
                turnover_slopes = ts_slopes
            if avg_sow is None:
                avg_sow = float(sow_avg)
            if not team_stats:
                team_stats = {}
                for _, row in feature_table.iterrows():
                    abbr = name_to_abbr_local.get(row['team'], '')
                    if abbr:
                        team_stats[abbr] = row.to_dict()
        except Exception as e:
            import traceback
            traceback.print_exc()
            return redirect(
                f"/?year={year}&week={week}"
                f"&error=Could+not+build+features:+{str(e).replace(' ', '+')}"
            )

    try:
        glossary = load_team_glossary(str(BASE_DIR / "glossary_teams.csv"))
        abbr_to_name = build_abbr_to_name(glossary)

        from projector import project_season
        projection = project_season(year, week, team_stats,
                                    turnover_slopes, avg_sow, abbr_to_name)
    except Exception as e:
        import traceback
        traceback.print_exc()
        err = str(e).replace(" ", "+")
        return redirect(f"/?year={year}&week={week}&error=Projection+failed:+{err}")

    tracker.save_projection(week, projection)

    msg = "Season+projection+complete"
    if projection.get('proxy_schedule'):
        proxy_year = projection.get('schedule_source_year')
        msg = f"Projection+complete+(used+{proxy_year}+schedule+as+proxy+—+{year}+slate+not+yet+published)"
    return redirect(f"/?year={year}&week={week}&success={msg}")


@app.route("/mirofish", methods=["POST"])
def run_mirofish():
    year = request.form.get("year", 2025, type=int)
    week = request.form.get("week", 1, type=int)

    auth_redirect = _require_write_auth_redirect(year, week)
    if auth_redirect:
        return auth_redirect

    tracker = _season_tracker(year)
    week_data = tracker.get_week(week)

    if not week_data or week_data["status"] != "pending":
        return redirect(f"/?year={year}&week={week}&error=Week+must+be+in+pending+state")

    from mirofish import analyze_week, is_available, MIROFISH_URL

    if not is_available():
        return redirect(
            f"/?year={year}&week={week}&error="
            "MiroFish+not+reachable+at+" + MIROFISH_URL.replace(":", "%3A").replace("/", "%2F")
            + "+—+make+sure+it+is+running")

    games = _build_games_list(week_data)
    team_stats = week_data.get("team_stats", {})

    verdicts = analyze_week(games, team_stats, year, week)

    if not verdicts:
        return redirect(f"/?year={year}&week={week}&error=MiroFish+returned+no+results")

    tracker.apply_mirofish(week, verdicts)

    agree = sum(1 for v in verdicts.values() if v["verdict"] == "agree")
    disagree = sum(1 for v in verdicts.values() if v["verdict"] == "disagree")

    return redirect(
        f"/?year={year}&week={week}&success="
        f"MiroFish+analysis+complete:+{agree}+agree,+{disagree}+disagree")


@app.route("/toggle", methods=["POST"])
def toggle_bet():
    auth_error = _require_write_auth_json()
    if auth_error:
        return auth_error

    data = request.get_json()
    year = data.get("year", 2025)
    week = data.get("week", 1)
    bet_id = data.get("bet_id")

    tracker = _season_tracker(year)
    tracker.toggle_bet(week, bet_id)

    week_data = tracker.get_week(week)
    bets = week_data["bets"]
    selected = [b for b in bets if b["selected"]]

    return jsonify({
        "ok": True,
        "bets": bets,
        "total_wagered": sum(b["amount"] for b in selected),
        "selected_count": len(selected),
        "projected_profit": _projected_profit(bets),
    })


@app.route("/sync-selection", methods=["POST"])
def sync_selection():
    auth_error = _require_write_auth_json()
    if auth_error:
        return auth_error

    data = request.get_json()
    year = data.get("year", 2025)
    week = data.get("week", 1)
    selected_ids = data.get("selected_ids", [])

    tracker = _season_tracker(year)
    ok = tracker.set_selected_bets(week, selected_ids)
    week_data = tracker.get_week(week) or {"bets": []}
    bets = week_data.get("bets", [])
    selected = [b for b in bets if b.get("selected")]
    return jsonify({
        "ok": ok,
        "bets": bets,
        "total_wagered": sum(b["amount"] for b in selected),
        "selected_count": len(selected),
        "projected_profit": _projected_profit(bets),
    })


@app.route("/lock", methods=["POST"])
def lock_bets():
    year = request.form.get("year", 2025, type=int)
    week = request.form.get("week", 1, type=int)

    auth_redirect = _require_write_auth_redirect(year, week)
    if auth_redirect:
        return auth_redirect

    tracker = _season_tracker(year)

    # Check at least one bet is selected
    week_data = tracker.get_week(week)
    if week_data:
        selected = [b for b in week_data["bets"] if b["selected"]]
        if not selected:
            return redirect(f"/?year={year}&week={week}&error=Select+at+least+one+bet+before+locking")

    if tracker.lock_bets(week):
        return redirect(f"/?year={year}&week={week}&success=Bets+locked")
    return redirect(f"/?year={year}&week={week}&error=Could+not+lock+bets")


@app.route("/unlock", methods=["POST"])
def unlock_bets():
    year = request.form.get("year", 2025, type=int)
    week = request.form.get("week", 1, type=int)

    auth_redirect = _require_write_auth_redirect(year, week)
    if auth_redirect:
        return auth_redirect

    tracker = _season_tracker(year)
    if tracker.unlock_bets(week):
        return redirect(f"/?year={year}&week={week}&success=Bets+unlocked")
    return redirect(f"/?year={year}&week={week}&error=Could+not+unlock+bets")


@app.route("/reset-week", methods=["POST"])
def reset_week():
    year = request.form.get("year", 2025, type=int)
    week = request.form.get("week", 1, type=int)

    auth_redirect = _require_write_auth_redirect(year, week)
    if auth_redirect:
        return auth_redirect

    tracker = _season_tracker(year)
    if tracker.reset_week(week):
        return redirect(f"/?year={year}&week={week}&success=Week+reset")
    return redirect(f"/?year={year}&week={week}&error=Nothing+to+reset")


@app.route("/grade", methods=["POST"])
def grade_week():
    year = request.form.get("year", 2025, type=int)
    week = request.form.get("week", 1, type=int)

    auth_redirect = _require_write_auth_redirect(year, week)
    if auth_redirect:
        return auth_redirect

    tracker = _season_tracker(year)
    week_data = tracker.get_week(week)

    if not week_data or week_data["status"] not in ("locked", "graded"):
        return redirect(f"/?year={year}&week={week}&error=Week+must+be+locked+first")

    # Fetch actual results from nflverse
    raw_results = _fetch_game_results(year, week)

    if not raw_results:
        return redirect(f"/?year={year}&week={week}&error=No+game+results+available+yet")

    # Map results to game numbers via team matching
    game_results = {}
    for gn, result in raw_results.items():
        # Find matching game in tracker bets
        for bet in week_data["bets"]:
            if (bet["away_team"] == result["away_team"] and
                    bet["home_team"] == result["home_team"]):
                game_results[bet["game_number"]] = result
                break
        else:
            # Direct game_number match as fallback
            game_results[gn] = result

    # Store actual scores in predictions for display
    predictions = week_data.get("predictions", [])
    for pred in predictions:
        gn = pred["game_number"]
        if gn in game_results:
            pred["actual_away_score"] = game_results[gn]["away_score"]
            pred["actual_home_score"] = game_results[gn]["home_score"]

    if tracker.grade_week(week, game_results):
        # Save updated predictions with actual scores
        tracker.data["weeks"][str(week)]["predictions"] = predictions
        tracker.save()
        return redirect(f"/?year={year}&week={week}&success=Week+graded")

    return redirect(f"/?year={year}&week={week}&error=Could+not+grade+week")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"Starting NFL Betting Tracker on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
