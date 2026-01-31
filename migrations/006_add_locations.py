#!/usr/bin/env python3
"""
Migration 006: Add locations feature

Adds:
- start_lat and start_lng columns to activities table (GPS coordinates)
- locations table for user-defined named locations
- activity_locations table for tagging activities to locations
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def migrate():
    """Add locations tables and activity coordinate columns"""
    conn = sqlite3.connect('strava_activities.db')
    cursor = conn.cursor()

    try:
        # Add start_lat and start_lng to activities
        cursor.execute("PRAGMA table_info(activities)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'start_lat' not in columns:
            print("Adding start_lat column to activities...")
            cursor.execute("ALTER TABLE activities ADD COLUMN start_lat REAL")

        if 'start_lng' not in columns:
            print("Adding start_lng column to activities...")
            cursor.execute("ALTER TABLE activities ADD COLUMN start_lng REAL")

        # Create locations table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='locations'")
        if not cursor.fetchone():
            print("Creating locations table...")
            cursor.execute("""
                CREATE TABLE locations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    center_lat REAL NOT NULL,
                    center_lng REAL NOT NULL,
                    radius_meters REAL NOT NULL DEFAULT 500,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)
        else:
            print("Migration 006 skipped: locations table already exists")

        # Create activity_locations table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='activity_locations'")
        if not cursor.fetchone():
            print("Creating activity_locations table...")
            cursor.execute("""
                CREATE TABLE activity_locations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    activity_id INTEGER NOT NULL,
                    location_id INTEGER NOT NULL,
                    tagged_by TEXT DEFAULT 'manual',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, activity_id, location_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (location_id) REFERENCES locations(id) ON DELETE CASCADE
                )
            """)
        else:
            print("Migration 006 skipped: activity_locations table already exists")

        conn.commit()
        print("Migration 006 completed: locations feature tables created")
        print("  Run backfill_coordinates.py to populate GPS coordinates for existing activities")

    except Exception as e:
        print(f"Migration 006 failed: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
