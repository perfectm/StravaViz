#!/usr/bin/env python3
"""
Database Schema Inspector

This script inspects the current database schema and displays:
- All tables and their columns
- Row counts for each table
- Sample data from each table
- Index information
"""

import sqlite3
import sys
from pathlib import Path


def inspect_database(db_path='strava_activities.db'):
    """Inspect and display database schema"""
    if not Path(db_path).exists():
        print(f"❌ Database not found: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("=" * 70)
    print("📊 STRAVA DATABASE SCHEMA INSPECTOR")
    print("=" * 70)
    print(f"Database: {db_path}\n")

    # Get all tables
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table'
        ORDER BY name
    """)
    tables = [row[0] for row in cursor.fetchall()]

    if not tables:
        print("⚠️  No tables found in database")
        conn.close()
        return

    # Check if multi-user schema
    has_multiuser = 'users' in tables
    schema_type = "Multi-User" if has_multiuser else "Single-User (Legacy)"

    print(f"Schema Type: {schema_type}")
    print(f"Total Tables: {len(tables)}\n")

    # Display each table
    for table in tables:
        print("─" * 70)
        print(f"📋 Table: {table}")
        print("─" * 70)

        # Get column information
        cursor.execute(f"PRAGMA table_info({table})")
        columns = cursor.fetchall()

        print("\nColumns:")
        for col in columns:
            col_id, name, col_type, not_null, default, pk = col
            pk_marker = " [PRIMARY KEY]" if pk else ""
            null_marker = " [NOT NULL]" if not_null else ""
            print(f"  • {name}: {col_type}{pk_marker}{null_marker}")

        # Get row count
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"\nRow Count: {count}")

        # Show sample data if available
        if count > 0:
            cursor.execute(f"SELECT * FROM {table} LIMIT 3")
            rows = cursor.fetchall()
            col_names = [col[1] for col in columns]

            print("\nSample Data (first 3 rows):")
            for i, row in enumerate(rows, 1):
                print(f"\n  Row {i}:")
                for col_name, value in zip(col_names, row):
                    # Truncate long values
                    str_value = str(value)
                    if len(str_value) > 50:
                        str_value = str_value[:47] + "..."
                    print(f"    {col_name}: {str_value}")

        # Get indexes
        cursor.execute(f"PRAGMA index_list({table})")
        indexes = cursor.fetchall()
        if indexes:
            print("\nIndexes:")
            for idx in indexes:
                idx_name = idx[1]
                cursor.execute(f"PRAGMA index_info({idx_name})")
                idx_cols = cursor.fetchall()
                cols = [col[2] for col in idx_cols]
                print(f"  • {idx_name} on ({', '.join(cols)})")

        print()

    # Foreign key information
    print("─" * 70)
    print("🔗 Foreign Key Relationships")
    print("─" * 70)

    for table in tables:
        cursor.execute(f"PRAGMA foreign_key_list({table})")
        fks = cursor.fetchall()
        if fks:
            print(f"\n{table}:")
            for fk in fks:
                print(f"  • {fk[3]} → {fk[2]}({fk[4]})")

    print("\n" + "=" * 70)

    # Summary statistics
    print("\n📈 SUMMARY STATISTICS")
    print("=" * 70)

    if has_multiuser:
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_active = 1")
        active_users = cursor.fetchone()[0]
        print(f"Active Users: {active_users}")

        cursor.execute("SELECT COUNT(DISTINCT user_id) FROM activities")
        users_with_activities = cursor.fetchone()[0]
        print(f"Users with Activities: {users_with_activities}")

    cursor.execute("SELECT COUNT(*) FROM activities")
    total_activities = cursor.fetchone()[0]
    print(f"Total Activities: {total_activities}")

    if total_activities > 0:
        cursor.execute("""
            SELECT type, COUNT(*) as count
            FROM activities
            GROUP BY type
            ORDER BY count DESC
        """)
        activity_types = cursor.fetchall()
        print("\nActivities by Type:")
        for act_type, count in activity_types:
            print(f"  • {act_type}: {count}")

    print("\n" + "=" * 70)

    conn.close()


if __name__ == '__main__':
    db_path = sys.argv[1] if len(sys.argv) > 1 else 'strava_activities.db'
    inspect_database(db_path)
