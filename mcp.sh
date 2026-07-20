#!/bin/bash
PORT=${1:-8002}
LOGDIR="/tmp/api2mcp"
LOG="$LOGDIR/server.log"
mkdir -p "$LOGDIR"

pid=$(netstat -tlnp 2>/dev/null | grep ":$PORT " | awk '{print $7}' | cut -d/ -f1)
if [ -n "$pid" ]; then
  echo "Port $PORT is in use by PID $pid, killing..."
  kill "$pid" && sleep 1 && echo "Killed."
fi

MCP_HOST=0.0.0.0 MCP_PORT=$PORT nohup python main.py > "$LOG" 2>&1 &
sleep 2
new_pid=$(netstat -tlnp 2>/dev/null | grep ":$PORT " | awk '{print $7}' | cut -d/ -f1)
echo "MCP server started on port $PORT (PID: $new_pid)"
tail -3 "$LOG"
