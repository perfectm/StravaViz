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

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Load environment variables from .env file
load_dotenv()

# Set your Strava API credentials here
CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("STRAVA_REFRESH_TOKEN")
CLUB_ID = os.getenv("STRAVA_CLUB_ID")

# OAuth configuration
OAUTH_REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8001/auth/callback")

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
        else:
            break
    return activities

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
    stats = {
        'total_activities': len(df),
        'total_distance_km': df['distance_km'].sum(),
        'walk_count': len(df[df['type'] == 'Walk']),
        'hike_count': len(df[df['type'] == 'Hike']),
        'run_count': len(df[df['type'] == 'Run'])
    }
    return stats

@app.on_event('startup')
def startup_event():
    init_db()


# ============================================================================
# Authentication Routes
# ============================================================================

@app.get("/auth/login")
async def auth_login(response: Response):
    """
    Initiate OAuth login with Strava
    Redirects to Strava authorization page
    """
    # Generate state for CSRF protection
    state = generate_oauth_state()

    # Store state in cookie for verification
    response = RedirectResponse(url=STRAVA_AUTHORIZE_URL)
    response.set_cookie(
        key='oauth_state',
        value=state,
        max_age=600,  # 10 minutes
        httponly=True,
        samesite='lax'
    )

    # Build authorization URL
    params = {
        'client_id': CLIENT_ID,
        'redirect_uri': OAUTH_REDIRECT_URI,
        'response_type': 'code',
        'approval_prompt': 'auto',
        'scope': 'read,activity:read_all',
        'state': state
    }

    auth_url = f"{STRAVA_AUTHORIZE_URL}?{urlencode(params)}"

    return RedirectResponse(url=auth_url)


