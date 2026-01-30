from fastapi import FastAPI, Request, Depends, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import requests
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend for FastAPI
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import io
import base64
import os
from dotenv import load_dotenv
import sqlite3
from datetime import datetime, timedelta
from urllib.parse import urlencode
import time

# Load environment variables FIRST before other imports that need them
load_dotenv()

# Import authentication utilities
from auth import (
    get_current_user,
    get_current_user_optional,
    create_or_update_user,
    create_session,
    create_session_cookie,
    clear_session_cookie,
    get_session_from_cookie,
    delete_session,
    generate_oauth_state,
    refresh_user_token
)

# Import background sync service
from sync_service import (
    sync_all_users,
    sync_user_activities,
    calculate_weekly_trophies,
    get_trophy_leaderboard,
    get_recent_trophy_winners,
    get_weekly_kudos_leaderboard,
    get_alltime_kudos_leaderboard,
    get_most_kudos_single_activity
)
from apscheduler.schedulers.background import BackgroundScheduler
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Initialize background scheduler
scheduler = BackgroundScheduler()


@app.on_event("startup")
async def startup_event():
    """Start background scheduler on app startup"""
    logger.info("Starting background activity sync scheduler...")

    # Schedule sync_all_users to run every 15 minutes
    scheduler.add_job(
        sync_all_users,
        'interval',
        minutes=15,
        id='sync_all_users',
        replace_existing=True,
        max_instances=1  # Prevent overlapping executions
    )

    # Schedule weekly trophy calculation to run daily at 1 AM
    scheduler.add_job(
        calculate_weekly_trophies,
        'cron',
        hour=1,
        minute=0,
        id='calculate_trophies',
        replace_existing=True,
        max_instances=1
    )

    scheduler.start()
    logger.info("Background scheduler started:")
    logger.info("  - Activity sync: every 15 minutes")
    logger.info("  - Trophy calculation: daily at 1:00 AM")

    # Run an initial sync on startup (after 30 seconds delay)
    scheduler.add_job(
        sync_all_users,
        'date',
        run_date=datetime.now() + timedelta(seconds=30),
        id='initial_sync'
    )


@app.on_event("shutdown")
async def shutdown_event():
    """Stop background scheduler on app shutdown"""
    logger.info("Stopping background scheduler...")
    scheduler.shutdown()
    logger.info("Background scheduler stopped")

# Set your Strava API credentials here (loaded at top of file)
CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("STRAVA_REFRESH_TOKEN")
CLUB_ID = os.getenv("STRAVA_CLUB_ID")

# OAuth configuration (fallback - will be dynamically determined per request)
OAUTH_REDIRECT_URI_FALLBACK = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8002/auth/callback")


def get_oauth_redirect_uri(request: Request) -> str:
    """
    Dynamically determine OAuth redirect URI based on the incoming request.
    This allows the app to work in multiple environments (dev/prod) automatically.

    Examples:
    - localhost:8002 -> http://localhost:8002/auth/callback
    - closet.cottonmike.com -> http://closet.cottonmike.com/auth/callback
    - strava.cottonmike.com -> http://strava.cottonmike.com/auth/callback
    """
    # Get the host from the request
    host = request.headers.get('host', 'localhost:8002')

    # Determine protocol (check if behind a proxy with HTTPS)
    proto = request.headers.get('x-forwarded-proto', 'http')

    # If running on standard ports (80/443), don't include port in URL
    if ':80' in host and proto == 'http':
        host = host.replace(':80', '')
    elif ':443' in host and proto == 'https':
        host = host.replace(':443', '')

    return f"{proto}://{host}/auth/callback"

# Strava API URLs
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
STRAVA_ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"
STRAVA_CLUB_ACTIVITIES_URL = "https://www.strava.com/api/v3/clubs/{club_id}/activities"
STRAVA_ATHLETE_URL = "https://www.strava.com/api/v3/athlete"

# Helper to get access token
def get_access_token():
    response = requests.post(
        STRAVA_TOKEN_URL,
        data={
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'grant_type': 'refresh_token',
            'refresh_token': REFRESH_TOKEN
        }
    )
    return response.json().get('access_token')

# Helper to get activities
def get_activities(token, per_page=200, pages=1):
    activities = []
    for page in range(1, pages+1):
        resp = requests.get(
            STRAVA_ACTIVITIES_URL,
            headers={'Authorization': f'Bearer {token}'},
            params={'per_page': per_page, 'page': page}
        )
        if resp.status_code == 200:
            activities.extend(resp.json())
        else:
            break
    return activities

