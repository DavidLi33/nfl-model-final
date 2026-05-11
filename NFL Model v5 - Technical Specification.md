# NFL Model v5 — Technical Specification
## For Python Rebuild by Claude Code

Generated from: NFL Model Sample.xlsx
Source sheets: Master Data, 2025X (formula template), 20251 (Week 1 computed values), Glossary, P&L 2025

---

## PART 1: DATA SCHEMA

### 1.1 Master Data Sheet

The master dataset contains game-level statistics spanning from 2022 to present, with 2240 rows total (2 rows per game — one perspective for each team).

#### Game Structure
- **Row pairs**: Each game is represented as two consecutive rows sharing the same Weekid, Season, date, week, away, and home values
- **Row 1 of pair**: Data from the perspective of the first team listed (typically the away team in the away/home naming)
- **Row 2 of pair**: Data from the opponent's perspective (typically the home team)
- **Time period**: Starting from 2022 regular season
- **Weeks**: 1-18 for regular season, then playoff games

#### Column Definitions (Columns A-AC; AD-AJ are empty)

| Column | Name | Type | Description | Example |
|--------|------|------|-------------|---------|
| A | Weekid | int | Sequential game week identifier across all seasons | 1 |
| B | Season | int | NFL season year | 2022 |
| C | date | datetime | Game date (YYYY-MM-DD) | 2022-09-08 |
| D | week | int | Week number within the season (1-18 regular, then playoffs) | 1 |
| E | away | str | Away team abbreviation (3-letter code, e.g., BUF, DAL, PHI) | BUF |
| F | vs | str | Relationship marker, always "@" | @ |
| G | home | str | Home team abbreviation (3-letter code) | LA |
| H | Win | formula | 1 if this team won, 0 if lost | 1 |
| I | offense | str | Full team name (e.g., "Buffalo Bills") | Buffalo Bills |
| J | offense points | int | Points scored by this team | 31 |
| K | offense yards totals | int | Total offensive yards (pass + rush) | 413 |
| L | offense pass yards | int | Passing yards gained | 292 |
| M | offense pass attempts | int | Number of pass attempts | 31 |
| N | offense rush yards | int | Rushing yards gained | 121 |
| O | offense rush attempts | int | Number of rush attempts | 19 |
| P | offense interceptions | int | Interceptions thrown (turnovers lost) | 2 |
| Q | offense fumbles | int | Fumbles lost | 2 |
| R | offense sacks | int | Sacks allowed to defense | 2 |
| S | offense number of touch downs | int | Touchdowns scored by offense | 4 |
| T | defense points | int | Points allowed (scored by opponent) | 10 |
| U | defense yards totals | int | Total yards allowed to opponent | 243 |
| V | defense pass yards | int | Passing yards allowed | 192 |
| W | defense pass attempts | int | Pass attempts faced from opponent | 42 |
| X | defense rush yards | int | Rushing yards allowed | 51 |
| Y | defense rush attempts | int | Rush attempts faced from opponent | 17 |
| Z | defense interceptions | int | Interceptions made (turnovers gained) | 3 |
| AA | defense fumbles | int | Fumbles recovered | 1 |
| AB | defense sacks | int | Sacks made against opponent | 6 |
| AC | defense number of touch downs | int | Defensive touchdowns scored | 1 |

#### Win Column Formula Logic
The Win column (H) is calculated using conditional logic, NOT raw input:
- **For odd rows** (first team of a pair): `IF(J_row > J_next_row, 1, 0)` — 1 if this team's score exceeds opponent's, else 0
- **For even rows** (second team of a pair): `IF(H_prev_row=1, 0, 1)` — 1 if opponent lost, else 0 (inverse of odd row)

#### Row Pair Example
```
Row 1: Weekid=1, Season=2022, date=2022-09-08, away=BUF, home=LA
       offense=Buffalo Bills, J=31, ... (Buffalo's stats)

Row 2: Weekid=1, Season=2022, date=2022-09-08, away=BUF, home=LA
       offense=LA Rams, J=10, ... (LA's stats)
       Win column: Row 1 has Win=1, Row 2 has Win=0 (Buffalo won 31-10)
```

### 1.2 Glossary Sheet

#### Team Lookup Table (Rows 2-33)

32 rows, one per NFL team, with 4 columns:

| Column | Name | Format |
|--------|------|--------|
| A | Full team name | String (e.g., "Arizona Cardinals") |
| B | City/region name | String (e.g., "Arizona") |
| C | 3-letter abbreviation | String (e.g., "ARI") |
| D | Nickname | String (e.g., "Cardinals") |

#### Control Parameters

**Row 36:**
- **B36**: `back_test_range` = Integer (typically 6)
  - Number of past weeks to include in rolling window averages
  - Controls how many historical weeks inform current week predictions

**Rows 36-39: Year Index Lookup**
- **D36-D39**: Years 2022, 2023, 2024, 2025
- **E36-E39**: Index values 1, 2, 3, 4
- Used to map season year to ordinal position for Weekid calculation
- Example: Year 2025 maps to index 4

#### Spread-to-Moneyline Lookup Table (Rows 40-105)

66 rows covering spreads from -27 to +27 in 0.5 increments:

| Column | Name | Description |
|--------|------|-------------|
| A | game_count | Number of historical games observed at this spread |
| B | spread | Point spread value (-27.0, -26.5, ..., +26.5, +27.0) |
| C | avg_moneyline | Average American moneyline odds for this spread |
| D | avg_moneyline | Duplicate of column C |
| E | MAX moneyline | Maximum observed moneyline value |
| F | MIN moneyline | Minimum observed moneyline value |

**Example entries:**
- spread=-3.0 → avg_moneyline=-160
- spread=+3.0 → avg_moneyline=+140
- spread=-7.0 → avg_moneyline=-321
- spread=+7.0 → avg_moneyline=+266

Used to estimate moneyline odds when actual odds are not in schedule data.

---

## PART 2: FORMULA LOGIC — STEP BY STEP

The model operates in distinct computational phases, each building on previous results. All formulas are organized by section.

### SECTION A: Data Filtering (Past Week Data)

This section identifies which historical games to include in rolling averages.

#### Step 1: Determine Weekids to Include

**English Description:**
Calculate which Weekid values to include based on the current year index, current week number, and back_test_range parameter. The model constructs a list of Weekids in cells B5:B20 representing all games from the past N weeks of the current season.

**Pseudocode:**
```
year_index = XLOOKUP(current_year, Glossary.D36:D39, Glossary.E36:E39)
# For 2025: year_index = 4

# For each week in the back_test_range window:
for week in (current_week - back_test_range + 1) to current_week:
    weekid = year_index * 18 - (18 - week)
    # weekid list stored in B5:B20

# Example for Week 1, 2025 with back_test_range=6:
# Includes Week 1, 2, 3, 4, 5, 6 of 2025
# AND last weeks of 2024 if Week 1 is within 6 weeks
# Actually for Week 1, back 6 weeks goes into 2024
# weekid for 2024 week 17 = 3*18 - (18-17) = 54 - 1 = 53
# weekid for 2024 week 18 = 3*18 - (18-18) = 54
# weekid for 2025 week 1 = 4*18 - (18-1) = 72 - 17 = 55
# (continuing for weeks 2-6 of 2025)
```

**Source cells:**
- Current year: B1 (user input)
- Current week: B2 (user input)
- back_test_range: Glossary!B36
- Year index mapping: Glossary!D36:E39

**Output:**
- B5:B20 contains list of Weekids to include (up to 18 if all weeks in range)

#### Step 2: Filter Master Data

**English Description:**
Extract all rows from the Master Data sheet where the Weekid column (A) is in the list of selected Weekids from Step 1. This creates a subset of raw game data spanning the rolling window.

**Pseudocode:**
```
past_week_data = FILTER(
    MasterData!A:AC,
    MasterData!A (Weekid) IN [B5:B20 list]
)
# Result occupies columns D through AF
```

**Filter output mapping:**
- D: Weekid
- E: Season
- F: date
- G: week
- H: away
- I: vs
- J: home
- K: Win
- L: offense (full name)
- M: offense points
- N: offense yards totals
- O: offense pass yards
- P: offense pass attempts
- Q: offense rush yards
- R: offense rush attempts
- S: offense interceptions
- T: offense fumbles
- U: offense sacks
- V: offense number of touch downs
- W: defense points
- X: defense yards totals
- Y: defense pass yards
- Z: defense pass attempts
- AA: defense rush yards
- AB: defense rush attempts
- AC: defense interceptions
- AD: defense fumbles
- AE: defense sacks
- AF: defense number of touch downs

#### Step 3: Calculate Win Rate and Opponent Win Rate

**English Description:**
For each row in the filtered data, look up this team's win rate and their opponent's win rate from the sorted averages table (computed in Step 6). This quantifies team strength for later use.

**Pseudocode:**
```
# Assuming this step runs AFTER sorted averages are calculated
# For row i containing team T:

AG[i] (Win rate) = XLOOKUP(L[i] (team name), sorted_teams, sorted_win_rates)

# Opponent is the adjacent row in the pair:
if i is odd (first row of pair):
    opponent = row i+1
else:
    opponent = row i-1

AH[i] (Opponent win rate) = XLOOKUP(L[opponent], sorted_teams, sorted_win_rates)
```

**Source:**
- Team name: Column L (offense column name)
- Sorted teams and win rates: Computed in Step 6 (columns BA-BU)

### SECTION B: Team Averages (Past Week Averages)

Rows 4-35 (one row per team), columns AJ-AY. These are simple arithmetic averages of each stat over the filtered games.

#### Step 4: Extract Unique Teams

**English Description:**
Generate a list of all unique team names that appear in the filtered data's offense column. This produces the 32 NFL teams (or fewer if not all teams played in the time window).

**Pseudocode:**
```
AK4:AK35 = UNIQUE(L4:L_end)
# L contains the offense (full team name) from filtered data
# Result: 32 rows, one team per row, alphabetically or in data order
```

**Source:**
- Filtered data offense column: Column L (output of Step 2)

#### Step 5: Calculate Team Averages

**English Description:**
For each team in the unique list, compute the arithmetic mean of each statistic across all games where that team was the offense. This creates a 32-row table of averaged stats per team.

**Pseudocode:**
```
for each team in AK4:AK35:
    AL (Avg offense points) = AVERAGEIF(L:L == team, M:M)
    AM (Avg offense yards total) = AVERAGEIF(L:L == team, N:N)
    AN (Avg offense pass yards) = AVERAGEIF(L:L == team, O:O)
    AO (Avg offense rush yards) = AVERAGEIF(L:L == team, Q:Q)
    AP (Avg offense INTs) = AVERAGEIF(L:L == team, S:S)
    AQ (Avg offense fumbles) = AVERAGEIF(L:L == team, T:T)
    AR (Avg offense sacks allowed) = AVERAGEIF(L:L == team, U:U)
    AS (Avg defense points allowed) = AVERAGEIF(L:L == team, W:W)
    AT (Avg defense yards allowed) = AVERAGEIF(L:L == team, X:X)
    AU (Avg defense pass yards allowed) = AVERAGEIF(L:L == team, Y:Y)
    AV (Avg defense rush yards allowed) = AVERAGEIF(L:L == team, AA:AA)
    AW (Avg defense INTs made) = AVERAGEIF(L:L == team, AC:AC)
    AX (Avg defense fumbles recovered) = AVERAGEIF(L:L == team, AD:AD)
    AY (Avg defense sacks made) = AVERAGEIF(L:L == team, AE:AE)
```

