# Database Migrations

This directory contains database migration scripts for StravaViz.

## Available Migrations

### 001_multiuser_schema.py
**Status**: ✅ Complete
**Purpose**: Transform single-user database to multi-user schema

This migration adds support for multiple users by:
- Creating `users` table for user authentication
- Updating `activities` table to link activities to users
- Creating `club_memberships` table for club associations
- Creating `sessions` table for session management
- Migrating existing data to user_id=1 (legacy user)
- Adding performance indexes

## Running Migrations

### Execute Migration
```bash
python migrations/001_multiuser_schema.py
```

### Rollback Migration
```bash
python migrations/001_multiuser_schema.py --rollback
```

## Migration Process

1. **Backup**: Automatic backup created before migration
2. **Schema Creation**: New tables and columns added
3. **Data Migration**: Existing data migrated to new structure
4. **Indexing**: Performance indexes created
5. **Verification**: Automatic verification of migration success

## Database Schema After Migration

### users
- `id` - Primary key
- `strava_athlete_id` - Unique Strava athlete identifier
- `firstname`, `lastname` - User name
- `profile_picture` - Profile image URL
- `access_token`, `refresh_token` - OAuth tokens
- `token_expires_at` - Token expiration timestamp
- `created_at`, `last_login` - Timestamps
- `privacy_level` - 'public', 'club_only', or 'private'
- `is_active` - Boolean active status

### activities
- `id` - Primary key
- `user_id` - Foreign key to users table
- `activity_id` - Strava activity ID
- `name`, `type` - Activity details
- `start_date` - Activity start timestamp
- `distance` - Distance in meters
- `moving_time`, `elapsed_time` - Time metrics
- `total_elevation_gain` - Elevation in meters
- `average_speed`, `max_speed` - Speed metrics
- `average_heartrate`, `max_heartrate` - Heart rate data
- `calories` - Calories burned

### club_memberships
- `id` - Primary key
- `user_id` - Foreign key to users
- `club_id` - Strava club ID
- `joined_at` - Timestamp

### sessions
- `id` - Session UUID
- `user_id` - Foreign key to users
- `created_at` - Session creation timestamp
- `expires_at` - Session expiration timestamp

## Backward Compatibility

The application code maintains backward compatibility:
- Detects whether multi-user schema exists
- Falls back to legacy single-user mode if migration not run
- Uses user_id=1 as default for single-user operations

## Next Steps

After completing this migration:
1. ✅ Database schema is ready for multi-user
2. 🔄 Proceed to Phase 2: Authentication System (OAuth implementation)
3. 🔄 Implement user management routes
4. 🔄 Add privacy controls
5. 🔄 Background sync for multiple users

## Troubleshooting

### Migration Failed
- Check `server.log` for error details
- Backup is automatically created before migration
- Use `--rollback` to restore from backup

### Verification Failed
- Ensure no other processes are accessing the database
- Check file permissions on `strava_activities.db`
- Review migration script output for specific errors

### Data Loss Concerns
- All migrations create automatic backups
- Backups are timestamped and include migration name
- Original table preserved as `activities_old` after migration

## File Structure
```
migrations/
├── __init__.py                    # Python package marker
├── README.md                      # This file
└── 001_multiuser_schema.py       # Multi-user schema migration
```
