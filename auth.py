"""
Authentication and Session Management

This module provides utilities for OAuth authentication, session management,
and user access control for the multi-user Strava dashboard.
"""

import os
import sqlite3
import uuid
import time
from datetime import datetime, timedelta
from typing import Optional
from fastapi import Request, HTTPException, Response
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature


# Load environment variables
OAUTH_REDIRECT_URI = os.getenv('OAUTH_REDIRECT_URI', 'http://localhost:8002/auth/callback')
SESSION_SECRET = os.getenv('SESSION_SECRET', 'change_this_to_a_random_secret_in_production')
COOKIE_SECURE = os.getenv('COOKIE_SECURE', 'false').lower() == 'true'
COOKIE_NAME = 'strava_session'

# Session cookie serializer
serializer = URLSafeTimedSerializer(SESSION_SECRET)


# Database helper functions

def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect('strava_activities.db')
    conn.row_factory = sqlite3.Row
    return conn


def get_user_by_id(user_id: int) -> Optional[dict]:
    """Get user by ID"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM users WHERE id = ? AND is_active = 1
    """, (user_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        return dict(row)
    return None


def get_user_by_strava_id(strava_athlete_id: int) -> Optional[dict]:
    """Get user by Strava athlete ID"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM users WHERE strava_athlete_id = ?
    """, (strava_athlete_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        return dict(row)
    return None


def create_or_update_user(athlete_data: dict, access_token: str, refresh_token: str, expires_at: int) -> dict:
    """
    Create or update user from Strava athlete data

    Args:
        athlete_data: Athlete data from Strava API
        access_token: OAuth access token
        refresh_token: OAuth refresh token
        expires_at: Token expiration timestamp

    Returns:
        User dict
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    strava_athlete_id = athlete_data['id']
    firstname = athlete_data.get('firstname', '')
    lastname = athlete_data.get('lastname', '')
    profile_picture = athlete_data.get('profile', '')

    # Check if user exists
    existing_user = get_user_by_strava_id(strava_athlete_id)

    if existing_user:
        # Update existing user
        cursor.execute("""
            UPDATE users
            SET firstname = ?, lastname = ?, profile_picture = ?,
                access_token = ?, refresh_token = ?, token_expires_at = ?,
                last_login = CURRENT_TIMESTAMP
            WHERE strava_athlete_id = ?
        """, (firstname, lastname, profile_picture, access_token,
              refresh_token, expires_at, strava_athlete_id))
        user_id = existing_user['id']
    else:
        # Create new user
        cursor.execute("""
            INSERT INTO users (
                strava_athlete_id, firstname, lastname, profile_picture,
                access_token, refresh_token, token_expires_at,
                privacy_level, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'club_only', 1)
        """, (strava_athlete_id, firstname, lastname, profile_picture,
              access_token, refresh_token, expires_at))
        user_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return get_user_by_id(user_id)


# Session management functions

def create_session(user_id: int) -> str:
    """
    Create a new session for user

    Args:
        user_id: User ID

    Returns:
        Session ID
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    session_id = str(uuid.uuid4())
    expires_at = datetime.now() + timedelta(days=30)  # 30-day sessions

    cursor.execute("""
        INSERT INTO sessions (id, user_id, expires_at)
        VALUES (?, ?, ?)
    """, (session_id, user_id, expires_at))

    conn.commit()
    conn.close()

    return session_id


def get_session(session_id: str) -> Optional[dict]:
    """
    Get session by ID

    Args:
        session_id: Session ID

    Returns:
        Session dict or None if not found/expired
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM sessions
        WHERE id = ? AND expires_at > datetime('now')
    """, (session_id,))

    row = cursor.fetchone()
    conn.close()

    if row:
        return dict(row)
    return None


def delete_session(session_id: str):
    """Delete session by ID"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    conn.commit()
    conn.close()


def cleanup_expired_sessions():
    """Remove expired sessions from database"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM sessions WHERE expires_at < datetime('now')")
    deleted = cursor.rowcount

    conn.commit()
    conn.close()

    return deleted


# Cookie management

def create_session_cookie(response: Response, session_id: str):
    """
    Create secure session cookie

    Args:
        response: FastAPI Response object
        session_id: Session ID
    """
    # Sign the session ID
    signed_value = serializer.dumps(session_id)

    response.set_cookie(
        key=COOKIE_NAME,
        value=signed_value,
        max_age=30 * 24 * 60 * 60,  # 30 days
        httponly=True,
        secure=COOKIE_SECURE,
        samesite='lax'
    )


def get_session_from_cookie(request: Request) -> Optional[str]:
    """
    Extract and verify session ID from cookie

    Args:
        request: FastAPI Request object

    Returns:
        Session ID or None
    """
    signed_value = request.cookies.get(COOKIE_NAME)

    if not signed_value:
        return None

    try:
        # Verify and extract session ID
        session_id = serializer.loads(signed_value, max_age=30 * 24 * 60 * 60)
        return session_id
    except (BadSignature, Exception):
        return None


def clear_session_cookie(response: Response):
    """Clear session cookie"""
    response.delete_cookie(key=COOKIE_NAME)


# Authentication dependencies

async def get_current_user(request: Request) -> dict:
    """
    Dependency to get currently authenticated user

    Args:
        request: FastAPI Request object

    Returns:
        User dict

    Raises:
        HTTPException: If not authenticated
    """
    session_id = get_session_from_cookie(request)

    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    session = get_session(session_id)

    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    user = get_user_by_id(session['user_id'])

    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return user


async def get_current_user_optional(request: Request) -> Optional[dict]:
    """
    Optional authentication - returns user if logged in, None otherwise

    Args:
        request: FastAPI Request object

    Returns:
        User dict or None
    """
    try:
        return await get_current_user(request)
    except HTTPException:
        return None


# Token refresh

def refresh_user_token(user: dict) -> dict:
    """
    Refresh Strava access token if expired

    Args:
        user: User dict

    Returns:
        Updated user dict with fresh tokens
    """
    import requests

    # Check if token is expired or about to expire (within 5 minutes)
    if user['token_expires_at'] > int(time.time()) + 300:
        return user  # Token still valid

    # Refresh the token
    CLIENT_ID = os.getenv('STRAVA_CLIENT_ID')
    CLIENT_SECRET = os.getenv('STRAVA_CLIENT_SECRET')

    response = requests.post(
        'https://www.strava.com/oauth/token',
        data={
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'grant_type': 'refresh_token',
            'refresh_token': user['refresh_token']
        }
    )

    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Failed to refresh token")

    token_data = response.json()

    # Update user with new tokens
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE users
        SET access_token = ?, refresh_token = ?, token_expires_at = ?
        WHERE id = ?
    """, (token_data['access_token'], token_data['refresh_token'],
          token_data['expires_at'], user['id']))

    conn.commit()
    conn.close()

    # Return updated user
    return get_user_by_id(user['id'])


# OAuth state management

def generate_oauth_state() -> str:
    """Generate random state for OAuth CSRF protection"""
    return str(uuid.uuid4())


def verify_oauth_state(request: Request, state: str) -> bool:
    """Verify OAuth state matches session state"""
    session_state = request.cookies.get('oauth_state')
    return session_state == state
