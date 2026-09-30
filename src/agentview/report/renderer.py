"""Report payload dict -> single self-contained HTML file.

Inlines the template, CSS, vendored Cytoscape.js, its MIT license, and the
report payload JSON into one file that opens offline. The payload is embedded
inside a `<script type="application/json">` element so it never enters an HTML
or JavaScript execution context; the runtime parses it with `JSON.parse`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_ASSETS_DIR = Path(__file__).parent / "assets"
_TEMPLATE_PATH = Path(__file__).parent / "template.html"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _safe_json(payload: dict[str, Any]) -> str:
    """Serialize payload for a <script type=application/json> block.

    The only sequence that can end a script element is a literal `</`. We
    escape it so the browser will never terminate the script prematurely.
    """
    raw = json.dumps(payload, ensure_ascii=False)
    return raw.replace("</", "<\\/")


def _run_label(run: dict[str, Any]) -> str:
    query = (run.get("metadata") or {}).get("query")
    if query:
        return f'Agent run: "{query}"'
    return f"Agent run: {run['name']}"


def _run_subtitle(run: dict[str, Any]) -> str:
    started = run.get("started_at", "")
    return f"Started {started} \u00b7 Run ID {run['id']}"


def _run_status_text(status: str | None) -> str:
    mapping = {
        "VALID": "Worked as expected",
        "PARTIAL": "Partial results",
        "EMPTY": "No results",
        "INVALID": "Failed or unsupported claim",
        "UNKNOWN": "Not judged",
    }
    return mapping.get(status or "UNKNOWN", status or "UNKNOWN")


def _run_status_class(status: str | None) -> str:
    mapping = {
        "VALID": "valid",
        "PARTIAL": "empty",
        "EMPTY": "empty",
        "INVALID": "invalid",
        "UNKNOWN": "unknown",
    }
    return mapping.get(status or "UNKNOWN", "unknown")


def _run_one_liner(payload: dict[str, Any]) -> str:
    run = payload["run"]
    query = (run.get("metadata") or {}).get("query")
    n_spans = len(payload["graph"]["nodes"])
    if query:
        return (
            f'The agent received the request "{query}" and worked through '
            f"{n_spans} step(s). The graph below shows every step and how it "
            f"turned out. Colors show which steps worked and which did not."
        )
    return (
        f"The agent worked through {n_spans} step(s). The graph below shows "
        f"every step and how it turned out. Colors show which steps worked "
        f"and which did not."
    )


def render(payload: dict[str, Any]) -> str:
    """Render a payload dict to a single self-contained HTML string."""
    template = _read(_TEMPLATE_PATH)
    css = _read(_ASSETS_DIR / "report.css")
    cytoscape_js = _read(_ASSETS_DIR / "cytoscape.min.js")
    cytoscape_license = _read(_ASSETS_DIR / "cytoscape.LICENSE")

    run = payload["run"]
    result_status = run.get("result_status") or "UNKNOWN"

    replacements = {
        "{{REPORT_TITLE}}": f"agentview report: {run['name']}",
        "{{REPORT_CSS}}": css,
        "{{REPORT_NOTICE}}": payload["notice"],
        "{{RUN_LABEL}}": _run_label(run),
        "{{RUN_SUBTITLE}}": _run_subtitle(run),
        "{{RUN_STATUS_CLASS}}": _run_status_class(result_status),
        "{{RUN_STATUS_TEXT}}": _run_status_text(result_status),
        "{{RUN_ONE_LINER}}": _run_one_liner(payload),
        "{{CYTOSCAPE_LICENSE}}": cytoscape_license,
        "{{REPORT_DATA}}": _safe_json(payload),
        "{{CYTOSCAPE_JS}}": cytoscape_js,
    }

    html = template
    for placeholder, value in replacements.items():
        html = html.replace(placeholder, value)
    return html


def render_to_file(payload: dict[str, Any], output_path: str | Path) -> Path:
    """Render and write a single HTML file. Returns the output path."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(payload), encoding="utf-8")
    return out
