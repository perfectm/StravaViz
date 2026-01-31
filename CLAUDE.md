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

### Database Migrations

```bash
# Run migrations in order
python migrations/001_multiuser_schema.py
python migrations/002_weekly_trophies.py
python migrations/003_add_kudos_tracking.py
python migrations/004_add_activity_visibility.py
python migrations/005_add_hr_zones.py
python migrations/006_add_locations.py
python migrations/007_add_segments.py

# Check current schema
python migrations/check_schema.py

# One-time data backfill scripts (after migrations)
python update_kudos.py          # Backfill kudos counts from Strava
python update_visibility.py     # Backfill visibility settings from Strava (REQUIRED after migration 004)
python backfill_hr_zones.py     # Backfill HR zone data from Strava (REQUIRED after migration 005)
python backfill_coordinates.py  # Backfill GPS coordinates from Strava (REQUIRED after migration 006)
python backfill_segments.py     # Backfill segment efforts from Strava (REQUIRED after migration 007)
# Note: New activities get visibility, HR zones, coordinates, and segments automatically on sync
```

### Environment Setup

1. Copy `.env.example` to `.env`
2. Configure Strava API credentials from https://developers.strava.com/:
   - `STRAVA_CLIENT_ID`
   - `STRAVA_CLIENT_SECRET`
   - `STRAVA_CLUB_ID` (optional, for club dashboard)
3. Configure OAuth settings:
   - `SESSION_SECRET` - Generate with: `python -c "import secrets; print(secrets.token_hex(32))"`
   - `COOKIE_SECURE` - Set to `true` in production with HTTPS
   - `OAUTH_REDIRECT_URI` - Auto-detected from request hostname (can override if needed)

## Architecture Overview

**Multi-user FastAPI application** with OAuth authentication, background activity sync, and real-time club statistics.

### Application Evolution

This application has evolved through multiple phases:
1. **Phase 0** - Single-user personal dashboard
2. **Phase 1** - Multi-user database schema (migration 001)
3. **Phase 2** - OAuth authentication and session management
4. **Phase 3** - Background sync service with APScheduler
5. **Phase 4** - Weekly trophies (migration 002) and kudos tracking (migration 003)
6. **Phase 5** - Activity-level visibility/privacy (migration 004)
7. **Phase 6** - Heart rate zone tracking (migration 005)
8. **Phase 7** - Location analysis with GPS tagging (migration 006)
9. **Current** - Segment PR tracking (migration 007)

### Core Components

#### 1. Authentication System (`auth.py`)
- **OAuth 2.0 Flow**: Complete Strava OAuth with CSRF protection via state parameter
- **Session Management**: 30-day sessions stored in SQLite with secure HTTP-only cookies
- **Token Refresh**: Automatic access token refresh when expired
- **User Management**: Creates/updates users from Strava athlete data
- **Dependencies**: `get_current_user()` for protected routes, `get_current_user_optional()` for flexible access

#### 2. Background Sync Service (`sync_service.py`)
- **Scheduled Jobs**: APScheduler runs background tasks
  - Activity sync: Every 15 minutes for all active users
  - Trophy calculation: Daily at 1:00 AM
  - Initial sync: 30 seconds after startup
- **Incremental Updates**: Only fetches new activities since last sync per user
- **Rate Limiting**: Respects Strava API rate limits with backoff
- **Trophy System**: Calculates weekly distance champions and maintains leaderboard (respects visibility)
- **Kudos Tracking**: Fetches and updates kudos counts for social engagement metrics
- **Visibility Tracking**: Syncs activity privacy settings from Strava and updates existing activities
- **HR Zone Tracking**: Fetches heart rate zone distribution for activities with HR data (up to 20 per sync cycle)
- **Segment Tracking**: Fetches segment efforts from activity details for activities not yet processed (up to 10 per sync cycle)

#### 3. Main Application (`strava_fastapi.py`)
- **Framework**: FastAPI with Jinja2 templates
- **Scheduler Integration**: APScheduler for background tasks
- **Database**: SQLite with multi-user schema
- **Chart Generation**: Matplotlib with Agg backend (non-interactive)

