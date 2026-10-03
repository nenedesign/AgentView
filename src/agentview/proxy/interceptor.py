"""MCP proxy capture hook: parse forwarded lines and write JSONL trace events.

Usage
-----
    from agentview.proxy.interceptor import MCPInterceptor
    from agentview.proxy import run_proxy

    ic = MCPInterceptor(trace_path="traces/session.jsonl")
    exit_code = run_proxy(server_argv, capture=ic.capture)

The interceptor assigns one run_id per proxy session (not per JSON-RPC request).
Each forwarded line becomes a `span_end` event in the JSONL trace, with
`mcp_metadata` carrying protocol context (direction, method, request_id,
tool_name). The run is opened at construction time and closed on `close()`.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import uuid


SCHEMA_VERSION = "0.1"


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _tool_name_from_params(params: Any) -> str | None:
    """Extract tool name from tools/call params if present."""
    if not isinstance(params, dict):
        return None
    return params.get("name")


class MCPInterceptor:
    """Stateful capture hook for `run_proxy(capture=...)`.

    Thread-safe: capture() is called from two threads (c2s and s2c).
    """

    def __init__(
        self,
        trace_path: str | Path | None = None,
        session_id: str | None = None,
        server_name: str | None = None,
    ) -> None:
        self._run_id = _new_id("run")
        self._session_id = session_id or _new_id("sess")
        self._server_name = server_name
        self._trace_path = self._resolve_trace_path(trace_path)
        self._lock = threading.Lock()
        self._closed = False
        self._write_run_start()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def capture(self, line: bytes, direction: str) -> None:
        """Called by run_proxy after each forwarded line.

        line      -- raw bytes of the forwarded line (may not be valid JSON)
        direction -- "client_to_server" or "server_to_client"
        """
        line = line.strip()
        if not line:
            return
        try:
            msg = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        event = self._build_event(msg, direction)
        if event is not None:
            self._append(event)

    def close(self) -> None:
        """Write the run_end event. Idempotent."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._write_run_end()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _resolve_trace_path(self, explicit: str | Path | None) -> Path:
        if explicit is not None:
            return Path(explicit)
        env_path = os.environ.get("AGENTVIEW_TRACE_PATH")
        if env_path:
            return Path(env_path)
        trace_dir = Path(os.environ.get("AGENTVIEW_TRACE_DIR", "traces"))
        return trace_dir / f"{self._run_id}.jsonl"

    def _build_event(self, msg: dict, direction: str) -> dict | None:
        """Convert a JSON-RPC message to a span_end event envelope."""
        method = msg.get("method")
        req_id = msg.get("id")

        # Determine message category: request, response, or notification
        if method is not None and req_id is not None:
            # JSON-RPC request (has method and id)
            msg_kind = "request"
        elif method is not None and req_id is None:
            # JSON-RPC notification (has method, no id)
            msg_kind = "notification"
        elif method is None and req_id is not None:
            # JSON-RPC response (has id, no method)
            msg_kind = "response"
            method = None
        else:
            # Batch or malformed -- capture as-is with minimal metadata
            msg_kind = "unknown"

        tool_name: str | None = None
        if method == "tools/call":
            tool_name = _tool_name_from_params(msg.get("params"))

        # Execution status based on whether response contains an error
        exec_status = "OK"
        result_status: str | None = None
        if msg_kind == "response":
            if "error" in msg:
                exec_status = "ERROR"
                result_status = "INVALID"
            elif "result" in msg:
                result = msg["result"]
                # Empty result heuristic: content list present but empty
                if isinstance(result, dict):
                    content = result.get("content")
                    if content is not None and isinstance(content, list) and len(content) == 0:
                        result_status = "EMPTY"
                    else:
                        result_status = "VALID"

        now = _now_iso()
        span_id = _new_id("sp")

        payload = {
            "id": span_id,
            "run_id": self._run_id,
            "parent_id": None,
            "kind": "TOOL" if method == "tools/call" or (msg_kind == "response" and tool_name is None) else "AGENT",
            "name": method or f"response:{req_id}",
            "label": _label(method, msg_kind, tool_name),
            "started_at": now,
            "ended_at": now,
            "execution_status": exec_status,
            "result_status": result_status,
            "attributes": {
                "msg_kind": msg_kind,
                "raw_method": method,
                "raw_id": req_id,
            },
        }

        mcp_metadata = {
            "protocol": "jsonrpc-2.0",
            "transport": "stdio",
            "session_id": self._session_id,
            "request_id": req_id,
            "message_direction": direction,
            "method": method,
            "tool_name": tool_name,
        }

        return {
            "schema_version": SCHEMA_VERSION,
            "event_type": "span_end",
            "event_id": _new_id("evt"),
            "run_id": self._run_id,
            "recorded_at": now,
            "payload": payload,
            "mcp_metadata": mcp_metadata,
        }

    def _write_run_start(self) -> None:
        now = _now_iso()
        event = {
            "schema_version": SCHEMA_VERSION,
            "event_type": "run_start",
            "event_id": _new_id("evt"),
            "run_id": self._run_id,
            "recorded_at": now,
            "payload": {
                "id": self._run_id,
                "name": self._server_name or "mcp-proxy-session",
                "started_at": now,
                "execution_status": "OK",
                "metadata": {
                    "session_id": self._session_id,
                    "transport": "stdio",
                },
            },
            "mcp_metadata": {
                "protocol": "jsonrpc-2.0",
                "transport": "stdio",
                "session_id": self._session_id,
            },
        }
        self._append(event)

    def _write_run_end(self) -> None:
        now = _now_iso()
        event = {
            "schema_version": SCHEMA_VERSION,
            "event_type": "run_end",
            "event_id": _new_id("evt"),
            "run_id": self._run_id,
            "recorded_at": now,
            "payload": {
                "id": self._run_id,
                "ended_at": now,
                "execution_status": "OK",
            },
            "mcp_metadata": {
                "protocol": "jsonrpc-2.0",
                "transport": "stdio",
                "session_id": self._session_id,
            },
        }
        self._append(event)

    def _append(self, event: dict) -> None:
        with self._lock:
            path = self._trace_path
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(event, ensure_ascii=False))
                    f.write("\n")
            except OSError as exc:
                print(f"agentview interceptor: write error: {exc}", file=sys.stderr)


def _label(method: str | None, msg_kind: str, tool_name: str | None) -> str:
    """Human-readable label for a JSON-RPC message span."""
    if method == "tools/call" and tool_name:
        name = tool_name.replace("_", " ").capitalize()
        return f"Called tool: {name}"
    if method == "tools/list":
        return "Listed available tools"
    if method == "initialize":
        return "Initialized MCP session"
    if method and msg_kind == "request":
        return f"Request: {method}"
    if msg_kind == "notification" and method:
        return f"Notification: {method}"
    if msg_kind == "response":
        return "Tool response"
    return method or "Unknown message"
