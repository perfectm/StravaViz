#!/bin/bash

# Strava Dashboard Server Restart Script

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🔄 Restarting Strava Dashboard Server..."
echo "=================================="

# Stop the server
echo "1️⃣ Stopping server..."
bash "$SCRIPT_DIR/stop.sh"

# Wait a moment
echo ""
echo "⏳ Waiting 3 seconds..."
sleep 3

# Start the server
echo ""
echo "2️⃣ Starting server..."
bash "$SCRIPT_DIR/start.sh"

echo ""
echo "🎉 Restart complete!"