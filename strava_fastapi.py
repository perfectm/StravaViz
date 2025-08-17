from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import requests
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend for FastAPI
import matplotlib.pyplot as plt
import io
import base64
import os
from dotenv import load_dotenv
import sqlite3

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Load environment variables from .env file
load_dotenv()

# Set your Strava API credentials here
CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("STRAVA_REFRESH_TOKEN")

STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"

# Helper to get access token
def get_access_token():
    response = requests.post(
        STRAVA_TOKEN_URL,
        data={
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'grant_type': 'refresh_token',
            'refresh_token': REFRESH_TOKEN
        }
    )
    return response.json().get('access_token')

# Helper to get activities
def get_activities(token, per_page=200, pages=1):
    activities = []
    for page in range(1, pages+1):
        resp = requests.get(
            STRAVA_ACTIVITIES_URL,
            headers={'Authorization': f'Bearer {token}'},
            params={'per_page': per_page, 'page': page}
        )
        if resp.status_code == 200:
            activities.extend(resp.json())
        else:
            break
    return activities

def init_db():
    conn = sqlite3.connect('strava_activities.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS activities (
        id INTEGER PRIMARY KEY,
        activity_id INTEGER UNIQUE,
        name TEXT,
        type TEXT,
        start_date TEXT,
        distance REAL
    )''')
    conn.commit()
    conn.close()

# Save new activities to DB and return all activities as DataFrame
def save_and_get_activities(all_activities):
    conn = sqlite3.connect('strava_activities.db')
    c = conn.cursor()
    # Insert only new activities
    for act in all_activities:
        try:
            c.execute('''INSERT INTO activities (activity_id, name, type, start_date, distance) VALUES (?, ?, ?, ?, ?)''',
                (act['id'], act['name'], act['type'], act['start_date'], act['distance']))
        except sqlite3.IntegrityError:
            continue  # Already in DB
    conn.commit()
    df = pd.read_sql_query('SELECT * FROM activities', conn)
    conn.close()
    return df

@app.on_event('startup')
def startup_event():
    init_db()

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    token = get_access_token()
    # Get latest activity in DB
    conn = sqlite3.connect('strava_activities.db')
    c = conn.cursor()
    c.execute('SELECT MAX(start_date) FROM activities')
    last_date = c.fetchone()[0]
    conn.close()
    # Download new activities only
    activities = []
    page = 1
    while True:
        acts = get_activities(token, per_page=200, pages=1)
        if not acts:
            break
        # If last_date is set, filter out already downloaded
        if last_date:
            acts = [a for a in acts if a['start_date'] > last_date]
        if not acts:
            break
        activities.extend(acts)
        if len(acts) < 200:
            break
        page += 1
    # Save new and get all
    df = save_and_get_activities(activities)
    if df.empty:
        debug_html = f"""
        <h2>No activities found or Strava API error.</h2>
        <h3>Debug Info:</h3>
        <ul>
            <li>Token: {'Set' if token else 'NOT SET'}</li>
            <li>Activities fetched: {len(activities)}</li>
            <li>CLIENT_ID: {'Set' if CLIENT_ID else 'NOT SET'}</li>
            <li>CLIENT_SECRET: {'Set' if CLIENT_SECRET else 'NOT SET'}</li>
            <li>REFRESH_TOKEN: {'Set' if REFRESH_TOKEN else 'NOT SET'}</li>
        </ul>
        <pre>Sample activities: {activities[:2] if activities else 'None'}</pre>
        """
        return HTMLResponse(debug_html)
    df = df[df['type'].isin(['Walk', 'Hike', 'Run'])]
    df['start_date'] = pd.to_datetime(df['start_date'])
    df['distance_km'] = df['distance'] / 1000
    df['month'] = df['start_date'].dt.to_period('M').dt.to_timestamp()
    grouped = df.groupby(['month', 'type'])['distance_km'].sum().unstack(fill_value=0)
    # Plot combined stacked bar (already done above)
    plt.figure(figsize=(12,6))
    grouped.plot(kind='bar', stacked=True, ax=plt.gca())
    plt.title('Monthly Distances Walked, Hiked, and Ran')
    plt.xlabel('Month')
    plt.ylabel('Distance (km)')
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    plt.close()
    buf.seek(0)
    img_base64 = base64.b64encode(buf.read()).decode('utf-8')

    # Additional graphs for each activity type
    activity_imgs = {}
    for activity in ['Walk', 'Hike', 'Run']:
        if activity in grouped.columns:
            plt.figure(figsize=(10,5))
            grouped[activity].plot(kind='bar', color='skyblue', ax=plt.gca())
            plt.title(f'Monthly Distance: {activity}')
            plt.xlabel('Month')
            plt.ylabel('Distance (km)')
            plt.tight_layout()
            buf2 = io.BytesIO()
            plt.savefig(buf2, format='png')
            plt.close()
            buf2.seek(0)
            activity_imgs[activity] = base64.b64encode(buf2.read()).decode('utf-8')

    html = f"""
    <h1>Strava Monthly Distances Walked, Hiked, and Ran</h1>
    <img src='data:image/png;base64,{img_base64}'/>
    <h2>Walks</h2>
    {f"<img src='data:image/png;base64,{activity_imgs['Walk']}'/>" if 'Walk' in activity_imgs else '<p>No walk data.</p>'}
    <h2>Hikes</h2>
    {f"<img src='data:image/png;base64,{activity_imgs['Hike']}'/>" if 'Hike' in activity_imgs else '<p>No hike data.</p>'}
    <h2>Runs</h2>
    {f"<img src='data:image/png;base64,{activity_imgs['Run']}'/>" if 'Run' in activity_imgs else '<p>No run data.</p>'}
    """
    return HTMLResponse(content=html)

# To run: uvicorn strava_fastapi:app --reload
