#!/usr/bin/env python3
"""
Migration 001: Multi-User Schema
=================================

This migration transforms the single-user database schema to support multiple users.

Changes:
- Creates new `users` table for multi-user authentication
- Updates `activities` table to link to users
- Creates `club_memberships` table for club associations
- Creates `sessions` table for session management
- Migrates existing single-user data to user_id=1
- Adds indexes for performance

Usage:
    python migrations/001_multiuser_schema.py [--rollback]

Options:
    --rollback    Roll back the migration (restore from backup)
"""

import sqlite3
import shutil
import os
import sys
from datetime import datetime
from pathlib import Path


class MultiUserMigration:
    def __init__(self, db_path='strava_activities.db'):
        self.db_path = db_path
        self.backup_path = None
        self.migration_name = '001_multiuser_schema'

    def backup_database(self):
        """Create a timestamped backup of the database"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.backup_path = f"{self.db_path}.backup_{timestamp}_{self.migration_name}"

        if not os.path.exists(self.db_path):
            print(f"⚠️  Database {self.db_path} does not exist. Creating new database.")
            return False

        print(f"📦 Creating backup: {self.backup_path}")
        shutil.copy2(self.db_path, self.backup_path)
        print(f"✅ Backup created successfully")
        return True

    def create_new_tables(self, conn):
        """Create new tables for multi-user support"""
        cursor = conn.cursor()

        print("📋 Creating new tables...")

        # 1. Users table
        print("  → Creating 'users' table")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strava_athlete_id INTEGER UNIQUE NOT NULL,
                firstname TEXT,
                lastname TEXT,
                profile_picture TEXT,
                access_token TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                token_expires_at INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP,
                privacy_level TEXT DEFAULT 'club_only',
                is_active BOOLEAN DEFAULT 1
            )
        """)

        # 2. Club memberships table
        print("  → Creating 'club_memberships' table")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS club_memberships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                club_id INTEGER NOT NULL,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, club_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # 3. Sessions table
        print("  → Creating 'sessions' table")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # 4. Create temporary activities table with new schema
        print("  → Creating new 'activities_new' table")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS activities_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                activity_id INTEGER NOT NULL,
                name TEXT,
                type TEXT,
                start_date TEXT,
                distance REAL,
                moving_time INTEGER,
                elapsed_time INTEGER,
                total_elevation_gain REAL,
                average_speed REAL,
                max_speed REAL,
                average_heartrate REAL,
                max_heartrate REAL,
                calories REAL,
                UNIQUE(user_id, activity_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        conn.commit()
        print("✅ New tables created successfully")

    def create_indexes(self, conn):
        """Create indexes for performance optimization"""
        cursor = conn.cursor()

        print("🔍 Creating indexes...")

        indexes = [
            ("idx_activities_user_id", "activities_new", "user_id"),
            ("idx_activities_start_date", "activities_new", "start_date"),
            ("idx_activities_type", "activities_new", "type"),
            ("idx_users_strava_id", "users", "strava_athlete_id"),
            ("idx_sessions_user_id", "sessions", "user_id"),
            ("idx_sessions_expires", "sessions", "expires_at"),
            ("idx_club_memberships_user", "club_memberships", "user_id"),
            ("idx_club_memberships_club", "club_memberships", "club_id"),
        ]

        for index_name, table_name, column_name in indexes:
            try:
                cursor.execute(f"""
                    CREATE INDEX IF NOT EXISTS {index_name}
                    ON {table_name}({column_name})
                """)
                print(f"  → Created index: {index_name}")
            except sqlite3.Error as e:
                print(f"  ⚠️  Warning: Could not create index {index_name}: {e}")

        conn.commit()
        print("✅ Indexes created successfully")

    def migrate_existing_data(self, conn):
        """Migrate existing single-user data to multi-user structure"""
        cursor = conn.cursor()

        print("🔄 Migrating existing data...")

        # Check if old activities table exists and has data
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='activities'
        """)

        if not cursor.fetchone():
            print("  ℹ️  No existing 'activities' table found. Skipping data migration.")
            return

        # Count existing activities
        cursor.execute("SELECT COUNT(*) FROM activities")
        activity_count = cursor.fetchone()[0]

        if activity_count == 0:
            print("  ℹ️  No existing activities to migrate.")
        else:
            print(f"  → Found {activity_count} existing activities")

            # Create default user (user_id=1) for existing data
            print("  → Creating default user (ID=1) for existing data")

            # Load .env to get current credentials
            from dotenv import load_dotenv
            load_dotenv()

            client_id = os.getenv('STRAVA_CLIENT_ID', '')
            client_secret = os.getenv('STRAVA_CLIENT_SECRET', '')
            refresh_token = os.getenv('STRAVA_REFRESH_TOKEN', '')

            # Insert default user
            cursor.execute("""
                INSERT OR IGNORE INTO users (
                    id, strava_athlete_id, firstname, lastname,
                    access_token, refresh_token, token_expires_at,
                    privacy_level, is_active
                ) VALUES (
                    1, 0, 'Legacy', 'User',
                    ?, ?, ?,
                    'club_only', 1
                )
            """, (refresh_token, refresh_token, int(datetime.now().timestamp()) + 21600))

            # Migrate activities to new table with user_id=1
            print("  → Migrating activities to new schema")
            cursor.execute("""
                INSERT INTO activities_new (
                    user_id, activity_id, name, type, start_date, distance
                )
                SELECT
                    1 as user_id,
                    activity_id, name, type, start_date, distance
                FROM activities
            """)

            migrated_count = cursor.rowcount
            print(f"  → Migrated {migrated_count} activities to user_id=1")

        conn.commit()
        print("✅ Data migration completed")

    def swap_tables(self, conn):
        """Swap old and new activities tables"""
        cursor = conn.cursor()

        print("🔄 Swapping old and new activities tables...")

        # Rename old activities table to activities_old
        cursor.execute("""
            ALTER TABLE activities RENAME TO activities_old
        """)
        print("  → Renamed 'activities' to 'activities_old'")

        # Rename new activities table to activities
        cursor.execute("""
            ALTER TABLE activities_new RENAME TO activities
        """)
        print("  → Renamed 'activities_new' to 'activities'")

        conn.commit()
        print("✅ Tables swapped successfully")
        print("  ℹ️  Old table preserved as 'activities_old' (can be dropped manually later)")

    def verify_migration(self, conn):
        """Verify the migration was successful"""
        cursor = conn.cursor()

        print("🔍 Verifying migration...")

        # Check tables exist
        tables = ['users', 'activities', 'club_memberships', 'sessions']
        for table in tables:
            cursor.execute(f"""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='{table}'
            """)
            if cursor.fetchone():
                print(f"  ✅ Table '{table}' exists")
            else:
                print(f"  ❌ Table '{table}' missing!")
                return False

        # Check activities have user_id
        cursor.execute("PRAGMA table_info(activities)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'user_id' in columns:
            print("  ✅ Activities table has 'user_id' column")
        else:
            print("  ❌ Activities table missing 'user_id' column!")
            return False

        # Count migrated data
        cursor.execute("SELECT COUNT(*) FROM activities")
        activity_count = cursor.fetchone()[0]
        print(f"  ✅ Activities table has {activity_count} records")

        cursor.execute("SELECT COUNT(*) FROM users")
        user_count = cursor.fetchone()[0]
        print(f"  ✅ Users table has {user_count} records")

        print("✅ Migration verification completed successfully")
        return True

    def run_migration(self):
        """Execute the migration"""
        print("=" * 60)
        print("🚀 Starting Multi-User Schema Migration")
        print("=" * 60)

        # Backup existing database
        has_backup = self.backup_database()

        try:
            # Connect to database
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA foreign_keys = ON")

            # Create new tables
            self.create_new_tables(conn)

            # Migrate existing data
            if has_backup:
                self.migrate_existing_data(conn)

            # Create indexes
            self.create_indexes(conn)

            # Swap tables
            if has_backup:
                self.swap_tables(conn)

            # Verify migration
            if not self.verify_migration(conn):
                raise Exception("Migration verification failed!")

            conn.close()

            print("\n" + "=" * 60)
            print("🎉 Migration completed successfully!")
            print("=" * 60)
            print(f"📦 Backup saved to: {self.backup_path}")
            print("\n📝 Next steps:")
            print("  1. Update your application code to use the new schema")
            print("  2. Test the application thoroughly")
            print("  3. Run: DROP TABLE activities_old; (when ready)")
            print("  4. Proceed to Phase 2: Authentication System")
            print("=" * 60)

        except Exception as e:
            print(f"\n❌ Migration failed: {e}")
            print(f"💾 Database backup available at: {self.backup_path}")
            print("🔄 To rollback, run: python migrations/001_multiuser_schema.py --rollback")
            sys.exit(1)

    def rollback(self):
        """Rollback the migration by restoring from backup"""
        print("=" * 60)
        print("🔄 Rolling back migration...")
        print("=" * 60)

        # Find most recent backup
        backups = sorted([
            f for f in os.listdir('.')
            if f.startswith(f"{self.db_path}.backup_") and self.migration_name in f
        ], reverse=True)

        if not backups:
            print("❌ No backup found for this migration!")
            sys.exit(1)

        backup_file = backups[0]
        print(f"📦 Restoring from: {backup_file}")

        # Backup current state before rollback
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        pre_rollback_backup = f"{self.db_path}.pre_rollback_{timestamp}"
        shutil.copy2(self.db_path, pre_rollback_backup)
        print(f"💾 Current state backed up to: {pre_rollback_backup}")

        # Restore from backup
        shutil.copy2(backup_file, self.db_path)

        print("✅ Rollback completed successfully")
        print(f"📦 Pre-rollback backup saved to: {pre_rollback_backup}")


def main():
    """Main entry point"""
    migration = MultiUserMigration()

    if '--rollback' in sys.argv:
        migration.rollback()
    else:
        migration.run_migration()


if __name__ == '__main__':
    main()
