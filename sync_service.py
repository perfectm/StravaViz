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
STRAVA_ACTIVITY_ZONES_URL = "https://www.strava.com/api/v3/activities/{activity_id}/zones"
STRAVA_ACTIVITY_DETAIL_URL = "https://www.strava.com/api/v3/activities/{activity_id}"

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
            kudos_count = activity.get('kudos_count', 0)
            visibility = activity.get('visibility', 'everyone')  # everyone, only_me, followers_only

            # Extract GPS coordinates from start_latlng
            start_latlng = activity.get('start_latlng')
            start_lat = start_latlng[0] if start_latlng and len(start_latlng) >= 2 else None
            start_lng = start_latlng[1] if start_latlng and len(start_latlng) >= 2 else None

            # Check if activity exists
            cursor.execute("""
                SELECT id FROM activities WHERE user_id = ? AND activity_id = ?
            """, (user_id, activity_id))
            existing = cursor.fetchone()

            if existing:
                # Update existing activity (visibility, kudos, coords may have changed)
                cursor.execute("""
                    UPDATE activities
                    SET kudos_count = ?, visibility = ?, start_lat = COALESCE(?, start_lat), start_lng = COALESCE(?, start_lng)
                    WHERE user_id = ? AND activity_id = ?
                """, (kudos_count, visibility, start_lat, start_lng, user_id, activity_id))
            else:
                # Insert new activity
                cursor.execute("""
                    INSERT INTO activities (
                        user_id, activity_id, name, type, start_date, distance,
                        moving_time, elapsed_time, total_elevation_gain,
                        average_speed, max_speed, average_heartrate,
                        max_heartrate, calories, kudos_count, visibility,
                        start_lat, start_lng
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (user_id, activity_id, name, activity_type, start_date, distance,
                      moving_time, elapsed_time, total_elevation_gain,
                      average_speed, max_speed, average_heartrate,
                      max_heartrate, calories, kudos_count, visibility,
                      start_lat, start_lng))
                new_count += 1

        except Exception as e:
            logger.error(f"Error saving activity {activity.get('id')}: {e}")
            continue

    conn.commit()
    conn.close()

    return new_count


def fetch_activity_zones(access_token: str, activity_id: int) -> Optional[dict]:
    """
    Fetch HR zone distribution for a specific activity from Strava API.

    Args:
        access_token: Strava access token
        activity_id: Strava activity ID

    Returns:
        Dict with zone seconds (zone_1 through zone_5) or None on failure
    """
    try:
        response = requests.get(
            STRAVA_ACTIVITY_ZONES_URL.format(activity_id=activity_id),
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=10
        )

        if response.status_code == 429:
            logger.warning("Rate limit hit while fetching activity zones")
            return None
        elif response.status_code != 200:
            logger.debug(f"Failed to fetch zones for activity {activity_id}: {response.status_code}")
            return None

        zones_data = response.json()

        # Find the heartrate zone distribution
        for zone_info in zones_data:
            if zone_info.get('type') == 'heartrate':
                buckets = zone_info.get('distribution_buckets', [])
                zone_seconds = {}
                for i, bucket in enumerate(buckets[:5], start=1):
                    zone_seconds[f'zone_{i}_seconds'] = bucket.get('time', 0)
                return zone_seconds

        return None

    except Exception as e:
        logger.debug(f"Error fetching zones for activity {activity_id}: {e}")
        return None


def sync_hr_zones_for_user(user_id: int, access_token: str, limit: int = 20) -> int:
    """
    Fetch and store HR zone data for activities that have heart rate data
    but no zone data yet.

    Args:
        user_id: User ID
        access_token: Strava access token
        limit: Max number of zone fetches per sync cycle (rate limit protection)

    Returns:
        Number of activities with zones fetched
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Find activities with heart rate data that don't have zone data yet
    cursor.execute("""
        SELECT a.activity_id
        FROM activities a
        LEFT JOIN activity_hr_zones z ON a.user_id = z.user_id AND a.activity_id = z.activity_id
        WHERE a.user_id = ?
          AND a.average_heartrate IS NOT NULL
          AND z.id IS NULL
        ORDER BY a.start_date DESC
        LIMIT ?
    """, (user_id, limit))

    activities_needing_zones = [row['activity_id'] for row in cursor.fetchall()]
    conn.close()

    if not activities_needing_zones:
        return 0

    fetched_count = 0
    for activity_id in activities_needing_zones:
        zone_data = fetch_activity_zones(access_token, activity_id)
        if zone_data is None:
            continue

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO activity_hr_zones
                (user_id, activity_id, zone_1_seconds, zone_2_seconds,
                 zone_3_seconds, zone_4_seconds, zone_5_seconds, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id, activity_id,
                zone_data.get('zone_1_seconds', 0),
                zone_data.get('zone_2_seconds', 0),
                zone_data.get('zone_3_seconds', 0),
                zone_data.get('zone_4_seconds', 0),
                zone_data.get('zone_5_seconds', 0),
                datetime.now().isoformat()
            ))
            conn.commit()
            fetched_count += 1
        except Exception as e:
            logger.error(f"Error saving HR zones for activity {activity_id}: {e}")
        finally:
            conn.close()

        # Small delay between API calls
        time.sleep(0.3)

    return fetched_count


