"""
Migration 003: Add kudos tracking to activities table
Adds kudos_count column to track kudos received on each activity
"""

import sqlite3
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

def migrate():
    """Add kudos_count column to activities table"""
    conn = sqlite3.connect('strava_activities.db')
    cursor = conn.cursor()

    try:
        # Check if kudos_count column already exists
        cursor.execute("PRAGMA table_info(activities)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'kudos_count' not in columns:
            print("Adding kudos_count column to activities table...")
            cursor.execute("""
                ALTER TABLE activities
                ADD COLUMN kudos_count INTEGER DEFAULT 0
            """)
            conn.commit()
            print("✅ Migration 003 completed: kudos_count column added")
        else:
            print("⏭️  Migration 003 skipped: kudos_count column already exists")

    except Exception as e:
        print(f"❌ Migration 003 failed: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
