#!/usr/bin/env python3
import json, sys
for line in sys.stdin.buffer:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line.decode("utf-8"))
    # echo the params back verbatim
    msg = {"jsonrpc": "2.0", "id": req.get("id"), "result": req.get("params", {})}
    sys.stdout.buffer.write(json.dumps(msg, ensure_ascii=False).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()
