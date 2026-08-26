# Instructions for Claude sessions working with this repo

This repo is a test rig that measures how the Claude MCP client handles
long-running tool calls: what headers it sends, whether it times out, and
whether server-sent progress notifications or SSE keep-alives extend its
timeout. Read README.md for the full background.

## If you are a Cowork (cloud) session

The repo folder lives on the user's device. To run the tests:

1. Stage `server.py` and `testbench.py` from the repo folder into your
   session environment and copy them into a working directory (no chmod
   needed; everything runs via python3).
2. Run `python3 testbench.py all` there (or one scenario; see README). The
   `claude` CLI inside your environment is the same engine Cowork uses, so
   this is a valid test of the Cowork client.
3. Report back per scenario: the client's outcome line, whether the server
   timeline shows events being delivered, and the verdict line. State the
   client version printed at the top of each run. The
   `results/<scenario>_result.json` files carry the same facts in
   structured form.
4. Results land in `./results/` in YOUR environment, not on the user's
   machine. Offer to send the log files to the user; do not commit them to
   the repo (gitignored by design).

## If you are a local Claude Code session

Just run `make test-all` in the repo root. Same reporting guidance as above.

## Rules

- Each `make test-*` launches a real headless `claude -p` session (a few
  cents of usage each, ~1-2 minutes per slow scenario). Do not loop or
  repeat runs beyond what the user asked for.
- Do not "fix" a failing verdict: a timeout in test-progress or
  test-sse-comments is a genuine finding about the client, not a bug in
  this rig. test-silent timing out is the expected control result.
- Do not modify server.py, testbench.py, or the Makefile unless the user
  explicitly asks for changes to the rig itself.
- If a port is busy, pass PORT=<other> rather than editing files.
- Never expose the server publicly (tunnels, connectors) on your own
  initiative: that path requires the user's org admin and has security
  implications (the server has no auth). See README's final section.

## Interpreting for the user

Plain-language summaries help. The key distinctions:

- "The client sends the right headers" (test-headers) is separate from
  "the client's timeout can be extended" (test-progress / test-sse-comments).
- JSON-RPC progress notifications (protocol level) and SSE comments
  (transport level) are different kinds of keep-alive; the two scenarios
  exist to test them separately.
- Results are specific to the client version printed in each run.
