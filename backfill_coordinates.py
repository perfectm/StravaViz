#!/usr/bin/env python3
"""
One-time script to backfill GPS coordinates for all existing activities.
Re-fetches activities from Strava API (list endpoint includes start_latlng)
and updates start_lat/start_lng in the database.

Run after migration 006_add_locations.py.
"""

import sqlite3
import requests
import time
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


def fetch_all_activities(access_token, per_page=200):
    activities = []
    page = 1

    print(f"  Fetching activities from Strava API...")

    while True:
        try:
            response = requests.get(
                STRAVA_ACTIVITIES_URL,
                headers={'Authorization': f'Bearer {access_token}'},
                params={'per_page': per_page, 'page': page},
                timeout=10
            )

            if response.status_code == 429:
                print(f"  Rate limit hit, waiting 60 seconds...")
                time.sleep(60)
                continue
            elif response.status_code != 200:
                print(f"  API error: {response.status_code}")
                break

            page_activities = response.json()

            if not page_activities:
                break

            activities.extend(page_activities)
            print(f"    Page {page}: {len(page_activities)} activities")

            if len(page_activities) < per_page:
                break

            page += 1
            time.sleep(0.5)

        except Exception as e:
            print(f"  Error fetching activities: {e}")
            break

    return activities


def update_coordinates(user_id, activities):
    conn = get_db_connection()
    cursor = conn.cursor()

    updated_count = 0
    skipped_count = 0
    no_coords_count = 0

    for activity in activities:
        try:
            activity_id = activity.get('id')
            start_latlng = activity.get('start_latlng')

            if not start_latlng or len(start_latlng) < 2:
                no_coords_count += 1
                continue

            start_lat = start_latlng[0]
            start_lng = start_latlng[1]

            cursor.execute("""
                UPDATE activities
                SET start_lat = ?, start_lng = ?
                WHERE user_id = ? AND activity_id = ?
            """, (start_lat, start_lng, user_id, activity_id))

            if cursor.rowcount > 0:
                updated_count += 1
            else:
                skipped_count += 1

        except Exception as e:
            print(f"  Error updating activity {activity.get('id')}: {e}")
            continue

    conn.commit()
    conn.close()

    return updated_count, skipped_count, no_coords_count


def main():
    print("=" * 70)
    print("COORDINATE BACKFILL SCRIPT")
    print("=" * 70)
    print("This will fetch all activities and update GPS coordinates")
    print()

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE is_active = 1")
    users = cursor.fetchall()
    conn.close()

    total_updated = 0
    total_skipped = 0
    total_no_coords = 0

    for user_row in users:
        user = dict(user_row)
        print(f"\nProcessing: {user['firstname']} {user['lastname']}")

        if user['token_expires_at'] <= int(time.time()) + 300:
            access_token = refresh_user_token(user)
            if not access_token:
                print(f"  Skipping due to token refresh failure")
                continue
        else:
            access_token = user['access_token']

        activities = fetch_all_activities(access_token)
        print(f"  Fetched {len(activities)} total activities")

        updated, skipped, no_coords = update_coordinates(user['id'], activities)
        print(f"  Updated {updated} activities, {skipped} not in database, {no_coords} had no coordinates")

        total_updated += updated
        total_skipped += skipped
        total_no_coords += no_coords

        time.sleep(2)

    print("\n" + "=" * 70)
    print(f"COMPLETE: Updated {total_updated} activities with coordinates")
    print(f"  {total_skipped} activities not found in database")
    print(f"  {total_no_coords} activities had no GPS coordinates")
    print("=" * 70)

    # Show results
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM activities WHERE start_lat IS NOT NULL")
    with_coords = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM activities WHERE start_lat IS NULL")
    without_coords = cursor.fetchone()[0]
    conn.close()
    print(f"\nDatabase status: {with_coords} activities with coordinates, {without_coords} without")


if __name__ == "__main__":
    main()
