#!/usr/bin/env python3
"""Orchestrates mcp-client-testbench scenarios.

Starts the local MCP test server (server.py), drives a real headless Claude
session against it, prints a human-readable verdict, and writes a structured
result to results/<scenario>_result.json.

Usage:
    python3 testbench.py <scenario>
    scenarios: headers | silent | progress | sse-comments | all

Tunables (flags override env vars, env vars override defaults):
    --timeout-ms  / TIMEOUT_MS    (60000)  per-server timeout in the MCP config
    --duration    / DURATION     (90)     seconds the probe tool takes
    --interval    / INTERVAL     (5)      seconds between progress/keep-alive events
    --port        / PORT         (8765)   local server port
    --model       / CLAUDE_MODEL (haiku)  model for the headless session

No third-party dependencies. Python 3.10+.
"""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

SCENARIOS = {
    # name -> (server mode, duration override or None)
    "headers": ("fast", None),
    "silent": ("silent", None),
    "progress": ("progress", None),
    "sse-comments": ("sse-comments", None),
    # Delivery control: same streaming format as `progress`, but the tool
    # finishes UNDER the timeout. If the client receives the result, the
    # server's SSE stream is proven consumable end to end, which rules out
    # "malformed stream" as an explanation for the timeout scenarios.
    "control-under-timeout": ("progress", "half-timeout"),
}

PROMPT = ("Call the probe tool from the testbench MCP server exactly once. "
          "Do not retry. Then report VERBATIM either the tool's result text "
          "or the exact error message you received.")


def env_default(name, default):
    v = os.environ.get(name, "").strip()
    return v if v else default


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def wait_for_port(port, timeout_s=10):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def claude_version(claude_bin):
    try:
        out = subprocess.run([claude_bin, "--version"], capture_output=True,
                             text=True, timeout=30)
        return out.stdout.strip() or out.stderr.strip()
    except Exception as e:
        return f"unknown ({e})"


