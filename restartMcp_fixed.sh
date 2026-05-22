#!/bin/bash
set -e

PORT=8002
DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON=/opt/conda/bin/python3

# Kill existing process on the port
PID=$(ss -tlnp 2>/dev/null | grep ":${PORT} " | grep -oP 'pid=\K[0-9]+')
if [ -n "$PID" ]; then
    echo "Killing existing MCP server (PID $PID)..."
    kill $PID 2>/dev/null
    sleep 1
fi

# Start new server
cd "$DIR"
echo "Starting MCP server on port $PORT..."
nohup $PYTHON main.py > /tmp/mcp_server.log 2>&1 &
MCP_PORT=$PORT

sleep 3
if ss -tlnp 2>/dev/null | grep -q ":${PORT} "; then
    PID=$(ss -tlnp 2>/dev/null | grep ":${PORT} " | grep -oP 'pid=\K[0-9]+')
    echo "MCP server started (PID $PID) on port $PORT."
else
    echo "ERROR: Server failed to start. Check /tmp/mcp_server.log"
    tail -30 /tmp/mcp_server.log
    exit 1
fi
