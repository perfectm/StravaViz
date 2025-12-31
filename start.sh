#!/bin/bash

# Strava Dashboard Server Start Script

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/server.pid"
LOG_FILE="$SCRIPT_DIR/server.log"
PORT=8002

echo "Starting Strava Dashboard Server..."

# Check if server is already running
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "Server is already running with PID $PID"
        echo "Visit: http://localhost:$PORT"
        exit 1
    else
        echo "Removing stale PID file..."
        rm -f "$PID_FILE"
    fi
fi

# Change to script directory
cd "$SCRIPT_DIR"

# Start the server in background
echo "Launching uvicorn server on port $PORT..."
nohup uvicorn strava_fastapi:app --host 0.0.0.0 --port $PORT --reload > "$LOG_FILE" 2>&1 &

# Save the PID
echo $! > "$PID_FILE"

# Wait a moment for server to start
sleep 2

# Check if server started successfully
if ps -p $(cat "$PID_FILE") > /dev/null 2>&1; then
    echo "✅ Server started successfully!"
    echo "📊 Dashboard available at: http://localhost:$PORT"
    echo "📝 Logs: $LOG_FILE"
    echo "🆔 PID: $(cat "$PID_FILE")"
else
    echo "❌ Failed to start server. Check logs: $LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
fi
