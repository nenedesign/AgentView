"""Report generation: trace payload -> single self-contained HTML file."""

from agentview.report.builder import build_payload
from agentview.report.renderer import render, render_to_file

__all__ = ["build_payload", "render", "render_to_file"]
