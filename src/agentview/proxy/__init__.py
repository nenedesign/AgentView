"""agentview proxy: transparent MCP stdio passthrough with trace capture.

Public surface for M2:
    run_proxy(server_argv, *, capture=None) -> int
        Spawns the given command, forwards stdin/stdout byte-for-byte,
        and returns the child's exit code.

capture is a callable(line: bytes, direction: str) invoked after each line is
forwarded. It is wired in M3 when the JSONL writer is added.
"""

from .forwarder import PROXY_EXIT_FAILURE, run_proxy

__all__ = ["run_proxy", "PROXY_EXIT_FAILURE"]
