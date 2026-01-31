#!/usr/bin/env python3
"""
Import users from a JSON export into the current StravaViz database.
Skips users that already exist (matched by strava_athlete_id).
After import, triggers a sync to fetch their activities.

Usage:
    python import_users.py users_export.json
"""

import sqlite3
import json
import sys
import time
from dotenv import load_dotenv

load_dotenv()


def get_db_connection():
    conn = sqlite3.connect('strava_activities.db')
    conn.row_factory = sqlite3.Row
    return conn


def main():
    if len(sys.argv) < 2:
        print("Usage: python import_users.py users_export.json")
        sys.exit(1)

    input_file = sys.argv[1]

    with open(input_file) as f:
        users = json.load(f)

    print(f"Importing {len(users)} users from {input_file}")
    print()

    conn = get_db_connection()
    cursor = conn.cursor()

    imported = 0
    skipped = 0

    for u in users:
        # Check if user already exists
        cursor.execute(
            "SELECT id FROM users WHERE strava_athlete_id = ?",
            (u['strava_athlete_id'],)
        )
        existing = cursor.fetchone()

        if existing:
            print(f"  Skipped: {u['firstname']} {u['lastname']} (already exists as user {existing['id']})")
            skipped += 1
            continue

        cursor.execute("""
            INSERT INTO users (
                strava_athlete_id, firstname, lastname, profile_picture,
                access_token, refresh_token, token_expires_at,
                privacy_level, is_active, created_at, last_login
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            u['strava_athlete_id'],
            u.get('firstname', ''),
            u.get('lastname', ''),
            u.get('profile_picture'),
            u['access_token'],
            u['refresh_token'],
            u['token_expires_at'],
            u.get('privacy_level', 'club_only'),
            1,
            u.get('created_at', ''),
            u.get('last_login', ''),
        ))

        new_id = cursor.lastrowid
        print(f"  Imported: {u['firstname']} {u['lastname']} -> user {new_id}")
        imported += 1

    conn.commit()
    conn.close()

    print(f"\nDone: {imported} imported, {skipped} skipped")

    if imported > 0:
        print("\nSyncing activities for imported users...")
        from sync_service import sync_user_activities

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, firstname, lastname FROM users WHERE is_active = 1")
        all_users = cursor.fetchall()
        conn.close()

        for row in all_users:
            print(f"\n  Syncing {row['firstname']} {row['lastname']}...")
            new_count, error = sync_user_activities(row['id'])
            if error:
                print(f"    Error: {error}")
            else:
                print(f"    {new_count} new activities")
            time.sleep(1)

        print("\nSync complete. You may also want to run:")
        print("  python backfill_old_activities.py  # fetch full history")


if __name__ == "__main__":
    main()
