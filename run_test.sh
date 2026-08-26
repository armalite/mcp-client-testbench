#!/usr/bin/env bash
# Runs one testbench scenario: starts the server, drives a headless Claude
# session against it, prints a verdict, and leaves full logs in results/.
#
# Usage: ./run_test.sh <test-name> <mode>
#   test-name: label for the results files (e.g. headers, progress)
#   mode:      fast | silent | progress | sse-comments
#
# Tunables (env vars):
#   TIMEOUT_MS    per-server timeout in the MCP config (default 60000)
#   DURATION      seconds the probe tool takes (default 90)
#   INTERVAL      seconds between progress/keep-alive events (default 5)
#   PORT          server port (default 8765)
#   CLAUDE_MODEL  model for the headless session (default haiku, cheapest)
set -u

TEST_NAME="${1:?usage: run_test.sh <test-name> <mode>}"
MODE="${2:?usage: run_test.sh <test-name> <mode>}"
TIMEOUT_MS="${TIMEOUT_MS:-60000}"
DURATION="${DURATION:-90}"
INTERVAL="${INTERVAL:-5}"
PORT="${PORT:-8765}"
CLAUDE_MODEL="${CLAUDE_MODEL:-haiku}"

DIR="$(cd "$(dirname "$0")" && pwd)"
RESULTS="$DIR/results"
mkdir -p "$RESULTS"
SERVER_LOG="$RESULTS/${TEST_NAME}_server.log"
CLIENT_LOG="$RESULTS/${TEST_NAME}_client.log"
MCP_CONFIG="$RESULTS/${TEST_NAME}_mcp.json"
: > "$SERVER_LOG"

command -v claude >/dev/null || { echo "ERROR: claude CLI not found on PATH"; exit 2; }
command -v python3 >/dev/null || { echo "ERROR: python3 not found"; exit 2; }

echo "=== $TEST_NAME (mode=$MODE, duration=${DURATION}s, timeout=${TIMEOUT_MS}ms, interval=${INTERVAL}s) ==="
echo "claude version: $(claude --version 2>/dev/null)"

python3 "$DIR/server.py" --port "$PORT" --mode "$MODE" \
  --duration "$DURATION" --interval "$INTERVAL" --log "$SERVER_LOG" \
  >/dev/null 2>&1 &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null' EXIT
sleep 1
kill -0 "$SERVER_PID" 2>/dev/null || { echo "ERROR: server failed to start (port $PORT busy?)"; exit 2; }

cat > "$MCP_CONFIG" <<EOF
{"mcpServers":{"testbench":{"type":"http","url":"http://127.0.0.1:${PORT}/mcp","timeout":${TIMEOUT_MS}}}}
EOF

PROMPT="Call the probe tool from the testbench MCP server exactly once. Do not retry. Then report VERBATIM either the tool's result text or the exact error message you received."
CLIENT_TIMEOUT=$(( DURATION + (TIMEOUT_MS / 1000) + 120 ))

timeout "$CLIENT_TIMEOUT" claude -p "$PROMPT" \
  --mcp-config "$MCP_CONFIG" --strict-mcp-config \
  --dangerously-skip-permissions --max-turns 3 \
  --model "$CLAUDE_MODEL" > "$CLIENT_LOG" 2>&1
kill "$SERVER_PID" 2>/dev/null
wait "$SERVER_PID" 2>/dev/null
trap - EXIT

echo "--- client said:"
sed 's/^/    /' "$CLIENT_LOG"
echo "--- server timeline (sends + disconnects):"
grep -E "tools/call received|sent |CLIENT " "$SERVER_LOG" | sed 's/^/    /'

echo "--- verdict:"
if grep -q "PROBE_COMPLETED_OK" "$CLIENT_LOG"; then
  echo "    TOOL RESULT DELIVERED: client received the completed result."
  [ "$MODE" != "fast" ] && echo "    (call survived past the configured timeout: timer was extended or not enforced)"
elif grep -q "timed out after" "$CLIENT_LOG"; then
  echo "    CLIENT TIMED OUT: $(grep -o 'timed out after [0-9]*s' "$CLIENT_LOG" | head -1)"
  case "$MODE" in
    progress)     echo "    Progress notifications were being delivered (see timeline) but did NOT extend the timeout." ;;
    sse-comments) echo "    SSE keep-alive comments were being delivered (see timeline) but did NOT extend the timeout." ;;
    silent)       echo "    Expected for silent mode: nothing arrived before the timeout (control case)." ;;
  esac
else
  echo "    INCONCLUSIVE: inspect $CLIENT_LOG and $SERVER_LOG"
fi
echo