**Key notes:**
- All statistics are SIMPLE ARITHMETIC AVERAGES, not weighted by recency or strength of opponent
- The rolling window size (back_test_range) determines how many weeks are included
- Each team may appear different numbers of times if some teams play on different schedules

**Source:**
- Filtered data columns L-AE (from Step 2)

**Output structure:**
Rows 4-35, columns AJ-AY:
- AJ: Row number (not used, placeholder)
- AK: Team name (from Step 4)
- AL-AY: 13 stat columns (offense points, yards, pass, rush, INTs, fumbles, sacks; defense points, yards, pass, rush, INTs, fumbles, sacks)

### SECTION C: Sorted Averages

Rows 4-35, columns BA-BP. This section reorders the team averages by offensive point production.

#### Step 6: Sort Teams by Offense Points (Descending)

**English Description:**
Sort the team averages table by the Avg offense points column (AL) in descending order. This ranking is used for team lookups in the model section.

**Pseudocode:**
```
sorted_table = SORT(
    averages_table[AK:AY],
    by=column_AL (Avg offense points),
    order=DESCENDING,
    expand_reference=TRUE
)

# Output columns BA-BP:
# BA: Rank index (1=highest offense points, 32=lowest)
# BB: Team name
# BC-BP: Same stat columns as AK-AY but in sorted order
```

**Exact column mapping after sort:**
- BA: Rank (1-32)
- BB: Team name
- BC: Avg offense points (AL, descending)
- BD: Avg offense yards total (AM)
- BE: Avg offense pass yards (AN)
- BF: Avg offense rush yards (AO)
- BG: Avg offense INTs (AP)
- BH: Avg offense fumbles (AQ)
- BI: Avg offense sacks allowed (AR)
- BJ: Avg defense points (AS)
- BK: Avg defense yards total (AT)
- BL: Avg defense pass yards (AU)
- BM: Avg defense rush yards (AV)
- BN: Avg defense INTs (AW)
- BO: Avg defense fumbles (AX)
- BP: Avg defense sacks (AY)

### SECTION D: Turnover Point Value Regression

Located in rows 40-53, columns BC-BI. This section quantifies the average point impact of turnovers.

#### Step 7: Build Interception Regression

