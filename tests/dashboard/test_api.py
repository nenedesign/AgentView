"""Smoke tests for Milestone 1 Day 1 session reconstruction.

Full acceptance tests (A1-A5) land in Milestone 2+. This file covers the
plumbing: fixture files parse, grouping produces the expected windows, and
unterminated sessions resolve to the 'unknown' state.
"""

from __future__ import annotations

from pathlib import Path

from agentview.dashboard.api import reconstruct_sessions

FIXTURES = Path(__file__).parent.parent.parent / "src" / "agentview" / "dashboard" / "fixtures"


def test_empty_fixture_returns_no_sessions() -> None:
    result = reconstruct_sessions(FIXTURES / "empty-state.jsonl")
    assert result["sessions"] == []
    assert result["skipped_lines"] == 0


def test_happy_session_is_completed_with_three_activities() -> None:
    result = reconstruct_sessions(FIXTURES / "single-session-happy.jsonl")
    assert len(result["sessions"]) == 1
    session = result["sessions"][0]
    assert session["state"] == "completed"
    assert len(session["activities"]) == 3
    assert all(a["status"] == "completed" for a in session["activities"])


def test_error_session_captures_empty_and_failed_statuses() -> None:
    result = reconstruct_sessions(FIXTURES / "single-session-with-error.jsonl")
    session = result["sessions"][0]
    statuses = [a["status"] for a in session["activities"]]
    assert "completed" in statuses
    assert "empty" in statuses
    assert "failed" in statuses


def test_multi_day_resolves_unknown_for_unterminated_session() -> None:
    result = reconstruct_sessions(FIXTURES / "multi-session-day.jsonl")
    sessions = {s["id"]: s for s in result["sessions"]}
    assert sessions["ses_db_afternoon"]["state"] == "unknown"
    assert sessions["ses_fs_morning"]["state"] == "completed"
    assert sessions["ses_search_midday"]["state"] == "exited_with_error"


def test_grouping_stress_produces_four_groups() -> None:
    result = reconstruct_sessions(
        FIXTURES / "grouping-stress.jsonl", group_window_seconds=2.0
    )
    session = result["sessions"][0]
    assert len(session["groups"]) == 4
    assert all(len(g["activity_ids"]) == 5 for g in session["groups"])
    assert all(a["group_id"] is not None for a in session["activities"])