def fetch_activity_segments(access_token: str, activity_id: int) -> Optional[list]:
    """
    Fetch segment efforts for a specific activity from Strava API.

    Args:
        access_token: Strava access token
        activity_id: Strava activity ID

    Returns:
        List of segment effort dicts, or None on failure
    """
    try:
        response = requests.get(
            STRAVA_ACTIVITY_DETAIL_URL.format(activity_id=activity_id),
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=10
        )

        if response.status_code == 429:
            logger.warning("Rate limit hit while fetching activity detail for segments")
            return None
        elif response.status_code != 200:
            logger.debug(f"Failed to fetch detail for activity {activity_id}: {response.status_code}")
            return None

        activity_data = response.json()
        return activity_data.get('segment_efforts', [])

    except Exception as e:
        logger.debug(f"Error fetching segments for activity {activity_id}: {e}")
        return None


def sync_segments_for_user(user_id: int, access_token: str, limit: int = 10) -> int:
    """
    Fetch and store segment data for activities that haven't been processed yet.

    Args:
        user_id: User ID
        access_token: Strava access token
        limit: Max number of activity detail fetches per sync cycle

    Returns:
        Number of activities processed for segments
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Find activities where segments_fetched = 0
    cursor.execute("""
        SELECT activity_id
        FROM activities
        WHERE user_id = ?
          AND segments_fetched = 0
        ORDER BY start_date DESC
        LIMIT ?
    """, (user_id, limit))

    activities_needing_segments = [row['activity_id'] for row in cursor.fetchall()]
    conn.close()

    if not activities_needing_segments:
        return 0

    processed_count = 0
    for activity_id in activities_needing_segments:
        segment_efforts = fetch_activity_segments(access_token, activity_id)

        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            if segment_efforts is None:
                # API failure - don't mark as fetched, retry next cycle
                conn.close()
                continue

            # Process each segment effort
            for effort in segment_efforts:
                segment = effort.get('segment', {})
                strava_segment_id = segment.get('id')
                if not strava_segment_id:
                    continue

                # Upsert segment master data
                cursor.execute("""
                    INSERT OR REPLACE INTO segments
                    (strava_segment_id, name, distance, average_grade, maximum_grade,
                     city, state, climb_category)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    strava_segment_id,
                    segment.get('name', ''),
                    segment.get('distance'),
                    segment.get('average_grade'),
                    segment.get('maximum_grade'),
                    segment.get('city'),
                    segment.get('state'),
                    segment.get('climb_category', 0),
                ))

                # Insert effort record
                strava_effort_id = effort.get('id')
                if strava_effort_id:
                    cursor.execute("""
                        INSERT OR IGNORE INTO segment_efforts
                        (user_id, activity_id, strava_segment_id, strava_effort_id,
                         elapsed_time, moving_time, start_date, pr_rank, kom_rank,
                         average_heartrate, max_heartrate, fetched_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        user_id,
                        activity_id,
                        strava_segment_id,
                        strava_effort_id,
                        effort.get('elapsed_time'),
                        effort.get('moving_time'),
                        effort.get('start_date'),
                        effort.get('pr_rank'),
                        effort.get('kom_rank'),
                        effort.get('average_heartrate'),
                        effort.get('max_heartrate'),
                        datetime.now().isoformat(),
                    ))

            # Mark activity as processed regardless of segment count
            cursor.execute("""
                UPDATE activities SET segments_fetched = 1
                WHERE user_id = ? AND activity_id = ?
            """, (user_id, activity_id))

            conn.commit()
            processed_count += 1

        except Exception as e:
            logger.error(f"Error saving segments for activity {activity_id}: {e}")
        finally:
            conn.close()

        # Small delay between API calls
        time.sleep(0.3)

    return processed_count


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

    # Fetch HR zones for activities with heart rate data (if table exists)
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='activity_hr_zones'")
        has_zones_table = cursor.fetchone() is not None
        conn.close()

        if has_zones_table:
            zones_count = sync_hr_zones_for_user(user_id, user['access_token'])
            if zones_count > 0:
                logger.info(f"User {user_id}: Fetched HR zones for {zones_count} activities")
    except Exception as e:
        logger.error(f"User {user_id}: Error syncing HR zones: {e}")

    # Fetch segments for activities that haven't been processed yet (if tables exist)
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='segments'")
        has_segments_table = cursor.fetchone() is not None
        conn.close()

        if has_segments_table:
            segments_count = sync_segments_for_user(user_id, user['access_token'])
            if segments_count > 0:
                logger.info(f"User {user_id}: Processed segments for {segments_count} activities")
    except Exception as e:
        logger.error(f"User {user_id}: Error syncing segments: {e}")

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
        # Trophy calculation starts from the beginning of 2026
        # (First Monday of 2026: January 5, 2026)
        TROPHY_START_DATE = datetime(2026, 1, 5, 0, 0, 0)

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

        # Calculate weekly winners from first activity to now, but not before TROPHY_START_DATE
        first_week_start = first_activity - timedelta(days=first_activity.weekday())  # Start of week (Monday)
        first_week_start = first_week_start.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)

        # Start from the later of: first activity week or trophy start date
        current_week_start = max(first_week_start, TROPHY_START_DATE)

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

            # Calculate weekly totals for each user (excluding private activities)
            cursor.execute("""
                SELECT
                    user_id,
                    SUM(distance) as total_distance,
                    COUNT(*) as activity_count
                FROM activities
                WHERE start_date >= ? AND start_date < ?
                AND type IN ('Walk', 'Hike', 'Run', 'Ride')
                AND visibility != 'only_me'
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
    Only counts trophies from 2026 onwards

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
        WHERE u.is_active = 1
          AND u.privacy_level != 'private'
          AND wt.week_start >= '2026-01-05'
        GROUP BY u.id
        ORDER BY trophy_count DESC, total_winning_distance DESC
    """)

    leaderboard = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return leaderboard


def get_recent_trophy_winners(limit: int = 10) -> list:
    """
    Get recent weekly trophy winners (respects privacy settings)
    Only shows trophies from 2026 onwards

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
        WHERE u.is_active = 1
          AND u.privacy_level != 'private'
          AND wt.week_start >= '2026-01-05'
        ORDER BY wt.week_start DESC
        LIMIT ?
    """, (limit,))

    winners = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return winners


def get_weekly_kudos_leaderboard() -> list:
    """
    Get the current week's kudos leaderboard (respects privacy settings)
    Shows who received the most kudos this week

    Returns:
        List of dicts with user info and kudos counts for current week
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get current week boundaries (Monday to Sunday)
    today = datetime.now()
    week_start = today - timedelta(days=today.weekday())  # Monday
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=7)

    cursor.execute("""
        SELECT
            u.id,
            u.firstname,
            u.lastname,
            u.profile_picture,
            SUM(a.kudos_count) as total_kudos,
            COUNT(a.id) as activity_count
        FROM users u
        INNER JOIN activities a ON u.id = a.user_id
        WHERE u.is_active = 1
          AND u.privacy_level != 'private'
          AND a.visibility != 'only_me'
          AND a.type IN ('Walk', 'Hike', 'Run', 'Ride')
          AND a.start_date >= ?
          AND a.start_date < ?
          AND a.kudos_count > 0
        GROUP BY u.id
        ORDER BY total_kudos DESC
        LIMIT 10
    """, (week_start.isoformat(), week_end.isoformat()))

    leaderboard = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return leaderboard


