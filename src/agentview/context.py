"""ContextVar-based propagation for the current run and span.

Works transparently for both sync and async code because `contextvars`
propagates across `await` boundaries and `asyncio.Task` creation.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

_current_run: ContextVar["RunHandle | None"] = ContextVar(
    "agentview_current_run", default=None
)
_current_span_id: ContextVar[str | None] = ContextVar(
    "agentview_current_span_id", default=None
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


VALIDATABLE_KINDS = {"MODEL", "TOOL", "RETRIEVAL"}


@dataclass
class RunHandle:
    id: str
    name: str
    started_at: str
    metadata: dict[str, Any] = field(default_factory=dict)
    trace_path: str | None = None
    # Per-parent, id of the most recently closed span whose kind is
    # validatable. Used so `@observe_validation` can auto-link to the thing it
    # judged without the user passing a span id explicitly.
    _last_closed_validatable: dict[str | None, str] = field(default_factory=dict)

    def set_metadata(self, updates: dict[str, Any]) -> None:
        """Merge metadata into the current run. Call from inside the agent."""
        self.metadata.update(updates)

    def note_closed_span(self, parent_id: str | None, kind: str, span_id: str) -> None:
        if kind in VALIDATABLE_KINDS:
            self._last_closed_validatable[parent_id] = span_id

    def last_validatable_under(self, parent_id: str | None) -> str | None:
        return self._last_closed_validatable.get(parent_id)


def current_run() -> RunHandle | None:
    """Return the active RunHandle, or None if no run is in progress."""
    return _current_run.get()


def current_span_id() -> str | None:
    """Return the id of the currently active span, or None."""
    return _current_span_id.get()


def _set_run(run: RunHandle | None):
    return _current_run.set(run)


def _reset_run(token) -> None:
    _current_run.reset(token)


def _set_span(span_id: str | None):
    return _current_span_id.set(span_id)


def _reset_span(token) -> None:
    _current_span_id.reset(token)