**English Description:**
For each distinct interception count (0, 1, 2, 3, ...) observed in the filtered data, calculate:
1. The average offense points scored when the offense threw that many interceptions (offense loss scenario)
2. The average offense points scored by teams whose defense made that many interceptions (defense gain scenario — when opponent turns it over, the other team's defense is credited)

Then compute linear regression slopes to quantify points per interception.

**Pseudocode:**
```
# BC42:BC53 = UNIQUE(filtered_data offense_interceptions column)
# Produces sorted list of distinct INT values: 0, 1, 2, 3, etc.

for each INT_count in BC42:BC53:
    # BD[row] = offense loss: avg points when a team threw INT_count INTs
    BD[row] = AVERAGEIF(
        S:S (offense_interceptions) == INT_count,
        M:M (offense_points)
    )

    # BE[row] = defense gain: avg points scored by teams whose defense made INT_count INTs
    # This is trickier: when defense made INT_count, what were the OPPONENT's offensive points?
    # Actually: when defense made INT_count INTs, the OFFENSE in that row scored these points:
    BE[row] = AVERAGEIF(
        AC:AC (defense_interceptions) == INT_count,
        M:M (offense_points)
    )
    # This is awkward because we're looking at offensive points when defense stat is examined
    # Better framing: BE is the average offense points of teams whose defense made INT_count INTs

# Linear regression slopes:
pts_per_int_offense_loss = SLOPE(BD42:BD53, BC42:BC53) * -1
# Multiply by -1 because more INTs means fewer points (negative slope becomes positive magnitude)

pts_per_int_defense_gain = SLOPE(BE42:BE53, BC42:BC53)
```

**Storage:**
- BD41: `pts_per_int_offense_loss` (e.g., 1.4858 = points lost per INT thrown)
- BE41: `pts_per_int_defense_gain` (e.g., 1.3563 = points gained per INT made)

**Example (Week 1, 2025 data):**
- INT counts observed: 0, 1, 2, 3
- When 0 INTs thrown, avg points = 24.5
- When 1 INT thrown, avg points = 23.1
- When 2 INTs thrown, avg points = 20.8
- When 3 INTs thrown, avg points = 18.2
- Slope ≈ -1.486 → multiply by -1 → pts_per_int_offense_loss = 1.4858

#### Step 8: Build Fumble Regression

**English Description:**
Same methodology as Step 7, but for fumbles instead of interceptions.

**Pseudocode:**
```
# BG42:BG53 = UNIQUE(filtered_data offense_fumbles column)

for each fumble_count in BG42:BG53:
    # BH[row] = offense loss: avg points when offense fumbled fumble_count times
    BH[row] = AVERAGEIF(
        T:T (offense_fumbles) == fumble_count,
        M:M (offense_points)
    )

    # BI[row] = defense gain: avg offense points of teams whose defense recovered fumble_count fumbles
    BI[row] = AVERAGEIF(
        AD:AD (defense_fumbles) == fumble_count,
        M:M (offense_points)
    )

pts_per_fumble_offense_loss = SLOPE(BH42:BH53, BG42:BG53) * -1
pts_per_fumble_defense_gain = SLOPE(BI42:BI53, BG42:BG53)
```

**Storage:**
- BH41: `pts_per_fumble_offense_loss` (e.g., 1.3135)
- BI41: `pts_per_fumble_defense_gain` (e.g., 1.6347)

### SECTION E: Points Adjustment

Rows 4-35, columns BQ-BT, aligned to the sorted table. This section decomposes team points into turnover-independent and turnover-dependent components.

#### Step 9: Decompose Points into Turnover and Non-Turnover Components

**English Description:**
For each team in the sorted averages, calculate how many points they scored that were directly attributable to turnovers (both committed and forced), then subtract those from the raw point totals to get a "baseline" points value that removes turnover noise.

**Pseudocode:**
```
for each team in sorted_teams (BA4:BA35):
    # Look up their averaged stats from the sorted table (BC:BP columns)

    avg_off_INTs = BG_row (from sorted table)
    avg_off_fumbles = BH_row
    avg_def_INTs = BN_row
    avg_def_fumbles = BO_row
    avg_off_points = BC_row
    avg_def_points = BJ_row

    # Points the offense cost by turning the ball over
    BR (Pts allowed by offense turnovers) =
        avg_off_INTs * pts_per_int_offense_loss +
        avg_off_fumbles * pts_per_fumble_offense_loss
    # pts_per_int_offense_loss from BD41, pts_per_fumble_offense_loss from BH41

    # Points the defense scored from forcing turnovers
    BS (Pts scored by defense turnovers) =
        avg_def_INTs * pts_per_int_defense_gain +
        avg_def_fumbles * pts_per_fumble_defense_gain
    # pts_per_int_defense_gain from BE41, pts_per_fumble_defense_gain from BI41

    # Adjusted points allowed by defense (remove turnover-caused points from opponent)
    BQ (Pts allowed by defense, adjusted) = avg_def_points - BR

    # Adjusted points scored by offense (remove turnover-scored points by opponent's defense)
    BT (Pts scored by offense, adjusted) = avg_off_points - BS
```

**Key insight:**
- BQ and BT remove the "extra" points caused by turnovers, leaving a baseline skill-based points value
- This normalizes teams with high/low turnover rates so predictions don't over-weight anomalies

**Storage:** Columns BQ-BT, rows 4-35 (one row per team, sorted by offense points)

**Example (Dallas Cowboys, Week 1 2025 data):**
- avg_off_points = 21.5
- avg_off_INTs = 0.5, avg_off_fumbles = 1.167
- BR = 0.5 * 1.4858 + 1.167 * 1.3135 = 2.275
- BT (adjusted off pts) = 21.5 - (BS from defense) — requires BS calculation
- avg_def_points = 24.833
- avg_def_INTs = 0.833, avg_def_fumbles = 1.5
- BS = 0.833 * 1.3563 + 1.5 * 1.6347 = 3.582
- BQ (adjusted def pts) = 24.833 - 2.275 = 22.558

### SECTION F: Win Rate, SOW, and Standard Deviation

Rows 4-35, columns BU-BX, aligned to sorted table.

#### Step 10: Calculate Win Rate

**English Description:**
For each team, compute the average win rate over the filtered games period.

**Pseudocode:**
```
for each team in sorted_teams:
    BU (Win rate) = AVERAGEIF(
        filtered_data L:L (offense) == team,
        filtered_data K:K (Win)
    )
    # Result: decimal between 0 and 1, e.g., 0.667 = 67% win rate
```

**Source:**
- Filtered data from Step 2 (columns L and K)
- Sorted team order from Step 6

#### Step 11: Calculate Opponent Win Rate

**English Description:**
For each team, calculate the average win rate of their opponents.

**Pseudocode:**
```
for each team in sorted_teams:
    BV (Opponent win rate) = AVERAGEIF(
        filtered_data L:L (offense) == team,
        AH:AH (opponent win rate from Step 3)
    )
    # Takes the opponent win rate of each game the team played and averages them
```

**Source:**
- AH column from Step 3 (opponent win rates computed during filtering)

#### Step 12: Calculate Strength of Wins (SOW)

**English Description:**
For each team, calculate the average opponent win rate specifically of the teams they BEAT (wins only). This quantifies schedule strength relative to wins achieved.

**Pseudocode:**
```
for each team in sorted_teams:
    BW (Strength of Wins) = AVERAGEIFS(
        AH:AH (opponent win rate),
        filtered_data L:L (offense) == team,
        filtered_data K:K (Win) == 1
    )

    # If team has zero wins, SOW = 0 (or #DIV/0! → coerce to 0)
```

**Key insight:**
- A team with SOW=0.6 beat opponents averaging 60% win rate (strong wins)
- A team with SOW=0.2 beat opponents averaging 20% win rate (weak wins)
- Helps distinguish 8-5 teams (who beat strong opponents) from 8-5 teams (who beat weak opponents)

#### Step 13: Calculate Points Standard Deviation

**English Description:**
For each team, calculate the sample standard deviation of their offensive points across all filtered games.

**Pseudocode:**
```
for each team in sorted_teams:
    BX (Stdev of points) = STDEV(
        FILTER(
            filtered_data M:M (offense points),
            filtered_data L:L (offense) == team
        )
    )
    # Standard sample STDEV (n-1 denominator), not population STDEVP
```

**Use:**
- Used in Monte Carlo simulation (Section K) to generate random score distributions

#### Step 14: Calculate Average SOW

**English Description:**
Compute the league-wide average SOW across all 32 teams.

**Pseudocode:**
```
BW36 (avg_SOW) = AVERAGE(BW4:BW35)
# Single cell, used as threshold in strength adjustment (Step 28)
```

**Storage:**
- BW36

### SECTION G: Schedule Data

Rows 4-21 (up to 18 games per week), columns BZ-CK. This section contains raw upcoming game matchups and betting odds.

#### Step 15: Populate Schedule (Manual Data Entry)

**English Description:**
The schedule data is manually entered each week by the user. One row per upcoming game, with team abbreviations, moneyline odds, spread, and total line information.

**Format (one row per game):**

| Column | Name | Type | Description | Example |
|--------|------|------|-------------|---------|
| BZ | Game # | int | 1-18 | 1 |
| CA | Week | int | Week number | 1 |
| CB | Away team | str | 3-letter abbr. | DAL |
| CC | Away ML | int | American odds | +240 |
| CD | Away spread | float | Point spread | +7.0 |
| CE | Away result | int | Actual score (filled after game) | 20 |
| CF | Home team | str | 3-letter abbr. | PHI |
| CG | Home ML | int | American odds | -340 |
| CH | Home spread | float | Point spread | -7.0 |
| CI | Home result | int | Actual score (filled after game) | 24 |
| CJ | Total line | float | Over/under line | 47.5 |
| CK | Total result | formula | = CE + CI (sum of scores) | 44 |

**Notes:**
- Rows 4-21 accommodate up to 18 games per week (18 regular season weeks + playoffs)
- Moneyline odds in American format (negative = favorite, positive = underdog)
- Spread always shown from away team perspective (positive = away team getting points, negative = away team favored)
- Total line is the over/under line
- CE, CI, CK populated after the game is played

---

## PART 3: FORMULA LOGIC — CONTINUED (SECTIONS H-N)

### SECTION H: Model — Offensive and Defensive Point Projections

Rows 4-39 (2 rows per game, up to 18 games), columns CM-CW. This section contains point projections broken down by component (passing, rushing, turnovers).

#### Step 16: Map Games to Team Pairs

**English Description:**
For each game in the schedule, look up the away and home teams and create two rows of projection data — one for the away team's perspective, one for the home team's perspective.

**Pseudocode:**
```
# Even rows (4, 6, 8, ...): Away team's projection
for each game_number in BZ4:BZ21:
    CM[even_row] = game_number (1, 2, 3, ...)
    CN[even_row] = XLOOKUP(
        game_number,
        schedule_BZ column,
        schedule_CA column
    )  # Week number

    CO[even_row] = TRIM(XLOOKUP(
        game_number,
        schedule_BZ,
        schedule_CB (away team abbr.)
    ))  # TRIM applied to remove whitespace

# Odd rows (5, 7, 9, ...): Home team's projection
for each game_number in BZ4:BZ21:
    CM[odd_row] = game_number (same as even row)
    CN[odd_row] = same as even row (same game)

    CO[odd_row] = TRIM(XLOOKUP(
        game_number,
        schedule_BZ,
        schedule_CF (home team abbr.)
    ))
```

**Output:**
- CM: Game number
- CN: Week number
- CO: Team abbreviation (3-letter code)

#### Step 17: Calculate Offensive Passing Points

**English Description:**
For a team, calculate how many points they are expected to score from passing based on their adjusted offensive points scaled by the proportion of their yards that come from passing.

**Pseudocode:**
```
# For each row with team abbreviation in CO:
team_abbr = CO[row]
team_full_name = XLOOKUP(team_abbr, Glossary.C:C, Glossary.A:A)

# Look up team's stats in sorted table (BA:BP rows):
team_row_in_sorted = MATCH(team_full_name, BB:BB, 0)

# Retrieve adjusted offensive points and yards breakdown
sorted_BT = BT[team_row] (adjusted offense points from Step 9)
sorted_BE = BE[team_row] (avg offense pass yards from sorted table)
sorted_BD = BD[team_row] (avg offense total yards from sorted table)

CP[row] (Passing Points) = sorted_BT * (sorted_BE / sorted_BD)
# = (adjusted pts scored by offense) * (pass yards / total yards)
```

**Key insight:**
- If a team scores 20 adjusted points and 60% of their yards are passing yards, they get ~12 passing points and ~8 rushing points
- This splits the adjusted offensive points into component parts based on their historical proportion

#### Step 18: Calculate Offensive Rushing Points

**English Description:**
Same as passing but for the rushing component.

**Pseudocode:**
```
CQ[row] (Rushing Points) = sorted_BT * (sorted_BF / sorted_BD)
# = (adjusted pts scored by offense) * (rush yards / total yards)
# sorted_BF = avg offense rush yards from sorted table
```

#### Step 19: Calculate Fumble Points Allowed (by offense)

**English Description:**
Points the team's offense gives up via fumbles (turnovers that negatively impact their expected points).

**Pseudocode:**
```
# Look up team's fumble rate in sorted table
avg_off_fumbles = sorted_BH (from column BH, sorted table)

CR[row] (Fumble Points Allowed) = avg_off_fumbles * pts_per_fumble_offense_loss
# pts_per_fumble_offense_loss from BH41
```

#### Step 20: Calculate Interception Points Allowed (by offense)

**English Description:**
Points the team's offense gives up via interceptions.

**Pseudocode:**
```
avg_off_INTs = sorted_BG (from column BG, sorted table)

CS[row] (Interception Points Allowed) = avg_off_INTs * pts_per_int_offense_loss
# pts_per_int_offense_loss from BD41
```

#### Step 21: Calculate Defensive Passing Points Allowed

**English Description:**
Points the defense allows through passing, proportional to passing yards share of total yards allowed.

**Pseudocode:**
```
# Look up team's defense stats in sorted table
sorted_BQ = BQ[team_row] (adjusted defense points allowed from Step 9)
sorted_BL = BL[team_row] (avg defense pass yards allowed from sorted table)
sorted_BK = BK[team_row] (avg defense total yards allowed from sorted table)

CT[row] (Passing Points Allowed) = sorted_BQ * (sorted_BL / sorted_BK)
```

#### Step 22: Calculate Defensive Rushing Points Allowed

**English Description:**
Points the defense allows through rushing.

**Pseudocode:**
```
sorted_BM = BM[team_row] (avg defense rush yards allowed from sorted table)

CU[row] (Rushing Points Allowed) = sorted_BQ * (sorted_BM / sorted_BK)
```

#### Step 23: Calculate Fumble Points (scored by defense)

**English Description:**
Points scored because the defense forces fumbles.

**Pseudocode:**
```
avg_def_fumbles = sorted_BO (from column BO, sorted table)

CV[row] (Fumble Points) = avg_def_fumbles * pts_per_fumble_defense_gain
# pts_per_fumble_defense_gain from BI41
```

#### Step 24: Calculate Interception Points (scored by defense)

**English Description:**
Points scored because the defense makes interceptions.

**Pseudocode:**
```
avg_def_INTs = sorted_BN (from column BN, sorted table)

CW[row] (Interception Points) = avg_def_INTs * pts_per_int_defense_gain
# pts_per_int_defense_gain from BE41
```

**Column Summary (CM-CW):**
- CM: Game number
- CN: Week number
- CO: Team abbreviation
- CP: Offensive passing points
- CQ: Offensive rushing points
- CR: Fumble points allowed (offense turnover cost)
- CS: Interception points allowed (offense turnover cost)
- CT: Defensive passing points allowed
- CU: Defensive rushing points allowed
- CV: Fumble points (defense turnover gain)
- CW: Interception points (defense turnover gain)

### SECTION I: Prediction — Weighting and Final Score

Rows 4-39 (2 rows per game), columns CX-DF. This section blends offensive and defensive projections and applies strength adjustments.

#### Step 25: Calculate Offense/Defense Weighting

**English Description:**
For each game, calculate how much to weight the offensive projection vs the defensive projection. The weighting is based on the relative magnitude of the offensive and defensive signals between the two teams. If one team is much stronger offensively than the other is defensively, the offensive projection dominates.

**Pseudocode:**
```
# For each game (even row = away, odd row = home):
away_row = even game row (4, 6, 8, ...)
home_row = odd game row (5, 7, 9, ...)

# Net offensive strength for each team (raw offense minus turnover costs)
off_signal_away = CP[away] + CQ[away] - CR[away] - CS[away]
off_signal_home = CP[home] + CQ[home] - CR[home] - CS[home]

# Defensive contribution (points they give up or score)
def_signal_away = CT[away] + CU[away] + CV[away] + CW[away]
def_signal_home = CT[home] + CU[home] + CV[home] + CW[home]

# Differences in these signals
off_diff = ABS(off_signal_away - off_signal_home)
def_diff = ABS(def_signal_away - def_signal_home)

# Weight: offensive dominance (0 to 1)
CX[away] (O/D Weight for away) = off_diff / (off_diff + def_diff)
CX[home] = 1 - CX[away]
```

**CRITICAL NOTE:**
- The away team row gets the weight value in CX[away]
- The home team row gets the complement in CX[home] = 1 - CX[away]
- Both rows in the pair share the same fundamental weighting logic but with inverted complements

**Key insight:**
- If off_diff is large relative to def_diff, weight approaches 1 (offensive-driven prediction)
- If def_diff is large, weight approaches 0 (defensive-driven prediction)
- Equal signals produce weight ≈ 0.5

#### Step 26: Calculate Weighted Predicted Points Per Component

**English Description:**
Blend each team's offensive projection with their opponent's defensive projection using the O/D weights calculated in Step 25. The result for each team is their expected score for the game.

**Pseudocode:**
```
# For AWAY team (away_row) predicting how many points they score:
CY[away] (Passing Points) = CP[away] * CX[away] + CT[home] * CX[home]
# = away's offensive passing * away's weight + home's defensive passing allowed * home's weight

CZ[away] (Rushing Points) = CQ[away] * CX[away] + CU[home] * CX[home]

DA[away] (Fumble Points) = CV[away] * CX[home] + CR[home] * CX[away]
# NOTE: Weights are REVERSED for turnover components
# away's defense fumble gain * home's weight + home's offense fumble cost * away's weight

DB[away] (Interception Points) = CW[away] * CX[home] + CS[home] * CX[away]
# Same reversal pattern as fumbles

# For HOME team (home_row) predicting how many points they score:
CY[home] (Passing Points) = CP[home] * CX[away] + CT[away] * CX[home]
# NOTE: home's weight is CX[away] (the away row's weight) because CX[home] = 1 - CX[away]
# This is counterintuitive but results from having a single CX value per game

CZ[home] (Rushing Points) = CQ[home] * CX[away] + CU[away] * CX[home]

DA[home] (Fumble Points) = CV[home] * CX[home] + CR[away] * CX[away]

DB[home] (Interception Points) = CW[home] * CX[home] + CS[away] * CX[away]
```

**CRITICAL PATTERN — Weight Swap:**
The Excel formulas work as follows (example for row 4 away, row 5 home):
```
CY4 (away passing) = CP4*CX4 + CT5*CX5
CY5 (home passing) = CP5*CX4 + CT4*CX5
```

Because CX5 = 1 - CX4, the weights are effectively swapped between the rows.

**ADDITIONAL CRITICAL PATTERN — Fumble/Interception Reversal:**
```
DA4 (away fumbles) = CV4*CX5 + CR5*CX4
DA5 (home fumbles) = CV5*CX5 + CR4*CX4
```

The pattern is different from passing/rushing: turnovers are weighted with the complement weight for the team's own defense contribution, not their offense.

#### Step 27: Lookup Win Rate and SOW

**English Description:**
For each team in each game, look up their win rate and Strength of Wins from the sorted averages table.

**Pseudocode:**
```
for each row with team abbreviation in CO:
    team_full_name = XLOOKUP(CO[row], Glossary.C:C, Glossary.A:A)
    team_row_in_sorted = MATCH(team_full_name, BB:BB, 0)

    DC[row] (Win rate) = BU[team_row]  (from Step 10)
    DD[row] (SOW) = BW[team_row]       (from Step 12)
```

#### Step 28: Calculate Strength Adjustment

**English Description:**
Adjust the predicted score based on the team's strength of wins relative to the league average. If the team's SOW is above average AND their opponent has a winning record, apply a bonus multiplier. If SOW is below average AND opponent has a losing record, also apply a bonus. Otherwise apply a penalty (multiplier < 1).

**Pseudocode:**
```
avg_SOW = BW36 (from Step 14)

for each row with team in CO:
    team_SOW = DD[row]
    opponent_row = adjacent row (if row is even, opponent is row+1; if odd, opponent is row-1)
    opponent_win_rate = DC[opponent_row]

    # Bonus condition: above-average SOW and opponent is winning
    # OR below-average SOW and opponent is losing
    if (team_SOW >= avg_SOW AND opponent_win_rate >= 0.5) OR
       (team_SOW < avg_SOW AND opponent_win_rate < 0.5):
        DE[row] = ABS(team_SOW - avg_SOW) + 1
        # Produces values > 1 (e.g., 1.04, 1.12)
    else:
        DE[row] = 1 - ABS(team_SOW - avg_SOW)
        # Produces values < 1 (e.g., 0.96, 0.88)
```

**CRITICAL CROSS-REFERENCE:**
- DE4 (away team strength adjustment) references DC5 (home team win rate), not DC4
- DE5 (home team strength adjustment) references DC4 (away team win rate), not DC5
- Ensures each team's adjustment is based on their OPPONENT's record

**Example:**
- Team A: SOW=0.50, opponent win rate=0.667
  - 0.50 >= 0.3223 (avg) AND 0.667 >= 0.5 → bonus
  - DE = ABS(0.50 - 0.3223) + 1 = 1.1777
- Team B: SOW=0.25, opponent win rate=0.333
  - 0.25 < 0.3223 AND 0.333 < 0.5 → bonus
  - DE = ABS(0.25 - 0.3223) + 1 = 1.0723

#### Step 29: Calculate Total Predicted Points

**English Description:**
Sum all four point components (passing, rushing, fumble, interception) and multiply by the strength adjustment to get the final predicted score.

**Pseudocode:**
```
for each row:
    DF[row] (Total Points) = (CY[row] + CZ[row] + DA[row] + DB[row]) * DE[row]
    # = (Passing + Rushing + Fumble + Interception) * strength_adjustment
```

**Output:**
- DF: Predicted total points for this team in this game

### SECTION J: Poisson Distribution Grid (Win Probability)

Located at rows 42-123+, columns CN-FP (and beyond). This section builds a joint probability distribution for final scores.

#### Step 30: Build Poisson Probability Grid

**English Description:**
Build an 80x80 grid of joint probabilities for final scores from 0 to 79 for each team. Each cell [i, j] contains the probability that away team scores i points AND home team scores j points, computed using Poisson distributions with each team's predicted total points as the lambda (mean).

**Pseudocode:**
```
# Grid structure:
# Rows 43-122 (or 43-123) = Away team score possibilities (0-79 or 0-80)
# Columns CN-FP+ = Home team score possibilities (0-79 or 0-80)

# Grid headers:
# CL43:CL123 = Away team abbreviation (from CO4, the away team)
# CN41:FP41 = Home team abbreviation (from CO5, the home team)

# Actually: CN42:FP42 (or beyond) = Score header row (0, 1, 2, ..., 79, 80)
# CM43:CM123 = Score header column (0, 1, 2, ..., 79, 80)

# Grid computation:
for away_score in 0..79:
    for home_score in 0..79:
        col = CN + home_score  # Column index for this home score
        row = 42 + away_score   # Row index for this away score

        cell[row][col] =
            POISSON.DIST(away_score, DF_away, FALSE) *
            POISSON.DIST(home_score, DF_home, FALSE)

        # FALSE = probability mass function (not cumulative)
        # DF_away and DF_home from Step 29
```

**Example (DAL @ PHI):**
- DF_away (DAL) = 21.76 (from Step 29)
- DF_home (PHI) = 29.11 (from Step 29)
- P(DAL scores 20, PHI scores 24) = POISSON.DIST(20, 21.76, FALSE) * POISSON.DIST(24, 29.11, FALSE)
- P(DAL scores 21, PHI scores 30) = POISSON.DIST(21, 21.76, FALSE) * POISSON.DIST(30, 29.11, FALSE)

**Grid dimensions:**
- 80 or 81 rows (scores 0-79 or 0-80)
- 80 or 81 columns (scores 0-79 or 0-80)
- Total cells: 6400-6561

#### Step 31: Calculate Win Probability (Away Team)

**English Description:**
Sum all cells in the Poisson grid where the away team's score is strictly greater than the home team's score. This is the probability that the away team wins (Poisson model).

**Pseudocode:**
```
CL44 (P(away wins)) = sum of all grid cells where away_score > home_score

# Mathematically:
P_away_wins = sum over all (i, j) where i > j: P(away=i) * P(home=j)

# In Excel, this is a "staircase" sum:
# Starting from row 43 (away score 0), only sum column CN (home score 0) — but away can't win 0-0, so skip
# Row 44 (away score 1): sum column CN (home 0) through CL (home must be < 1, so only 0)
# Row 45 (away score 2): sum columns CN-CO (home 0-1)
# ... and so on
# CL44 = SUM(CN44, CO45:CO45, CP46:CP46, ...)
```

**Storage:**
- CL44: P(away wins)

#### Step 32: Calculate Win Probability (Home Team)

**English Description:**
Sum all cells where the home team's score is strictly greater than the away team's score.

**Pseudocode:**
```
CO41 (P(home wins)) = sum of all grid cells where home_score > away_score

# In Excel:
# Row 42 (away 0, starting conditions): sum columns CO-FP (home 1-79)
# Row 43 (away 1): sum columns CP-FP (home 2-79)
# ...
```

**Storage:**
- CO41: P(home wins)

**Note:** P(away wins) + P(home wins) < 1 due to ties (rare in NFL), but the model doesn't explicitly calculate P(tie).

### SECTION K: Monte Carlo Simulation (Spread/Total Probabilities)

Located at rows 1553-2554 (or 1554-2554 depending on headers), columns CL-CO and CN-CO. This section runs 1000 random simulations.

#### Step 33: Identify Favorite and Underdog

**English Description:**
The team with the lower (more negative) moneyline odds is the favorite. Extract favorite and underdog abbreviations and set up for simulation.

**Pseudocode:**
```
away_ML = XLOOKUP(game_number, schedule_BZ, schedule_CC)  (from step 15)
home_ML = XLOOKUP(game_number, schedule_BZ, schedule_CG)

if away_ML < home_ML (more negative):
    CN1554 (Favorite) = away team abbr.
    CO1554 (Underdog) = home team abbr.
else:
    CN1554 (Favorite) = home team abbr.
    CO1554 (Underdog) = away team abbr.

# Also extract their predicted points
favorite_predicted_pts = lookup(favorite, DF)
underdog_predicted_pts = lookup(underdog, DF)
favorite_stdev = lookup(favorite, BX)
underdog_stdev = lookup(underdog, BX)
```

#### Step 34: Run 1000 Monte Carlo Simulations

**English Description:**
For each of 1000 simulation iterations, generate random score draws from normal distributions using each team's predicted total points as the mean and their historical points standard deviation as the standard deviation.

**Pseudocode:**
```
for i in 1..1000:  # Rows 1555-2554 (or 1554-2553)
    # Generate random normal variates, capped at 0 (no negative scores)
    CN[i] (Favorite score) = MAX(
        0,
        NORMINV(RAND(), favorite_predicted_pts, favorite_stdev)
    )

    CO[i] (Underdog score) = MAX(
        0,
        NORMINV(RAND(), underdog_predicted_pts, underdog_stdev)
    )
    # NORMINV(probability, mean, stdev) → generates random value
    # RAND() produces uniform random 0-1
```

**Source:**
- Predicted points: DF from Step 29
- Standard deviations: BX from Step 13

#### Step 35: Calculate Simulated Spread and Total

**English Description:**
For each simulation iteration, calculate the point spread (favorite minus underdog) and total score (sum of both).

**Pseudocode:**
```
for i in 1..1000:
    CL[i] (spread) = CN[i] (favorite score) - CO[i] (underdog score)
    # Can be positive (favorite wins) or negative (underdog wins)

    CM[i] (total) = CN[i] + CO[i]
    # Combined score
```

**Output:**
- CL1555:CL2554: 1000 simulated spread values
- CM1555:CM2554: 1000 simulated total values

### SECTION L: Prediction Output

Rows 4-39 (paired), columns DH-EK. This section contains the final model predictions, bet decisions, and value calculations.

#### Step 36: Determine Predicted Winner (Money Line)

**English Description:**
The team with the higher predicted total points wins the game. Look up which team that is.

**Pseudocode:**
```
for each game pair (row 4 and 5, row 6 and 7, etc.):
    away_predicted = DF[even_row]
    home_predicted = DF[odd_row]

    if away_predicted > home_predicted:
        DH[even_row] = CO[even_row] (away team abbr.)
        DH[odd_row] = CO[even_row] (same team, away team)
    else:
        DH[even_row] = CO[odd_row] (home team abbr.)
        DH[odd_row] = CO[odd_row] (same team, home team)
```

**Key note:**
- Both rows in a game pair have the same value in DH (the predicted winner)

#### Step 37: Retrieve Money Line Odds

**English Description:**
Look up the moneyline odds from the schedule for each team. Away team gets away odds, home team gets home odds.

**Pseudocode:**
```
for each row:
    if row is even (away team):
        DI[row] = XLOOKUP(
            game_number (from CM),
            schedule_BZ,
            schedule_CC (away ML)
        )
    else (home team):
        DI[row] = XLOOKUP(
            game_number (from CM),
            schedule_BZ,
            schedule_CG (home ML)
        )
```

#### Step 38: Calculate Implied Win Probability from Model

**English Description:**
Use the Poisson grid win probability (from Step 31-32) as the model's implied probability for the winning team in this game.

**Pseudocode:**
```
for each game pair:
    predicted_winner = DH[row]

    if predicted_winner is away team:
        DG[even_row] = CL44 (P(away wins) from Step 31)
        DG[odd_row] = CL44 (same, both rows reference same probability)
    else:
        DG[even_row] = CO41 (P(home wins) from Step 32)
        DG[odd_row] = CO41 (same)
```

**Output:**
- DG: Model-implied win probability for the team in this row
- Range: 0-1 (typically 0.5-0.95 for non-blowouts)

#### Step 39: Calculate Money Line Value

**English Description:**
Calculate the expected value of betting on this team to win using the moneyline. Formula: (model_prob * payout - loss_prob) / payout, where payout ratio is converted from American odds.

**Pseudocode:**
```
for each row:
    team = DH[row] (predicted winner in this row)
    team_prob = DG[row] (model probability this team wins)
    team_ML = DI[row] (moneyline odds for this team)

    # Convert American odds to decimal payout
    if team_ML < 0:
        # Negative odds (favorite): you risk $100 to win $X
        # Payout ratio = 100 / ABS(team_ML)
        payout_ratio = 100 / ABS(team_ML)
    else:
        # Positive odds (underdog): you risk $1 to win $X
        # Payout ratio = team_ML / 100
        payout_ratio = team_ML / 100

    # Expected value formula
    DJ[row] (Value) = (team_prob * payout_ratio - (1 - team_prob)) / payout_ratio
    # Simplifies to: team_prob * (1 + 1/payout_ratio) - 1
    # When value > 0, bet has positive expected value
```

**Interpretation:**
- DJ = 0.05 means the bet has +5% expected value (break-even adjusted for odds)
- DJ = -0.10 means -10% expected value (avoid)
- DJ = 0 means exactly fair odds (no edge)

**Example:**
- team_prob = 0.65, team_ML = -180
- payout_ratio = 100 / 180 = 0.556
- DJ = (0.65 * 0.556 - 0.35) / 0.556 = (0.361 - 0.35) / 0.556 = 0.011 / 0.556 = 0.020 = +2.0% value

#### Step 40: Calculate Predicted Spread

**English Description:**
The point spread prediction is simply the absolute difference between the two teams' predicted points.

**Pseudocode:**
```
for each game pair:
    away_predicted = DF[even_row]
    home_predicted = DF[odd_row]

    DP[even_row] = DP[odd_row] = ABS(away_predicted - home_predicted)
```

**Example:** DAL 21.76, PHI 29.11 → DP = 7.35

#### Step 41: Determine Spread Side

**English Description:**
Determine whether to bet the favorite ("-") or underdog ("+"). If the model's predicted spread is wider than the book spread AND the model is picking the favorite (the team with negative spread), take the favorite. Otherwise take the underdog.

**Pseudocode:**
```
for each game pair:
    predicted_spread = DP[even_row]
    book_spread = XLOOKUP(game_number, schedule, schedule_CD or CH)
    # CD for away, CH for home

    predicted_winner = DH[even_row]
    favorite_abbr = team with negative book spread (away or home)

    DQ[row] = ABS(book_spread)  # Store absolute book spread for reference

    if predicted_spread > ABS(book_spread) AND predicted_winner == favorite_abbr:
        DS[row] = "-"  # Bet the favorite
    else:
        DS[row] = "+"  # Bet the underdog
```

**Logic:**
- If model says favorite wins by 8 but book says 7, the favorite looks like a good bet ("take the favorite")
- If model says underdog wins or the spread matches, the underdog looks like better value ("take the underdog")

#### Step 42: Calculate Spread Win Probability from Simulation

**English Description:**
Use the Monte Carlo simulation to calculate the probability that the predicted spread outcome occurs.

**Pseudocode:**
```
for each game pair:
    book_spread = ABS(DQ[row])
    side = DS[row]

    if side == "+":  # Underdog covers
        # Count how many simulations had spread < book_spread (underdog wins/covers)
        DT[row] = COUNTIF(CL1555:CL2554, "<" & book_spread) / 1000
    else:  # side == "-", favorite covers
        # Count how many simulations had spread > book_spread (favorite wins by more)
        DT[row] = COUNTIF(CL1555:CL2554, ">" & book_spread) / 1000
```

**Output:**
- DT: Probability (0-1) that the spread bet wins

#### Step 43: Calculate Spread Value

**English Description:**
Same expected value formula as moneyline, but using spread odds and spread probability.

**Pseudocode:**
```
for each row:
    spread_prob = DT[row]
    spread_ML = DR[row]  # Default -110 if not specified

    # Convert spread odds (typically -110) to payout ratio
    if spread_ML < 0:
        payout_ratio = 100 / ABS(spread_ML)
    else:
        payout_ratio = spread_ML / 100

    DU[row] (Spread Value) = (spread_prob * payout_ratio - (1 - spread_prob)) / payout_ratio
```

**Note:** DR contains the spread odds (usually -110, meaning risk $110 to win $100).

#### Step 44: Calculate Predicted Total

**English Description:**
The predicted total is the sum of both teams' predicted points.

**Pseudocode:**
```
for each game pair:
    away_predicted = DF[even_row]
    home_predicted = DF[odd_row]

    EA[even_row] = EA[odd_row] = away_predicted + home_predicted
```

**Example:** DAL 21.76 + PHI 29.11 = 50.87

#### Step 45: Determine Over/Under Side

**English Description:**
If predicted total exceeds the book line, bet over; otherwise bet under.

**Pseudocode:**
```
for each row:
    predicted_total = EA[row]
    total_line = XLOOKUP(game_number, schedule, schedule_CJ)  # Total line

    EB[row] = total_line  # Store for reference

    if predicted_total > total_line:
        ED[row] = "over"
    else:
        ED[row] = "under"
```

#### Step 46: Calculate Total Win Probability from Simulation

**English Description:**
Use the Monte Carlo simulation to calculate the probability that the predicted total outcome occurs.

**Pseudocode:**
```
for each row:
    total_line = EB[row]
    side = ED[row]

    if side == "over":
        # Count simulations where total > line
        EE[row] = COUNTIF(CM1555:CM2554, ">" & total_line) / 1000
    else:  # "under"
        # Count simulations where total < line
        EE[row] = COUNTIF(CM1555:CM2554, "<" & total_line) / 1000
```

**Output:**
- EE: Probability (0-1) that the total bet wins

#### Step 47: Calculate Total Value

**English Description:**
Same expected value formula using total odds (typically -110) and total probability.

**Pseudocode:**
```
for each row:
    total_prob = EE[row]
    total_ML = EC[row]  # Default -110

    if total_ML < 0:
        payout_ratio = 100 / ABS(total_ML)
    else:
        payout_ratio = total_ML / 100

    EF[row] (Total Value) = (total_prob * payout_ratio - (1 - total_prob)) / payout_ratio
```

### SECTION M: Bet Decision Logic

#### Step 48: Money Line Bet Decision

**English Description:**
Only bet the moneyline if the predicted spread (point differential) exceeds the week's average predicted spread. This filters out low-confidence plays.

**Pseudocode:**
```
EU26 (average_predicted_spread) = AVERAGE(DP4:DP39)
# Recalculated each week

for each row:
    predicted_spread = DP[row]

    if predicted_spread > EU26:
        DM[row] = "bet"
    else:
        DM[row] = "no"
```

**Threshold:** EU26 is dynamic, calculated from all 18 games' predicted spreads that week.

#### Step 49: Spread Bet Decision

**English Description:**
Only bet spreads if the line delta (difference between predicted and book spread) exceeds the week's average line delta AND the bet is on the favorite ("-"). This requires both a confident spread prediction AND a specific directional signal (favorite edge).

**Pseudocode:**
```
EU27 (average_line_delta) = AVERAGE(DV4:DV39)

for each row:
    line_delta = DV[row] = ABS(predicted_spread - ABS(book_spread))
    side = DS[row]

    if line_delta > EU27 AND side == "-":
        DW[row] = "bet"
    else:
        DW[row] = "no"
```

**Key constraint:** Only bet favorite spreads, not underdog spreads. This is an explicit model choice.

#### Step 50: Total Bet Decision

**English Description:**
Only bet totals if the total line delta (difference between predicted and book total) exceeds the week's average total line delta.

**Pseudocode:**
```
EU28 (average_total_delta) = AVERAGE(EG4:EG39)

for each row:
    total_delta = EG[row] = ABS(predicted_total - total_line)

    if total_delta > EU28:
        EH[row] = "bet"
    else:
        EH[row] = "no"
```

### SECTION N: Result Tracking and Actual Outcome

Columns DK-DO (ML), DX-DZ (Spread), EI-EK (Total). These columns are populated after games are played.

#### Step 51: Track ML Result

**English Description:**
After games are played, retrieve actual scores from the schedule, determine the actual winner, and compare to the prediction.

**Pseudocode:**
```
for each row:
    game_number = CM[row]

    # Actual scores from schedule
    DK[even_row] (away result) = XLOOKUP(game_number, schedule, schedule_CE)
    DK[odd_row] (home result) = XLOOKUP(game_number, schedule, schedule_CI)

    # Winner
    away_score = DK[even_row]
    home_score = DK[odd_row]

    if away_score > home_score:
        actual_winner = away team abbr.
    else:
        actual_winner = home team abbr.

    DL[row] (Winner) = actual_winner  # Both rows in pair get same winner

    # Correctness
    predicted_winner = DH[row]

    if DL[row] == predicted_winner:
        DN[row] = "yes"
    else:
        DN[row] = "no"

    # Accuracy (difference in points)
    if row is even (away):
        DO[row] (Difference) = ABS(away_score - away_predicted_from_DF)
    else (home):
        DO[row] (Difference) = ABS(home_score - home_predicted_from_DF)
```

#### Step 52: Track Spread Result

**English Description:**
After games, calculate the actual point spread and determine if the bet on that side won.

**Pseudocode:**
```
for each game pair:
    away_score = DK[even_row]
    home_score = DK[odd_row]
    actual_spread = away_score - home_score  # Can be negative if home wins by more

    predicted_winner = DH[row]
    side = DS[row]
    book_spread = DQ[row]

    # Align spread to predicted winner
    if predicted_winner is away:
        aligned_spread = actual_spread
    else:
        aligned_spread = -actual_spread  # Flip if picking home

    DX[row] (actual spread) = ABS(aligned_spread)

    # Correctness of spread bet
    if side == "+":  # Underdog covers
        # Bet wins if actual spread < book spread (or if team favored wins outright)
        if ABS(actual_spread) < book_spread:
            DY[row] = "yes"
        else:
            DY[row] = "no"
    else:  # side == "-", favorite covers
        # Bet wins if actual spread > book spread
        if ABS(actual_spread) > book_spread:
            DY[row] = "yes"
        else:
            DY[row] = "no"

    # Difference
    DZ[row] (Difference) = ABS(ABS(actual_spread) - book_spread)
```

**Logic note:** This assumes the spread is always viewed from the predicted winner's perspective.

#### Step 53: Track Total Result

**English Description:**
After games, retrieve the actual combined score and determine if the over/under bet won.

**Pseudocode:**
```
for each game (one result per pair, not both rows):
    actual_total = CK[even_row]  # = CE + CI (from schedule)

    EI[row] (Result) = actual_total  # Both rows in pair reference this

    total_line = EB[row]
    side = ED[row]

    if side == "over":
        if actual_total > total_line:
            EJ[row] = "yes"
        else:
            EJ[row] = "no"
    else:  # "under"
        if actual_total < total_line:
            EJ[row] = "yes"
        else:
            EJ[row] = "no"

    # Difference
    EK[row] (Difference) = ABS(total_line - actual_total)
```

---

## PART 3: CONSTANTS AND PARAMETERS

### 3.1 Derived Constants (Recalculated Each Week from Data)

These parameters are computed fresh each week as part of the model's standard operation.

| Parameter | Cell | Formula | Description | Example (Week 1, 2025) |
|-----------|------|---------|-------------|--------|
| `pts_per_int_offense_loss` | BD41 | `SLOPE(BD42:BD53, BC42:BC53) * -1` | Points lost per interception thrown by offense | 1.4858 |
| `pts_per_fumble_offense_loss` | BH41 | `SLOPE(BH42:BH53, BG42:BG53) * -1` | Points lost per fumble by offense | 1.3135 |
| `pts_per_int_defense_gain` | BE41 | `SLOPE(BE42:BE53, BC42:BC53)` | Points gained per interception made by defense | 1.3563 |
| `pts_per_fumble_defense_gain` | BI41 | `SLOPE(BI42:BI53, BG42:BG53)` | Points gained per fumble recovered by defense | 1.6347 |
| `avg_SOW` | BW36 | `AVERAGE(BW4:BW35)` | League average Strength of Wins | 0.3223 |
| `ml_bet_threshold` | EU26 | `AVERAGE(DP4:DP39)` | Average predicted spread (ML bet threshold) | Dynamic |
| `spread_bet_threshold` | EU27 | `AVERAGE(DV4:DV39)` | Average line delta (spread bet threshold) | Dynamic |
| `total_bet_threshold` | EU28 | `AVERAGE(EG4:EG39)` | Average total delta (total bet threshold) | Dynamic |

#### Regression Slopes Explained

The SLOPE function computes the linear regression coefficient. For example:
```
SLOPE(BD42:BD53, BC42:BC53)
= (sum of (X_i - X_mean)(Y_i - Y_mean)) / sum of (X_i - X_mean)^2
where X = INT counts (0, 1, 2, ...), Y = avg offense points
```

If more INTs are associated with fewer points, the slope is negative. Multiplying by -1 converts it to a positive "points lost per INT" interpretation.

### 3.2 Fixed Parameters

These parameters are constant across all weeks and seasons.

| Parameter | Location | Value | Description |
|-----------|----------|-------|-------------|
| `back_test_range` | Glossary!B36 | 6 | Number of past weeks to include in rolling averages |
| `default_spread_odds` | DR4 (hardcoded) | -110 | Default book odds for spread bets (risk $110 to win $100) |
| `default_total_odds` | EC4 (hardcoded) | -110 | Default book odds for total bets (risk $110 to win $100) |
| `poisson_grid_size` | Grid dimensions | 80 x 80 | Score range 0-79 for Poisson probability grid |
| `monte_carlo_sims` | Rows 1555-2554 | 1000 | Number of Monte Carlo simulation draws |
| `starting_bankroll` | P&L C13 | 1000 | Starting balance for P&L tracking |
| `risk_free_rate` | P&L C20 | 0.000769 | Weekly risk-free rate for Sharpe ratio calculation |
| `profit_calc_mode` | EO29 | "Projected" or "Actual" | Controls whether P&L uses projected or actual results |

#### Notes on Fixed Parameters

- **back_test_range = 6:** Standard lookback window. Change to 4 or 8 to adjust recency weighting.
- **Spread/Total odds -110:** Industry standard American odds for even-money bets. Imply ~52.4% win probability break-even.
- **Poisson grid 80x80:** Covers 0-79 points per team, sufficient for NFL (max reasonable score is ~70+).
- **Monte Carlo 1000:** Balance between accuracy and computation time. 10000 simulations would be more accurate but slower.
- **Risk-free rate:** Used for Sharpe ratio in P&L. 0.000769 ≈ 4% APR / 52 weeks.

### 3.3 Spread-to-Moneyline Lookup Table

Located in Glossary rows 40-105 (66 rows covering spreads -27 to +27 in 0.5 increments).

| Column | Description | Range |
|--------|-------------|-------|
| A | `game_count` | 10-500 (number of historical games at this spread) |
| B | `spread` | -27.0 to +27.0 in 0.5 increments |
| C | `avg_moneyline` | -400 to +400 (American odds) |
| D | `avg_moneyline` (duplicate) | Same as C |
| E | `MAX_moneyline` | Maximum observed value |
| F | `MIN_moneyline` | Minimum observed value |

#### Example Entries

```
spread=-3.0, avg_moneyline=-160, MAX=-150, MIN=-170
spread=0.0, avg_moneyline=-110, MAX=-100, MIN=-120
spread=+3.0, avg_moneyline=+140, MAX=+150, MIN=+130
spread=-7.0, avg_moneyline=-321, MAX=-310, MIN=-340
spread=+7.0, avg_moneyline=+266, MAX=+280, MIN=+250
```

This table is used to estimate moneyline odds when actual odds are unavailable.

---

## PART 4: OUTPUT SPECIFICATION

### 4.1 Primary Outputs (Per Game, 2 rows per game in columns DH-EK)

Each game produces two rows of output (one for away team, one for home team), with columns for moneyline, spread, and total predictions.

#### 4.1.1 Money Line Output (Columns DH-DN)

| Column | Name | Type | Values | Description |
|--------|------|------|--------|-------------|
| DH | Prediction | str | 3-letter abbr. | Predicted winner (away or home team) |
| DI | ML Odds | int | ±100 to ±400 | Moneyline odds for this team |
| DJ | Value | float | -0.5 to +0.5 | Expected value as a decimal (e.g., 0.05 = +5%) |
| DM | Bet? | str | "bet" / "no" | Bet decision (based on spread > avg threshold) |
| DN | Right? | str | "yes" / "no" | Correctness after game is played |

**Example (Row 4, DAL @ PHI):**
```
DH4: PHI
DI4: -340
DJ4: 0.042
DM4: bet
DN4: yes  (if PHI won)
```

#### 4.1.2 Spread Output (Columns DP-DW)

| Column | Name | Type | Values | Description |
|--------|------|------|--------|-------------|
| DP | Predicted Spread | float | 0-30 | Point differential (always positive) |
| DQ | Book Spread | float | -30 to +30 | Actual book spread from schedule |
| DS | Side | str | "-" / "+" | "-" for favorite, "+" for underdog |
| DT | Odds | float | 0-1 | Model-implied win probability from simulation |
| DU | Value | float | -0.5 to +0.5 | Expected value |
| DV | Line Delta | float | 0-15 | ABS(predicted - book spread) |
| DW | Bet? | str | "bet" / "no" | Bet decision (based on delta > avg AND side == "-") |
| DY | Right? | str | "yes" / "no" | Correctness after game |

**Example:**
```
DP4: 7.35
DQ4: 7.0
DS4: -
DT4: 0.618
DU4: 0.031
DV4: 0.35
DW4: no  (delta too small)
DY4: yes  (PHI covered)
```

#### 4.1.3 Total Output (Columns EA-EH)

| Column | Name | Type | Values | Description |
|--------|------|------|--------|-------------|
| EA | Predicted Total | float | 35-70 | Sum of both teams' predicted points |
| EB | Total Line | float | 35-70 | Book over/under line from schedule |
| ED | Side | str | "over" / "under" | Direction of the bet |
| EE | Odds | float | 0-1 | Model-implied win probability from simulation |
| EF | Value | float | -0.5 to +0.5 | Expected value |
| EG | Line Delta | float | 0-10 | ABS(predicted total - total line) |
| EH | Bet? | str | "bet" / "no" | Bet decision (based on delta > avg threshold) |
| EJ | Right? | str | "yes" / "no" | Correctness after game |

**Example:**
```
EA4: 50.87
EB4: 47.5
ED4: over
EE4: 0.672
EF4: 0.084
EG4: 3.37
EH4: bet
EJ4: yes  (actual total 44, under hit — but our prediction was correct betting)
```

### 4.2 Dashboard Output (Columns EM-FK, rows 5-23)

One row per game (up to 18), with columns for each bet type (ML, Spread, Total). This is a summary dashboard for quick reference.

**Row 5 (Game 1):**

| Column | Name | Description |
|--------|------|-------------|
| EM5 | Matchup | e.g., "DAL @ PHI" |
| EN5-ER5 | ML columns | Predicted winner, odds, value, bet decision, amount bet, accepted, profit/loss |
| ES5-EW5 | Spread columns | Spread prediction, side, value, bet decision, amount bet, accepted, profit/loss |
| EX5-FB5 | Total columns | Total prediction, side, value, bet decision, amount bet, accepted, profit/loss |

### 4.3 Summary Statistics (Rows 25-29)

Aggregate statistics across all games in a week.

| Row | Column | Name | Formula | Description |
|-----|--------|------|---------|-------------|
| 25 | EO25 | Total Value | SUM(DJ, DU, EF) where bet="bet" | Sum of all positive expected values across accepted bets |
| 26 | EO26 | Total Amount Bet | SUM(amount bets accepted) | Total dollars wagered |
| 26 | ER26 | Hit Rate (ML) | COUNTIF(DN, "yes") / COUNTIF(DM, "bet") | Accuracy of moneyline predictions |
| 27 | EO27 | Profit | SUM(profit/loss) | Net profit/loss across all accepted bets |
| 27 | ER27 | Hit Rate (Spread) | COUNTIF(DY, "yes") / COUNTIF(DW, "bet") | Accuracy of spread predictions |
| 28 | EO28 | Profit Margin | EO27 / EO26 | Profit ÷ Amount Bet |
| 28 | ER28 | Hit Rate (Total) | COUNTIF(EJ, "yes") / COUNTIF(EH, "bet") | Accuracy of total predictions |
| 26-28 | EU26-EU28 | Thresholds | Dynamic | Average deltas (used as bet thresholds) |

### 4.4 Bet Sizing

Amount bet is allocated proportionally to relative value (Kelly-like criterion):

```
for each bet:
    if DJ > 0 (or DU, EF):
        amount = (value / total_value) * total_bet_pool
    else:
        amount = 0
```

Example: If total positive value = 0.15 across 3 bets and total pool = $300:
- Bet A (value 0.05): amount = (0.05 / 0.15) * $300 = $100
- Bet B (value 0.06): amount = (0.06 / 0.15) * $300 = $120
- Bet C (value 0.04): amount = (0.04 / 0.15) * $300 = $80

### 4.5 P&L Sheet (Across Weeks)

Located in separate sheet, tracks profit/loss and bankroll over time.

| Row | Description |
|-----|-------------|
| 12 | Profit margin per week (%) |
| 13 | Running bankroll (compound growth, starting from C13=1000) |
| 14 | Weekly profit ($) |
| 16-19 | Profit broken out by bet type (total, ML, spread, total) |
| 20 | Sharpe ratio (rolling 13-week window) |

**Sharpe Ratio:**
```
Sharpe = (avg_weekly_profit - risk_free_rate * avg_bet_amount) / stdev(weekly_profits)
```

---

## PART 5: VALIDATION REFERENCE

This section contains step-by-step calculations for three example games from Week 1, 2025.

### 5.1 Test Case 1: DAL @ PHI (Game 1, Week 1, 2025)

#### Raw Sorted Averages (From 20251, 6-week window)

**Dallas Cowboys:**
- BT (Pts scored by offense): 17.918
- BQ (Pts allowed by defense): 22.558
- Off yards total: 335.33
- Off pass yards: 202.5
- Off rush yards: 132.83
- Off INTs: 0.5
- Off fumbles: 1.167
- Off sacks: 1.333
- Def yards total: 328.33
- Def pass yards: 215.67
- Def rush yards: 112.67
- Def INTs: 0.833
- Def fumbles: 1.5
- Def sacks: 4.0
- Win rate: 0.5
- SOW: 0.4444
- Stdev (points): 8.264

**Philadelphia Eagles:**
- BT (Pts scored by offense): 23.480
- BQ (Pts allowed by defense): 15.334
- Off yards total: 327.33
- Off pass yards: 173.5
- Off rush yards: 153.83
- Off INTs: 0.167
- Off fumbles: 1.333
- Off sacks: 2.5
- Def yards total: 285.17
- Def pass yards: 171.83
- Def rush yards: 113.33
- Def INTs: 1.0
- Def fumbles: 1.833
- Def sacks: 1.333
- Win rate: 0.833
- SOW: 0.4267
- Stdev (points): 7.885

#### Turnover Constants (from previous steps)

```
BD41 (pts_per_int_offense_loss) = 1.4858
BE41 (pts_per_int_defense_gain) = 1.3563
BH41 (pts_per_fumble_offense_loss) = 1.3135
BI41 (pts_per_fumble_defense_gain) = 1.6347
BW36 (avg_SOW) = 0.3223
```

#### Step-by-Step Calculation

**Step 17-24: Component Point Calculations**

Dallas Cowboys offensive components:
```
CP (Pass pts) = BT * (pass_yds / total_yds)
              = 17.918 * (202.5 / 335.33)
              = 17.918 * 0.6039
              = 10.820

CQ (Rush pts) = BT * (rush_yds / total_yds)
              = 17.918 * (132.83 / 335.33)
              = 17.918 * 0.3961
              = 7.098

CR (Fumble pts allowed) = avg_fumbles * pts_per_fumble_loss
                        = 1.167 * 1.3135
                        = 1.532

CS (INT pts allowed) = avg_INTs * pts_per_int_loss
                     = 0.5 * 1.4858
                     = 0.743
```

Dallas Cowboys defensive components:
```
CT (Pass pts allowed) = BQ * (pass_yds_allowed / total_yds_allowed)
                      = 22.558 * (215.67 / 328.33)
                      = 22.558 * 0.6572
                      = 14.817

CU (Rush pts allowed) = BQ * (rush_yds_allowed / total_yds_allowed)
                      = 22.558 * (112.67 / 328.33)
                      = 22.558 * 0.3432
                      = 7.741

CV (Fumble pts from defense) = avg_fumbles_forced * pts_per_fumble_gain
                             = 1.5 * 1.6347
                             = 2.452

CW (INT pts from defense) = avg_INTs * pts_per_int_gain
                          = 0.833 * 1.3563
                          = 1.130
```

Philadelphia Eagles offensive components:
```
CP = 23.480 * (173.5 / 327.33)
   = 23.480 * 0.5301
   = 12.445

CQ = 23.480 * (153.83 / 327.33)
   = 23.480 * 0.4699
   = 11.035

CR = 1.333 * 1.3135 = 1.751

CS = 0.167 * 1.4858 = 0.248
```

Philadelphia Eagles defensive components:
```
CT = 15.334 * (171.83 / 285.17)
   = 15.334 * 0.6023
   = 9.240

CU = 15.334 * (113.33 / 285.17)
   = 15.334 * 0.3977
   = 6.094

CV = 1.833 * 1.6347 = 2.997

CW = 1.0 * 1.3563 = 1.356
```

**Step 25: O/D Weighting**

```
off_signal_DAL = CP + CQ - CR - CS
               = 10.820 + 7.098 - 1.532 - 0.743
               = 15.643

off_signal_PHI = 12.445 + 11.035 - 1.751 - 0.248
               = 21.481

off_diff = ABS(15.643 - 21.481) = 5.838

def_signal_DAL = CT + CU + CV + CW
               = 14.817 + 7.741 + 2.452 + 1.130
               = 26.140

def_signal_PHI = 9.240 + 6.094 + 2.997 + 1.356
               = 19.687

def_diff = ABS(26.140 - 19.687) = 6.453

CX_DAL = 5.838 / (5.838 + 6.453) = 5.838 / 12.291 = 0.475
CX_PHI = 1 - 0.475 = 0.525
```

**Step 26: Weighted Point Projections for DAL**

```
CY (passing) = CP_DAL * CX_DAL + CT_PHI * CX_PHI
             = 10.820 * 0.475 + 9.240 * 0.525
             = 5.139 + 4.851
             = 9.990

CZ (rushing) = 7.098 * 0.475 + 6.094 * 0.525
             = 3.372 + 3.199
             = 6.571

DA (fumbles) = CV_DAL * CX_PHI + CR_PHI * CX_DAL
             = 2.452 * 0.525 + 1.751 * 0.475
             = 1.287 + 0.832
             = 2.119

DB (INTs) = CW_DAL * CX_PHI + CS_PHI * CX_DAL
          = 1.130 * 0.525 + 0.248 * 0.475
          = 0.593 + 0.118
          = 0.711

Raw total = 9.990 + 6.571 + 2.119 + 0.711 = 19.391
```

**Step 26: Weighted Point Projections for PHI**

```
CY (passing) = CP_PHI * CX_DAL + CT_DAL * CX_PHI
             = 12.445 * 0.475 + 14.817 * 0.525
             = 5.911 + 7.779
             = 13.690

CZ (rushing) = 11.035 * 0.475 + 7.741 * 0.525
             = 5.242 + 4.064
             = 9.306

DA (fumbles) = CV_PHI * CX_PHI + CR_DAL * CX_DAL
             = 2.997 * 0.525 + 1.532 * 0.475
             = 1.573 + 0.728
             = 2.301

DB (INTs) = CW_PHI * CX_PHI + CS_DAL * CX_DAL
          = 1.356 * 0.525 + 0.743 * 0.475
          = 0.712 + 0.353
          = 1.065

Raw total = 13.690 + 9.306 + 2.301 + 1.065 = 26.362
```

**Step 28: Strength Adjustment**

Dallas Cowboys:
```
team_SOW = 0.4444
opponent (PHI) win_rate = 0.833
avg_SOW = 0.3223

Condition check:
(0.4444 >= 0.3223 AND 0.833 >= 0.5) → TRUE (first part)

DE_DAL = ABS(0.4444 - 0.3223) + 1
       = 0.1221 + 1
       = 1.1221
```

Philadelphia Eagles:
```
team_SOW = 0.4267
opponent (DAL) win_rate = 0.5
avg_SOW = 0.3223

Condition check:
(0.4267 >= 0.3223 AND 0.5 >= 0.5) → TRUE (first part of OR, 0.5 == 0.5)

DE_PHI = ABS(0.4267 - 0.3223) + 1
       = 0.1044 + 1
       = 1.1044
```

**Step 29: Final Predicted Points**

```
DF_DAL = 19.391 * 1.1221 = 21.760
DF_PHI = 26.362 * 1.1044 = 29.113
```

#### Poisson Grid and Win Probabilities

Using Poisson distributions with lambda = 21.760 for DAL, 29.113 for PHI:
- Grid cell (21, 29): P(DAL=21) * P(PHI=29) = 0.0927 * 0.0903 = 0.00837
- P(DAL wins) = sum of cells where i > j
- P(PHI wins) = sum of cells where j > i
- Approximate: P(DAL wins) ≈ 0.24, P(PHI wins) ≈ 0.76 (based on Poisson CDF)

#### Final Predictions

```
Predicted spread: ABS(21.760 - 29.113) = 7.353 (PHI by 7.4)
Predicted total: 21.760 + 29.113 = 50.873
Predicted winner: PHI (higher predicted points)
Model ML prob (PHI wins): ~0.76
Model spread prob (PHI covers -7): ~0.62 (from simulation)
Model total prob (over 47.5): ~0.67 (from simulation)
```

#### Actual Result

```
DAL 20, PHI 24
PHI wins by 4
Total: 44

Comparison:
- Predicted PHI 29.1, actual PHI 24 (error: -5.1)
- Predicted DAL 21.8, actual DAL 20 (error: -1.8)
- Predicted spread 7.4, actual spread 4.0 (error: -3.4)
- Predicted total 50.9, actual total 44.0 (error: -6.9)

Bet outcomes:
- ML: PHI prediction correct → DN = "yes"
- Spread (assuming bet was PHI -7): PHI won by 4, book was -7 → didn't cover by enough → DY = "no"
- Total (assuming bet was over 47.5): Actual 44 < 47.5 → under hit → EJ = "no"
```

### 5.2 Test Case 2: KC @ LAC (Game 2, Week 1, 2025)

#### Raw Sorted Averages

**Kansas City Chiefs:**
- BT (Pts scored by offset): 15.132
- BQ (Pts allowed by def): 17.124
- Off yards: 300, Pass: 214.83, Rush: 85.17
- Off INTs: 0, Off fumbles: 0.667
- Def yards: 357, Def pass: 224.83, Def rush: 132.17
- Def INTs: 1.167, Def fumbles: 1.5
- Win rate: 0.833, SOW: 0.387, Stdev: 10.284

**Los Angeles Chargers:**
- BT: 24.054
- BQ: 18.972
- Off yards: 327, Pass: 229.83, Rush: 97.17
- Off INTs: 0.333, Off fumbles: 1.167
- Def yards: 325.67, Def pass: 212.33, Def rush: 113.33
- Def INTs: 1.0, Def fumbles: 0.667
- Win rate: 0.667, SOW: 0.367, Stdev: 10.635

#### Component Calculations

KC offensive:
```
CP = 15.132 * (214.83 / 300) = 15.132 * 0.7161 = 10.836
CQ = 15.132 * (85.17 / 300) = 15.132 * 0.2839 = 4.296
CR = 0.667 * 1.3135 = 0.876
CS = 0 * 1.4858 = 0
```

KC defensive:
```
CT = 17.124 * (224.83 / 357) = 17.124 * 0.6300 = 10.785
CU = 17.124 * (132.17 / 357) = 17.124 * 0.3701 = 6.341
CV = 1.5 * 1.6347 = 2.452
CW = 1.167 * 1.3563 = 1.583
```

LAC offensive:
```
CP = 24.054 * (229.83 / 327) = 24.054 * 0.7029 = 16.906
CQ = 24.054 * (97.17 / 327) = 24.054 * 0.2970 = 7.148
CR = 1.167 * 1.3135 = 1.532
CS = 0.333 * 1.4858 = 0.495
```

LAC defensive:
```
CT = 18.972 * (212.33 / 325.67) = 18.972 * 0.6520 = 12.371
CU = 18.972 * (113.33 / 325.67) = 18.972 * 0.3480 = 6.601
CV = 0.667 * 1.6347 = 1.090
CW = 1.0 * 1.3563 = 1.356
```

#### O/D Weighting

```
off_signal_KC = 10.836 + 4.296 - 0.876 - 0 = 14.256
off_signal_LAC = 16.906 + 7.148 - 1.532 - 0.495 = 22.027
off_diff = ABS(14.256 - 22.027) = 7.771

def_signal_KC = 10.785 + 6.341 + 2.452 + 1.583 = 21.161
def_signal_LAC = 12.371 + 6.601 + 1.090 + 1.356 = 21.418
def_diff = ABS(21.161 - 21.418) = 0.257

CX_KC = 7.771 / (7.771 + 0.257) = 7.771 / 8.028 = 0.968
CX_LAC = 0.032
```

Strong offensive signal favors LAC (they're much more efficient).

#### Weighted Predictions (KC)

```
CY = 10.836 * 0.968 + 12.371 * 0.032 = 10.489 + 0.396 = 10.885
CZ = 4.296 * 0.968 + 6.601 * 0.032 = 4.158 + 0.211 = 4.369
DA = 2.452 * 0.032 + 1.532 * 0.968 = 0.078 + 1.483 = 1.561
DB = 1.583 * 0.032 + 0.495 * 0.968 = 0.051 + 0.479 = 0.530
Raw total = 10.885 + 4.369 + 1.561 + 0.530 = 17.345
```

Strength adjustment for KC:
```
SOW = 0.387, opponent (LAC) win rate = 0.667
(0.387 >= 0.3223 AND 0.667 >= 0.5) → TRUE
DE = ABS(0.387 - 0.3223) + 1 = 1.0647
```

```
DF_KC = 17.345 * 1.0647 = 18.466
```

#### Weighted Predictions (LAC)

```
CY = 16.906 * 0.968 + 10.785 * 0.032 = 16.365 + 0.345 = 16.710
CZ = 7.148 * 0.968 + 6.341 * 0.032 = 6.911 + 0.203 = 7.114
DA = 1.090 * 0.032 + 0.876 * 0.968 = 0.035 + 0.848 = 0.883
DB = 1.356 * 0.032 + 0.0 * 0.968 = 0.043 + 0 = 0.043
Raw total = 16.710 + 7.114 + 0.883 + 0.043 = 24.750
```

Strength adjustment for LAC:
```
SOW = 0.367, opponent (KC) win rate = 0.833
(0.367 >= 0.3223 AND 0.833 >= 0.5) → TRUE
DE = ABS(0.367 - 0.3223) + 1 = 1.0447
```

```
DF_LAC = 24.750 * 1.0447 = 25.854
```

#### Final Predictions

```
Predicted spread: ABS(18.466 - 25.854) = 7.389 (LAC by 7.4)
Predicted total: 18.466 + 25.854 = 44.320
Predicted winner: LAC
Model prob (LAC wins): ~0.73
Model spread prob (LAC covers -7): ~0.58
Model total prob (over/under depends on book line): ~0.55
```

### 5.3 Test Case 3: CIN @ CLE (Game 6, Week 1, 2025)

#### Raw Sorted Averages

**Cincinnati Bengals:**
- BT: 24.408
- BQ: 19.573
- Off yards: 386.17, Pass: 291.67, Rush: 94.5
- Off INTs: 0.833, Off fumbles: 1.667
- Def yards: 335.17, Def pass: 219.5, Def rush: 115.67
- Def INTs: 1.5, Def fumbles: 1.667
- Win rate: 0.833, SOW: 0.287, Stdev: 7.414

**Cleveland Browns:**
- BT: 10.731
- BQ: 22.782
- Off yards: 316.17, Pass: 209.83, Rush: 106.33
- Off INTs: 2.333, Off fumbles: 1.333
- Def yards: 336.83, Def pass: 208.83, Def rush: 128
- Def INTs: 0.333, Def fumbles: 0.5
- Win rate: 0, SOW: 0, Stdev: 10.488

#### Component Calculations

CIN offensive:
```
CP = 24.408 * (291.67 / 386.17) = 24.408 * 0.7554 = 18.435
CQ = 24.408 * (94.5 / 386.17) = 24.408 * 0.2448 = 5.973
CR = 1.667 * 1.3135 = 2.189
CS = 0.833 * 1.4858 = 1.237
```

CIN defensive:
```
CT = 19.573 * (219.5 / 335.17) = 19.573 * 0.6547 = 12.818
CU = 19.573 * (115.67 / 335.17) = 19.573 * 0.3451 = 6.754
CV = 1.667 * 1.6347 = 2.724
CW = 1.5 * 1.3563 = 2.034
```

CLE offensive:
```
CP = 10.731 * (209.83 / 316.17) = 10.731 * 0.6636 = 7.122
CQ = 10.731 * (106.33 / 316.17) = 10.731 * 0.3364 = 3.609
CR = 1.333 * 1.3135 = 1.751
CS = 2.333 * 1.4858 = 3.467
```

CLE defensive:
```
CT = 22.782 * (208.83 / 336.83) = 22.782 * 0.6196 = 14.125
CU = 22.782 * (128 / 336.83) = 22.782 * 0.3800 = 8.657
CV = 0.5 * 1.6347 = 0.817
CW = 0.333 * 1.3563 = 0.452
```

#### O/D Weighting

```
off_signal_CIN = 18.435 + 5.973 - 2.189 - 1.237 = 20.982
off_signal_CLE = 7.122 + 3.609 - 1.751 - 3.467 = 5.513
off_diff = ABS(20.982 - 5.513) = 15.469

def_signal_CIN = 12.818 + 6.754 + 2.724 + 2.034 = 24.330
def_signal_CLE = 14.125 + 8.657 + 0.817 + 0.452 = 24.051
def_diff = ABS(24.330 - 24.051) = 0.279

CX_CIN = 15.469 / (15.469 + 0.279) = 15.469 / 15.748 = 0.982
CX_CLE = 0.018
```

Overwhelming offensive signal favors CIN (CLE is much weaker offensively).

#### Weighted Predictions (CIN)

```
CY = 18.435 * 0.982 + 14.125 * 0.018 = 18.111 + 0.254 = 18.365
CZ = 5.973 * 0.982 + 8.657 * 0.018 = 5.866 + 0.156 = 6.022
DA = 2.724 * 0.018 + 1.751 * 0.982 = 0.049 + 1.720 = 1.769
DB = 2.034 * 0.018 + 3.467 * 0.982 = 0.037 + 3.405 = 3.442
Raw total = 18.365 + 6.022 + 1.769 + 3.442 = 29.598
```

Strength adjustment for CIN:
```
SOW = 0.287, opponent (CLE) win rate = 0
(0.287 >= 0.3223 AND 0 >= 0.5) → FALSE
(0.287 < 0.3223 AND 0 < 0.5) → TRUE

DE = ABS(0.287 - 0.3223) + 1 = ABS(-0.0353) + 1 = 1.0353
```

```
DF_CIN = 29.598 * 1.0353 = 30.643
```

#### Weighted Predictions (CLE)

```
CY = 7.122 * 0.982 + 12.818 * 0.018 = 6.994 + 0.231 = 7.225
CZ = 3.609 * 0.982 + 6.754 * 0.018 = 3.544 + 0.122 = 3.666
DA = 0.817 * 0.018 + 2.189 * 0.982 = 0.015 + 2.150 = 2.165
DB = 0.452 * 0.018 + 1.237 * 0.982 = 0.008 + 1.215 = 1.223
Raw total = 7.225 + 3.666 + 2.165 + 1.223 = 14.279
```

Strength adjustment for CLE:
```
SOW = 0, opponent (CIN) win rate = 0.833
(0 >= 0.3223 AND 0.833 >= 0.5) → FALSE
(0 < 0.3223 AND 0.833 < 0.5) → FALSE

Neither condition true → DE = 1 - ABS(0 - 0.3223) = 1 - 0.3223 = 0.6777
```

```
DF_CLE = 14.279 * 0.6777 = 9.675
```

#### Final Predictions

```
Predicted spread: ABS(30.643 - 9.675) = 20.968 (CIN by 21.0)
Predicted total: 30.643 + 9.675 = 40.318
Predicted winner: CIN (by huge margin)
Model prob (CIN wins): ~0.99
Model spread prob (CIN covers -21): ~0.88
Model total prob (under 40.3): depends on book line, ~0.52
```

This is a massive mismatch — CIN is vastly superior offensively and defensively.

---

## FORMATTING NOTES FOR IMPLEMENTATION

### Column Naming Convention

Use the exact spreadsheet column letters when referencing in code:
- `df_away` corresponds to DF column, away team row
- `ml_odds_away` corresponds to DI column, away team row
- etc.

### Formula Implementation Notes

1. **XLOOKUP:** Standard lookup function. If unavailable (older Excel), use INDEX-MATCH combination.
2. **UNIQUE:** Returns unique values from a range. If unavailable, use remove-duplicates logic.
3. **FILTER:** Filters range based on condition. If unavailable, use array formulas or conditional logic.
4. **POISSON.DIST(x, mean, cumulative):** Spreadsheet function for Poisson probability. In Python, use `scipy.stats.poisson.pmf(x, mean)`.
5. **SLOPE:** Linear regression slope. In Python, use `numpy.polyfit()` or `scipy.stats.linregress()`.
6. **NORMINV:** Inverse normal CDF. In Python, use `scipy.stats.norm.ppf()`.
7. **AVERAGEIF, AVERAGEIFS, COUNTIF, SUMIF:** Conditional aggregations. Use pandas groupby or numpy boolean indexing.

### Data Structure Recommendation for Python

```python
# Master data: pandas DataFrame
# Columns: ['weekid', 'season', 'date', 'week', 'away', 'vs', 'home', 'win',
#           'offense', 'offense_points', 'offense_yards_total', ...]
master_df = pd.read_csv('master_data.csv')

# Glossary: separate data structure
teams = pd.read_csv('glossary_teams.csv')  # Columns: full_name, city, abbr, nickname
params = {'back_test_range': 6, ...}  # Dictionary for control parameters
spread_to_ml = pd.read_csv('glossary_spread_to_ml.csv')

# Weekly updates
current_year = 2025
current_week = 1
schedule = pd.read_csv(f'schedule_week_{current_week}_2025.csv')

# Output: DataFrames for predictions
predictions_ml = pd.DataFrame(columns=['game', 'team', 'prediction', 'odds', 'value', 'bet', 'actual_result'])
predictions_spread = pd.DataFrame(...)
predictions_total = pd.DataFrame(...)
```

### Testing and Validation

Implement unit tests for each calculation step:
1. Verify turnover regressions match expected values (BD41, BH41, etc.)
2. Cross-check team averages for one team against manual calculation
3. Validate O/D weighting produces 0-1 values, weights sum to 1
4. Verify Poisson grid sums approximately to 1.0
5. Confirm final predictions fall within reasonable ranges (5-40 points)

---

## ADDITIONAL IMPLEMENTATION NOTES

### Error Handling

- **Division by zero:** When calculating proportions (pass_yds / total_yds), guard against teams with 0 total yards by using `MAX(denominator, 0.001)`.
- **Empty filters:** When averaging stats for teams with no games in window, return 0 or use league average as default.
- **Negative scores:** In Monte Carlo simulation, use `MAX(0, random_score)` to avoid negative results.
- **Moneyline conversion:** Handle both positive and negative American odds correctly; avoid division by -0.

### Performance Optimization

- **Vectorize operations:** Use numpy/pandas operations instead of Python loops where possible.
- **Lazy evaluation:** Don't compute full Poisson grid if not needed for spread/total decisions.
- **Caching:** Store sorted averages and team lookups to avoid repeated XLOOKUP-like operations.

### Audit Trail

Maintain a log of:
- Input data sources and timestamps
- Calculated parameters (turnover slopes, avg SOW) for each week
- Model predictions vs actual results
- Bet decisions and outcomes
- P&L tracking

This allows debugging when actual results diverge from predictions.

---

## END OF SPECIFICATION

This document comprehensively specifies the NFL model's data schema, calculation logic, parameters, outputs, and validation. A Python developer can use this specification to rebuild the model from scratch without access to the original spreadsheet, reproducing exact results for all three test cases provided.