### Route Structure

```
/                          Landing page (redirects to /dashboard if authenticated)
/auth/login               OAuth login initiation
/auth/callback            OAuth callback handler
/auth/logout              Logout and session cleanup

/dashboard                Personal dashboard (requires auth)
/club                     Club dashboard with leaderboards (requires auth)
/settings                 User settings page (requires auth)
/settings/privacy         Privacy settings update (POST, requires auth)
/sync                     Manual activity sync trigger (POST, requires auth)

/locations                List user's locations with stats (requires auth)
/locations (POST)         Create new location (with optional auto-tag)
/locations/{id}           Location detail: stats, chart, tagged activities
/locations/{id}/edit      Update location (POST, requires auth)
/locations/{id}/delete    Delete location + cascade tags (POST, requires auth)
/locations/{id}/tag       Tag activity to location (POST, requires auth)
/locations/{id}/untag     Remove activity tag (POST, requires auth)
/locations/{id}/auto-tag  Auto-tag nearby activities (POST, requires auth)
/api/locations/{id}/nearby  JSON: untagged activities sorted by distance

/segments                  List user's segments with stats and PRs (requires auth)
/segments/{strava_segment_id}  Segment detail: efforts, chart, stats
```

### Database Schema

**users** - Multi-user authentication
- `id` (PK), `strava_athlete_id` (unique)
- `firstname`, `lastname`, `profile_picture`
- `access_token`, `refresh_token`, `token_expires_at`
- `privacy_level` (public/club_only/private)
- `is_active`, `created_at`, `last_login`

**sessions** - Session management
- `id` (PK, UUID), `user_id` (FK)
- `created_at`, `expires_at`

**activities** - User activity data
- `id` (PK), `user_id` (FK), `activity_id` (Strava ID)
- `name`, `type`, `start_date`, `distance`
- `moving_time`, `elapsed_time`, `total_elevation_gain`
- `average_speed`, `max_speed`
- `average_heartrate`, `max_heartrate`, `calories`
- `kudos_count` (added in migration 003)
- `visibility` (added in migration 004) - Strava privacy setting: 'everyone', 'only_me', 'followers_only'
- UNIQUE constraint: `(user_id, activity_id)`

**weekly_trophies** - Weekly distance champions
- `id` (PK), `user_id` (FK)
- `week_start`, `week_end`
- `total_distance`, `activity_count`
- `created_at`
- UNIQUE constraint: `(user_id, week_start)`

**activities** table also includes (added in migration 006):
- `start_lat` (REAL) - GPS latitude of activity start
- `start_lng` (REAL) - GPS longitude of activity start

**activity_hr_zones** - Per-activity heart rate zone distribution
- `id` (PK), `user_id` (FK), `activity_id` (Strava ID)
- `zone_1_seconds`, `zone_2_seconds`, `zone_3_seconds`, `zone_4_seconds`, `zone_5_seconds`
- `fetched_at`
- UNIQUE constraint: `(user_id, activity_id)`

**locations** - User-defined named locations for performance tracking
- `id` (PK), `user_id` (FK)
- `name`, `description`
- `center_lat`, `center_lng`, `radius_meters` (default 500)
- `created_at`, `updated_at`

**activity_locations** - Many-to-many: activities tagged to locations
- `id` (PK), `user_id` (FK), `activity_id` (Strava ID), `location_id` (FK)
- `tagged_by` ('manual' or 'auto')
- `created_at`
- UNIQUE constraint: `(user_id, activity_id, location_id)`

**activities** table also includes (added in migration 007):
- `segments_fetched` (INTEGER DEFAULT 0) - Whether activity has been processed for segments

**segments** - Global segment master data (not user-scoped)
- `id` (PK), `strava_segment_id` (UNIQUE)
- `name`, `distance`, `average_grade`, `maximum_grade`
- `city`, `state`, `climb_category`

