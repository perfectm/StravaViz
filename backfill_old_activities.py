#!/usr/bin/env python3
"""
One-time script to backfill older activities that were missed during initial sync.

The sync service caps at 500 activities (10 pages x 50) on first login, fetching
newest-first. This script fetches activities BEFORE the oldest one in the database,
working backwards until all history is retrieved.

Run after initial sync to fill in complete Strava history.
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
STRAVA_ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"


def get_db_connection():
    conn = sqlite3.connect('strava_activities.db')
    conn.row_factory = sqlite3.Row
    return conn


def refresh_user_token(user):
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

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE users SET access_token = ?, refresh_token = ?, token_expires_at = ?
        WHERE id = ?
    """, (token_data['access_token'], token_data['refresh_token'],
          token_data['expires_at'], user['id']))
    conn.commit()
    conn.close()

    return token_data['access_token']


def get_oldest_activity_date(user_id):
    """Get the timestamp of the oldest activity for a user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT MIN(start_date) as oldest FROM activities WHERE user_id = ?
    """, (user_id,))
    row = cursor.fetchone()
    conn.close()

    if row and row['oldest']:
        try:
            return datetime.fromisoformat(row['oldest'].replace('Z', '+00:00'))
        except (ValueError, TypeError):
            return None
    return None


def fetch_activities_before(access_token, before_date, per_page=200):
    """Fetch activities older than before_date, working backwards."""
    activities = []
    page = 1

    before_ts = int(before_date.timestamp())

    while True:
        try:
            response = requests.get(
                STRAVA_ACTIVITIES_URL,
                headers={'Authorization': f'Bearer {access_token}'},
                params={'per_page': per_page, 'page': page, 'before': before_ts},
                timeout=15
            )

            if response.status_code == 429:
                print(f"    Rate limit hit, waiting 60 seconds...")
                time.sleep(60)
                continue
            elif response.status_code != 200:
                print(f"    API error: {response.status_code}")
                break

            page_activities = response.json()

            if not page_activities:
                break

            activities.extend(page_activities)
            oldest_in_page = page_activities[-1].get('start_date', '')
            print(f"    Page {page}: {len(page_activities)} activities (oldest: {oldest_in_page[:10]})")

            if len(page_activities) < per_page:
                break

            page += 1
            time.sleep(0.5)

        except requests.exceptions.Timeout:
            print(f"    Timeout on page {page}, retrying...")
            time.sleep(2)
            continue
        except Exception as e:
            print(f"    Error: {e}")
            break

    return activities


def save_activities(user_id, activities):
    """Save activities to database, returning counts."""
    conn = get_db_connection()
    cursor = conn.cursor()

    new_count = 0
    updated_count = 0

    for activity in activities:
        try:
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
            visibility = activity.get('visibility', 'everyone')

            start_latlng = activity.get('start_latlng')
            start_lat = start_latlng[0] if start_latlng and len(start_latlng) >= 2 else None
            start_lng = start_latlng[1] if start_latlng and len(start_latlng) >= 2 else None

            cursor.execute("""
                SELECT id FROM activities WHERE user_id = ? AND activity_id = ?
            """, (user_id, activity_id))
            existing = cursor.fetchone()

            if existing:
                cursor.execute("""
                    UPDATE activities
                    SET kudos_count = ?, visibility = ?,
                        start_lat = COALESCE(?, start_lat),
                        start_lng = COALESCE(?, start_lng)
                    WHERE user_id = ? AND activity_id = ?
                """, (kudos_count, visibility, start_lat, start_lng, user_id, activity_id))
                updated_count += 1
            else:
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
            print(f"    Error saving activity {activity.get('id')}: {e}")
            continue

    conn.commit()
    conn.close()

    return new_count, updated_count


def main():
    print("=" * 70)
    print("HISTORICAL ACTIVITY BACKFILL")
    print("=" * 70)
    print("Fetches activities older than what's currently in the database.")
    print()

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE is_active = 1")
    users = cursor.fetchall()
    conn.close()

    total_new = 0

    for user_row in users:
        user = dict(user_row)
        print(f"\nProcessing: {user['firstname']} {user['lastname']}")

        # Get current activity count and oldest date
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM activities WHERE user_id = ?", (user['id'],))
        current_count = cursor.fetchone()[0]
        conn.close()

        oldest_date = get_oldest_activity_date(user['id'])

        if not oldest_date:
            print(f"  No activities in database — run a normal sync first")
            continue

        print(f"  Current activities: {current_count}")
        print(f"  Oldest activity: {oldest_date.strftime('%Y-%m-%d')}")
        print(f"  Fetching activities before {oldest_date.strftime('%Y-%m-%d')}...")

        # Refresh token if needed
        if user['token_expires_at'] <= int(time.time()) + 300:
            access_token = refresh_user_token(user)
            if not access_token:
                print(f"  Skipping due to token refresh failure")
                continue
        else:
            access_token = user['access_token']

        # Fetch older activities
        activities = fetch_activities_before(access_token, oldest_date)
        print(f"  Fetched {len(activities)} older activities from Strava")

        if activities:
            new_count, updated_count = save_activities(user['id'], activities)
            print(f"  Saved {new_count} new, {updated_count} already existed")
            total_new += new_count
        else:
            print(f"  No older activities found")

        # Show updated stats
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM activities WHERE user_id = ?", (user['id'],))
        final_count = cursor.fetchone()[0]
        cursor.execute("SELECT MIN(start_date) FROM activities WHERE user_id = ?", (user['id'],))
        new_oldest = cursor.fetchone()[0]
        conn.close()

        print(f"  Total activities now: {final_count} (was {current_count})")
        print(f"  Oldest activity now: {new_oldest[:10] if new_oldest else 'N/A'}")

        time.sleep(2)

    print("\n" + "=" * 70)
    print(f"COMPLETE: Added {total_new} historical activities")
    print("=" * 70)


if __name__ == "__main__":
    main()
