# MCP client testbench. See README.md. Thin wrapper around testbench.py.
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
	@echo "  make test-control       streaming tool that finishes UNDER the timeout -> proves the SSE stream is consumable"
	@echo "  make server             just run the server in the foreground (for manual poking)"
	@echo "  make clean              delete results/"
	@echo ""
	@echo "Tunables: TIMEOUT_MS (60000) DURATION (90) INTERVAL (5) PORT (8765) CLAUDE_MODEL (haiku)"
	@echo "Direct use: python3 testbench.py <headers|silent|progress|sse-comments|all> [--help]"

test-headers:
	@python3 testbench.py headers

test-silent:
	@python3 testbench.py silent

test-progress:
	@python3 testbench.py progress

test-sse-comments:
	@python3 testbench.py sse-comments

test-control:
	@python3 testbench.py control-under-timeout

test-all:
	@python3 testbench.py all

server:
	python3 server.py --mode $${MODE:-progress} --duration $${DURATION:-90} --interval $${INTERVAL:-5}

clean:
	rm -rf results/
