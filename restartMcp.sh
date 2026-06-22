#!/bin/bash

PORT=8002
DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="python3"

# Kill existing process on the port (macOS-compatible)
PID=$(lsof -ti :$PORT 2>/dev/null)
if [ -n "$PID" ]; then
    echo "Killing existing MCP server (PID $PID)..."
    kill $PID 2>/dev/null || true
    sleep 1
    if kill -0 $PID 2>/dev/null; then
        kill -9 $PID 2>/dev/null || true
        sleep 1
    fi
fi

# Start new server
cd "$DIR"
echo "Starting MCP server on port $PORT..."
env MCP_PORT=$PORT nohup $PYTHON main.py > /Users/apple/claude/log/mcp_server.log 2>&1 &

sleep 3
if lsof -ti :$PORT >/dev/null 2>&1; then
    PID=$(lsof -ti :$PORT 2>/dev/null)
    echo "MCP server started (PID $PID) on port $PORT."
else
    echo "ERROR: Server failed to start. Check /Users/apple/claude/log/mcp_server.log"
    tail -30 /Users/apple/claude/log/mcp_server.log
    exit 1
fi