# Helper to get club activities
def get_club_activities(token, club_id, per_page=200):
    """
    Fetch recent activities from club members.
    Note: This endpoint has limitations - no timestamps, limited data, partial names.

    Returns:
        tuple: (activities_list, error_message or None)
    """
    activities = []
    page = 1
    while True:
        resp = requests.get(
            STRAVA_CLUB_ACTIVITIES_URL.format(club_id=club_id),
            headers={'Authorization': f'Bearer {token}'},
            params={'per_page': per_page, 'page': page}
        )
        if resp.status_code == 200:
            page_activities = resp.json()
            if not page_activities:
                break
            activities.extend(page_activities)
            if len(page_activities) < per_page:
                break
            page += 1
        elif resp.status_code == 429:
            return None, "Strava API rate limit exceeded. Please wait 15 minutes and try again."
        elif resp.status_code == 401:
            return None, "Authentication failed. Please log in again."
        elif resp.status_code == 404:
            return None, f"Club {club_id} not found. Make sure you're a member of this club."
        else:
            return None, f"API error: {resp.status_code} - {resp.text[:200]}"

    return activities, None

def init_db():
    """
    Initialize database schema.
    Note: For multi-user schema, run migrations/001_multiuser_schema.py instead.
    This maintains backward compatibility for fresh installs.
    """
    conn = sqlite3.connect('strava_activities.db')
    c = conn.cursor()

    # Check if new multi-user schema exists
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    has_multiuser = c.fetchone() is not None

    if not has_multiuser:
        # Legacy single-user schema
        c.execute('''CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY,
            activity_id INTEGER UNIQUE,
            name TEXT,
            type TEXT,
            start_date TEXT,
            distance REAL
        )''')
    # If multi-user schema exists, tables are already created by migration

    conn.commit()
    conn.close()

# Save new activities to DB and return all activities as DataFrame
def save_and_get_activities(all_activities, user_id=1):
    """
    Save new activities to database and return all activities as DataFrame.

    Args:
        all_activities: List of activity dictionaries from Strava API
        user_id: User ID to associate activities with (default: 1 for legacy/single-user)

    Returns:
        DataFrame containing all activities for the specified user
    """
    conn = sqlite3.connect('strava_activities.db')
    c = conn.cursor()

    # Check if using multi-user schema
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    has_multiuser = c.fetchone() is not None

    # Insert only new activities
    for act in all_activities:
        try:
            if has_multiuser:
                # Multi-user schema: include user_id
                c.execute('''INSERT INTO activities (user_id, activity_id, name, type, start_date, distance)
                            VALUES (?, ?, ?, ?, ?, ?)''',
                    (user_id, act['id'], act['name'], act['type'], act['start_date'], act['distance']))
            else:
                # Legacy schema: no user_id
                c.execute('''INSERT INTO activities (activity_id, name, type, start_date, distance)
                            VALUES (?, ?, ?, ?, ?)''',
                    (act['id'], act['name'], act['type'], act['start_date'], act['distance']))
        except sqlite3.IntegrityError:
            continue  # Already in DB

    conn.commit()

    # Fetch activities for this user
    if has_multiuser:
        df = pd.read_sql_query('SELECT * FROM activities WHERE user_id = ?', conn, params=(user_id,))
    else:
        df = pd.read_sql_query('SELECT * FROM activities', conn)

    conn.close()
    return df

def get_recent_activities_by_type(df, activity_type, limit=10):
    type_df = df[df['type'] == activity_type].copy()
    if type_df.empty:
        return pd.DataFrame()
    type_df = type_df.sort_values('start_date', ascending=False).head(limit)
    return type_df

def get_activity_stats(df):
    # Calculate total duration in hours
    total_duration_hours = 0
    if 'moving_time' in df.columns:
        total_duration_hours = df['moving_time'].sum() / 3600  # Convert seconds to hours
    elif 'elapsed_time' in df.columns:
        total_duration_hours = df['elapsed_time'].sum() / 3600

    stats = {
        'total_activities': len(df),
        'total_distance_km': df['distance_km'].sum(),
        'total_duration_hours': round(total_duration_hours, 1),
        'walk_count': len(df[df['type'] == 'Walk']),
        'hike_count': len(df[df['type'] == 'Hike']),
        'run_count': len(df[df['type'] == 'Run']),
        'ride_count': len(df[df['type'] == 'Ride'])
    }
    return stats

def get_personal_records(df):
    """Calculate personal records from activities"""
    if df.empty:
        return None

    records = {}

    # Longest single activity
    if 'distance_km' in df.columns and not df.empty:
        longest_idx = df['distance_km'].idxmax()
        records['longest_distance'] = {
            'value': round(df.loc[longest_idx, 'distance_km'], 2),
            'name': df.loc[longest_idx, 'name'],
            'date': df.loc[longest_idx, 'start_date'].strftime('%Y-%m-%d') if pd.notnull(df.loc[longest_idx, 'start_date']) else 'N/A',
            'type': df.loc[longest_idx, 'type']
        }

    # Most elevation gain
    if 'total_elevation_gain' in df.columns:
        elevation_df = df[df['total_elevation_gain'].notnull() & (df['total_elevation_gain'] > 0)]
        if not elevation_df.empty:
            elevation_idx = elevation_df['total_elevation_gain'].idxmax()
            records['most_elevation'] = {
                'value': round(elevation_df.loc[elevation_idx, 'total_elevation_gain'], 1),
                'name': elevation_df.loc[elevation_idx, 'name'],
                'date': elevation_df.loc[elevation_idx, 'start_date'].strftime('%Y-%m-%d') if pd.notnull(elevation_df.loc[elevation_idx, 'start_date']) else 'N/A',
                'type': elevation_df.loc[elevation_idx, 'type']
            }

    # Most activities in a week
    if 'start_date' in df.columns:
        df_copy = df.copy()
        df_copy['week'] = df_copy['start_date'].dt.isocalendar().week
        df_copy['year'] = df_copy['start_date'].dt.year
        weekly_counts = df_copy.groupby(['year', 'week']).size()
        if not weekly_counts.empty:
            max_week = weekly_counts.idxmax()
            records['most_weekly_activities'] = {
                'value': int(weekly_counts.max()),
                'week': f"Week {max_week[1]}, {max_week[0]}"
            }

    return records if records else None

