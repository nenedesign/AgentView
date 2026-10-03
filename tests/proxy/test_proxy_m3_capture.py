"""M3 acceptance tests: JSONL capture via MCPInterceptor.

Verifies that the interceptor correctly writes JSONL events for each forwarded
JSON-RPC message, that the events round-trip through the Pydantic event model,
and that the trace file is well-formed and portable.
"""

from __future__ import annotations

import json
import sys
import threading
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from agentview.events import EventEnvelope
from agentview.proxy.interceptor import MCPInterceptor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _req(n: int, method: str = "tools/call", tool: str = "search_catalog") -> dict:
    return {
        "jsonrpc": "2.0",
        "id": n,
        "method": method,
        "params": {"name": tool, "arguments": {"q": "test"}},
    }


def _resp(n: int, content: list | None = None, error: dict | None = None) -> dict:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": n}
    if error:
        msg["error"] = error
    else:
        msg["result"] = {"content": content if content is not None else [{"type": "text", "text": "ok"}]}
    return msg


def _notification(method: str = "notifications/progress") -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": {"progress": 50}}


def _read_events(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(l) for l in lines if l.strip()]


# ---------------------------------------------------------------------------
# A3.1 -- Basic capture: events appear in JSONL
# ---------------------------------------------------------------------------

class TestInterceptorBasic:
    def test_run_start_and_end_written(self, tmp_path):
        trace = tmp_path / "t.jsonl"
        ic = MCPInterceptor(trace_path=trace, session_id="sess1")
        ic.close()
        events = _read_events(trace)
        types = [e["event_type"] for e in events]
        assert types[0] == "run_start"
        assert types[-1] == "run_end"

    def test_request_captured_as_span_end(self, tmp_path):
        trace = tmp_path / "t.jsonl"
        ic = MCPInterceptor(trace_path=trace)
        ic.capture(json.dumps(_req(1)).encode() + b"\n", "client_to_server")
        ic.close()
        events = _read_events(trace)
        span_events = [e for e in events if e["event_type"] == "span_end"]
        assert len(span_events) == 1

    def test_response_captured_as_span_end(self, tmp_path):
        trace = tmp_path / "t.jsonl"
        ic = MCPInterceptor(trace_path=trace)
        ic.capture(json.dumps(_resp(1)).encode() + b"\n", "server_to_client")
        ic.close()
        events = _read_events(trace)
        span_events = [e for e in events if e["event_type"] == "span_end"]
        assert len(span_events) == 1

    def test_notification_captured(self, tmp_path):
        trace = tmp_path / "t.jsonl"
        ic = MCPInterceptor(trace_path=trace)
        ic.capture(json.dumps(_notification()).encode() + b"\n", "server_to_client")
        ic.close()
        events = _read_events(trace)
        span_events = [e for e in events if e["event_type"] == "span_end"]
        assert len(span_events) == 1

    def test_non_json_line_skipped(self, tmp_path):
        trace = tmp_path / "t.jsonl"
        ic = MCPInterceptor(trace_path=trace)
        ic.capture(b"not json at all\n", "server_to_client")
        ic.close()
        events = _read_events(trace)
        span_events = [e for e in events if e["event_type"] == "span_end"]
        assert len(span_events) == 0

    def test_empty_line_skipped(self, tmp_path):
        trace = tmp_path / "t.jsonl"
        ic = MCPInterceptor(trace_path=trace)
        ic.capture(b"\n", "server_to_client")
        ic.capture(b"   \n", "server_to_client")
        ic.close()
        events = _read_events(trace)
        span_events = [e for e in events if e["event_type"] == "span_end"]
        assert len(span_events) == 0


# ---------------------------------------------------------------------------
# A3.2 -- MCP metadata attached correctly
# ---------------------------------------------------------------------------

