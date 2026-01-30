#!/usr/bin/env python3
"""
Migration 004: Add visibility field to activities table

Adds visibility column to track activity privacy settings from Strava:
- 'everyone' = Public (visible to everyone)
- 'only_me' = Private (only visible to the athlete)
- 'followers_only' = Followers only

This allows the club dashboard to respect user privacy preferences.
"""

import sqlite3
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

def migrate():
    """Add visibility column to activities table"""
    conn = sqlite3.connect('strava_activities.db')
    cursor = conn.cursor()

    try:
        # Check if visibility column already exists
        cursor.execute("PRAGMA table_info(activities)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'visibility' not in columns:
            print("Adding visibility column to activities table...")
            cursor.execute("""
                ALTER TABLE activities
                ADD COLUMN visibility TEXT DEFAULT 'only_me'
            """)
            conn.commit()
            print("✅ Migration 004 completed: visibility column added")
            print("   Default value 'only_me' (private) applied to existing activities")
            print("   This is a privacy-safe default - activities will be hidden until synced")
            print("   Run sync to update visibility from Strava for existing activities")
        else:
            print("⏭️  Migration 004 skipped: visibility column already exists")

    except Exception as e:
        print(f"❌ Migration 004 failed: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
