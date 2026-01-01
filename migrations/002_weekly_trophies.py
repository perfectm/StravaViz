#!/usr/bin/env python3
"""
Migration 002: Weekly Trophies Table

Creates a table to track weekly distance champions and maintain a trophy leaderboard.
"""

import sqlite3
import sys
from datetime import datetime

DATABASE = 'strava_activities.db'


def backup_database():
    """Create a backup before migration"""
    import shutil
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_name = f"{DATABASE}.backup_{timestamp}_002_weekly_trophies"
    shutil.copy2(DATABASE, backup_name)
    print(f"✅ Database backed up to: {backup_name}")
    return backup_name


def create_weekly_trophies_table(conn):
    """Create the weekly_trophies table"""
    cursor = conn.cursor()

    # Create weekly_trophies table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS weekly_trophies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            week_start DATE NOT NULL,
            week_end DATE NOT NULL,
            total_distance REAL NOT NULL,
            activity_count INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, week_start),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # Create index for faster queries
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_weekly_trophies_week
        ON weekly_trophies(week_start, week_end)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_weekly_trophies_user
        ON weekly_trophies(user_id)
    """)

    conn.commit()
    print("✅ Created weekly_trophies table with indexes")


def verify_table_creation(conn):
    """Verify the table was created correctly"""
    cursor = conn.cursor()

    # Check table exists
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='weekly_trophies'
    """)

    if not cursor.fetchone():
        raise Exception("weekly_trophies table was not created")

    # Check columns
    cursor.execute("PRAGMA table_info(weekly_trophies)")
    columns = cursor.fetchall()
    column_names = [col[1] for col in columns]

    expected_columns = ['id', 'user_id', 'week_start', 'week_end',
                        'total_distance', 'activity_count', 'created_at']

    for expected in expected_columns:
        if expected not in column_names:
            raise Exception(f"Column '{expected}' missing from weekly_trophies table")

    print("✅ Table structure verified")


def run_migration():
    """Execute the migration"""
    print("=" * 60)
    print("Migration 002: Weekly Trophies Table")
    print("=" * 60)

    # Backup first
    backup_name = backup_database()

    try:
        # Connect to database
        conn = sqlite3.connect(DATABASE)
        print(f"✅ Connected to database: {DATABASE}")

        # Create table
        create_weekly_trophies_table(conn)

        # Verify
        verify_table_creation(conn)

        conn.close()

        print("\n" + "=" * 60)
        print("✅ Migration completed successfully!")
        print("=" * 60)
        print("\nNext steps:")
        print("1. Run the trophy calculation function to backfill historical data")
        print("2. The background scheduler will automatically calculate new trophies")
        print(f"\nBackup location: {backup_name}")

        return True

    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        print(f"Database backup available at: {backup_name}")
        print("\nTo rollback:")
        print(f"  cp {backup_name} {DATABASE}")
        return False


def rollback(backup_file):
    """Rollback the migration"""
    import shutil
    try:
        shutil.copy2(backup_file, DATABASE)
        print(f"✅ Rolled back to: {backup_file}")
        return True
    except Exception as e:
        print(f"❌ Rollback failed: {e}")
        return False


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "rollback":
        if len(sys.argv) > 2:
            rollback(sys.argv[2])
        else:
            print("Usage: python 002_weekly_trophies.py rollback <backup_file>")
    else:
        run_migration()