@app.get("/auth/callback")
async def auth_callback(request: Request, response: Response, code: str = None, state: str = None, error: str = None):
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

    # Show landing page with optional club stats
    club_stats = None
    if CLUB_ID:
        try:
            # Fetch basic club stats for landing page
            token = get_access_token()
            club_activities = get_club_activities(token, CLUB_ID, per_page=100)

            if club_activities:
                import pandas as pd
                df = pd.DataFrame(club_activities)

                # Extract athlete names
                if 'athlete' in df.columns:
                    df['athlete.firstname'] = df['athlete'].apply(
                        lambda x: x.get('firstname', 'Unknown') if isinstance(x, dict) else 'Unknown'
                    )

                # Filter to relevant types
                if 'type' in df.columns:
                    df = df[df['type'].isin(['Walk', 'Hike', 'Run'])]

                if not df.empty:
                    club_stats = {
                        'total_activities': len(df),
                        'total_distance_km': df['distance'].sum() / 1000 if 'distance' in df.columns else 0,
                        'athlete_count': len(df['athlete.firstname'].unique()) if 'athlete.firstname' in df.columns else 0
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
    """
    # Refresh user's access token if needed
    user = refresh_user_token(user)
    user_id = user['id']

    # Use user's access token instead of global refresh token
    token = user['access_token']
    # Get latest activity in DB
    conn = sqlite3.connect('strava_activities.db')
    c = conn.cursor()

    # Check if using multi-user schema
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    has_multiuser = c.fetchone() is not None

    if has_multiuser:
        c.execute('SELECT MAX(start_date) FROM activities WHERE user_id = ?', (user_id,))
    else:
        c.execute('SELECT MAX(start_date) FROM activities')

    last_date = c.fetchone()[0]
    conn.close()

    # Download new activities only
    activities = []
    page = 1
    while True:
        acts = get_activities(token, per_page=200, pages=1)
        if not acts:
            break
        # If last_date is set, filter out already downloaded
        if last_date:
            acts = [a for a in acts if a['start_date'] > last_date]
        if not acts:
            break
        activities.extend(acts)
        if len(acts) < 200:
            break
        page += 1
    # Save new and get all
    df = save_and_get_activities(activities, user_id=user_id)
    if df.empty:
        debug_html = f"""
        <h2>No activities found or Strava API error.</h2>
        <h3>Debug Info:</h3>
        <ul>
            <li>Token: {'Set' if token else 'NOT SET'}</li>
            <li>Activities fetched: {len(activities)}</li>
            <li>CLIENT_ID: {'Set' if CLIENT_ID else 'NOT SET'}</li>
            <li>CLIENT_SECRET: {'Set' if CLIENT_SECRET else 'NOT SET'}</li>
            <li>REFRESH_TOKEN: {'Set' if REFRESH_TOKEN else 'NOT SET'}</li>
        </ul>
        <pre>Sample activities: {activities[:2] if activities else 'None'}</pre>
        """
        return HTMLResponse(debug_html)
    
    # Process data
    df = df[df['type'].isin(['Walk', 'Hike', 'Run'])]
    df['start_date'] = pd.to_datetime(df['start_date'])
    df['distance_km'] = df['distance'] / 1000
    df['month'] = df['start_date'].dt.to_period('M').dt.to_timestamp()
    
    # Get statistics
    stats = get_activity_stats(df)
    
    # Get recent activities by type
    recent_walks = get_recent_activities_by_type(df, 'Walk')
    recent_hikes = get_recent_activities_by_type(df, 'Hike')
    recent_runs = get_recent_activities_by_type(df, 'Run')
    
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
    colors = {'Walk': '#4CAF50', 'Hike': '#FF9800', 'Run': '#2196F3'}
    
    for activity in ['Walk', 'Hike', 'Run']:
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
    
    context = {
        "request": request,
        "user": user,  # Add authenticated user information
        "combined_chart": combined_chart,
        "walk_chart": activity_charts.get('Walk'),
        "hike_chart": activity_charts.get('Hike'),
        "run_chart": activity_charts.get('Run'),
        "recent_walks": format_activities_for_template(recent_walks),
        "recent_hikes": format_activities_for_template(recent_hikes),
        "recent_runs": format_activities_for_template(recent_runs),
        **stats
    }

    return templates.TemplateResponse("dashboard.html", context)

@app.get("/club", response_class=HTMLResponse)
async def club_dashboard(request: Request, user: dict = Depends(get_current_user)):
    """
    Display club-wide activity dashboard.
    Shows aggregated statistics and recent activities from all club members.
    Requires authentication.
    """
    if not CLUB_ID:
        return HTMLResponse("<h2>Club ID not configured</h2><p>Please set STRAVA_CLUB_ID in your .env file</p>")

    # Refresh user's token if needed
    user = refresh_user_token(user)
    token = user['access_token']

    try:
        # Fetch club activities (limited data from API)
        club_activities = get_club_activities(token, CLUB_ID)

        if not club_activities:
            return HTMLResponse(f"""
                <h2>No club activities found</h2>
                <p>Club ID: {CLUB_ID}</p>
                <p>Make sure you're a member of this club and it has recent activities.</p>
            """)

        # Convert to DataFrame for analysis
        df = pd.DataFrame(club_activities)

        # Extract athlete firstname from nested dict
        if 'athlete' in df.columns:
            df['athlete.firstname'] = df['athlete'].apply(lambda x: x.get('firstname', 'Unknown') if isinstance(x, dict) else 'Unknown')

        # Filter for Walk/Hike/Run activities if type field exists
        if 'type' in df.columns:
            df = df[df['type'].isin(['Walk', 'Hike', 'Run'])]

        if df.empty:
            return HTMLResponse(f"""
                <h2>No Walk/Hike/Run activities found</h2>
                <p>Club has activities but none are Walk, Hike, or Run types.</p>
            """)

        # Calculate statistics
        total_activities = len(df)
        total_distance_km = df['distance'].sum() / 1000 if 'distance' in df.columns else 0

        # Get athlete counts (names are limited to "FirstName L." format)
        athlete_names = df['athlete.firstname'].unique() if 'athlete.firstname' in df.columns else []
        athlete_count = len(athlete_names)

        # Activity type breakdown
        type_counts = df['type'].value_counts().to_dict() if 'type' in df.columns else {}

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
        if 'athlete.firstname' in df.columns and 'distance' in df.columns:
            # Get current week boundaries (Monday to Sunday)
            today = datetime.now()
            week_start = today - timedelta(days=today.weekday())  # Monday
            week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)

            # Try to filter by date if available in the data
            # The club API may have limited date info, so we'll work with what's available
            df_week = df.copy()

            # Check if there's any date field we can use
            # Common fields: 'start_date', 'start_date_local', or activities might be ordered by recency
            # Since API has limitations, we'll use the first 50 activities as a proxy for "recent/this week"
            # as the API returns activities in reverse chronological order
            df_week = df.head(50)  # Most recent activities as proxy for current week

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

        context = {
            "request": request,
            "user": user,  # Add authenticated user information
            "club_id": CLUB_ID,
            "total_activities": total_activities,
            "total_distance_km": round(total_distance_km, 2),
            "athlete_count": athlete_count,
            "leaderboard": leaderboard_data[:10],  # Top 10
            "weekly_leaderboard": weekly_leaderboard_data[:10],  # Top 10 for the week
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
