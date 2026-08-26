# MCP client testbench. See README.md.
#
# Tunables (pass as env or make vars): TIMEOUT_MS, DURATION, INTERVAL, PORT, CLAUDE_MODEL
#   e.g.  make test-progress TIMEOUT_MS=60000 DURATION=90

SHELL := /bin/bash
export TIMEOUT_MS DURATION INTERVAL PORT CLAUDE_MODEL

.PHONY: help test-all test-headers test-silent test-progress test-sse-comments server clean

help:
	@echo "MCP client testbench"
	@echo ""
	@echo "  make test-all           run every scenario (~4-5 min with defaults)"
	@echo "  make test-headers       what headers does the client send? (fast, ~30s)"
	@echo "  make test-silent        control: slow tool, nothing streamed -> expect timeout"
	@echo "  make test-progress      slow tool + JSON-RPC progress notifications -> does the timeout extend?"
	@echo "  make test-sse-comments  slow tool + SSE comment keep-alives -> does the timeout extend?"
	@echo "  make server             just run the server in the foreground (for manual poking)"
	@echo "  make clean              delete results/"
	@echo ""
	@echo "Tunables: TIMEOUT_MS (60000) DURATION (90) INTERVAL (5) PORT (8765) CLAUDE_MODEL (haiku)"

test-headers:
	@./run_test.sh headers fast
	@echo "--- headers the client sent (from results/headers_server.log):"
	@grep -iE "    (accept|content-type|user-agent)" results/headers_server.log | sed 's/^[^ ]* *//' | sort -u | sed 's/^/    /'

test-silent:
	@./run_test.sh silent silent

test-progress:
	@./run_test.sh progress progress

test-sse-comments:
	@./run_test.sh sse-comments sse-comments

test-all: test-headers test-silent test-progress test-sse-comments
	@echo "=== all scenarios complete; full logs in results/ ==="

server:
	python3 server.py --mode $${MODE:-progress} --duration $${DURATION:-90} --interval $${INTERVAL:-5}

clean:
	rm -rf results/
