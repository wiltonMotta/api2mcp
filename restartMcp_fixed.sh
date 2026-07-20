#!/bin/bash
set -e

# Usage: restartMcp_fixed.sh [prod|test]
#   prod (default): production on port 8002
#   test: test env on port 8003 with itos2.sugon.com URLs

MODE=${1:-prod}

if [ "$MODE" = "test" ]; then
    PORT=8003
    export SCNET_TOKEN_URL="https://itos2.sugon.com/api/user/v3/tokens"
    export SCNET_CENTER_URL="https://itos2.sugon.com/ac/openapi/v2/center"
    export SCNET_USER_URL="https://itos2.sugon.com/ac/openapi/v2/user"
    export SCNET_RENEW_TOKEN_URL="https://itos2.sugon.com/ac/openapi/v2/tokens/next"
    export SCNET_TOKEN_STATE_URL="https://itos2.sugon.com/ac/openapi/v2/tokens/state"
    export MCP_AUTH_PREFIX="auth_test"
    export no_proxy="127.0.0.1,localhost"
    LOGFILE="/tmp/api2mcp_test.log"
    DESC="TEST"
else
    PORT=8002
    LOGFILE="/tmp/api2mcp.log"
    DESC="PROD"
    export no_proxy="127.0.0.1,localhost"
fi

DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PYTHON=/opt/conda/bin/python3
export MCP_PORT=$PORT

# Kill existing process on the port
PID=$(ss -tlnp 2>/dev/null | grep ":${PORT} " | grep -oP 'pid=\K[0-9]+' || true)
if [ -n "$PID" ]; then
    echo "[${DESC}] Killing existing MCP server (PID $PID)..."
    # Try regular kill first; fall back to sudo if that fails
    kill $PID 2>/dev/null || sudo kill $PID 2>/dev/null || true
    sleep 2
fi

# Start new server
cd "$DIR"
echo "[${DESC}] Starting MCP server on port $PORT..."
rm -f "$LOGFILE" 2>/dev/null || true
nohup $PYTHON main.py > "$LOGFILE" 2>&1 &

sleep 3
if ss -tlnp 2>/dev/null | grep -q ":${PORT} "; then
    PID=$(ss -tlnp 2>/dev/null | grep ":${PORT} " | grep -oP 'pid=\K[0-9]+')
    echo "[${DESC}] MCP server started (PID $PID) on port $PORT."
    if [ "$MODE" = "test" ]; then
        echo "[${DESC}] Environment: itos2.sugon.com"
    else
        echo "[${DESC}] Environment: scnet.cn (production)"
    fi
else
    echo "[${DESC}] ERROR: Server failed to start. Check $LOGFILE"
    tail -30 "$LOGFILE"
    exit 1
fi
