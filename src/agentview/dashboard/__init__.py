"""Agentview dashboard: local-only read-only viewer over JSONL trace files."""

from __future__ import annotations

from .server import create_app

__all__ = ["create_app"]