def get_alltime_kudos_leaderboard() -> list:
    """
    Get the all-time kudos leaderboard (respects privacy settings)
    Shows who has received the most kudos overall (from 2026 onwards)

    Returns:
        List of dicts with user info and total kudos counts
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            u.id,
            u.firstname,
            u.lastname,
            u.profile_picture,
            SUM(a.kudos_count) as total_kudos,
            COUNT(a.id) as activity_count,
            ROUND(AVG(a.kudos_count), 1) as avg_kudos_per_activity
        FROM users u
        INNER JOIN activities a ON u.id = a.user_id
        WHERE u.is_active = 1
          AND u.privacy_level != 'private'
          AND a.visibility != 'only_me'
          AND a.type IN ('Walk', 'Hike', 'Run', 'Ride')
          AND a.start_date >= '2026-01-01'
          AND a.kudos_count > 0
        GROUP BY u.id
        ORDER BY total_kudos DESC
        LIMIT 10
    """)

    leaderboard = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return leaderboard


def get_most_kudos_single_activity() -> dict:
    """
    Get the single activity with the most kudos (respects privacy settings)
    From 2026 onwards

    Returns:
        Dict with activity info and kudos count, or None if no activities
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            a.id,
            a.name,
            a.type,
            a.distance,
            a.start_date,
            a.kudos_count,
            u.firstname,
            u.lastname,
            u.profile_picture
        FROM activities a
        INNER JOIN users u ON a.user_id = u.id
        WHERE u.is_active = 1
          AND u.privacy_level != 'private'
          AND a.visibility != 'only_me'
          AND a.type IN ('Walk', 'Hike', 'Run', 'Ride')
          AND a.start_date >= '2026-01-01'
          AND a.kudos_count > 0
        ORDER BY a.kudos_count DESC
        LIMIT 1
    """)

    row = cursor.fetchone()
    conn.close()

    if row:
        return dict(row)
    return None


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
