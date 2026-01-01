"""
Activity Synchronization Service

This module handles background synchronization of Strava activities for all users.
It fetches new activities incrementally to minimize API calls and avoid rate limits.
"""

import os
import sqlite3
import requests
import time
from datetime import datetime, timedelta
from typing import Optional, Tuple, List
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Strava API URLs
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"

# Load environment
CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")


def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect('strava_activities.db')
    conn.row_factory = sqlite3.Row
    return conn


def refresh_user_token_if_needed(user: dict) -> Tuple[Optional[dict], Optional[str]]:
    """
    Refresh user's access token if expired

    Args:
        user: User dict from database

    Returns:
        Tuple of (updated_user_dict, error_message)
    """
    # Check if token is expired or about to expire (within 5 minutes)
    if user['token_expires_at'] > int(time.time()) + 300:
        return user, None  # Token still valid

    logger.info(f"Refreshing token for user {user['id']} ({user['firstname']})")

    try:
        response = requests.post(
            STRAVA_TOKEN_URL,
            data={
                'client_id': CLIENT_ID,
                'client_secret': CLIENT_SECRET,
                'grant_type': 'refresh_token',
                'refresh_token': user['refresh_token']
            },
            timeout=10
        )

        if response.status_code != 200:
            return None, f"Token refresh failed: {response.status_code}"

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

        # Fetch updated user
        cursor.execute("SELECT * FROM users WHERE id = ?", (user['id'],))
        updated_user = dict(cursor.fetchone())

        conn.close()

        return updated_user, None

    except Exception as e:
        logger.error(f"Error refreshing token for user {user['id']}: {e}")
        return None, str(e)


def get_last_activity_date(user_id: int) -> Optional[datetime]:
    """
    Get the date of the most recent activity for a user

    Args:
        user_id: User ID

    Returns:
        Datetime of last activity or None
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT MAX(start_date) as last_date
        FROM activities
        WHERE user_id = ?
    """, (user_id,))

    row = cursor.fetchone()
    conn.close()

    if row and row['last_date']:
        # Parse the datetime string
        try:
            return datetime.fromisoformat(row['last_date'].replace('Z', '+00:00'))
        except:
            return None

    return None


def fetch_activities_since(access_token: str, after_date: Optional[datetime] = None, per_page: int = 50) -> Tuple[List[dict], Optional[str]]:
    """
    Fetch activities from Strava API since a specific date

    Args:
        access_token: Strava access token
        after_date: Fetch activities after this date (None = fetch all recent)
        per_page: Number of activities per page

    Returns:
        Tuple of (activities_list, error_message)
    """
    activities = []
    page = 1
    max_pages = 10  # Safety limit to prevent infinite loops

    params = {'per_page': per_page, 'page': page}

    # Add after parameter if we have a date
    if after_date:
        # Convert to Unix timestamp
        params['after'] = int(after_date.timestamp())

    while page <= max_pages:
        try:
            response = requests.get(
                STRAVA_ACTIVITIES_URL,
                headers={'Authorization': f'Bearer {access_token}'},
                params=params,
                timeout=10
            )

            if response.status_code == 429:
                return None, "Strava API rate limit exceeded. Will retry on next sync."
            elif response.status_code == 401:
                return None, "Authentication failed. Token may be invalid."
            elif response.status_code != 200:
                return None, f"API error: {response.status_code}"

            page_activities = response.json()

            if not page_activities:
                break

            activities.extend(page_activities)

            # If we got less than per_page, we've reached the end
            if len(page_activities) < per_page:
                break

            page += 1
            params['page'] = page

        except requests.exceptions.Timeout:
            logger.error("Request timeout while fetching activities")
            break
        except Exception as e:
            logger.error(f"Error fetching activities: {e}")
            return None, str(e)

    return activities, None


