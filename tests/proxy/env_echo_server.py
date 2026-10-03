#!/usr/bin/env python3
import json, os, sys
for line in sys.stdin.buffer:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    val = os.environ.get("AGENTVIEW_TEST_VAR", "__missing__")
    msg = {"jsonrpc": "2.0", "id": req.get("id"), "result": {"env_val": val}}
    sys.stdout.buffer.write(json.dumps(msg).encode() + b"\n")
    sys.stdout.buffer.flush()
