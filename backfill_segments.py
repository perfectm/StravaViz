#!/usr/bin/env python3
"""
One-time script to backfill segment data for existing activities.
Fetches activity details from Strava API and parses segment efforts.

Usage:
    python backfill_segments.py

Prerequisites:
    - Run migrations/007_add_segments.py first
    - Users must have valid Strava tokens
"""

import sqlite3
import requests
import time
from datetime import datetime
from dotenv import load_dotenv
import os

load_dotenv()

CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_ACTIVITY_DETAIL_URL = "https://www.strava.com/api/v3/activities/{activity_id}"


def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect('strava_activities.db')
    conn.row_factory = sqlite3.Row
    return conn


def refresh_user_token(user):
    """Refresh user's access token"""
    print(f"  Refreshing token for {user['firstname']}...")

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
        print(f"  Token refresh failed: {response.status_code}")
        return None

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

    return token_data['access_token']


def fetch_activity_segments(access_token, activity_id):
    """Fetch segment efforts for a single activity"""
    try:
        response = requests.get(
            STRAVA_ACTIVITY_DETAIL_URL.format(activity_id=activity_id),
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=10
        )

        if response.status_code == 429:
            print(f"    Rate limit hit, waiting 60 seconds...")
            time.sleep(60)
            # Retry once
            response = requests.get(
                STRAVA_ACTIVITY_DETAIL_URL.format(activity_id=activity_id),
                headers={'Authorization': f'Bearer {access_token}'},
                timeout=10
            )

        if response.status_code != 200:
            return None

        activity_data = response.json()
        return activity_data.get('segment_efforts', [])

    except Exception as e:
        print(f"    Error fetching segments for activity {activity_id}: {e}")
        return None


def main():
    """Main backfill process"""
    print("=" * 70)
    print("SEGMENTS BACKFILL SCRIPT")
    print("=" * 70)
    print("Fetching segment data for activities not yet processed")
    print()

    # Check that tables exist
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='segments'")
    if not cursor.fetchone():
        print("segments table does not exist.")
        print("Run migrations/007_add_segments.py first.")
        conn.close()
        return

    # Get all active users
    cursor.execute("SELECT * FROM users WHERE is_active = 1")
    users = cursor.fetchall()
    conn.close()

    total_processed = 0
    total_segments_found = 0
    total_skipped = 0

    for user_row in users:
        user = dict(user_row)
        print(f"\nProcessing: {user['firstname']} {user['lastname']}")

        # Refresh token if needed
        if user['token_expires_at'] <= int(time.time()) + 300:
            access_token = refresh_user_token(user)
            if not access_token:
                print(f"  Skipping due to token refresh failure")
                continue
        else:
            access_token = user['access_token']

        # Find activities that haven't been processed for segments
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT activity_id, name, type
            FROM activities
            WHERE user_id = ?
              AND segments_fetched = 0
            ORDER BY start_date DESC
        """, (user['id'],))
        activities = cursor.fetchall()
        conn.close()

        print(f"  {len(activities)} activities need segment processing")

        user_processed = 0
        user_segments = 0
        for activity in activities:
            activity_id = activity['activity_id']
            segment_efforts = fetch_activity_segments(access_token, activity_id)

            if segment_efforts is None:
                total_skipped += 1
                continue

            conn = get_db_connection()
            cursor = conn.cursor()
            try:
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
                            user['id'],
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
                        user_segments += 1

                # Mark activity as processed
                cursor.execute("""
                    UPDATE activities SET segments_fetched = 1
                    WHERE user_id = ? AND activity_id = ?
                """, (user['id'], activity_id))

                conn.commit()
                user_processed += 1
                total_processed += 1

            except Exception as e:
                print(f"    Error saving segments for activity {activity_id}: {e}")
            finally:
                conn.close()

            # Rate limiting
            time.sleep(0.5)

        total_segments_found += user_segments
        print(f"  Processed {user_processed} activities, found {user_segments} segment efforts")

        # Wait between users
        time.sleep(2)

    print("\n" + "=" * 70)
    print(f"COMPLETE: Processed {total_processed} activities")
    print(f"  {total_segments_found} segment efforts found and saved")
    print(f"  {total_skipped} activities had API errors (will retry next run)")
    print("=" * 70)


if __name__ == "__main__":
    main()