def save_activities(user_id: int, activities: List[dict]) -> int:
    """
    Save activities to database

    Args:
        user_id: User ID
        activities: List of activity dicts from Strava API

    Returns:
        Number of new activities saved
    """
    if not activities:
        return 0

    conn = get_db_connection()
    cursor = conn.cursor()

    new_count = 0

    for activity in activities:
        try:
            # Extract relevant fields
            activity_id = activity.get('id')
            name = activity.get('name', '')
            activity_type = activity.get('type', '')
            start_date = activity.get('start_date', '')
            distance = activity.get('distance', 0)
            moving_time = activity.get('moving_time', 0)
            elapsed_time = activity.get('elapsed_time', 0)
            total_elevation_gain = activity.get('total_elevation_gain', 0)
            average_speed = activity.get('average_speed', 0)
            max_speed = activity.get('max_speed', 0)
            average_heartrate = activity.get('average_heartrate')
            max_heartrate = activity.get('max_heartrate')
            calories = activity.get('calories')

            # Insert or ignore (skip duplicates)
            cursor.execute("""
                INSERT OR IGNORE INTO activities (
                    user_id, activity_id, name, type, start_date, distance,
                    moving_time, elapsed_time, total_elevation_gain,
                    average_speed, max_speed, average_heartrate,
                    max_heartrate, calories
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, activity_id, name, activity_type, start_date, distance,
                  moving_time, elapsed_time, total_elevation_gain,
                  average_speed, max_speed, average_heartrate,
                  max_heartrate, calories))

            if cursor.rowcount > 0:
                new_count += 1

        except Exception as e:
            logger.error(f"Error saving activity {activity.get('id')}: {e}")
            continue

    conn.commit()
    conn.close()

    return new_count


def sync_user_activities(user_id: int) -> Tuple[int, Optional[str]]:
    """
    Synchronize activities for a specific user

    Args:
        user_id: User ID to sync

    Returns:
        Tuple of (new_activities_count, error_message)
    """
    logger.info(f"Starting sync for user {user_id}")

    # Get user from database
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ? AND is_active = 1", (user_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return 0, "User not found or inactive"

    user = dict(row)

    # Refresh token if needed
    user, error = refresh_user_token_if_needed(user)
    if error:
        return 0, error

    # Get last activity date for incremental sync
    last_date = get_last_activity_date(user_id)

    if last_date:
        logger.info(f"User {user_id}: Last activity date is {last_date}, fetching newer activities")
    else:
        logger.info(f"User {user_id}: No activities yet, fetching all recent activities")

    # Fetch new activities
    activities, error = fetch_activities_since(user['access_token'], last_date)

    if error:
        return 0, error

    if not activities:
        logger.info(f"User {user_id}: No new activities found")
        return 0, None

    # Save activities
    new_count = save_activities(user_id, activities)

    logger.info(f"User {user_id}: Saved {new_count} new activities (fetched {len(activities)})")

    return new_count, None


def sync_all_users() -> dict:
    """
    Synchronize activities for all active users

    Returns:
        Dict with sync statistics
    """
    logger.info("Starting sync for all users")

    # Get all active users
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, firstname, lastname FROM users WHERE is_active = 1")
    users = cursor.fetchall()
    conn.close()

    stats = {
        'total_users': len(users),
        'successful': 0,
        'failed': 0,
        'new_activities': 0,
        'errors': []
    }

    for user_row in users:
        user_id = user_row['id']
        user_name = f"{user_row['firstname']} {user_row['lastname']}"

        try:
            new_count, error = sync_user_activities(user_id)

            if error:
                stats['failed'] += 1
                stats['errors'].append(f"User {user_name} ({user_id}): {error}")
                logger.error(f"Sync failed for user {user_name}: {error}")
            else:
                stats['successful'] += 1
                stats['new_activities'] += new_count
                logger.info(f"Sync successful for user {user_name}: {new_count} new activities")

        except Exception as e:
            stats['failed'] += 1
            stats['errors'].append(f"User {user_name} ({user_id}): {str(e)}")
            logger.error(f"Unexpected error syncing user {user_name}: {e}")

    logger.info(f"Sync complete: {stats['successful']}/{stats['total_users']} users successful, {stats['new_activities']} new activities")

    return stats


def calculate_weekly_trophies() -> dict:
    """
    Calculate weekly distance champions and award trophies

    This function:
    1. Finds all weeks that need trophy calculation
    2. For each week, determines the athlete(s) with the longest total distance
    3. Stores the results in the weekly_trophies table

    Returns:
        Dict with calculation statistics
    """
    logger.info("Starting weekly trophy calculation")

    conn = get_db_connection()
    cursor = conn.cursor()

    stats = {
        'weeks_processed': 0,
        'trophies_awarded': 0,
        'weeks_skipped': 0,
        'errors': []
    }

    try:
        # Get the date range of all activities
        cursor.execute("""
            SELECT MIN(start_date) as first_activity, MAX(start_date) as last_activity
            FROM activities
        """)
        row = cursor.fetchone()

        if not row or not row['first_activity']:
            logger.info("No activities found for trophy calculation")
            return stats

        first_activity = datetime.fromisoformat(row['first_activity'].replace('Z', '+00:00'))
        last_activity = datetime.fromisoformat(row['last_activity'].replace('Z', '+00:00'))

        # Calculate weekly winners from first activity to now
        current_week_start = first_activity - timedelta(days=first_activity.weekday())  # Start of week (Monday)
        current_week_start = current_week_start.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)

        # Use timezone-naive datetime for comparison
        now = datetime.now()

        while current_week_start <= now:
            week_end = current_week_start + timedelta(days=7)

            # Check if this week already has trophies calculated
            cursor.execute("""
                SELECT COUNT(*) as count FROM weekly_trophies
                WHERE week_start = ?
            """, (current_week_start.date(),))

            existing = cursor.fetchone()['count']

            # Skip if week is current week (not complete yet) or if already calculated
            if week_end > now:
                stats['weeks_skipped'] += 1
                current_week_start = week_end
                continue

            if existing > 0:
                stats['weeks_skipped'] += 1
                current_week_start = week_end
                continue

            # Calculate weekly totals for each user
            cursor.execute("""
                SELECT
                    user_id,
                    SUM(distance) as total_distance,
                    COUNT(*) as activity_count
                FROM activities
                WHERE start_date >= ? AND start_date < ?
                AND type IN ('Walk', 'Hike', 'Run', 'Ride')
                GROUP BY user_id
                HAVING total_distance > 0
                ORDER BY total_distance DESC
            """, (current_week_start.isoformat(), week_end.isoformat()))

            weekly_results = cursor.fetchall()

            if not weekly_results:
                # No activities this week
                stats['weeks_skipped'] += 1
                current_week_start = week_end
                continue

            # Get the winning distance
            max_distance = weekly_results[0]['total_distance']

            # Award trophy to all users who achieved max distance (handles ties)
            for result in weekly_results:
                if result['total_distance'] >= max_distance:
                    try:
                        cursor.execute("""
                            INSERT INTO weekly_trophies
                            (user_id, week_start, week_end, total_distance, activity_count)
                            VALUES (?, ?, ?, ?, ?)
                        """, (
                            result['user_id'],
                            current_week_start.date(),
                            week_end.date(),
                            result['total_distance'],
                            result['activity_count']
                        ))
                        stats['trophies_awarded'] += 1
                        logger.info(f"Trophy awarded for week {current_week_start.date()}: User {result['user_id']} - {result['total_distance']/1000:.2f}km")
                    except Exception as e:
                        logger.error(f"Error awarding trophy: {e}")
                        stats['errors'].append(str(e))
                else:
                    break  # Stop after winners (already sorted by distance DESC)

            stats['weeks_processed'] += 1
            current_week_start = week_end

        conn.commit()

    except Exception as e:
        logger.error(f"Error in trophy calculation: {e}")
        stats['errors'].append(str(e))
        conn.rollback()
    finally:
        conn.close()

    logger.info(f"Trophy calculation complete: {stats['weeks_processed']} weeks processed, {stats['trophies_awarded']} trophies awarded")
    return stats


def get_trophy_leaderboard() -> list:
    """
    Get the all-time trophy leaderboard (respects privacy settings)

    Returns:
        List of dicts with user info and trophy counts
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            u.id,
            u.firstname,
            u.lastname,
            u.profile_picture,
            COUNT(wt.id) as trophy_count,
            SUM(wt.total_distance) as total_winning_distance,
            MIN(wt.week_start) as first_trophy,
            MAX(wt.week_start) as latest_trophy
        FROM users u
        INNER JOIN weekly_trophies wt ON u.id = wt.user_id
        WHERE u.is_active = 1 AND u.privacy_level != 'private'
        GROUP BY u.id
        ORDER BY trophy_count DESC, total_winning_distance DESC
    """)

    leaderboard = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return leaderboard


def get_recent_trophy_winners(limit: int = 10) -> list:
    """
    Get recent weekly trophy winners (respects privacy settings)

    Args:
        limit: Number of recent winners to return

    Returns:
        List of dicts with trophy winner info
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            wt.week_start,
            wt.week_end,
            wt.total_distance,
            wt.activity_count,
            u.firstname,
            u.lastname,
            u.profile_picture
        FROM weekly_trophies wt
        INNER JOIN users u ON wt.user_id = u.id
        WHERE u.is_active = 1 AND u.privacy_level != 'private'
        ORDER BY wt.week_start DESC
        LIMIT ?
    """, (limit,))

    winners = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return winners


if __name__ == "__main__":
    # For testing: sync all users
    from dotenv import load_dotenv
    load_dotenv()

    stats = sync_all_users()
    print(f"\nSync Statistics:")
    print(f"Total users: {stats['total_users']}")
    print(f"Successful: {stats['successful']}")
    print(f"Failed: {stats['failed']}")
    print(f"New activities: {stats['new_activities']}")

    if stats['errors']:
        print(f"\nErrors:")
        for error in stats['errors']:
            print(f"  - {error}")