def get_weekly_progress(df):
    """Calculate current week's progress"""
    if df.empty or 'start_date' not in df.columns:
        return None

    # Get current week (Monday to Sunday)
    today = datetime.now()
    week_start = today - timedelta(days=today.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=7)

    # Ensure start_date is timezone-naive for comparison
    df_copy = df.copy()
    if pd.api.types.is_datetime64tz_dtype(df_copy['start_date']):
        df_copy['start_date'] = df_copy['start_date'].dt.tz_localize(None)

    # Filter to current week
    df_week = df_copy[(df_copy['start_date'] >= week_start) & (df_copy['start_date'] < week_end)]

    if df_week.empty:
        return {
            'current_distance': 0,
            'current_activities': 0,
            'week_start': week_start.strftime('%b %d'),
            'week_end': (week_end - timedelta(days=1)).strftime('%b %d')
        }

    return {
        'current_distance': round(df_week['distance_km'].sum(), 1),
        'current_activities': len(df_week),
        'week_start': week_start.strftime('%b %d'),
        'week_end': (week_end - timedelta(days=1)).strftime('%b %d')
    }

def get_weekly_hr_zones(user_id, num_weeks=8):
    """
    Get weekly heart rate zone summaries for the last N weeks.
    Includes ALL activity types (not just Walk/Hike/Run).

    Args:
        user_id: User ID
        num_weeks: Number of weeks to include

    Returns:
        List of weekly summaries with zone minutes, or None if no data
    """
    try:
        conn = sqlite3.connect('strava_activities.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Check if table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='activity_hr_zones'")
        if not cursor.fetchone():
            conn.close()
            return None

        # Calculate date range
        today = datetime.now()
        week_start = today - timedelta(days=today.weekday())  # Monday
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        range_start = week_start - timedelta(weeks=num_weeks - 1)

        cursor.execute("""
            SELECT
                date(a.start_date, 'weekday 1', '-7 days') as week_start_date,
                SUM(z.zone_1_seconds) as zone_1,
                SUM(z.zone_2_seconds) as zone_2,
                SUM(z.zone_3_seconds) as zone_3,
                SUM(z.zone_4_seconds) as zone_4,
                SUM(z.zone_5_seconds) as zone_5,
                SUM(z.zone_1_seconds + z.zone_2_seconds + z.zone_3_seconds + z.zone_4_seconds + z.zone_5_seconds) as total
            FROM activity_hr_zones z
            INNER JOIN activities a ON z.user_id = a.user_id AND z.activity_id = a.activity_id
            WHERE z.user_id = ?
              AND a.start_date >= ?
            GROUP BY week_start_date
            ORDER BY week_start_date ASC
        """, (user_id, range_start.isoformat()))

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return None

        weeks = []
        for row in rows:
            weeks.append({
                'week_label': row['week_start_date'] if row['week_start_date'] else row['year_week'],
                'zone_1_mins': round(row['zone_1'] / 60, 1),
                'zone_2_mins': round(row['zone_2'] / 60, 1),
                'zone_3_mins': round(row['zone_3'] / 60, 1),
                'zone_4_mins': round(row['zone_4'] / 60, 1),
                'zone_5_mins': round(row['zone_5'] / 60, 1),
                'total_mins': round(row['total'] / 60, 1),
            })

        return weeks

    except Exception as e:
        logger.error(f"Error getting weekly HR zones: {e}")
        return None


def generate_hr_zone_chart(weekly_data):
    """
    Generate a stacked bar chart of weekly HR zone distribution.

    Args:
        weekly_data: List of weekly zone dicts from get_weekly_hr_zones()

    Returns:
        Base64-encoded PNG string, or None
    """
    if not weekly_data:
        return None

    import numpy as np

    labels = []
    for w in weekly_data:
        try:
            dt = datetime.strptime(w['week_label'], '%Y-%m-%d')
            labels.append(dt.strftime('%b %d'))
        except (ValueError, TypeError):
            labels.append(str(w['week_label']))

    z1 = [w['zone_1_mins'] for w in weekly_data]
    z2 = [w['zone_2_mins'] for w in weekly_data]
    z3 = [w['zone_3_mins'] for w in weekly_data]
    z4 = [w['zone_4_mins'] for w in weekly_data]
    z5 = [w['zone_5_mins'] for w in weekly_data]

    x = np.arange(len(labels))
    width = 0.6

    fig, ax = plt.subplots(figsize=(12, 6))

    colors = {
        'Z1': '#9E9E9E',   # Gray
        'Z2': '#2196F3',   # Blue
        'Z3': '#4CAF50',   # Green
        'Z4': '#FF9800',   # Orange
        'Z5': '#F44336',   # Red
    }

    b1 = ax.bar(x, z1, width, label='Zone 1 (Recovery)', color=colors['Z1'])
    b2 = ax.bar(x, z2, width, bottom=z1, label='Zone 2 (Endurance)', color=colors['Z2'])
    bottom3 = [a + b for a, b in zip(z1, z2)]
    b3 = ax.bar(x, z3, width, bottom=bottom3, label='Zone 3 (Tempo)', color=colors['Z3'])
    bottom4 = [a + b for a, b in zip(bottom3, z3)]
    b4 = ax.bar(x, z4, width, bottom=bottom4, label='Zone 4 (Threshold)', color=colors['Z4'])
    bottom5 = [a + b for a, b in zip(bottom4, z4)]
    b5 = ax.bar(x, z5, width, bottom=bottom5, label='Zone 5 (VO2 Max)', color=colors['Z5'])

    ax.set_xlabel('Week Starting')
    ax.set_ylabel('Minutes')
    ax.set_title('Heart Rate Zone Distribution (Last 8 Weeks)')
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha='right')
    ax.legend(loc='upper left', fontsize=8)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    plt.close()
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


