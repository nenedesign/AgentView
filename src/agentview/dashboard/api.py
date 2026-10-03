"""Session reconstruction from JSONL trace events.

Converts the proxy's event stream (session_start, activity_start, activity_end,
session_end) into the session-centric shape the dashboard renders. Also runs the
activity-grouping algorithm (default 2s inter-activity gap) and resolves each
session's six-state status.

The reader contract follows docs/event-schema.md: parse what we recognise, skip
what we do not. Malformed lines are counted but do not stop the stream.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_GROUP_WINDOW_SECONDS = 2.0


def _parse_timestamp(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _read_events(path: Path) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    skipped = 0
    if not path.exists() or path.stat().st_size == 0:
        return events, skipped
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                skipped += 1
    return events, skipped


def _group_activities(
    activities: list[dict[str, Any]],
    window_seconds: float = DEFAULT_GROUP_WINDOW_SECONDS,
) -> list[dict[str, Any]]:
    """Walk activities in start-time order; open a new group whenever the gap
    since the previous activity's end exceeds the window.

    Non-overlapping only: v1 does not try to correlate overlapping activities
    into the same group (per v1.5 Section 10 A3).
    """
    if not activities:
        return []

    ordered = sorted(activities, key=lambda a: a["_started_at_epoch"])
    groups: list[dict[str, Any]] = []
    current: dict[str, Any] = {
        "id": "grp_0001",
        "window_seconds": window_seconds,
        "started_at": ordered[0]["started_at"],
        "ended_at": ordered[0]["ended_at"],
        "activity_ids": [ordered[0]["id"]],
        "_last_end_epoch": ordered[0]["_ended_at_epoch"],
    }
    for activity in ordered[1:]:
        gap = activity["_started_at_epoch"] - current["_last_end_epoch"]
        if gap > window_seconds:
            groups.append(current)
            current = {
                "id": f"grp_{len(groups) + 1:04d}",
                "window_seconds": window_seconds,
                "started_at": activity["started_at"],
                "ended_at": activity["ended_at"],
                "activity_ids": [activity["id"]],
                "_last_end_epoch": activity["_ended_at_epoch"],
            }
        else:
            current["activity_ids"].append(activity["id"])
            current["ended_at"] = activity["ended_at"]
            current["_last_end_epoch"] = max(
                current["_last_end_epoch"], activity["_ended_at_epoch"]
            )
    groups.append(current)

    activity_to_group = {}
    for group in groups:
        for aid in group["activity_ids"]:
            activity_to_group[aid] = group["id"]
    for activity in activities:
        activity["group_id"] = activity_to_group.get(activity["id"])

    for group in groups:
        group.pop("_last_end_epoch", None)
    return groups


def reconstruct_sessions(
    path: Path,
    group_window_seconds: float = DEFAULT_GROUP_WINDOW_SECONDS,
) -> dict[str, Any]:
    """Parse a JSONL trace file into the dashboard's session-centric view."""
    events, skipped = _read_events(path)
    sessions: dict[str, dict[str, Any]] = {}
    activities_by_session: dict[str, dict[str, dict[str, Any]]] = {}

    for event in events:
        event_type = event.get("event_type")
        session_id = event.get("session_id")
        if not session_id:
            continue

        if event_type == "session_start":
            sessions[session_id] = {
                "id": session_id,
                "server_name": event.get("server_name"),
                "started_at": event.get("recorded_at"),
                "ended_at": None,
                "state": "active",
                "exit_code": None,
                "activities": [],
                "groups": [],
            }
            activities_by_session.setdefault(session_id, {})

        elif event_type == "session_end":
            session = sessions.get(session_id)
            if session is None:
                continue
            session["ended_at"] = event.get("recorded_at")
            session["exit_code"] = event.get("exit_code")
            session["state"] = event.get("state") or "completed"

        elif event_type == "activity_start":
            activity_id = event.get("activity_id")
            if not activity_id:
                continue
            meta = event.get("mcp_metadata") or {}
            activities_by_session.setdefault(session_id, {})[activity_id] = {
                "id": activity_id,
                "session_id": session_id,
                "tool_name": meta.get("tool_name"),
                "method": meta.get("method"),
                "message_direction": meta.get("message_direction"),
                "request_id": meta.get("request_id"),
                "started_at": event.get("recorded_at"),
                "ended_at": None,
                "status": "in_progress",
                "duration_ms": None,
                "result_summary": None,
                "error": None,
                "group_id": None,
            }

        elif event_type == "activity_end":
            activity_id = event.get("activity_id")
            if not activity_id:
                continue
            bucket = activities_by_session.setdefault(session_id, {})
            activity = bucket.get(activity_id)
            if activity is None:
                continue
            activity["ended_at"] = event.get("recorded_at")
            activity["status"] = event.get("status") or "completed"
            activity["duration_ms"] = event.get("duration_ms")
            activity["result_summary"] = event.get("result_summary")
            activity["error"] = event.get("error")

    for session_id, bucket in activities_by_session.items():
        session = sessions.get(session_id)
        if session is None:
            continue
        activities: list[dict[str, Any]] = []
        for activity in bucket.values():
            if activity["started_at"]:
                activity["_started_at_epoch"] = _parse_timestamp(
                    activity["started_at"]
                ).timestamp()
            else:
                activity["_started_at_epoch"] = 0.0
            if activity["ended_at"]:
                activity["_ended_at_epoch"] = _parse_timestamp(
                    activity["ended_at"]
                ).timestamp()
            else:
                activity["_ended_at_epoch"] = activity["_started_at_epoch"]
            activities.append(activity)
        session["groups"] = _group_activities(
            activities, window_seconds=group_window_seconds
        )
        for activity in activities:
            activity.pop("_started_at_epoch", None)
            activity.pop("_ended_at_epoch", None)
        activities.sort(key=lambda a: a["started_at"] or "")
        session["activities"] = activities

        if session["ended_at"] is None:
            session["state"] = "unknown"

    ordered_sessions = sorted(
        sessions.values(), key=lambda s: s["started_at"] or "", reverse=True
    )
    return {
        "sessions": ordered_sessions,
        "skipped_lines": skipped,
        "group_window_seconds": group_window_seconds,
    }
