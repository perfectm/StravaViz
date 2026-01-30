"""
One-time script to update kudos counts for all existing activities
This fetches activities from Strava API and updates kudos_count in the database
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
        print(f"  ❌ Token refresh failed: {response.status_code}")
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

def fetch_all_activities(access_token, per_page=200):
    """Fetch all activities from Strava"""
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
                print(f"  ⚠️  Rate limit hit, waiting 60 seconds...")
                time.sleep(60)
                continue
            elif response.status_code != 200:
                print(f"  ❌ API error: {response.status_code}")
                break

            page_activities = response.json()

            if not page_activities:
                break

            activities.extend(page_activities)
            print(f"    Page {page}: {len(page_activities)} activities")

            if len(page_activities) < per_page:
                break

            page += 1
            time.sleep(0.5)  # Be nice to the API

        except Exception as e:
            print(f"  ❌ Error fetching activities: {e}")
            break

    return activities

def update_activity_kudos(user_id, activities):
    """Update kudos counts for activities"""
    conn = get_db_connection()
    cursor = conn.cursor()

    updated_count = 0

    for activity in activities:
        try:
            activity_id = activity.get('id')
            kudos_count = activity.get('kudos_count', 0)

            # Update kudos_count for existing activities
            cursor.execute("""
                UPDATE activities
                SET kudos_count = ?
                WHERE user_id = ? AND activity_id = ?
            """, (kudos_count, user_id, activity_id))

            if cursor.rowcount > 0:
                updated_count += 1

        except Exception as e:
            print(f"  ❌ Error updating activity {activity.get('id')}: {e}")
            continue

    conn.commit()
    conn.close()

    return updated_count

def main():
    """Main update process"""
    print("=" * 60)
    print("KUDOS UPDATE SCRIPT")
    print("=" * 60)
    print("This will fetch all activities and update kudos counts")
    print()

    # Get all active users
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE is_active = 1")
    users = cursor.fetchall()
    conn.close()

    total_updated = 0

    for user_row in users:
        user = dict(user_row)
        print(f"\n📊 Processing: {user['firstname']} {user['lastname']}")

        # Refresh token if needed
        if user['token_expires_at'] <= int(time.time()) + 300:
            access_token = refresh_user_token(user)
            if not access_token:
                print(f"  ⏭️  Skipping due to token refresh failure")
                continue
        else:
            access_token = user['access_token']

        # Fetch all activities
        activities = fetch_all_activities(access_token)
        print(f"  ✅ Fetched {len(activities)} total activities")

        # Update kudos counts
        updated = update_activity_kudos(user['id'], activities)
        print(f"  ✅ Updated {updated} activities with kudos counts")

        total_updated += updated

        # Rate limiting - wait between users
        time.sleep(2)

    print("\n" + "=" * 60)
    print(f"✅ COMPLETE: Updated {total_updated} total activities")
    print("=" * 60)

if __name__ == "__main__":
    main()