**segment_efforts** - Per-user segment effort records
- `id` (PK), `user_id` (FK), `activity_id`, `strava_segment_id` (FK)
- `strava_effort_id`, `elapsed_time`, `moving_time`, `start_date`
- `pr_rank`, `kom_rank`, `average_heartrate`, `max_heartrate`, `fetched_at`
- UNIQUE constraint: `(user_id, strava_effort_id)`

**club_memberships** - Club associations
- `id` (PK), `user_id` (FK), `club_id`
- `joined_at`
- UNIQUE constraint: `(user_id, club_id)`

### Authentication Flow

1. User visits landing page → "Connect with Strava" button
2. `/auth/login` → Generates OAuth state, redirects to Strava
3. User authorizes → Strava redirects to `/auth/callback`
4. Callback validates state, exchanges code for tokens
5. Creates/updates user in database with tokens
6. **Triggers immediate activity sync** in background (doesn't wait for 15-min scheduled sync)
7. Creates session and sets secure cookie
8. Redirects to `/dashboard`

**Note**: The immediate sync on login ensures new users see their activities right away instead of waiting up to 15 minutes for the next scheduled background sync. The sync runs asynchronously so the user is redirected immediately while activities are fetched in the background.

### Background Sync Flow

**Scheduled Background Sync** (every 15 minutes):
1. Scheduler triggers `sync_all_users()` every 15 minutes
2. For each active user:
   - Check token expiration, refresh if needed
   - Query database for latest activity date
   - Fetch only new activities from Strava API
   - Insert new activities (skip duplicates via UNIQUE constraint)
   - Update kudos counts if changed

**Login-Triggered Sync** (immediate):
- When user authenticates via OAuth, `sync_user_activities(user_id)` is scheduled immediately
- Runs as a one-time background job (doesn't block the redirect to dashboard)
- Ensures new users see activities right away without waiting for scheduled sync
- Also triggers on every login to refresh data for returning users

**Trophy Calculation** (daily at 1:00 AM):
- Calculate current week (Monday-Sunday)
- Aggregate user distances for the week
- Update `weekly_trophies` table
- Determine weekly champion

### Data Processing Pipeline

#### Personal Dashboard (`/dashboard`)
1. Get authenticated user from session
2. Refresh token if expired
3. Load user's activities from database
4. Filter to Walk/Hike/Run types
5. Group by month and activity type
6. Generate matplotlib charts → base64 PNG
7. Calculate summary statistics
8. Render template with embedded charts

#### Club Dashboard (`/club`)
1. Get authenticated user from session
2. Load all users' activities (respecting privacy settings)
3. Filter to Walk/Hike/Run types
4. Calculate leaderboards:
   - **Weekly Distance**: From `weekly_trophies` table for current week
   - **All-Time Distance**: Aggregated from all activities
   - **Trophy Leaderboard**: Count of weekly wins per user
   - **Kudos Leaderboards**: Weekly and all-time kudos received
5. Generate charts (weekly leaderboard, activity type distribution)
6. Render template with stats and recent activities

### Privacy System

**Two-Layer Privacy Model:**

**Layer 1: User-Level Privacy** (`users.privacy_level`)
- **public** - User's activities visible to everyone
- **club_only** - Activities visible only to authenticated club members (default)
- **private** - All activities completely hidden from club views

**Layer 2: Activity-Level Visibility** (`activities.visibility`)
- **everyone** - Public activity (visible in club views)
- **followers_only** - Followers-only activity (visible in club views)
- **only_me** - Private activity (hidden from club views)

**Privacy Filtering Rules:**
- Personal dashboard (`/dashboard`) - Shows ALL user's activities regardless of visibility
- Club dashboard (`/club`) - Filters out:
  - Users with `privacy_level='private'`
  - Activities with `visibility='only_me'`
- Leaderboards & Trophies - Only count activities where `visibility != 'only_me'`

This respects both user-wide privacy preferences and individual activity privacy settings from Strava.

### Critical Implementation Details

#### Token Management
- Tokens stored per-user in database (not environment variables)
- `refresh_user_token()` in `auth.py` handles automatic refresh
- Expiration checked on each request via `get_current_user()` dependency
- Refresh token rotation handled by Strava API

#### Activity Filtering
- Only Walk, Hike, Run types processed
- Color coding: Walk=#4CAF50 (green), Hike=#FF9800 (orange), Run=#2196F3 (blue)
- Filter applied consistently across personal and club dashboards

#### Incremental Sync
- Database query: `SELECT MAX(start_date) FROM activities WHERE user_id = ?`
- Strava API: `after` parameter set to latest activity timestamp
- Prevents re-downloading historical data
- UNIQUE constraint on `(user_id, activity_id)` prevents duplicates

#### Session Security
- HTTP-only cookies prevent XSS attacks
- Signed cookies via `itsdangerous` prevent tampering
- CSRF protection via OAuth state parameter
- 30-day expiration with automatic cleanup
- `COOKIE_SECURE=true` enforces HTTPS in production

#### Rate Limiting
- Strava API limits: 100 requests per 15 minutes, 1000 per day
- Background sync spaces out user syncs
- Handles 429 responses with exponential backoff
- Sync interval (15 min) keeps well under limits for reasonable user counts

#### Kudos Tracking
- `kudos_count` column added in migration 003
- Updated during background sync via Strava API
- Leaderboards: Weekly and all-time kudos received
- Special leaderboard: Most kudos on single activity

#### Activity Visibility Sync
- `visibility` column added in migration 004 with default `'only_me'` (privacy-safe)
- Synced from Strava API on every activity fetch
- **Important**: After migration 004, run `python update_visibility.py` to backfill visibility for existing activities
- Sync service checks if activity exists and UPDATE if found, INSERT if new
- Existing activities get visibility updated on re-fetch (kudos and visibility can change)
- Personal dashboard shows all activities regardless of visibility
- Club views filter out `visibility='only_me'` (private activities)
- Trophy calculations and leaderboards respect visibility settings
- Users can change activity visibility in Strava; changes sync within 15 minutes

### Template Structure

**landing.html** - Pre-authentication landing page
- Feature highlights
- "Connect with Strava" OAuth button
- Optional public club statistics preview

**dashboard.html** - Personal activity dashboard
- User navigation with logout button
- Monthly distance charts (stacked + individual per type)
- Recent activity tables by type
- Summary statistics

**club_dashboard.html** - Club statistics and leaderboards
- Weekly distance leaderboard
- All-time distance leaderboard with chart
- Trophy leaderboard (weekly wins count)
- Kudos leaderboards (weekly, all-time, single activity)
- Activity type distribution chart
- Recent activities feed (respects privacy)

**settings.html** - User settings and privacy controls
- Account information display
- Privacy level selector (public/club_only/private)
- Last sync timestamp
- Logout option

**segments.html** - Segment list page
- Summary stats (total segments, efforts, PRs)
- Recent PR highlights (last 30 days)
- Sortable segment cards (by attempts, recent, name)

**segment_detail.html** - Segment detail page
- Segment info with Strava link
- Performance stat cards (best/avg time, attempts, improvement %)
- Time progression chart (matplotlib, y-axis inverted)
- All efforts table with PR highlighting

**error.html** - OAuth error handling
- User-friendly error messages
- Link back to landing page

### Key Functions

#### auth.py
- `get_current_user(request)` - Auth dependency for protected routes
- `get_current_user_optional(request)` - Optional auth for flexible routes
- `create_or_update_user(strava_data, tokens)` - User creation/update from OAuth
- `create_session(user_id)` - Session creation with 30-day expiry
- `refresh_user_token(user)` - Automatic token refresh
- `generate_oauth_state()` - CSRF state generation

#### sync_service.py
- `sync_all_users()` - Background sync for all active users
- `sync_user_activities(user)` - Individual user activity sync
- `calculate_weekly_trophies()` - Weekly champion calculation
- `get_trophy_leaderboard()` - Trophy count leaderboard
- `get_weekly_kudos_leaderboard()` - Weekly kudos rankings
- `get_alltime_kudos_leaderboard()` - All-time kudos rankings
- `get_most_kudos_single_activity()` - Single activity kudos leaders

#### strava_fastapi.py
- Route handlers for all endpoints
- Scheduler initialization and job management
- Chart generation utilities
- Template rendering with context data

## Deployment

See `DEPLOYMENT.md` for Hostinger VPS deployment guide including:
- systemd service configuration
- nginx reverse proxy setup
- SSL certificate with Let's Encrypt
- Log rotation and backup strategies
- Multi-domain support (dev and production)

## Troubleshooting

### Users With Sync Issues

**Symptom**: User's activities aren't syncing, logs show "Authentication failed" or "Token refresh failed"

**Common Causes**:

1. **Missing activity:read_all scope** - User only granted `read` permission, not `activity:read_all`
   - **Solution**: User needs to log out and re-authenticate, accepting ALL permissions
   - New scope validation in OAuth callback will catch this and show clear error message

2. **Environment variables not loaded** - CLIENT_ID/CLIENT_SECRET are None in sync_service
   - **Symptom**: "Token refresh failed: 400" errors in logs
   - **Solution**: Ensure `load_dotenv()` is called BEFORE importing sync_service in strava_fastapi.py
   - **Critical**: Environment variables must be loaded at import time, not runtime

3. **Revoked access** - User revoked app access in Strava settings
   - **Solution**: User needs to re-authenticate

**Diagnostic Script**:
```python
# Check all users for permission issues
import sqlite3, requests
conn = sqlite3.connect('strava_activities.db')
for row in conn.execute("SELECT * FROM users"):
    response = requests.get('https://www.strava.com/api/v3/athlete/activities',
        headers={'Authorization': f"Bearer {row['access_token']}"}, params={'per_page': 1})
    print(f"User {row['id']} ({row['firstname']}): {response.status_code}")
```

## Development Notes

### OAuth Redirect URI Auto-Detection

The app auto-detects the hostname from incoming requests and constructs the OAuth redirect URI dynamically. This allows seamless deployment across:
- `localhost:8002` (local development)
- `closet.cottonmike.com` (dev environment)
- `strava.cottonmike.com` (production)

You can override this by setting `OAUTH_REDIRECT_URI` in `.env`, but auto-detection is recommended.

### Strava App Configuration

Before OAuth works, configure at https://developers.strava.com/:
1. **Authorization Callback Domain**: Add all domains (localhost, dev, production)
2. **Authorization Callback URL**: Must match computed URI (e.g., `https://strava.cottonmike.com/auth/callback`)
3. **Requested Scopes**: `read`, `activity:read_all`

### Testing Background Sync

Background sync runs automatically, but you can trigger manual sync:
```bash
# Via API (requires authentication)
curl -X POST http://localhost:8002/sync \
  -H "Cookie: strava_session=your_session_cookie"
```

Check sync status in logs:
```bash
tail -f server.log
```

### Database Migrations Best Practices

- Always run migrations in order (001 → 002 → 003)
- Migrations create automatic backups before running
- Check schema after migration: `python migrations/check_schema.py`
- Rollback available via backup files: `strava_activities.db.backup_TIMESTAMP_migration_name`

### Common Development Tasks

**Add new activity metrics:**
1. Update `activities` table schema (create new migration)
2. Modify `sync_user_activities()` to fetch new fields
3. Update template to display new metrics
4. Run migration and backfill data if needed

**Add new leaderboard:**
1. Create query function in `sync_service.py`
2. Add route handler in `strava_fastapi.py`
3. Update `club_dashboard.html` template
4. Optionally create background job for calculation

**Change privacy logic:**
1. Update privacy filtering in club dashboard route
2. Modify `WHERE` clause to respect new privacy rules
3. Update `settings.html` if new privacy levels added

## Migration from Single-User

Existing single-user installations:
1. Run migration 001 → Creates user_id=1 for legacy data
2. First OAuth login → Creates your actual user account
3. Legacy data remains accessible to user_id=1
4. Optionally migrate data to new account:
   ```sql
   UPDATE activities SET user_id = <your_new_user_id> WHERE user_id = 1;
   ```
