# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Running the Application

#### Using Shell Scripts (Recommended)
```bash
./start.sh    # Start server in background on port 8002
./stop.sh     # Stop the server
./restart.sh  # Restart the server
./status.sh   # Check server status
```

Server management files:
- `server.pid`: Tracks running process
- `server.log`: Server output and errors

#### Manual Start
```bash
pip install -r requirements.txt
uvicorn strava_fastapi:app --reload --port 8002
```

### Environment Setup
1. Copy `.env.example` to `.env`
2. Configure Strava API credentials from https://developers.strava.com/:
   - `STRAVA_CLIENT_ID`
   - `STRAVA_CLIENT_SECRET`
   - `STRAVA_REFRESH_TOKEN` (obtain through Strava OAuth flow)
   - `STRAVA_CLUB_ID` (optional, for club dashboard)

## Architecture Overview

**Single-file FastAPI application** (`strava_fastapi.py`) with two main views:

### Personal Dashboard (`GET /`)
- **Purpose**: Individual athlete's activity visualization
- **Data Source**: Personal Strava activities via athlete API
- **Template**: `templates/dashboard.html`
- **Features**: Monthly charts (combined stacked + individual per type), recent activity tables

### Club Dashboard (`GET /club`)
- **Purpose**: Aggregated club activity visualization
- **Data Source**: Club activities via Strava club API
- **Template**: `templates/club_dashboard.html`
- **Features**: Weekly leaderboard, all-time leaderboard with chart, activity type distribution, recent activities feed

### Data Flow

#### Personal Dashboard Flow
1. Refresh OAuth token via `get_access_token()` (strava_fastapi.py:33-43)
2. Query database for latest activity date
3. Fetch only new activities from Strava API via pagination (strava_fastapi.py:147-161)
4. Insert new activities into SQLite, skipping duplicates via unique `activity_id` constraint
5. Load all activities into pandas DataFrame, filter to Walk/Hike/Run types (strava_fastapi.py:180)
6. Group by month and activity type
7. Generate matplotlib charts → convert to base64 PNG
8. Render dashboard template with embedded charts

#### Club Dashboard Flow
1. Refresh OAuth token
2. Fetch club activities via `get_club_activities()` (strava_fastapi.py:61-84)
3. Extract athlete names from nested API response structure (strava_fastapi.py:286-287)
4. Filter to Walk/Hike/Run activities
5. Calculate two leaderboards:
   - **Weekly leaderboard**: Top 10 from most recent 50 activities (proxy for current week due to API limitations)
   - **All-time leaderboard**: Aggregated totals across all fetched club activities
6. Generate horizontal bar chart for weekly leaderboard
7. Generate activity type distribution bar chart
8. Render club dashboard template

### Critical Implementation Details

#### Incremental Updates
- Database query for `MAX(start_date)` prevents re-downloading historical data (strava_fastapi.py:143-145)
- Only fetches activities newer than the latest stored date

#### Activity Filtering
- Only Walk, Hike, Run types are processed (strava_fastapi.py:180 for personal, strava_fastapi.py:291 for club)
- Color coding: Walk=green (#4CAF50), Hike=orange (#FF9800), Run=blue (#2196F3)

#### Date Formatting
- Month labels show "Jan 2024" format using strftime (strava_fastapi.py:204, 227)
- Non-interactive backend: Uses `matplotlib.use('Agg')` for server-side rendering

#### Club API Data Handling
- **Nested athlete structure**: Club API returns `athlete` as nested dict; must extract `athlete.firstname` (strava_fastapi.py:286-287)
- **Limited metadata**: Club API provides limited data (no full timestamps, partial names as "FirstName L.")
- **Weekly leaderboard workaround**: Uses first 50 activities as proxy for current week since club API lacks proper date filtering (strava_fastapi.py:338)

#### Template Rendering
- **Jinja2 constraints**: Use `loop.index` instead of `enumerate()` which is not available in Jinja2
- **Conditional sections**: Weekly leaderboard only renders if data exists: `{% if weekly_leaderboard and weekly_leaderboard|length > 0 %}`

### Database Schema

Table `activities`:
- `id`: Primary key
- `activity_id`: Unique Strava ID (prevents duplicates)
- `name`: Activity name
- `type`: Activity type (Walk, Hike, Run)
- `start_date`: ISO timestamp
- `distance`: Distance in meters

### Key Functions

#### OAuth & API
- `get_access_token()`: Refreshes Strava OAuth token (strava_fastapi.py:33-43)
- `get_activities(token, per_page, pages)`: Fetches personal activities with pagination (strava_fastapi.py:46-58)
- `get_club_activities(token, club_id, per_page)`: Fetches club activities with pagination (strava_fastapi.py:61-84)

#### Database Operations
- `init_db()`: Creates activities table if not exists (strava_fastapi.py:86-98)
- `save_and_get_activities(activities)`: Inserts new activities, returns full DataFrame (strava_fastapi.py:101-114)

#### Data Processing
- `get_recent_activities_by_type(df, type, limit)`: Filters and sorts activities by type (strava_fastapi.py:116-121)
- `get_activity_stats(df)`: Calculates summary statistics (strava_fastapi.py:123-131)

#### Route Handlers
- `index(request)`: Personal dashboard endpoint (strava_fastapi.py:137-257)
- `club_dashboard(request)`: Club dashboard endpoint (strava_fastapi.py:259-408)

### Weekly Leaderboard Logic

The weekly leaderboard (strava_fastapi.py:318-347) works around Strava club API limitations:
1. Calculate current week boundaries (Monday to Sunday) for context, though not directly used due to API limitations
2. Use most recent 50 activities as proxy for "current week" since club API returns activities in reverse chronological order without full timestamps
3. Group by `athlete.firstname`, aggregate distance and activity count
4. Sort by total distance descending
5. Convert to kilometers and format for display

**Important**: This is an approximation. For true weekly data, would need multi-user OAuth implementation (see MULTI_USER_PLAN.md).

### Template Structure

Both templates share common styling but differ in data presentation:
- **Common**: Responsive design, gradient headers, stat cards, data tables
- **dashboard.html**: Individual activity type sections with charts and tables
- **club_dashboard.html**: Leaderboard sections, aggregated statistics, activity type distribution

## Deployment

See `DEPLOYMENT.md` for comprehensive Hostinger VPS deployment guide including:
- systemd service configuration
- nginx reverse proxy setup
- SSL certificate with Let's Encrypt
- Log rotation and backup strategies

## Future Development

See `MULTI_USER_PLAN.md` for detailed plan to transform into multi-user club platform with:
- Individual OAuth for each club member
- User management and privacy controls
- Background activity syncing
- Enhanced analytics and social features
