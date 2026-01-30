#!/usr/bin/env python3
"""
Migration 005: Add heart rate zones tracking table

Creates activity_hr_zones table to store per-activity HR zone distribution data.
Zone data is fetched from Strava's activity zones endpoint.

Zones:
- Zone 1: Recovery (lowest HR)
- Zone 2: Endurance
- Zone 3: Tempo
- Zone 4: Threshold
- Zone 5: VO2 Max (highest HR)
"""

import sqlite3
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


def migrate():
    """Create activity_hr_zones table"""
    conn = sqlite3.connect('strava_activities.db')
    cursor = conn.cursor()

    try:
        # Check if table already exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='activity_hr_zones'")
        if cursor.fetchone():
            print("Migration 005 skipped: activity_hr_zones table already exists")
            return

        print("Creating activity_hr_zones table...")
        cursor.execute("""
            CREATE TABLE activity_hr_zones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                activity_id INTEGER NOT NULL,
                zone_1_seconds INTEGER DEFAULT 0,
                zone_2_seconds INTEGER DEFAULT 0,
                zone_3_seconds INTEGER DEFAULT 0,
                zone_4_seconds INTEGER DEFAULT 0,
                zone_5_seconds INTEGER DEFAULT 0,
                fetched_at TEXT,
                UNIQUE(user_id, activity_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        conn.commit()
        print("Migration 005 completed: activity_hr_zones table created")
        print("  Run backfill_hr_zones.py to fetch HR zone data for existing activities")

    except Exception as e:
        print(f"Migration 005 failed: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
