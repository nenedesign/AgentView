#!/usr/bin/env python3
"""Thin wrapper so tests can spawn run_proxy() as a subprocess.

Usage: python _proxy_runner.py <server command and args...>
"""

import sys
from pathlib import Path

# Add src/ to path so agentview is importable without an editable install
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from agentview.proxy.forwarder import PROXY_EXIT_FAILURE, run_proxy

server_argv = sys.argv[1:]
if not server_argv:
    print("_proxy_runner: no server_argv", file=sys.stderr)
    sys.exit(PROXY_EXIT_FAILURE)

sys.exit(run_proxy(server_argv))