def get_club_comparison(user_id):
    """Compare user stats with club averages"""
    try:
        conn = sqlite3.connect('strava_activities.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get user's total distance
        cursor.execute("""
            SELECT SUM(distance) as total_distance, COUNT(*) as activity_count
            FROM activities
            WHERE user_id = ? AND type IN ('Walk', 'Hike', 'Run', 'Ride')
        """, (user_id,))
        user_stats = cursor.fetchone()

        # Get club averages (excluding private users)
        cursor.execute("""
            SELECT
                AVG(total_distance) as avg_distance,
                AVG(activity_count) as avg_activities
            FROM (
                SELECT
                    a.user_id,
                    SUM(a.distance) as total_distance,
                    COUNT(*) as activity_count
                FROM activities a
                INNER JOIN users u ON a.user_id = u.id
                WHERE u.is_active = 1
                  AND u.privacy_level != 'private'
                  AND a.type IN ('Walk', 'Hike', 'Run', 'Ride')
                GROUP BY a.user_id
            )
        """)
        club_stats = cursor.fetchone()

        conn.close()

        if user_stats and club_stats and club_stats['avg_distance']:
            user_distance = user_stats['total_distance'] / 1000 if user_stats['total_distance'] else 0
            avg_distance = club_stats['avg_distance'] / 1000 if club_stats['avg_distance'] else 1

            return {
                'user_distance': round(user_distance, 1),
                'club_avg_distance': round(avg_distance, 1),
                'user_activities': user_stats['activity_count'],
                'club_avg_activities': round(club_stats['avg_activities'], 1) if club_stats['avg_activities'] else 0,
                'distance_percentile': round((user_distance / avg_distance) * 100, 0) if avg_distance > 0 else 100
            }
    except Exception as e:
        logger.error(f"Error calculating club comparison: {e}")
        return None

@app.on_event('startup')
def startup_event():
    init_db()


# ============================================================================
# Authentication Routes
# ============================================================================

@app.get("/auth/login")
async def auth_login(request: Request):
    """
    Initiate OAuth login with Strava
    Redirects to Strava authorization page
    """
    # Generate state for CSRF protection
    state = generate_oauth_state()

    # Get dynamic redirect URI based on request host
    redirect_uri = get_oauth_redirect_uri(request)

    # Build authorization URL with dynamic redirect URI
    params = {
        'client_id': CLIENT_ID,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'approval_prompt': 'auto',
        'scope': 'read,activity:read_all',
        'state': state
    }

    auth_url = f"{STRAVA_AUTHORIZE_URL}?{urlencode(params)}"

    # Create redirect response and set state cookie
    response = RedirectResponse(url=auth_url)
    response.set_cookie(
        key='oauth_state',
        value=state,
        max_age=600,  # 10 minutes
        httponly=True,
        samesite='lax'
    )

    return response


@app.get("/auth/callback")
async def auth_callback(request: Request, response: Response, code: str = None, state: str = None, error: str = None, scope: str = None):
    """
    OAuth callback from Strava
    Exchanges code for tokens and creates user session
    """
    # Check for errors
    if error:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error_title": "Authentication Failed",
            "error_message": f"Strava returned an error: {error}"
        })

    # Verify state (CSRF protection)
    oauth_state = request.cookies.get('oauth_state')
    if not oauth_state or oauth_state != state:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error_title": "Authentication Failed",
            "error_message": "Invalid state parameter. Please try logging in again."
        })

    # Verify required scopes were granted
    granted_scopes = set(scope.split(',')) if scope else set()
    required_scopes = {'read', 'activity:read_all'}

    if not required_scopes.issubset(granted_scopes):
        missing_scopes = required_scopes - granted_scopes
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error_title": "Insufficient Permissions",
            "error_message": (
                f"The app requires permission to read your activities. "
                f"Missing permissions: {', '.join(missing_scopes)}. "
                f"Please try logging in again and accept all requested permissions."
            )
        })

    # Exchange code for tokens
    try:
        token_response = requests.post(
            STRAVA_TOKEN_URL,
            data={
                'client_id': CLIENT_ID,
                'client_secret': CLIENT_SECRET,
                'code': code,
                'grant_type': 'authorization_code'
            }
        )

        if token_response.status_code != 200:
            return templates.TemplateResponse("error.html", {
                "request": request,
                "error_title": "Authentication Failed",
                "error_message": "Failed to exchange authorization code for tokens."
            })

        token_data = token_response.json()

        # Extract tokens and athlete data
        access_token = token_data['access_token']
        refresh_token = token_data['refresh_token']
        expires_at = token_data['expires_at']
        athlete_data = token_data['athlete']

        # Create or update user
        user = create_or_update_user(athlete_data, access_token, refresh_token, expires_at)

        # Trigger immediate activity sync for this user (runs in background)
        # This ensures first-time users don't have to wait for the 15-minute background sync
        scheduler.add_job(
            sync_user_activities,
            args=[user['id']],
            id=f'login_sync_{user["id"]}_{int(time.time())}',
            replace_existing=False
        )
        logger.info(f"Scheduled immediate activity sync for user {user['id']} after login")

        # Create session
        session_id = create_session(user['id'])

        # Set session cookie
        response = RedirectResponse(url="/dashboard")
        create_session_cookie(response, session_id)

        # Clear oauth_state cookie
        response.delete_cookie(key='oauth_state')

        return response

    except Exception as e:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error_title": "Authentication Failed",
            "error_message": f"An error occurred during authentication: {str(e)}"
        })


