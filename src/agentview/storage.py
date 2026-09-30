"""Append-only JSONL trace writer.

One file per run. Events are appended as they are produced, so a partially
written trace is still a valid prefix of the file format contract (every line
is a complete JSON object).
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from agentview.context import _new_id, _now_iso
from agentview.events import SCHEMA_VERSION

_lock = threading.Lock()
_open_files: dict[str, Any] = {}


def default_trace_path(run_id: str) -> Path:
    """Trace file path: env override, else ./traces/{run_id}.jsonl."""
    explicit = os.environ.get("AGENTVIEW_TRACE_PATH")
    if explicit:
        return Path(explicit)
    trace_dir = Path(os.environ.get("AGENTVIEW_TRACE_DIR", "traces"))
    return trace_dir / f"{run_id}.jsonl"


def write_event(
    trace_path: str | Path,
    event_type: str,
    run_id: str,
    payload: dict[str, Any],
) -> None:
    """Append a single event envelope to the trace file."""
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "event_type": event_type,
        "event_id": _new_id("evt"),
        "run_id": run_id,
        "recorded_at": _now_iso(),
        "payload": payload,
    }
    path = Path(trace_path)
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(envelope, ensure_ascii=False))
            f.write("\n")
