#!/usr/bin/env python3
"""Fake MCP server for proxy tests.

Reads JSON-RPC 2.0 requests from stdin line by line, sends scripted responses.

Modes (selected via first CLI argument):
  echo          -- reflect each request back as a result (default)
  error         -- return a JSON-RPC error for every request
  crash N       -- crash (sys.exit(N)) after receiving the first message
  slow MS       -- sleep MS milliseconds before each response
  notification  -- emit one unsolicited notification before echoing
  large N       -- respond with a payload of N bytes
"""

from __future__ import annotations

import json
import sys
import time


def _respond(req_id, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result if result is not None else {"content": [{"type": "text", "text": "ok"}]}
    sys.stdout.buffer.write(json.dumps(msg).encode() + b"\n")
    sys.stdout.buffer.flush()


def _notify(method, params=None):
    msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    sys.stdout.buffer.write(json.dumps(msg).encode() + b"\n")
    sys.stdout.buffer.flush()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "echo"
    mode_arg = sys.argv[2] if len(sys.argv) > 2 else None

    for line in sys.stdin.buffer:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        req_id = req.get("id")
        method = req.get("method", "")

        if mode == "crash":
            exit_code = int(mode_arg) if mode_arg else 1
            sys.exit(exit_code)

        if mode == "slow":
            delay_ms = int(mode_arg) if mode_arg else 50
            time.sleep(delay_ms / 1000.0)

        if mode == "notification":
            _notify("notifications/progress", {"progress": 50, "total": 100})

        if mode == "error":
            _respond(req_id, error={"code": -32000, "message": "scripted error"})
            continue

        if mode == "large":
            size = int(mode_arg) if mode_arg else 1024
            _respond(req_id, result={"content": [{"type": "text", "text": "x" * size}]})
            continue

        # Default: echo mode (also covers slow, notification)
        _respond(req_id, result={"content": [{"type": "text", "text": f"ok:{method}:{req_id}"}]})


if __name__ == "__main__":
    main()