@app.get("/auth/logout")
async def auth_logout(request: Request, response: Response):
    """Logout user and clear session"""
    # Get session ID from cookie
    session_id = get_session_from_cookie(request)

    if session_id:
        # Delete session from database
        delete_session(session_id)

    # Clear session cookie and redirect to landing page
    response = RedirectResponse(url="/")
    clear_session_cookie(response)

    return response


@app.get("/settings", response_class=HTMLResponse)
async def settings(request: Request, user: dict = Depends(get_current_user)):
    """User settings page"""
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "user": user
    })


@app.post("/settings/privacy")
async def update_privacy(request: Request, user: dict = Depends(get_current_user)):
    """Update user privacy settings"""
    from fastapi import Form

    form_data = await request.form()
    privacy_level = form_data.get('privacy_level', 'club_only')

    # Validate privacy level
    if privacy_level not in ['public', 'club_only', 'private']:
        privacy_level = 'club_only'

    # Update database
    conn = sqlite3.connect('strava_activities.db')
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE users SET privacy_level = ? WHERE id = ?
    """, (privacy_level, user['id']))
    conn.commit()
    conn.close()

    # Redirect back to settings
    return RedirectResponse(url="/settings?updated=true", status_code=303)


@app.post("/sync")
async def manual_sync(request: Request, user: dict = Depends(get_current_user)):
    """
    Manual sync endpoint - triggers immediate activity sync for current user

    Returns:
        Redirects back to dashboard with sync status
    """
    user_id = user['id']
    logger.info(f"Manual sync triggered by user {user_id} ({user['firstname']})")

    try:
        # Trigger sync for this user
        new_count, error = sync_user_activities(user_id)

        if error:
            logger.error(f"Manual sync failed for user {user_id}: {error}")
            return RedirectResponse(
                url=f"/dashboard?sync_error={error}",
                status_code=303
            )

        logger.info(f"Manual sync completed for user {user_id}: {new_count} new activities")
        return RedirectResponse(
            url=f"/dashboard?sync_success={new_count}",
            status_code=303
        )

    except Exception as e:
        logger.error(f"Manual sync exception for user {user_id}: {e}")
        return RedirectResponse(
            url=f"/dashboard?sync_error=Unexpected error during sync",
            status_code=303
        )


# ============================================================================
# Main Application Routes
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """
    Landing page - shows login button for unauthenticated users
    Redirects to dashboard if already authenticated
    """
    # Check if user is already logged in
    user = await get_current_user_optional(request)

    if user:
        # Already logged in, redirect to dashboard
        return RedirectResponse(url="/dashboard")

    # Show landing page with optional club stats from database
    # Only show stats from users with 'public' privacy level
    club_stats = None
    try:
        conn = sqlite3.connect('strava_activities.db')
        cursor = conn.cursor()

        # Get stats from public users only
        cursor.execute("""
            SELECT
                COUNT(DISTINCT a.id) as total_activities,
                SUM(a.distance) as total_distance,
                COUNT(DISTINCT a.user_id) as athlete_count
            FROM activities a
            INNER JOIN users u ON a.user_id = u.id
            WHERE u.is_active = 1
              AND u.privacy_level = 'public'
              AND a.type IN ('Walk', 'Hike', 'Run', 'Ride')
        """)

        row = cursor.fetchone()
        conn.close()

        if row and row[0] > 0:
            club_stats = {
                'total_activities': row[0],
                'total_distance_km': (row[1] / 1000) if row[1] else 0,
                'athlete_count': row[2]
            }
    except Exception:
        pass  # Ignore errors loading club stats for landing page

    return templates.TemplateResponse("landing.html", {
        "request": request,
        "club_stats": club_stats
    })


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user: dict = Depends(get_current_user)):
    """
    Personal dashboard - requires authentication
    Shows user's personal activity statistics and charts

    Note: Activities are synced in the background every 15 minutes.
    This route now only reads from the database for better performance.
    """
    user_id = user['id']

    # Get all activities from database (background sync keeps this updated)
    conn = sqlite3.connect('strava_activities.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute("""
        SELECT * FROM activities
        WHERE user_id = ?
        ORDER BY start_date DESC
    """, (user_id,))

    rows = c.fetchall()
    conn.close()

    # Convert to DataFrame
    if rows:
        df = pd.DataFrame([dict(row) for row in rows])
    else:
        df = pd.DataFrame()
    if df.empty:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error_title": "No Activities Found",
            "error_message": "You don't have any activities yet. Activities are automatically synced from Strava every 15 minutes. If you just connected your account, please wait a few minutes for the initial sync to complete, or use the manual sync button below."
        })
    
    # Process data - filter for tracked activity types
    df = df[df['type'].isin(['Walk', 'Hike', 'Run', 'Ride'])]
    df['start_date'] = pd.to_datetime(df['start_date'])
    df['distance_km'] = df['distance'] / 1000
    df['month'] = df['start_date'].dt.to_period('M').dt.to_timestamp()

    # Get statistics
    stats = get_activity_stats(df)

    # Get recent activities by type
    recent_walks = get_recent_activities_by_type(df, 'Walk')
    recent_hikes = get_recent_activities_by_type(df, 'Hike')
    recent_runs = get_recent_activities_by_type(df, 'Run')
    recent_rides = get_recent_activities_by_type(df, 'Ride')
    
    # Create monthly grouping for charts
    grouped = df.groupby(['month', 'type'])['distance_km'].sum().unstack(fill_value=0)
    
    # Generate combined chart
    plt.figure(figsize=(12,6))
    ax = grouped.plot(kind='bar', stacked=True, ax=plt.gca())
    plt.title('Monthly Distances Walked, Hiked, and Ran')
    plt.xlabel('Month')
    plt.ylabel('Distance (km)')
    
    # Format x-axis labels to show month/year (e.g., "Jan 2024")
    labels = [date.strftime('%b %Y') for date in grouped.index]
    ax.set_xticklabels(labels, rotation=45)
    
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    plt.close()
    buf.seek(0)
    combined_chart = base64.b64encode(buf.read()).decode('utf-8')

    # Generate individual charts
    activity_charts = {}
    colors = {'Walk': '#4CAF50', 'Hike': '#FF9800', 'Run': '#2196F3', 'Ride': '#9C27B0'}

    for activity in ['Walk', 'Hike', 'Run', 'Ride']:
        if activity in grouped.columns and not grouped[activity].empty:
            plt.figure(figsize=(10,5))
            ax = grouped[activity].plot(kind='bar', color=colors[activity], ax=plt.gca())
            plt.title(f'Monthly Distance: {activity}')
            plt.xlabel('Month')
            plt.ylabel('Distance (km)')
            
            # Format x-axis labels to show month/year (e.g., "Jan 2024")
            labels = [date.strftime('%b %Y') for date in grouped.index]
            ax.set_xticklabels(labels, rotation=45)
            
            plt.tight_layout()
            buf2 = io.BytesIO()
            plt.savefig(buf2, format='png', dpi=150, bbox_inches='tight')
            plt.close()
            buf2.seek(0)
            activity_charts[activity] = base64.b64encode(buf2.read()).decode('utf-8')

    # Prepare template context
    def format_activities_for_template(activities_df):
        if activities_df.empty:
            return None
        activities = activities_df.copy()
        activities['start_date_str'] = activities['start_date'].dt.strftime('%Y-%m-%d')
        return activities.to_dict('records')

    # Calculate personal records, weekly progress, and club comparison
    personal_records = get_personal_records(df)
    weekly_progress = get_weekly_progress(df)
    club_comparison = get_club_comparison(user_id)

    # Get HR zone data
    weekly_hr_zones = get_weekly_hr_zones(user_id)
    hr_zone_chart = generate_hr_zone_chart(weekly_hr_zones)

    context = {
        "request": request,
        "user": user,  # Add authenticated user information
        "combined_chart": combined_chart,
        "walk_chart": activity_charts.get('Walk'),
        "hike_chart": activity_charts.get('Hike'),
        "run_chart": activity_charts.get('Run'),
        "ride_chart": activity_charts.get('Ride'),
        "recent_walks": format_activities_for_template(recent_walks),
        "recent_hikes": format_activities_for_template(recent_hikes),
        "recent_runs": format_activities_for_template(recent_runs),
        "recent_rides": format_activities_for_template(recent_rides),
        "personal_records": personal_records,
        "weekly_progress": weekly_progress,
        "club_comparison": club_comparison,
        "hr_zone_chart": hr_zone_chart,
        "weekly_hr_zones": weekly_hr_zones,
        **stats
    }

    return templates.TemplateResponse("dashboard.html", context)

@app.get("/club", response_class=HTMLResponse)
async def club_dashboard(request: Request, user: dict = Depends(get_current_user)):
    """
    Display club-wide activity dashboard.
    Shows aggregated statistics and recent activities from all club members.
    Requires authentication and respects privacy settings.
    """
    if not CLUB_ID:
        return HTMLResponse("<h2>Club ID not configured</h2><p>Please set STRAVA_CLUB_ID in your .env file</p>")

    # Refresh user's token if needed
    user = refresh_user_token(user)

    try:
        # Query database for club members' activities (respecting privacy)
        conn = sqlite3.connect('strava_activities.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get activities from all users who are NOT private
        # Since all viewers are authenticated club members, we show:
        #   - User privacy: 'public' and 'club_only' (not 'private')
        #   - Activity visibility: 'everyone' and 'followers_only' (not 'only_me')
        cursor.execute("""
            SELECT
                a.*,
                u.firstname,
                u.lastname,
                u.profile_picture
            FROM activities a
            INNER JOIN users u ON a.user_id = u.id
            WHERE u.is_active = 1
              AND u.privacy_level != 'private'
              AND a.visibility != 'only_me'
              AND a.type IN ('Walk', 'Hike', 'Run', 'Ride')
            ORDER BY a.start_date DESC
        """)

        activities_rows = cursor.fetchall()
        conn.close()

        if not activities_rows:
            return HTMLResponse(f"""
                <h2>No club activities found</h2>
                <p>Club ID: {CLUB_ID}</p>
                <p>No authenticated club members have shared their activities yet.</p>
                <p>Make sure club members have logged in and set their privacy to "Club Only" or "Public".</p>
            """)

        # Convert to DataFrame for analysis
        activities_data = []
        for row in activities_rows:
            activity_dict = dict(row)
            # Add distance_km for consistency
            activity_dict['distance_km'] = activity_dict['distance'] / 1000 if activity_dict.get('distance') else 0
            # Format athlete name
            activity_dict['athlete.firstname'] = f"{activity_dict['firstname']} {activity_dict['lastname'][0]}." if activity_dict.get('lastname') else activity_dict.get('firstname', 'Unknown')
            activities_data.append(activity_dict)

        df = pd.DataFrame(activities_data)

        if df.empty:
            return HTMLResponse(f"""
                <h2>No Walk/Hike/Run/Ride activities found</h2>
                <p>Club members haven't shared any activities yet.</p>
            """)

        # Calculate statistics
        total_activities = len(df)
        total_distance_km = df['distance'].sum() / 1000 if 'distance' in df.columns else 0

        # Calculate total duration in hours
        total_duration_hours = 0
        if 'moving_time' in df.columns:
            total_duration_hours = round(df['moving_time'].sum() / 3600, 1)
        elif 'elapsed_time' in df.columns:
            total_duration_hours = round(df['elapsed_time'].sum() / 3600, 1)

        # Get athlete counts (names are limited to "FirstName L." format)
        athlete_names = df['athlete.firstname'].unique() if 'athlete.firstname' in df.columns else []
        athlete_count = len(athlete_names)

        # Activity type breakdown
        type_counts = df['type'].value_counts().to_dict() if 'type' in df.columns else {}

        # Individual activity type counts
        walk_count = len(df[df['type'] == 'Walk']) if 'type' in df.columns else 0
        hike_count = len(df[df['type'] == 'Hike']) if 'type' in df.columns else 0
        run_count = len(df[df['type'] == 'Run']) if 'type' in df.columns else 0
        ride_count = len(df[df['type'] == 'Ride']) if 'type' in df.columns else 0

        # Aggregate by athlete for leaderboard
        if 'athlete.firstname' in df.columns and 'distance' in df.columns:
            leaderboard = df.groupby('athlete.firstname').agg({
                'distance': 'sum',
                'type': 'count'
            }).rename(columns={'distance': 'total_distance', 'type': 'activity_count'})
            leaderboard['total_distance_km'] = leaderboard['total_distance'] / 1000
            leaderboard = leaderboard.sort_values('total_distance', ascending=False)
            leaderboard_data = leaderboard.reset_index().to_dict('records')
        else:
            leaderboard_data = []

        # Calculate weekly leaderboard (current week)
        weekly_leaderboard_data = []
        if 'athlete.firstname' in df.columns and 'distance' in df.columns and 'start_date' in df.columns:
            # Get current week boundaries (Monday to Sunday)
            today = datetime.now()
            week_start = today - timedelta(days=today.weekday())  # Monday
            week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
            week_end = week_start + timedelta(days=7)

            # Convert start_date to datetime for filtering
            df['start_date_dt'] = pd.to_datetime(df['start_date'], errors='coerce', utc=True)
            df['start_date_dt'] = df['start_date_dt'].dt.tz_localize(None)  # Remove timezone for comparison

            # Filter to current week
            df_week = df[(df['start_date_dt'] >= week_start) & (df['start_date_dt'] < week_end)]

            if not df_week.empty and 'athlete.firstname' in df_week.columns:
                weekly_leaderboard = df_week.groupby('athlete.firstname').agg({
                    'distance': 'sum',
                    'type': 'count'
                }).rename(columns={'distance': 'total_distance', 'type': 'activity_count'})
                weekly_leaderboard['total_distance_km'] = weekly_leaderboard['total_distance'] / 1000
                weekly_leaderboard = weekly_leaderboard.sort_values('total_distance', ascending=False)
                weekly_leaderboard_data = weekly_leaderboard.reset_index().to_dict('records')

        # Create visualization - Top athletes by distance (weekly)
        if weekly_leaderboard_data:
            plt.figure(figsize=(12, 6))
            # Convert weekly leaderboard data to DataFrame for plotting
            weekly_df = pd.DataFrame(weekly_leaderboard_data[:10])
            weekly_df = weekly_df.set_index('athlete.firstname')

            plt.barh(range(len(weekly_df)), weekly_df['total_distance_km'])
            plt.yticks(range(len(weekly_df)), weekly_df.index)
            plt.xlabel('Total Distance (km)')
            plt.title('Club Leaderboard - Top Athletes by Distance This Week')
            plt.gca().invert_yaxis()
            plt.tight_layout()

            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
            plt.close()
            buf.seek(0)
            leaderboard_chart = base64.b64encode(buf.read()).decode('utf-8')
        else:
            leaderboard_chart = None

        # Activity type distribution
        if type_counts:
            plt.figure(figsize=(10, 6))
            colors = {'Walk': '#4CAF50', 'Hike': '#FF9800', 'Run': '#2196F3'}
            activity_colors = [colors.get(act_type, '#666666') for act_type in type_counts.keys()]
            plt.bar(type_counts.keys(), type_counts.values(), color=activity_colors)
            plt.xlabel('Activity Type')
            plt.ylabel('Number of Activities')
            plt.title('Club Activities by Type')
            plt.tight_layout()

            buf2 = io.BytesIO()
            plt.savefig(buf2, format='png', dpi=150, bbox_inches='tight')
            plt.close()
            buf2.seek(0)
            type_chart = base64.b64encode(buf2.read()).decode('utf-8')
        else:
            type_chart = None

        # Recent activities (limited to 20)
        recent_activities = df.head(20).to_dict('records') if not df.empty else []

        # Get trophy leaderboard and recent winners
        trophy_leaderboard = get_trophy_leaderboard()
        recent_trophy_winners = get_recent_trophy_winners(limit=5)

        # Get kudos leaderboards
        weekly_kudos_leaderboard = get_weekly_kudos_leaderboard()
        alltime_kudos_leaderboard = get_alltime_kudos_leaderboard()
        most_kudos_activity = get_most_kudos_single_activity()

        context = {
            "request": request,
            "user": user,  # Add authenticated user information
            "club_id": CLUB_ID,
            "total_activities": total_activities,
            "total_distance_km": round(total_distance_km, 2),
            "total_duration_hours": total_duration_hours,
            "athlete_count": athlete_count,
            "walk_count": walk_count,
            "hike_count": hike_count,
            "run_count": run_count,
            "ride_count": ride_count,
            "leaderboard": leaderboard_data[:10],  # Top 10
            "weekly_leaderboard": weekly_leaderboard_data[:10],  # Top 10 for the week
            "trophy_leaderboard": trophy_leaderboard,  # All-time trophy winners
            "recent_trophy_winners": recent_trophy_winners,  # Recent 5 weeks
            "weekly_kudos_leaderboard": weekly_kudos_leaderboard,  # Weekly kudos leaders
            "alltime_kudos_leaderboard": alltime_kudos_leaderboard,  # All-time kudos leaders
            "most_kudos_activity": most_kudos_activity,  # Single activity with most kudos
            "leaderboard_chart": leaderboard_chart,
            "type_chart": type_chart,
            "type_counts": type_counts,
            "recent_activities": recent_activities
        }

        return templates.TemplateResponse("club_dashboard.html", context)

    except Exception as e:
        return HTMLResponse(f"""
            <h2>Error fetching club data</h2>
            <p>Error: {str(e)}</p>
            <p>Make sure you're a member of club {CLUB_ID} and have proper API permissions.</p>
        """)

# To run: uvicorn strava_fastapi:app --reload
