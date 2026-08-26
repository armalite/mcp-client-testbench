#!/usr/bin/env python3
"""Configurable MCP test server (streamable HTTP, stdlib only).

Serves one tool, `probe`, whose behaviour is set by server flags. Logs every
request's headers and a timestamped timeline of everything sent, so you can
see exactly what an MCP client (e.g. Claude Code) sends and how it reacts.

Modes:
  fast          respond immediately (control; also used for header capture)
  silent        wait --duration seconds, then respond as plain JSON
  progress      SSE response; JSON-RPC notifications/progress every --interval
                seconds (correlated to the client's progressToken), final
                result at --duration
  sse-comments  SSE response; comment keep-alives (": keep-alive") every
                --interval seconds, final result at --duration

No third-party dependencies. Python 3.8+.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ARGS = None


def log(msg):
    line = f"{datetime.now(timezone.utc).isoformat()} {msg}"
    print(line, flush=True)
    if ARGS.log:
        with open(ARGS.log, "a") as f:
            f.write(line + "\n")


TOOL = {
    "name": "probe",
    "description": (
        "Test probe tool. Takes a configurable amount of time to complete "
        "(set server-side). Call it exactly once and report the outcome."
    ),
    "inputSchema": {"type": "object", "properties": {}},
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _log_headers(self, method, body=b""):
        log(f"--- {method} {self.path}")
        for k, v in self.headers.items():
            log(f"    {k}: {v}")
        if body:
            log(f"    [body] {body[:300].decode('utf-8', errors='replace')}")

    def _json(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _sse_event(self, data):
        self.wfile.write(f"event: message\ndata: {json.dumps(data)}\n\n".encode())
        self.wfile.flush()

    def do_GET(self):
        self._log_headers("GET")
        # The spec's standalone SSE listener channel; we hold nothing on it.
        self.send_response(405)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_DELETE(self):
        self._log_headers("DELETE")
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n) if n else b""
        self._log_headers("POST", body)
        try:
            req = json.loads(body)
        except Exception:
            req = {}
        method = req.get("method")

        if method == "initialize":
            self._json({"jsonrpc": "2.0", "id": req["id"], "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mcp-client-testbench", "version": "1.0"}}})
        elif method == "tools/list":
            self._json({"jsonrpc": "2.0", "id": req["id"],
                        "result": {"tools": [TOOL]}})
        elif method == "tools/call":
            self.handle_call(req)
        elif "id" in req:
            self._json({"jsonrpc": "2.0", "id": req["id"], "result": {}})
        else:
            log(f"    (notification: {method})")
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def handle_call(self, req):
        token = (req.get("params", {}).get("_meta") or {}).get("progressToken")
        mode = ARGS.mode
        log(f"tools/call received, mode={mode}, duration={ARGS.duration}s, "
            f"progressToken={token!r}")
        start = time.time()

        def final_payload():
            return {"jsonrpc": "2.0", "id": req["id"], "result": {"content": [{
                "type": "text",
                "text": (f"PROBE_COMPLETED_OK after {int(time.time()-start)}s "
                         f"(mode={mode})")}]}}

        if mode == "fast":
            self._json(final_payload())
            log("sent final result immediately")
            return

        if mode == "silent":
            time.sleep(ARGS.duration)
            try:
                self._json(final_payload())
                log(f"sent final result at +{int(time.time()-start)}s")
            except (BrokenPipeError, ConnectionResetError) as e:
                log(f"CLIENT GONE before result could be sent "
                    f"(+{int(time.time()-start)}s, {type(e).__name__})")
            return

        # Streaming modes: progress | sse-comments
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        i = 0
        try:
            while time.time() - start < ARGS.duration:
                time.sleep(ARGS.interval)
                i += 1
                elapsed = int(time.time() - start)
                if mode == "progress":
                    if token is None:
                        log(f"+{elapsed}s: NO progressToken from client; "
                            f"cannot send correlated progress")
                    else:
                        total = max(1, ARGS.duration // ARGS.interval)
                        self._sse_event({
                            "jsonrpc": "2.0",
                            "method": "notifications/progress",
                            "params": {"progressToken": token, "progress": i,
                                       "total": total,
                                       "message": f"step {i}/{total} at +{elapsed}s"}})
                        log(f"sent progress notification {i} at +{elapsed}s")
                else:  # sse-comments
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    log(f"sent SSE comment keep-alive {i} at +{elapsed}s")
            self._sse_event(final_payload())
            log(f"sent final result at +{int(time.time()-start)}s")
        except (BrokenPipeError, ConnectionResetError) as e:
            log(f"CLIENT DISCONNECTED at +{int(time.time()-start)}s "
                f"({type(e).__name__})")

    def log_message(self, *a):
        pass


def main():
    global ARGS
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--mode", choices=["fast", "silent", "progress",
                                      "sse-comments"], default="progress")
    p.add_argument("--duration", type=int, default=90,
                   help="seconds the probe tool takes (default 90)")
    p.add_argument("--interval", type=int, default=5,
                   help="seconds between progress/keep-alive events (default 5)")
    p.add_argument("--log", default=None, help="also append log lines to this file")
    ARGS = p.parse_args()
    log(f"server starting on 127.0.0.1:{ARGS.port} mode={ARGS.mode} "
        f"duration={ARGS.duration}s interval={ARGS.interval}s")
    ThreadingHTTPServer(("127.0.0.1", ARGS.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