def run_scenario(name, cfg):
    mode, duration_override = SCENARIOS[name]
    cfg = dict(cfg)
    if duration_override == "half-timeout":
        cfg["duration"] = max(10, cfg["timeout_ms"] // 2000)
    RESULTS.mkdir(exist_ok=True)
    server_log = RESULTS / f"{name}_server.log"
    client_log = RESULTS / f"{name}_client.log"
    mcp_config = RESULTS / f"{name}_mcp.json"
    result_json = RESULTS / f"{name}_result.json"
    server_log.write_text("")

    print(f"=== {name} (mode={mode}, duration={cfg['duration']}s, "
          f"timeout={cfg['timeout_ms']}ms, interval={cfg['interval']}s) ===")
    version = claude_version(cfg["claude_bin"])
    print(f"claude version: {version}")

    server = subprocess.Popen(
        [sys.executable, str(HERE / "server.py"),
         "--port", str(cfg["port"]), "--mode", mode,
         "--duration", str(cfg["duration"]),
         "--interval", str(cfg["interval"]),
         "--log", str(server_log)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_for_port(cfg["port"]):
            die(f"server did not start on port {cfg['port']} (busy? try --port)")

        mcp_config.write_text(json.dumps({"mcpServers": {"testbench": {
            "type": "http",
            "url": f"http://127.0.0.1:{cfg['port']}/mcp",
            "timeout": cfg["timeout_ms"]}}}))

        client_budget = cfg["duration"] + cfg["timeout_ms"] // 1000 + 120
        started_at = datetime.now(timezone.utc).isoformat()
        t0 = time.time()
        try:
            proc = subprocess.run(
                [cfg["claude_bin"], "-p", PROMPT,
                 "--mcp-config", str(mcp_config), "--strict-mcp-config",
                 # Explicitly allowlist the probe tool. This works under
                 # normal permission rules, including orgs whose managed
                 # settings disable bypass-permissions mode (where
                 # --dangerously-skip-permissions is silently ignored).
                 "--allowedTools", "mcp__testbench__probe",
                 "--max-turns", "3",
                 "--model", cfg["model"]],
                capture_output=True, text=True, timeout=client_budget)
            client_out = (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired as e:
            client_out = ((e.stdout or "") if isinstance(e.stdout, str) else "")
            client_out += f"\n[harness] claude session hung; killed after {client_budget}s"
        client_secs = round(time.time() - t0, 1)
        client_log.write_text(client_out)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    timeline = server_log.read_text()
    events_sent = len(re.findall(r"sent (progress notification|SSE comment keep-alive)", timeline))
    delivered_final = "sent final result" in timeline
    client_disconnected = "CLIENT DISCONNECTED" in timeline or "CLIENT GONE" in timeline
    progress_token = None
    m = re.search(r"progressToken=(\S+)", timeline)
    if m:
        progress_token = m.group(1)

    print("--- client said:")
    for line in client_out.strip().splitlines():
        print(f"    {line}")
    print("--- server timeline (sends + disconnects):")
    for line in timeline.splitlines():
        if re.search(r"tools/call received|sent |CLIENT ", line):
            print(f"    {line}")

    # Verdict
    timeout_match = re.search(r"timed out after (\d+)s", client_out)
    if "PROBE_COMPLETED_OK" in client_out:
        outcome = "delivered"
        verdict = "TOOL RESULT DELIVERED: client received the completed result."
        if mode != "fast" and cfg["duration"] > cfg["timeout_ms"] / 1000:
            verdict += (" (call survived past the configured timeout: "
                        "timer was extended or not enforced)")
        elif mode in ("progress", "sse-comments"):
            verdict += (" (delivery control passed: the client parses this "
                        "server's SSE stream end to end)")
    elif timeout_match:
        outcome = "timeout"
        verdict = f"CLIENT TIMED OUT: timed out after {timeout_match.group(1)}s"
        extra = {
            "progress": "Progress notifications were being delivered (see timeline) but did NOT extend the timeout.",
            "sse-comments": "SSE keep-alive comments were being delivered (see timeline) but did NOT extend the timeout.",
            "silent": "Expected for silent mode: nothing arrived before the timeout (control case).",
        }.get(mode)
        if extra:
            verdict += "\n    " + extra
    else:
        outcome = "inconclusive"
        verdict = f"INCONCLUSIVE: inspect {client_log} and {server_log}"

    print("--- verdict:")
    print(f"    {verdict}")

    headers_seen = sorted(set(
        m.group(1) for m in re.finditer(
            r"    ((?:Accept|Content-Type|User-Agent)[^\n]*)", timeline)))
    if name == "headers":
        print("--- headers the client sent:")
        for h in headers_seen:
            print(f"    {h}")

    result = {
        "scenario": name,
        "mode": mode,
        "params": {"timeout_ms": cfg["timeout_ms"], "duration_s": cfg["duration"],
                   "interval_s": cfg["interval"], "port": cfg["port"],
                   "model": cfg["model"]},
        "claude_version": version,
        "started_at": started_at,
        "client_wall_seconds": client_secs,
        "outcome": outcome,  # delivered | timeout | inconclusive
        "timed_out_after_s": int(timeout_match.group(1)) if timeout_match else None,
        "progress_token_issued": progress_token,
        "streamed_events_sent": events_sent,
        "server_sent_final_result": delivered_final,
        # True only if the server directly observed the disconnect (a failed
        # write); the harness may stop the server before that happens, so
        # False does NOT mean the client stayed connected.
        "server_observed_client_disconnect": client_disconnected,
        "headers_seen": headers_seen,
        "client_said": client_out.strip()[:2000],
    }
    result_json.write_text(json.dumps(result, indent=2))
    print(f"--- structured result: {result_json.relative_to(HERE)}")
    print()
    return result


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("scenario", choices=list(SCENARIOS) + ["all"])
    p.add_argument("--timeout-ms", type=int,
                   default=int(env_default("TIMEOUT_MS", "60000")))
    p.add_argument("--duration", type=int,
                   default=int(env_default("DURATION", "90")))
    p.add_argument("--interval", type=int,
                   default=int(env_default("INTERVAL", "5")))
    p.add_argument("--port", type=int, default=int(env_default("PORT", "8765")))
    p.add_argument("--model", default=env_default("CLAUDE_MODEL", "haiku"))
    args = p.parse_args()

    claude_bin = shutil.which("claude")
    if not claude_bin:
        die("claude CLI not found on PATH")

    cfg = {"timeout_ms": args.timeout_ms, "duration": args.duration,
           "interval": args.interval, "port": args.port, "model": args.model,
           "claude_bin": claude_bin}

    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    results = [run_scenario(n, cfg) for n in names]
    if args.scenario == "all":
        print("=== summary ===")
        for r in results:
            print(f"    {r['scenario']:14s} -> {r['outcome']}"
                  + (f" (after {r['timed_out_after_s']}s)"
                     if r["outcome"] == "timeout" else ""))
        print(f"    full logs and *_result.json in {RESULTS.relative_to(HERE)}/")
    sys.exit(0 if all(r["outcome"] != "inconclusive" for r in results) else 2)


if __name__ == "__main__":
    main()
