#!/usr/bin/env python3
"""
Migration 007: Add segment tracking

Adds:
- segments table for global segment master data
- segment_efforts table for per-user effort records
- segments_fetched column to activities table (tracks which activities have been processed)
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def migrate():
    """Add segment tracking tables and activity column"""
    conn = sqlite3.connect('strava_activities.db')
    cursor = conn.cursor()

    try:
        # Add segments_fetched to activities
        cursor.execute("PRAGMA table_info(activities)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'segments_fetched' not in columns:
            print("Adding segments_fetched column to activities...")
            cursor.execute("ALTER TABLE activities ADD COLUMN segments_fetched INTEGER DEFAULT 0")

        # Create segments table (global, not user-scoped)
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='segments'")
        if not cursor.fetchone():
            print("Creating segments table...")
            cursor.execute("""
                CREATE TABLE segments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strava_segment_id INTEGER NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    distance REAL,
                    average_grade REAL,
                    maximum_grade REAL,
                    city TEXT,
                    state TEXT,
                    climb_category INTEGER DEFAULT 0
                )
            """)
        else:
            print("Migration 007 skipped: segments table already exists")

        # Create segment_efforts table
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='segment_efforts'")
        if not cursor.fetchone():
            print("Creating segment_efforts table...")
            cursor.execute("""
                CREATE TABLE segment_efforts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    activity_id INTEGER NOT NULL,
                    strava_segment_id INTEGER NOT NULL,
                    strava_effort_id INTEGER NOT NULL,
                    elapsed_time INTEGER,
                    moving_time INTEGER,
                    start_date TEXT,
                    pr_rank INTEGER,
                    kom_rank INTEGER,
                    average_heartrate REAL,
                    max_heartrate REAL,
                    fetched_at TEXT,
                    UNIQUE(user_id, strava_effort_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (strava_segment_id) REFERENCES segments(strava_segment_id)
                )
            """)
            # Index for common queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_segment_efforts_user_segment
                ON segment_efforts(user_id, strava_segment_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_segment_efforts_activity
                ON segment_efforts(user_id, activity_id)
            """)
        else:
            print("Migration 007 skipped: segment_efforts table already exists")

        conn.commit()
        print("Migration 007 completed: segment tracking tables created")
        print("  Run backfill_segments.py to populate segment data for existing activities")

    except Exception as e:
        print(f"Migration 007 failed: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
