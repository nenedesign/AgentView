"""Day 1 smoke tests: proxy starts, forwards one message, preserves exit code."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from tests.proxy.harness import run_through_proxy


def _req(n: int, method: str = "tools/call") -> dict:
    return {
        "jsonrpc": "2.0",
        "id": n,
        "method": method,
        "params": {"name": "test_tool", "arguments": {"n": n}},
    }


class TestProxySmoke:
    def test_single_round_trip(self):
        result = run_through_proxy([_req(1)])
        assert result.exit_code == 0
        assert result.skipped_lines == 0
        assert len(result.responses) == 1
        assert result.responses[0]["id"] == 1

    def test_stdout_contains_only_json(self):
        result = run_through_proxy([_req(1), _req(2)])
        for resp in result.responses:
            assert "jsonrpc" in resp or "id" in resp

    def test_exit_code_preserved_on_server_crash(self):
        result = run_through_proxy([_req(1)], server_mode="crash", server_mode_arg="7")
        # Server exits 7 before responding; proxy should propagate that code.
        assert result.exit_code == 7

    def test_proxy_exit_failure_on_missing_command(self):
        from agentview.proxy import PROXY_EXIT_FAILURE, run_proxy
        # run_proxy with empty argv should return PROXY_EXIT_FAILURE
        assert run_proxy([]) == PROXY_EXIT_FAILURE

    @pytest.mark.skipif(sys.platform == "win32", reason="Windows not supported")
    def test_proxy_cli_invocation(self):
        """Integration test: `agentview proxy -- <server>` via the installed CLI."""
        from pathlib import Path
        fake_server = str(Path(__file__).parent / "fake_server.py")
        proc = subprocess.Popen(
            [sys.executable, "-m", "agentview.cli", "proxy", "--", sys.executable, fake_server, "echo"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        req = json.dumps({"jsonrpc": "2.0", "id": 99, "method": "tools/call", "params": {}})
        stdout, _ = proc.communicate(input=(req + "\n").encode(), timeout=10)
        lines = [l for l in stdout.splitlines() if l.strip()]
        assert len(lines) == 1
        msg = json.loads(lines[0])
        assert msg["id"] == 99
