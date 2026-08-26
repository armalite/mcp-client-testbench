# mcp-client-testbench

A controlled test rig for MCP client timeout and streaming behaviour.

It spins up a small, dependency-free local MCP server whose one tool
(`probe`) can be made arbitrarily slow, and can stream JSON-RPC progress
notifications or SSE keep-alive comments while it works. It then drives a
real headless Claude session against that server and reports how the client
behaved: what headers it sent, whether it timed out, and whether streamed
events extended its timeout.

Because the server is local, this isolates the CLIENT leg completely: no
connector platform, no vendor infrastructure, no network in between.

## Prerequisites

- Claude Code CLI installed and logged in (`claude --version` works)
- Python 3.8+ (stdlib only, nothing to install)
- make

Each test run drives a real (headless) Claude session, so it consumes a few
cents of model usage. Tests default to the cheapest model (haiku); the model
never affects timeout behaviour, only the driving of the tool call.

## Quick start

```bash
make test-all        # every scenario, ~4-5 minutes with defaults
```

Or one scenario at a time:

```bash
make test-headers        # capture exactly what headers the client sends (~30s)
make test-silent         # control: 90s tool, nothing streamed, 60s timeout -> times out
make test-progress       # 90s tool + progress notifications every 5s -> does the timeout extend?
make test-sse-comments   # 90s tool + SSE comment keep-alives every 5s -> does the timeout extend?
```

Every run prints the client's reported outcome, the server's timeline
(exactly what was sent and when the client disconnected), and a one-line
verdict. Full logs land in `results/`.

## Tunables

Pass as make variables or environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `TIMEOUT_MS` | 60000 | per-server `timeout` in the MCP config given to the client |
| `DURATION` | 90 | seconds the probe tool takes to complete |
| `INTERVAL` | 5 | seconds between progress/keep-alive events |
| `PORT` | 8765 | local server port |
| `CLAUDE_MODEL` | haiku | model for the headless session |

Example: `make test-progress TIMEOUT_MS=30000 DURATION=45`

## Interpreting results

- `test-headers`: shows the raw headers the client sends. Per the MCP
  streamable HTTP spec, every POST should carry
  `Accept: application/json, text/event-stream`.
- `test-silent` timing out is EXPECTED: it's the control showing what the
  configured timeout does when nothing arrives.
- `test-progress` is the key scenario. The server sends spec-compliant
  `notifications/progress` correlated to the client's own progressToken.
  If the client still times out at exactly `TIMEOUT_MS`, its timeout is a
  hard cap that progress does not extend (see
  https://github.com/anthropics/claude-code/issues/58687). If it survives
  to `DURATION`, the client extends its timer on progress.
- `test-sse-comments` distinguishes transport-level keep-alives (SSE
  comments, which never reach the JSON-RPC layer) from protocol-level
  progress notifications. Useful when a server vendor says "we send
  keep-alives": this shows whether that kind helps at all.

Results are specific to the client version (printed at the top of each
run). Re-run after client updates to detect behaviour changes.

## Testing the connector-platform path (optional)

Run locally, this rig tests only the direct client-to-server leg. To see
what an org connector platform (e.g. claude.ai connectors) sends to an MCP
server and whether it passes streams through:

1. Expose the server publicly: `cloudflared tunnel --url http://127.0.0.1:8765`
2. Have your Claude org admin register the tunnel URL as a custom connector.
3. Call the probe tool from a normal Claude session using that connector.
4. Read `results/` / server output: the logged headers are now what the
   PLATFORM sends, and the timeline shows whether it consumed the stream.
5. Tear the connector and tunnel down afterwards.

Note the server implements no auth, so only expose it briefly and only for
this purpose.

## Layout

- `server.py` - the configurable MCP server (also runnable standalone:
  `python3 server.py --help`)
- `run_test.sh` - orchestrates one scenario end to end
- `Makefile` - the scenarios
- `results/` - logs and verdicts (gitignored)
