#!/bin/bash

# Strava Dashboard Server Status Script

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/server.pid"
LOG_FILE="$SCRIPT_DIR/server.log"
PORT=8001

echo "📊 Strava Dashboard Server Status"
echo "================================="

# Check PID file
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    echo "🆔 PID File: $PID"
    
    # Check if process is actually running
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "✅ Status: RUNNING"
        echo "🌐 URL: http://localhost:$PORT"
        
        # Check if port is responding
        if curl -s -o /dev/null -w "%{http_code}" http://localhost:$PORT/ | grep -q "200"; then
            echo "🟢 Health: HEALTHY (responding to requests)"
        else
            echo "🟡 Health: UNHEALTHY (process running but not responding)"
        fi
        
        # Show process info
        echo "⏰ Started: $(ps -p "$PID" -o lstart= 2>/dev/null | xargs)"
        echo "💾 Memory: $(ps -p "$PID" -o rss= 2>/dev/null | awk '{printf "%.1f MB", $1/1024}')"
        
    else
        echo "❌ Status: STOPPED (stale PID file)"
        echo "⚠️ Removing stale PID file..."
        rm -f "$PID_FILE"
    fi
else
    echo "❌ Status: STOPPED (no PID file)"
    
    # Check if something else is using the port
    PIDS=$(lsof -ti:$PORT 2>/dev/null)
    if [ -n "$PIDS" ]; then
        echo "⚠️ Warning: Port $PORT is in use by other processes: $PIDS"
    fi
fi

# Log file info
if [ -f "$LOG_FILE" ]; then
    echo ""
    echo "📝 Log File: $LOG_FILE"
    echo "📏 Log Size: $(du -h "$LOG_FILE" | cut -f1)"
    echo "🕐 Last Modified: $(stat -f "%Sm" -t "%Y-%m-%d %H:%M:%S" "$LOG_FILE" 2>/dev/null || stat -c "%y" "$LOG_FILE" 2>/dev/null | cut -d. -f1)"
    
    # Show last few lines if server is not running
    if [ ! -f "$PID_FILE" ] || ! ps -p "$(cat "$PID_FILE" 2>/dev/null)" > /dev/null 2>&1; then
        echo ""
        echo "📖 Last 5 log entries:"
        echo "----------------------"
        tail -5 "$LOG_FILE" 2>/dev/null || echo "Unable to read log file"
    fi
else
    echo ""
    echo "📝 Log File: Not found"
fi