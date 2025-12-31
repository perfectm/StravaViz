# Multi-User Dashboard Implementation Plan

## Overview

Transform StravaViz from a single-user application to a multi-user club dashboard where each club member can authenticate individually. This provides full access to each athlete's activity data with proper privacy controls and rich metadata.

## Architecture Changes

### Current Architecture
- Single-user: Uses one refresh token for one athlete
- Personal dashboard only
- Basic club view with limited data (club activities endpoint)

### Target Architecture
- Multi-user: Each club member authenticates via OAuth
- User management with privacy controls
- Individual + aggregated club views
- Full activity data for authenticated users

## Implementation Phases

---

## Phase 1: Database Schema Updates

### New Tables

#### 1. `users` Table
```sql
CREATE TABLE users (
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
    privacy_level TEXT DEFAULT 'club_only',  -- 'public', 'club_only', 'private'
    is_active BOOLEAN DEFAULT 1
);
```

#### 2. Update `activities` Table
```sql
CREATE TABLE activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,  -- NEW: Link to users table
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
    UNIQUE(user_id, activity_id),  -- Composite unique constraint
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
```

#### 3. `club_memberships` Table
```sql
CREATE TABLE club_memberships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    club_id INTEGER NOT NULL,
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, club_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
```

#### 4. `sessions` Table
```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,  -- UUID
    user_id INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
```

### Migration Script
Create `migrations/001_multiuser_schema.py`:
- Backup existing database
- Create new tables
- Migrate existing single-user data to multi-user structure
- Add indexes for performance

---

## Phase 2: Authentication System

### OAuth 2.0 Flow Implementation

#### 1. Environment Variables
Add to `.env`:
```bash
# OAuth Configuration
OAUTH_REDIRECT_URI=http://localhost:8001/auth/callback
SESSION_SECRET=your_session_secret_here
COOKIE_SECURE=false  # Set to true in production with HTTPS

# Optional: Auto-join club
DEFAULT_CLUB_ID=1577284
```

#### 2. OAuth Routes

##### `/auth/login` - Initiate OAuth
- Redirect to Strava authorization URL
- Request scopes: `read,activity:read_all`
- Store state parameter in session for CSRF protection

##### `/auth/callback` - OAuth Callback
- Validate state parameter
- Exchange authorization code for tokens
- Fetch athlete profile
- Create/update user record
- Store tokens (encrypted in production)
- Create session
- Set secure HTTP-only cookie
- Redirect to dashboard

##### `/auth/logout` - Logout
- Clear session from database
- Clear cookie
- Redirect to landing page

#### 3. Session Management
```python
# middleware/auth.py
from fastapi import Request, HTTPException, Depends
from fastapi.responses import RedirectResponse

async def get_current_user(request: Request):
    """
    Dependency to get currently authenticated user.
    Checks session cookie, validates session, returns user object.
    """
    session_id = request.cookies.get('session_id')
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Validate session, check expiry
    user = get_user_from_session(session_id)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid session")

    return user

async def optional_user(request: Request):
    """
    Optional authentication - returns user if logged in, None otherwise.
    """
    try:
        return await get_current_user(request)
    except HTTPException:
        return None
```

#### 4. Token Refresh Logic
```python
def refresh_user_token(user_id):
    """
    Refresh access token when expired.
    Called automatically before Strava API requests.
    """
    user = get_user(user_id)
    if user.token_expires_at < time.time():
        # Use refresh token to get new access token
        response = requests.post(STRAVA_TOKEN_URL, {
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'grant_type': 'refresh_token',
            'refresh_token': user.refresh_token
        })
        # Update user record with new tokens
```

---

## Phase 3: Route Updates

### 1. Landing Page - `/`
- Public landing page explaining the app
- "Login with Strava" button
- Show club stats if DEFAULT_CLUB_ID is set
- Redirect to `/dashboard` if already authenticated

### 2. Personal Dashboard - `/dashboard`
- **Protected route** (requires authentication)
- Show logged-in athlete's personal statistics
- Individual activity charts (Walk/Hike/Run)
- Recent activities table
- Link to club view

