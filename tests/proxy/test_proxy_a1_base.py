"""A1 base test cases: 100 round-trips, ID preservation, ordering, crashes.

Acceptance criterion A1: The proxy forwards every byte it receives from
client to server and from server to client, in order, without modification,
regardless of message count, content, or server exit behavior.
"""

from __future__ import annotations

import json

import pytest

from tests.proxy.harness import run_through_proxy


def _req(n: int, method: str = "tools/call") -> dict:
    return {
        "jsonrpc": "2.0",
        "id": n,
        "method": method,
        "params": {"name": "test_tool", "arguments": {"n": n}},
    }


# ---------------------------------------------------------------------------
# A1.1 -- Throughput: 100 sequential round-trips
# ---------------------------------------------------------------------------

class TestA1Throughput:
    def test_100_round_trips(self):
        """Proxy must forward all 100 requests and return 100 responses."""
        requests = [_req(i) for i in range(100)]
        result = run_through_proxy(requests)
        assert result.exit_code == 0
        assert result.skipped_lines == 0
        assert len(result.responses) == 100

    def test_id_preservation_100(self):
        """Every response id must match the corresponding request id."""
        requests = [_req(i) for i in range(100)]
        result = run_through_proxy(requests)
        assert result.exit_code == 0
        response_ids = {r["id"] for r in result.responses}
        expected_ids = set(range(100))
        assert response_ids == expected_ids

    def test_no_duplicate_ids(self):
        """No response id appears more than once."""
        requests = [_req(i) for i in range(100)]
        result = run_through_proxy(requests)
        ids = [r["id"] for r in result.responses]
        assert len(ids) == len(set(ids))

    def test_no_extra_responses(self):
        """Proxy must not inject additional responses beyond the server's output."""
        requests = [_req(i) for i in range(10)]
        result = run_through_proxy(requests)
        assert len(result.responses) == 10


# ---------------------------------------------------------------------------
# A1.2 -- Content integrity: payload survives the proxy unchanged
# ---------------------------------------------------------------------------

class TestA1ContentIntegrity:
    def test_result_field_present_on_all_responses(self):
        """Echo server wraps every response in a 'result' field."""
        requests = [_req(i) for i in range(20)]
        result = run_through_proxy(requests)
        for resp in result.responses:
            assert "result" in resp, f"Missing result field in response: {resp}"

    def test_jsonrpc_version_preserved(self):
        requests = [_req(i) for i in range(5)]
        result = run_through_proxy(requests)
        for resp in result.responses:
            assert resp.get("jsonrpc") == "2.0"

    def test_large_payload_survives(self):
        """A 64 KB payload must arrive at the client intact (byte count check)."""
        result = run_through_proxy([_req(1)], server_mode="large", server_mode_arg="65536")
        assert result.exit_code == 0
        assert len(result.responses) == 1
        resp = result.responses[0]
        text = resp["result"]["content"][0]["text"]
        assert len(text) == 65536

    def test_stdout_is_valid_json_on_every_line(self):
        """Every non-empty stdout line must parse as JSON -- no proxy bleed-through."""
        requests = [_req(i) for i in range(20)]
        result = run_through_proxy(requests)
        assert result.skipped_lines == 0

    def test_notification_forwarded_without_id(self):
        """Server-emitted notifications (no id) must be forwarded as-is."""
        result = run_through_proxy([_req(1)], server_mode="notification")
        assert result.exit_code == 0
        # 1 notification + 1 response
        assert len(result.responses) == 2
        notification = next(r for r in result.responses if "id" not in r or r.get("id") is None)
        assert notification.get("method") == "notifications/progress"


# ---------------------------------------------------------------------------
# A1.3 -- Error responses: server-side JSON-RPC errors forwarded intact
# ---------------------------------------------------------------------------

class TestA1ErrorForwarding:
    def test_json_rpc_error_forwarded(self):
        """A JSON-RPC error response from the server must reach the client."""
        result = run_through_proxy([_req(1)], server_mode="error")
        assert result.exit_code == 0
        assert len(result.responses) == 1
        assert "error" in result.responses[0]
        assert result.responses[0]["error"]["code"] == -32000

    def test_multiple_errors_forwarded(self):
        requests = [_req(i) for i in range(5)]
        result = run_through_proxy(requests, server_mode="error")
        assert len(result.responses) == 5
        for resp in result.responses:
            assert "error" in resp


# ---------------------------------------------------------------------------
# A1.4 -- Crash propagation: server exit code reflected
# ---------------------------------------------------------------------------

class TestA1CrashPropagation:
    @pytest.mark.parametrize("code", [1, 2, 3, 7, 42, 127])
    def test_exit_code_propagated(self, code: int):
        """Proxy must return the exact server exit code on crash."""
        result = run_through_proxy([_req(1)], server_mode="crash", server_mode_arg=str(code))
        assert result.exit_code == code

    def test_no_output_on_crash_before_response(self):
        """When the server crashes before responding, stdout must be empty."""
        result = run_through_proxy([_req(1)], server_mode="crash", server_mode_arg="1")
        assert len(result.responses) == 0

    def test_stdout_clean_on_crash(self):
        """Proxy must not write proxy diagnostics to stdout on crash."""
        result = run_through_proxy([_req(1)], server_mode="crash", server_mode_arg="5")
        # Any stdout bytes must be valid JSON (not proxy diagnostic text)
        for line in result.stdout_raw.splitlines():
            line = line.strip()
            if line:
                json.loads(line)  # raises if proxy leaked a non-JSON line


# ---------------------------------------------------------------------------
# A1.5 -- Slow server: proxy does not time out on legitimate delays
# ---------------------------------------------------------------------------

class TestA1SlowServer:
    def test_slow_server_50ms_passes(self):
        """Proxy must not drop responses just because the server is slow."""
        result = run_through_proxy(
            [_req(i) for i in range(5)],
            server_mode="slow",
            server_mode_arg="50",
        )
        assert result.exit_code == 0
        assert len(result.responses) == 5

    def test_slow_server_ids_intact(self):
        requests = [_req(i) for i in range(5)]
        result = run_through_proxy(requests, server_mode="slow", server_mode_arg="50")
        response_ids = {r["id"] for r in result.responses}
        assert response_ids == {r["id"] for r in requests}