class TestInterceptorMCPMetadata:
    def test_direction_client_to_server(self, tmp_path):
        trace = tmp_path / "t.jsonl"
        ic = MCPInterceptor(trace_path=trace, session_id="sess42")
        ic.capture(json.dumps(_req(1)).encode() + b"\n", "client_to_server")
        ic.close()
        events = _read_events(trace)
        span = next(e for e in events if e["event_type"] == "span_end")
        meta = span["mcp_metadata"]
        assert meta["message_direction"] == "client_to_server"
        assert meta["session_id"] == "sess42"

    def test_direction_server_to_client(self, tmp_path):
        trace = tmp_path / "t.jsonl"
        ic = MCPInterceptor(trace_path=trace)
        ic.capture(json.dumps(_resp(1)).encode() + b"\n", "server_to_client")
        ic.close()
        events = _read_events(trace)
        span = next(e for e in events if e["event_type"] == "span_end")
        assert span["mcp_metadata"]["message_direction"] == "server_to_client"

    def test_method_captured_on_request(self, tmp_path):
        trace = tmp_path / "t.jsonl"
        ic = MCPInterceptor(trace_path=trace)
        ic.capture(json.dumps(_req(1, method="tools/call")).encode() + b"\n", "client_to_server")
        ic.close()
        events = _read_events(trace)
        span = next(e for e in events if e["event_type"] == "span_end")
        assert span["mcp_metadata"]["method"] == "tools/call"

    def test_tool_name_extracted_from_tools_call(self, tmp_path):
        trace = tmp_path / "t.jsonl"
        ic = MCPInterceptor(trace_path=trace)
        ic.capture(json.dumps(_req(1, method="tools/call", tool="my_tool")).encode() + b"\n", "client_to_server")
        ic.close()
        events = _read_events(trace)
        span = next(e for e in events if e["event_type"] == "span_end")
        assert span["mcp_metadata"]["tool_name"] == "my_tool"

    def test_request_id_preserved(self, tmp_path):
        trace = tmp_path / "t.jsonl"
        ic = MCPInterceptor(trace_path=trace)
        ic.capture(json.dumps(_req(77)).encode() + b"\n", "client_to_server")
        ic.close()
        events = _read_events(trace)
        span = next(e for e in events if e["event_type"] == "span_end")
        assert span["mcp_metadata"]["request_id"] == 77

    def test_protocol_always_jsonrpc(self, tmp_path):
        trace = tmp_path / "t.jsonl"
        ic = MCPInterceptor(trace_path=trace)
        ic.capture(json.dumps(_req(1)).encode() + b"\n", "client_to_server")
        ic.close()
        events = _read_events(trace)
        for e in events:
            if "mcp_metadata" in e and e["mcp_metadata"]:
                assert e["mcp_metadata"]["protocol"] == "jsonrpc-2.0"


# ---------------------------------------------------------------------------
# A3.3 -- Status mapping
# ---------------------------------------------------------------------------

class TestInterceptorStatusMapping:
    def test_ok_response_maps_valid(self, tmp_path):
        trace = tmp_path / "t.jsonl"
        ic = MCPInterceptor(trace_path=trace)
        ic.capture(json.dumps(_resp(1)).encode() + b"\n", "server_to_client")
        ic.close()
        events = _read_events(trace)
        span = next(e for e in events if e["event_type"] == "span_end")
        assert span["payload"]["result_status"] == "VALID"
        assert span["payload"]["execution_status"] == "OK"

    def test_error_response_maps_invalid(self, tmp_path):
        trace = tmp_path / "t.jsonl"
        ic = MCPInterceptor(trace_path=trace)
        ic.capture(
            json.dumps(_resp(1, error={"code": -32000, "message": "scripted error"})).encode() + b"\n",
            "server_to_client",
        )
        ic.close()
        events = _read_events(trace)
        span = next(e for e in events if e["event_type"] == "span_end")
        assert span["payload"]["execution_status"] == "ERROR"
        assert span["payload"]["result_status"] == "INVALID"

    def test_empty_content_maps_empty(self, tmp_path):
        trace = tmp_path / "t.jsonl"
        ic = MCPInterceptor(trace_path=trace)
        ic.capture(json.dumps(_resp(1, content=[])).encode() + b"\n", "server_to_client")
        ic.close()
        events = _read_events(trace)
        span = next(e for e in events if e["event_type"] == "span_end")
        assert span["payload"]["result_status"] == "EMPTY"

    def test_request_has_no_result_status(self, tmp_path):
        trace = tmp_path / "t.jsonl"
        ic = MCPInterceptor(trace_path=trace)
        ic.capture(json.dumps(_req(1)).encode() + b"\n", "client_to_server")
        ic.close()
        events = _read_events(trace)
        span = next(e for e in events if e["event_type"] == "span_end")
        assert span["payload"]["result_status"] is None


# ---------------------------------------------------------------------------
# A3.4 -- Pydantic round-trip: every event validates against the model
# ---------------------------------------------------------------------------

