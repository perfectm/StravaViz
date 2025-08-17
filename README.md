# Strava Activity Dashboard

A FastAPI web application that connects to the Strava API to fetch and visualize your walking, hiking, and running activities with interactive graphs.

## Features

- **Strava API Integration**: Fetches activity data using OAuth refresh tokens
- **Activity Filtering**: Focuses on Walk, Hike, and Run activities
- **Data Persistence**: Stores activities in SQLite database for efficient incremental updates
- **Visual Analytics**: Generates multiple chart types:
  - Combined stacked bar chart showing monthly distances for all activity types
  - Individual bar charts for each activity type (Walk, Hike, Run)
- **Web Interface**: Displays graphs as embedded PNG images in HTML

## Setup

### Prerequisites

- Python 3.7+
- Strava Developer Account
- Strava API credentials (Client ID, Client Secret, Refresh Token)

### Installation

1. Clone this repository:
   ```bash
   git clone <repository-url>
   cd strava-dashboard
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Set up environment variables:
   ```bash
   cp .env.example .env
   # Edit .env with your Strava API credentials
   ```

4. Configure your Strava API credentials in the `.env` file:
   ```
   STRAVA_CLIENT_ID=your_client_id
   STRAVA_CLIENT_SECRET=your_client_secret
   STRAVA_REFRESH_TOKEN=your_refresh_token
   ```

### Getting Strava API Credentials

1. Go to [Strava Developers](https://developers.strava.com/)
2. Create a new application
3. Note your Client ID and Client Secret
4. Follow Strava's OAuth flow to get a refresh token

## Usage

1. Start the FastAPI server:
   ```bash
   uvicorn strava_fastapi:app --reload
   ```

2. Open your browser to `http://localhost:8000`

3. The application will:
   - Fetch new activities from Strava API
   - Store them in the local SQLite database
   - Generate and display activity graphs

## Database

The application uses SQLite to store activity data locally in `strava_activities.db`. The database schema includes:

- `activity_id`: Unique Strava activity identifier
- `name`: Activity name
- `type`: Activity type (Walk, Hike, Run)
- `start_date`: Activity start date
- `distance`: Distance in meters

## API Endpoints

- `GET /`: Main dashboard displaying activity graphs

## Development

The application automatically handles incremental data updates by:
1. Checking the latest activity date in the local database
2. Fetching only new activities from Strava API
3. Storing new activities while avoiding duplicates

## License

MIT License