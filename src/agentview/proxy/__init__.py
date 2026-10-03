"""agentview proxy: transparent MCP stdio passthrough with trace capture.

Public surface:
    run_proxy(server_argv, *, capture=None) -> int
        Spawns the given command, forwards stdin/stdout byte-for-byte,
        and returns the child's exit code.

    MCPInterceptor(trace_path=None, session_id=None, server_name=None)
        Stateful capture hook. Pass ic.capture to run_proxy().
        Call ic.close() after run_proxy() returns to flush the run_end event.
"""

from .forwarder import PROXY_EXIT_FAILURE, run_proxy
from .interceptor import MCPInterceptor

__all__ = ["run_proxy", "PROXY_EXIT_FAILURE", "MCPInterceptor"]