### 3. Club Dashboard - `/club`
- **Protected route**
- Show club members who have authenticated the app
- Aggregated statistics across all authenticated members
- Privacy filtering (respects each user's privacy_level)
- Leaderboards:
  - Total distance
  - Most active (activity count)
  - Longest single activity
  - Most elevation gain
- Activity feed (recent activities from all members)
- Individual athlete comparison charts

### 4. Settings - `/settings`
- **Protected route**
- Privacy level selection
- Account management
- Data sync controls
- Disconnect account option

---

## Phase 4: Data Synchronization

### Background Sync System

#### 1. Sync Service
```python
# services/activity_sync.py
class ActivitySyncService:
    def sync_user_activities(self, user_id):
        """
        Fetch new activities for a specific user.
        Called on login and periodically.
        """
        user = get_user(user_id)
        token = ensure_fresh_token(user)

        # Get latest activity date from DB
        last_sync = get_last_activity_date(user_id)

        # Fetch new activities from Strava
        activities = fetch_activities_since(token, last_sync)

        # Store in database
        save_activities(user_id, activities)

        return len(activities)

    def sync_all_users(self):
        """
        Background job to sync all active users.
        Run every 15 minutes via scheduler.
        """
        active_users = get_active_users()
        for user in active_users:
            try:
                self.sync_user_activities(user.id)
            except Exception as e:
                log_error(f"Sync failed for user {user.id}: {e}")
```

#### 2. Background Scheduler
Use APScheduler or similar:
```python
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()
scheduler.add_job(
    sync_service.sync_all_users,
    'interval',
    minutes=15,
    id='sync_all_users'
)
scheduler.start()
```

---

## Phase 5: Privacy & Permissions

### Privacy Levels

#### 1. Public
- Data visible to all club dashboard visitors
- Name shown in full
- Activities visible in club feed

#### 2. Club Only (Default)
- Data visible only to other authenticated club members
- Name shown in full to club members
- Activities visible in club feed

#### 3. Private
- Data not shown in club views
- Only visible in personal dashboard
- Completely excluded from leaderboards

### Implementation
```python
def get_club_activities_with_privacy(club_id, requesting_user_id=None):
    """
    Fetch club activities respecting privacy settings.
    """
    members = get_club_members(club_id)

    activities = []
    for member in members:
        # Check privacy level
        if member.privacy_level == 'private':
            continue
        elif member.privacy_level == 'club_only':
            if not requesting_user_id or not is_club_member(requesting_user_id, club_id):
                continue

        # Fetch member's activities
        member_activities = get_activities(member.id)
        activities.extend(member_activities)

    return activities
```

---

## Phase 6: UI/UX Enhancements

### 1. Landing Page Template (`landing.html`)
- Hero section with app description
- "Login with Strava" OAuth button
- Preview of club statistics (if public)
- Screenshots/demo

### 2. Navigation Updates
- Show username and profile picture when logged in
- Logout button
- Settings link
- Dropdown menu for navigation

### 3. Club Dashboard Enhancements
- Tabs for different views:
  - Overview (stats)
  - Leaderboards
  - Activity Feed
  - Member List
- Filter controls:
  - Date range picker
  - Activity type filter
  - Athlete filter
- Export functionality (CSV/PDF)

### 4. Personal Dashboard
- Goal tracking (weekly/monthly distance)
- Personal records (PR badges)
- Achievement tracking
- Comparison with club averages

---

## Phase 7: Testing & Security

### Security Checklist

- [ ] Encrypt tokens at rest (use cryptography library)
- [ ] Use HTTPS in production (COOKIE_SECURE=true)
- [ ] HTTP-only, SameSite cookies for sessions
- [ ] CSRF protection on OAuth flow (state parameter)
- [ ] Rate limiting on API endpoints
- [ ] SQL injection prevention (parameterized queries)
- [ ] Input validation on all user inputs
- [ ] Secure session ID generation (UUID4)
- [ ] Token expiry checks before API calls
- [ ] Audit logging for authentication events

### Testing Plan

#### Unit Tests
- Authentication flow
- Token refresh logic
- Privacy filtering
- Activity sync

#### Integration Tests
- OAuth callback flow
- Multi-user data isolation
- Club membership management

#### Manual Testing
- Login/logout flow
- Privacy settings changes
- Activity sync accuracy
- Dashboard rendering with multiple users

---

## Phase 8: Deployment Updates

### Environment Variables
```bash
# Production .env
STRAVA_CLIENT_ID=your_client_id
STRAVA_CLIENT_SECRET=your_client_secret
OAUTH_REDIRECT_URI=https://yourdomain.com/auth/callback
SESSION_SECRET=strong_random_secret_here
COOKIE_SECURE=true
DATABASE_URL=sqlite:///strava_multiuser.db
DEFAULT_CLUB_ID=1577284
```

### Nginx Configuration (if applicable)
```nginx
server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl;
    server_name yourdomain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Docker Support
Create `Dockerfile`:
```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8001

CMD ["uvicorn", "strava_fastapi:app", "--host", "0.0.0.0", "--port", "8001"]
```

Create `docker-compose.yml`:
```yaml
version: '3.8'
services:
  web:
    build: .
    ports:
      - "8001:8001"
    volumes:
      - ./strava_multiuser.db:/app/strava_multiuser.db
      - ./templates:/app/templates
    env_file:
      - .env
    restart: unless-stopped
```

---

## Implementation Timeline Estimate

### Week 1: Database & Authentication
- Days 1-2: Database schema design and migration
- Days 3-5: OAuth flow implementation
- Days 6-7: Session management and middleware

### Week 2: Core Features
- Days 1-3: Route updates (landing, dashboard, club)
- Days 4-5: Activity sync service
- Days 6-7: Privacy controls

### Week 3: UI/UX & Testing
- Days 1-3: Template redesign
- Days 4-5: Testing (unit + integration)
- Days 6-7: Security audit and fixes

### Week 4: Deployment & Polish
- Days 1-2: Deployment setup
- Days 3-4: Production testing
- Days 5-7: Documentation and bug fixes

**Total: ~4 weeks for full implementation**

---

## Dependencies to Add

```txt
# Add to requirements.txt
fastapi-sessions==0.3.2  # Session management
python-jose[cryptography]==3.3.0  # JWT tokens
passlib[bcrypt]==1.7.4  # Password hashing (if adding email/password backup)
apscheduler==3.10.4  # Background job scheduling
cryptography==41.0.7  # Token encryption
python-multipart==0.0.6  # Form data handling
httpx==0.25.2  # Async HTTP client for Strava API
```

---

## Key Design Decisions

### 1. Why SQLite?
- Sufficient for small-to-medium clubs (< 500 members)
- Zero configuration
- Easy backups
- Can migrate to PostgreSQL later if needed

### 2. Why Session Cookies vs JWT?
- HTTP-only cookies more secure than localStorage
- Server-side session management easier to invalidate
- Better for web dashboard (not API-first)

### 3. Why Background Sync vs On-Demand?
- Better user experience (data always fresh)
- Reduces Strava API rate limit issues
- Can batch requests efficiently

### 4. Privacy First
- Opt-in to club sharing (default: club-only)
- Easy to disconnect account
- No data shared without consent

---

## Future Enhancements (Post-MVP)

1. **Email Notifications**
   - Weekly summary emails
   - PR achievements
   - Club milestones

2. **Challenges & Competitions**
   - Monthly distance challenges
   - Segment leaderboards
   - Team competitions

3. **Advanced Analytics**
   - Trend analysis
   - Training load tracking
   - Comparative analytics

4. **Social Features**
   - Activity comments
   - Kudos tracking
   - Club announcements

5. **Mobile App**
   - React Native or Flutter app
   - Push notifications
   - Quick activity logging

6. **API Endpoints**
   - REST API for third-party integrations
   - Webhook support for real-time updates
   - API key management

---

## Migration Path for Existing Users

### For Current Single-User Setup

1. **Backup Current Data**
   ```bash
   cp strava_activities.db strava_activities_backup.db
   ```

2. **Run Migration**
   ```bash
   python migrations/001_multiuser_schema.py
   ```
   - Creates new tables
   - Migrates existing activities to user_id=1
   - Prompts for initial user setup

3. **Update .env File**
   - Add OAuth redirect URI
   - Add session secret
   - Keep existing credentials for migration user

4. **First Login**
   - Existing user logs in via OAuth
   - System links to migrated data
   - Other club members can now join

---

## Documentation Updates Needed

1. **README.md**
   - Update setup instructions
   - Add OAuth configuration steps
   - Update screenshots
   - Add multi-user features section

2. **CLAUDE.md**
   - Document new architecture
   - Update database schema
   - Add authentication flow details
   - Update key functions reference

3. **API_DOCS.md** (new)
   - Document all routes
   - Authentication requirements
   - Request/response formats
   - Error codes

4. **PRIVACY.md** (new)
   - Data collection policy
   - Privacy level explanations
   - User rights (data export, deletion)
   - GDPR compliance notes

---

## Risks & Mitigations

### Risk 1: Strava API Rate Limits
- **Mitigation**: Implement exponential backoff, cache data, sync in batches

### Risk 2: Token Security
- **Mitigation**: Encrypt tokens at rest, use secure cookies, regular security audits

### Risk 3: User Adoption
- **Mitigation**: Keep single-user flow simple, gradual rollout, clear privacy controls

### Risk 4: Database Growth
- **Mitigation**: Implement data retention policies, archive old activities, add pagination

### Risk 5: Session Management
- **Mitigation**: Session expiry, secure ID generation, regular cleanup of expired sessions

---

## Success Metrics

1. **Adoption**: 80%+ of club members authenticate within first month
2. **Engagement**: Daily active users > 30% of authenticated users
3. **Performance**: Page load < 2 seconds, sync time < 30 seconds
4. **Reliability**: 99.5%+ uptime, zero data loss incidents
5. **Privacy**: Zero privacy violation reports

---

## Conclusion

This multi-user dashboard transforms StravaViz from a personal tool to a full-featured club platform. The phased approach allows for iterative development and testing, while the privacy-first design ensures user trust and adoption.

The architecture is designed to scale from small clubs (10-20 members) to larger groups (100+ members) with minimal changes, and provides a foundation for future enhancements like challenges, social features, and mobile apps.