class TestInterceptorPydanticRoundTrip:
    def test_run_start_validates(self, tmp_path):
        trace = tmp_path / "t.jsonl"
        ic = MCPInterceptor(trace_path=trace)
        ic.close()
        events = _read_events(trace)
        run_start = next(e for e in events if e["event_type"] == "run_start")
        env = EventEnvelope.model_validate(run_start)
        assert env.event_type == "run_start"

    def test_span_end_validates(self, tmp_path):
        trace = tmp_path / "t.jsonl"
        ic = MCPInterceptor(trace_path=trace)
        ic.capture(json.dumps(_req(1)).encode() + b"\n", "client_to_server")
        ic.close()
        events = _read_events(trace)
        span_evt = next(e for e in events if e["event_type"] == "span_end")
        env = EventEnvelope.model_validate(span_evt)
        assert env.event_type == "span_end"
        assert env.mcp_metadata is not None
        assert env.mcp_metadata.message_direction == "client_to_server"

    def test_run_end_validates(self, tmp_path):
        trace = tmp_path / "t.jsonl"
        ic = MCPInterceptor(trace_path=trace)
        ic.close()
        events = _read_events(trace)
        run_end = next(e for e in events if e["event_type"] == "run_end")
        env = EventEnvelope.model_validate(run_end)
        assert env.event_type == "run_end"

    def test_full_session_all_events_validate(self, tmp_path):
        trace = tmp_path / "t.jsonl"
        ic = MCPInterceptor(trace_path=trace, session_id="round_trip_sess")
        for i in range(10):
            ic.capture(json.dumps(_req(i)).encode() + b"\n", "client_to_server")
            ic.capture(json.dumps(_resp(i)).encode() + b"\n", "server_to_client")
        ic.close()
        events = _read_events(trace)
        # 1 run_start + 20 span_ends + 1 run_end = 22
        assert len(events) == 22
        for raw in events:
            env = EventEnvelope.model_validate(raw)
            assert env.run_id == ic._run_id


# ---------------------------------------------------------------------------
# A3.5 -- Concurrent capture: thread-safety
# ---------------------------------------------------------------------------

class TestInterceptorThreadSafety:
    def test_concurrent_captures_no_corruption(self, tmp_path):
        trace = tmp_path / "t.jsonl"
        ic = MCPInterceptor(trace_path=trace)

        errors: list[Exception] = []

        def sender(n: int) -> None:
            try:
                for i in range(10):
                    ic.capture(json.dumps(_req(n * 10 + i)).encode() + b"\n", "client_to_server")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=sender, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        ic.close()

        assert errors == []
        events = _read_events(trace)
        span_events = [e for e in events if e["event_type"] == "span_end"]
        assert len(span_events) == 50
        # every line must be valid JSON (no interleaved partial writes)
        for raw in events:
            EventEnvelope.model_validate(raw)

    def test_close_idempotent(self, tmp_path):
        trace = tmp_path / "t.jsonl"
        ic = MCPInterceptor(trace_path=trace)
        ic.close()
        ic.close()
        ic.close()
        events = _read_events(trace)
        run_ends = [e for e in events if e["event_type"] == "run_end"]
        assert len(run_ends) == 1


# ---------------------------------------------------------------------------
# A3.6 -- Trace path resolution
# ---------------------------------------------------------------------------

class TestInterceptorTracePath:
    def test_explicit_trace_path_used(self, tmp_path):
        trace = tmp_path / "custom" / "out.jsonl"
        ic = MCPInterceptor(trace_path=trace)
        ic.close()
        assert trace.exists()

    def test_env_var_trace_path(self, tmp_path, monkeypatch):
        trace = tmp_path / "env_path.jsonl"
        monkeypatch.setenv("AGENTVIEW_TRACE_PATH", str(trace))
        ic = MCPInterceptor()
        ic.close()
        assert trace.exists()

    def test_env_var_trace_dir(self, tmp_path, monkeypatch):
        trace_dir = tmp_path / "traces"
        monkeypatch.setenv("AGENTVIEW_TRACE_DIR", str(trace_dir))
        monkeypatch.delenv("AGENTVIEW_TRACE_PATH", raising=False)
        ic = MCPInterceptor()
        ic.close()
        files = list(trace_dir.glob("*.jsonl"))
        assert len(files) == 1
