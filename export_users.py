#!/usr/bin/env python3
"""
Export users from one StravaViz database to a JSON file that can be
imported into another instance. Includes tokens so users don't need
to re-authenticate.

Usage:
    python export_users.py                    # exports to users_export.json
    python export_users.py output.json        # exports to custom filename
"""

import sqlite3
import json
import sys


def main():
    output_file = sys.argv[1] if len(sys.argv) > 1 else 'users_export.json'

    conn = sqlite3.connect('strava_activities.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users WHERE is_active = 1")
    users = [dict(row) for row in cursor.fetchall()]
    conn.close()

    if not users:
        print("No active users found.")
        return

    print(f"Exporting {len(users)} users:")
    for u in users:
        print(f"  {u['id']}: {u['firstname']} {u['lastname']} (athlete {u['strava_athlete_id']})")

    with open(output_file, 'w') as f:
        json.dump(users, f, indent=2)

    print(f"\nWritten to {output_file}")
    print("Copy this file to the production server and run:")
    print(f"  python import_users.py {output_file}")


if __name__ == "__main__":
    main()
