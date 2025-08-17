#!/bin/bash

# Strava Dashboard Server Stop Script

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/server.pid"
PORT=8001

echo "Stopping Strava Dashboard Server..."

# Check if PID file exists
if [ ! -f "$PID_FILE" ]; then
    echo "No PID file found. Checking for any uvicorn processes on port $PORT..."
    
    # Find and kill any process using the port
    PIDS=$(lsof -ti:$PORT 2>/dev/null)
    if [ -n "$PIDS" ]; then
        echo "Found processes using port $PORT: $PIDS"
        for pid in $PIDS; do
            echo "Killing process $pid..."
            kill -TERM "$pid" 2>/dev/null
            sleep 2
            if ps -p "$pid" > /dev/null 2>&1; then
                echo "Force killing process $pid..."
                kill -KILL "$pid" 2>/dev/null
            fi
        done
        echo "✅ Stopped processes using port $PORT"
    else
        echo "ℹ️ No server processes found"
    fi
    exit 0
fi

# Read PID from file
PID=$(cat "$PID_FILE")

# Check if process is running
if ! ps -p "$PID" > /dev/null 2>&1; then
    echo "⚠️ Process $PID is not running. Removing stale PID file..."
    rm -f "$PID_FILE"
    exit 0
fi

echo "Stopping server with PID $PID..."

# Try graceful shutdown first
kill -TERM "$PID" 2>/dev/null

# Wait up to 10 seconds for graceful shutdown
for i in {1..10}; do
    if ! ps -p "$PID" > /dev/null 2>&1; then
        echo "✅ Server stopped gracefully"
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 1
    echo -n "."
done

echo ""
echo "⚠️ Graceful shutdown failed. Force killing..."

# Force kill if still running
kill -KILL "$PID" 2>/dev/null

# Wait a bit more
sleep 2

if ps -p "$PID" > /dev/null 2>&1; then
    echo "❌ Failed to stop server process $PID"
    exit 1
else
    echo "✅ Server force stopped"
    rm -f "$PID_FILE"
fi