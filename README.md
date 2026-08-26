# mcp-client-testbench

**The question this repo answers:** when an MCP tool call takes a long time,
what does the Claude client actually do, and does anything the server sends
(progress notifications, keep-alives) stop it from timing out?

**Why it exists:** Claude Code and Claude Cowork abort MCP tool calls that
exceed a timeout (observed: 60 seconds for claude.ai connectors). Server
vendors say "we send keep-alives so long calls stay alive". This rig tests
whether that actually works, against a server you fully control, with no
vendor infrastructure in the way.

## What happens when you run a test

Every `make test-*` target does the same five things:

1. Starts a tiny MCP server on localhost (`server.py`, stdlib Python, no
   installs). It has one tool, `probe`, which takes a configurable amount
   of time and streams configurable events while it works.
2. Writes a scoped MCP config pointing a client at that server, with a
   configurable per-server `timeout`.
3. Launches a REAL headless Claude session (`claude -p ...`) that connects
   to the server and calls `probe` once. This is the actual Claude client
   binary on your machine, not a simulation. It costs a few cents of model
   usage per run (defaults to haiku; the model doesn't affect timeout
   behaviour).
4. The server logs every request's headers and a timestamped timeline of
   every event it sends and when the client hangs up.
5. Prints: what the client reported, the server's timeline, and a one-line
   verdict. Full logs go to `./results/` (gitignored).

## The scenarios

| Target | Server behaviour | What it tells you |
|---|---|---|
| `make test-headers` | responds instantly | The exact headers the client sends. Spec compliance check: every POST should carry `Accept: application/json, text/event-stream` |
| `make test-silent` | takes 90s, sends nothing | Control case: confirms what the configured timeout does when nothing arrives. EXPECTED to time out |
| `make test-progress` | takes 90s, sends spec-compliant JSON-RPC `notifications/progress` every 5s | The key question: do protocol-level progress notifications extend the client's timeout? |
| `make test-sse-comments` | takes 90s, sends SSE comment keep-alives (`: keep-alive`) every 5s | Same question for transport-level keep-alives (what some vendors mean by "keep-alives") |
| `make test-all` | all of the above | ~4-5 minutes total |

## Reading the output

Example (`make test-progress`), annotated:

```
=== progress (mode=progress, duration=90s, timeout=60000ms, interval=5s) ===
claude version: 2.1.246 (Claude Code)      <- results are per client version
--- client said:
    MCP server "testbench" tool "probe" timed out after 60s   <- the client's outcome
--- server timeline (sends + disconnects):
    ... tools/call received, progressToken=2   <- client DID request progress
    ... sent progress notification 1 at +5s    <- server WAS streaming
    ... sent progress notification 11 at +55s
--- verdict:
    CLIENT TIMED OUT: timed out after 60s
    Progress notifications were being delivered (see timeline) but did NOT extend the timeout.
```

A passing (extended-timeout) run would instead end with the client
reporting `PROBE_COMPLETED_OK after 90s`.

Results observed on claude-code 2.1.246 (Aug 2026): headers compliant;
silent times out (expected); progress and sse-comments BOTH still time out
at exactly the configured timeout, i.e. nothing the server streams extends
it. Matches https://github.com/anthropics/claude-code/issues/58687.
One positive: the per-server `timeout` value in the MCP config IS honored
for directly-configured servers, so local CLI users can raise their own
cap. Connector users (claude.ai / Cowork) cannot; the platform sets it.

## Prerequisites

- Claude Code CLI installed and logged in (`claude --version` works)
- Python 3.10+ (stdlib only, nothing to install), make
- macOS or Linux

## Quick start

```bash
make            # menu
make test-all   # everything
```

## Running it against Claude Code vs Cowork

**Claude Code (local CLI):** clone the repo on the machine where Claude Code
is installed and run `make test-all`. This tests that machine's client.

**Cowork:** Cowork sessions run in Anthropic's cloud, not on your machine,
so they cannot reach a server on your laptop's localhost. Two options:

1. Test the Cowork client engine: in a Cowork session, connect this repo's
   folder and ask Claude to copy the repo contents into its session
   environment and run `make test-all` there. The headless client inside
   the Cowork environment is the same engine Cowork itself uses (the
   header capture will show `remote_cowork` in the User-Agent). This
   covers timeout and streaming behaviour of the Cowork client.
2. Test the full Cowork connector path, Anthropic's connector platform
   included: use the tunnel method described at the bottom of this README,
   then call the `probe` tool from a normal Cowork session via the
   registered connector.

Option 1 tests the client only. Option 2 additionally tests what the
connector platform forwards and whether it passes streams through. Note
that in Cowork the per-server `timeout` is set by the platform, not by
you, so option 2 runs against the platform's own timeout config.

## Tunables

Pass as make variables or environment variables,
e.g. `make test-progress TIMEOUT_MS=30000 DURATION=45`:

| Variable | Default | Meaning |
|---|---|---|
| `TIMEOUT_MS` | 60000 | per-server `timeout` in the MCP config given to the client |
| `DURATION` | 90 | seconds the probe tool takes to complete |
| `INTERVAL` | 5 | seconds between progress/keep-alive events |
| `PORT` | 8765 | local server port |
| `CLAUDE_MODEL` | haiku | model for the headless session |

## Limitation, and the connector-platform extension

Run locally, this tests the DIRECT client-to-server leg only. Connector
traffic in claude.ai/Cowork additionally passes through Anthropic's
connector platform, which this rig does not see. To point the same
instrument at that platform:

1. Expose the server publicly: `cloudflared tunnel --url http://127.0.0.1:8765`
2. Have your Claude org admin register the tunnel URL as a custom connector.
3. Call the `probe` tool from a normal Claude session via that connector.
4. The server's logged headers are now what the PLATFORM sends, and the
   timeline shows whether it passes streams through or buffers them.
5. Tear the connector and tunnel down afterwards. The server has no auth;
   expose it only briefly and only for this purpose.

## Layout

- `server.py` - the configurable MCP server (standalone: `python3 server.py --help`)
- `run_test.sh` - orchestrates one scenario end to end
- `Makefile` - the scenarios
- `results/` - logs, verdicts, and the generated MCP configs (gitignored)
