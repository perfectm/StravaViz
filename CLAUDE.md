# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Running the Application

#### Using Shell Scripts (Recommended)
```bash
./start.sh    # Start the server in background
./stop.sh     # Stop the server
./restart.sh  # Restart the server
./status.sh   # Check server status
```

#### Manual Start
```bash
uvicorn strava_fastapi:app --reload --port 8001
```
The application serves on `http://localhost:8001` by default.

#### Server Management
- **PID file**: `server.pid` (tracks running process)
- **Log file**: `server.log` (server output and errors)
- **Port**: 8001 (configurable in scripts)

### Installing Dependencies
```bash
pip install -r requirements.txt
```

### Environment Setup
1. Copy `.env.example` to `.env`
2. Configure Strava API credentials:
   - `STRAVA_CLIENT_ID`: Get from https://developers.strava.com/
   - `STRAVA_CLIENT_SECRET`: Get from https://developers.strava.com/
   - `STRAVA_REFRESH_TOKEN`: Obtain through Strava OAuth flow

## Architecture Overview

This is a single-file FastAPI application (`strava_fastapi.py`) that:

### Core Components
- **FastAPI Application**: Single endpoint serving HTML dashboard via Jinja2 templates
- **Strava API Integration**: OAuth token refresh and activity fetching with pagination
- **SQLite Database**: Local storage in `strava_activities.db` for incremental data updates
- **Data Visualization**: Matplotlib charts with improved formatting, rendered as base64-encoded PNG images

### Data Flow
1. Application fetches new Strava activities since last update
2. Activities are filtered for Walk/Hike/Run types only
3. Data is stored in SQLite with duplicate prevention
4. Monthly aggregations are calculated using pandas
5. Charts are generated in-memory and embedded as base64 images in HTML

### Key Functions
- `get_access_token()`: Refreshes Strava OAuth token
- `get_activities()`: Fetches activities from Strava API with pagination
- `save_and_get_activities()`: Handles database operations and returns DataFrame
- `init_db()`: Creates SQLite schema on startup
- `get_recent_activities_by_type()`: Returns recent activities for a specific type (Walk/Hike/Run)
- `get_activity_stats()`: Calculates summary statistics (total activities, distances, counts by type)
- `format_activities_for_template()`: Prepares activity data for Jinja2 template rendering

### Database Schema
Table `activities`:
- `activity_id`: Unique Strava activity ID (prevents duplicates)
- `name`: Activity name
- `type`: Activity type (Walk, Hike, Run)
- `start_date`: ISO datetime string
- `distance`: Distance in meters

### Chart Features
- **Combined Stacked Bar Chart**: Shows monthly distances for all activity types in one view
- **Individual Activity Charts**: Separate charts for Walk, Hike, and Run activities
- **Improved Date Formatting**: Month labels display as "Jan 2024" format for better readability
- **Color Coding**: Walk (green), Hike (orange), Run (blue)
- **High DPI Output**: Charts rendered at 150 DPI for crisp display

### Template System
The application uses Jinja2 templates located in the `templates/` directory:
- `dashboard.html`: Main dashboard template with charts and activity listings
- Template context includes: charts (base64), recent activities, and summary statistics

### Data Processing
The application automatically handles incremental updates by checking the latest activity date in the database and only fetching newer activities from the Strava API.

### Recent Updates
- Enhanced chart formatting with proper month/year labels
- Added template-based HTML rendering
- Improved data organization with helper functions
- Better visual design with color-coded activity types