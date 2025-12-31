# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Running the Application

#### Using Shell Scripts (Recommended)
```bash
./start.sh    # Start server in background on port 8001
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
uvicorn strava_fastapi:app --reload --port 8001
```

### Environment Setup
1. Copy `.env.example` to `.env`
2. Configure Strava API credentials from https://developers.strava.com/:
   - `STRAVA_CLIENT_ID`
   - `STRAVA_CLIENT_SECRET`
   - `STRAVA_REFRESH_TOKEN` (obtain through Strava OAuth flow)

## Architecture Overview

**Single-file FastAPI application** (`strava_fastapi.py`) with:
- **Web Interface**: Jinja2 template (`templates/dashboard.html`) served at single `GET /` endpoint
- **Strava Integration**: OAuth token refresh → paginated activity fetching → filters for Walk/Hike/Run only
- **SQLite Storage**: `strava_activities.db` with duplicate prevention via unique `activity_id` column
- **Visualization**: Matplotlib charts (combined stacked + individual per type) → base64 PNG → embedded in HTML

### Data Flow
1. On page load, refresh OAuth token via `get_access_token()`
2. Query database for latest activity date
3. Fetch only new activities from Strava API via pagination (strava_fastapi.py:119-133)
4. Insert new activities into SQLite, skipping duplicates (strava_fastapi.py:73-86)
5. Load all activities into pandas DataFrame, filter to Walk/Hike/Run types
6. Group by month and type, generate charts with color coding (Walk=green, Hike=orange, Run=blue)
7. Render dashboard template with base64-encoded charts and recent activity lists

### Critical Implementation Details
- **Incremental Updates**: Database query for `MAX(start_date)` prevents re-downloading historical data
- **Activity Filtering**: Only Walk, Hike, Run types are processed (strava_fastapi.py:152)
- **Date Formatting**: Month labels show "Jan 2024" format using strftime (strava_fastapi.py:176, 199)
- **Non-interactive Backend**: Uses `matplotlib.use('Agg')` for server-side rendering without display

### Database Schema
Table `activities`:
- `id`: Primary key
- `activity_id`: Unique Strava ID (prevents duplicates)
- `name`, `type`, `start_date`, `distance` (meters)