#!/usr/bin/env python3
"""
One-time script to backfill HR zone data for existing activities.
Fetches zone distribution from Strava API for activities that have heart rate data.

Usage:
    python backfill_hr_zones.py

Prerequisites:
    - Run migrations/005_add_hr_zones.py first
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
STRAVA_ACTIVITY_ZONES_URL = "https://www.strava.com/api/v3/activities/{activity_id}/zones"


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


def fetch_activity_zones(access_token, activity_id):
    """Fetch HR zone distribution for a single activity"""
    try:
        response = requests.get(
            STRAVA_ACTIVITY_ZONES_URL.format(activity_id=activity_id),
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=10
        )

        if response.status_code == 429:
            print(f"    Rate limit hit, waiting 60 seconds...")
            time.sleep(60)
            # Retry once
            response = requests.get(
                STRAVA_ACTIVITY_ZONES_URL.format(activity_id=activity_id),
                headers={'Authorization': f'Bearer {access_token}'},
                timeout=10
            )

        if response.status_code != 200:
            return None

        zones_data = response.json()

        for zone_info in zones_data:
            if zone_info.get('type') == 'heartrate':
                buckets = zone_info.get('distribution_buckets', [])
                zone_seconds = {}
                for i, bucket in enumerate(buckets[:5], start=1):
                    zone_seconds[f'zone_{i}_seconds'] = bucket.get('time', 0)
                return zone_seconds

        return None

    except Exception as e:
        print(f"    Error fetching zones for activity {activity_id}: {e}")
        return None


def main():
    """Main backfill process"""
    print("=" * 70)
    print("HR ZONES BACKFILL SCRIPT")
    print("=" * 70)
    print("Fetching HR zone data for activities with heart rate data")
    print()

    # Check that table exists
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='activity_hr_zones'")
    if not cursor.fetchone():
        print("activity_hr_zones table does not exist.")
        print("Run migrations/005_add_hr_zones.py first.")
        conn.close()
        return

    # Get all active users
    cursor.execute("SELECT * FROM users WHERE is_active = 1")
    users = cursor.fetchall()
    conn.close()

    total_fetched = 0
    total_skipped = 0
    total_no_hr = 0

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

        # Find activities with HR data that don't have zone data yet
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT a.activity_id, a.name, a.type
            FROM activities a
            LEFT JOIN activity_hr_zones z ON a.user_id = z.user_id AND a.activity_id = z.activity_id
            WHERE a.user_id = ?
              AND a.average_heartrate IS NOT NULL
              AND z.id IS NULL
            ORDER BY a.start_date DESC
        """, (user['id'],))
        activities = cursor.fetchall()

        # Also count activities without HR data
        cursor.execute("""
            SELECT COUNT(*) as count FROM activities
            WHERE user_id = ? AND average_heartrate IS NULL
        """, (user['id'],))
        no_hr_count = cursor.fetchone()['count']
        total_no_hr += no_hr_count
        conn.close()

        print(f"  {len(activities)} activities need zone data, {no_hr_count} have no HR data")

        user_fetched = 0
        for activity in activities:
            activity_id = activity['activity_id']
            zone_data = fetch_activity_zones(access_token, activity_id)

            if zone_data is None:
                total_skipped += 1
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
                    user['id'], activity_id,
                    zone_data.get('zone_1_seconds', 0),
                    zone_data.get('zone_2_seconds', 0),
                    zone_data.get('zone_3_seconds', 0),
                    zone_data.get('zone_4_seconds', 0),
                    zone_data.get('zone_5_seconds', 0),
                    datetime.now().isoformat()
                ))
                conn.commit()
                user_fetched += 1
                total_fetched += 1
            except Exception as e:
                print(f"    Error saving zones for activity {activity_id}: {e}")
            finally:
                conn.close()

            # Rate limiting - pause between requests
            time.sleep(0.5)

        print(f"  Fetched zones for {user_fetched} activities")

        # Rate limiting - wait between users
        time.sleep(2)

    print("\n" + "=" * 70)
    print(f"COMPLETE: Fetched HR zones for {total_fetched} activities")
    print(f"  {total_skipped} activities had no zone data available")
    print(f"  {total_no_hr} activities have no heart rate data")
    print("=" * 70)


if __name__ == "__main__":
    main()
