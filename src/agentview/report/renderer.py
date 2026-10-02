"""Report payload dict -> single self-contained HTML file.

Inlines the template, CSS, and the report payload into one file that opens
offline. The primary view is a vertical timeline of plain-English step cards,
modelled on delivery-tracking and post-mortem layouts. The payload is embedded
inside a `<script type="application/json">` element so it never enters an HTML
or JavaScript execution context.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

_ASSETS_DIR = Path(__file__).parent / "assets"
_TEMPLATE_PATH = Path(__file__).parent / "template.html"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _safe_json(payload: dict[str, Any]) -> str:
    """Serialize payload for a <script type=application/json> block."""
    raw = json.dumps(payload, ensure_ascii=False)
    return raw.replace("</", "<\\/")


def _esc(text: Any) -> str:
    if text is None:
        return ""
    return html.escape(str(text), quote=True)


# ---------------------------------------------------------------------------
# Status vocabulary
# ---------------------------------------------------------------------------

_STATUS_CLASS = {
    "VALID": "valid",
    "PARTIAL": "empty",
    "EMPTY": "empty",
    "INVALID": "invalid",
    "UNKNOWN": "unknown",
}

_STATUS_GLYPH = {
    "valid": "\u2713",
    "empty": "!",
    "invalid": "\u2715",
    "unknown": "?",
}

_OUTCOME_LABEL = {
    "VALID": "Worked as expected",
    "PARTIAL": "Partial results",
    "EMPTY": "No results",
    "INVALID": "Unsupported claim",
    "UNKNOWN": "Not judged",
}


def _cls(status: str | None) -> str:
    return _STATUS_CLASS.get(status or "UNKNOWN", "unknown")


def _glyph(cls: str) -> str:
    return _STATUS_GLYPH.get(cls, "")


def _step_class(node: dict) -> str:
    if node.get("execution_status") and node["execution_status"] != "OK":
        return "invalid"
    return _cls(node.get("result_status"))


# ---------------------------------------------------------------------------
# Timing helpers
# ---------------------------------------------------------------------------

def _seconds(ms: int | None) -> str:
    if ms is None:
        return ""
    s = ms / 1000
    if s < 10:
        return f"{s:.2f}s"
    return f"{s:.0f}s"


def _time_offset(start_iso: str | None, at_iso: str | None) -> str:
    if not start_iso or not at_iso:
        return ""
    from datetime import datetime

    a = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    b = datetime.fromisoformat(at_iso.replace("Z", "+00:00"))
    delta_ms = int((b - a).total_seconds() * 1000)
    return _seconds(delta_ms)


# ---------------------------------------------------------------------------
# Payload accessors
# ---------------------------------------------------------------------------

def _agent_node(payload: dict) -> dict | None:
    for n in payload["graph"]["nodes"]:
        if n.get("kind") == "AGENT":
            return n
    return None


def _first_failed(payload: dict) -> dict | None:
    for n in payload["graph"]["nodes"]:
        if n.get("kind") in ("AGENT", "VALIDATION"):
            continue
        if n.get("result_status") == "INVALID":
            return n
        if n.get("execution_status") and n["execution_status"] != "OK":
            return n
    # Fall back to any INVALID node including VALIDATION
    for n in payload["graph"]["nodes"]:
        if n.get("kind") == "AGENT":
            continue
        if n.get("result_status") == "INVALID":
            return n
    return None


def _validators_by_target(payload: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for n in payload["graph"]["nodes"]:
        if n.get("kind") == "VALIDATION" and n.get("validates"):
            out.setdefault(n["validates"], []).append(n)
    return out


# ---------------------------------------------------------------------------
# Plain-English text for a step
# ---------------------------------------------------------------------------

def _step_outcome_text(node: dict) -> str:
    kind = node.get("kind")
    status = node.get("result_status")
    attrs = node.get("attributes") or {}

    if kind == "TOOL":
        if status == "EMPTY":
            return "Returned no results."
        if status == "VALID":
            length = attrs.get("return_length")
            if isinstance(length, int):
                noun = "result" if length == 1 else "results"
                return f"Returned {length} {noun}."
            return "Completed successfully."
        if status == "PARTIAL":
            return "Returned partial results."
        if status == "INVALID":
            return "The output did not pass its check."
        return "Completed."

    if kind == "MODEL":
        if status == "INVALID":
            return "The reply did not pass its check."
        if status == "VALID":
            return "Produced a reply."
        return "Produced a reply. Its content was not judged here."

    if kind == "RETRIEVAL":
        if status == "EMPTY":
            return "No matching documents were found."
        if status == "VALID":
            return "Retrieved matching documents."
        return "Completed."

    return node.get("explanation") or "Completed."


def _agent_finish_outcome(agent: dict) -> str:
    status = agent.get("result_status") or "UNKNOWN"
    if status == "VALID":
        return "The overall run completed successfully."
    if status == "INVALID":
        return "The overall run is marked failed because a step did not pass its check."
    if status == "EMPTY":
        return "The overall run is marked as returning no results."
    if status == "PARTIAL":
        return "The overall run returned partial results."
    return "The run finished."


def _validator_substep_html(validator: dict) -> str:
    vr = validator.get("validation_result") or {}
    v_status = validator.get("result_status")
    v_cls = _cls(v_status)
    badge_class = f"substep-badge substep-badge--{v_cls}"
    if v_cls == "valid":
        badge_text = "Passed check"
    elif v_cls == "empty":
        badge_text = "Partial check"
    elif v_cls == "invalid":
        badge_text = "Failed check"
    else:
        badge_text = "Check"

    text = _esc(validator.get("label") or validator.get("name") or "Validator")
    message = vr.get("message")
    if v_cls == "invalid" and vr.get("category"):
        detail = f"judged this step as <em>{_esc(vr['category'].replace('_', ' '))}</em>"
    elif v_cls == "valid":
        detail = "judged this step as passing"
    else:
        detail = "reviewed this step"

    inner = f"<strong>{text}</strong> {detail}."
    if message and v_cls != "valid":
        inner += f' <span class="substep-msg">{_esc(message)}</span>'

    return (
        f'<div class="substep">'
        f'<span class="{badge_class}">{_esc(badge_text)}</span>'
        f'<div class="substep-text">{inner}</div>'
        f"</div>"
    )


def _failure_callout_html(node: dict, validators: list[dict]) -> str:
    invalid_validator = next(
        (v for v in validators if _cls(v.get("result_status")) == "invalid"),
        None,
    )
    psr = node.get("parent_status_reason") or {}
    exec_error = node.get("error") if node.get("execution_status") not in (None, "OK") else None

    lines: list[str] = ['<div class="callout" role="alert">']
    lines.append('<h4>Why this failed</h4>')

    if invalid_validator:
        vr = invalid_validator.get("validation_result") or {}
        message = vr.get("message") or "A validator judged this step as failing."
        lines.append(f'<p>{_esc(message)}</p>')
        if vr.get("category"):
            lines.append(
                '<dl class="evidence">'
                f'<dt>Category</dt><dd>{_esc(vr["category"].replace("_", " "))}</dd>'
                f'<dt>Judged by</dt><dd>{_esc(invalid_validator.get("label") or invalid_validator.get("name"))}</dd>'
                "</dl>"
            )
    elif exec_error:
        err_type = exec_error.get("type", "Error")
        err_msg = exec_error.get("message", "The step raised an exception.")
        lines.append(f'<p>{_esc(err_msg)}</p>')
        lines.append(
            '<dl class="evidence">'
            f'<dt>Error type</dt><dd>{_esc(err_type)}</dd>'
            "</dl>"
        )
    elif psr:
        lines.append(f'<p>{_esc(node.get("explanation") or "This step inherited a failure from a child step.")}</p>')
    else:
        lines.append(f'<p>{_esc(node.get("explanation") or "This step did not complete cleanly.")}</p>')

    lines.append("</div>")
    return "".join(lines)


def _dev_detail_html(node: dict) -> str:
    """Collapsed developer-detail block per step."""
    detail = {
        "id": node.get("id"),
        "name": node.get("name"),
        "kind": node.get("kind"),
        "execution_status": node.get("execution_status"),
        "result_status": node.get("result_status"),
        "duration_ms": node.get("duration_ms"),
        "attributes": node.get("attributes"),
    }
    if node.get("validation_result"):
        detail["validation_result"] = node["validation_result"]
    if node.get("parent_status_reason"):
        detail["parent_status_reason"] = node["parent_status_reason"]
    body = _esc(json.dumps(detail, indent=2, ensure_ascii=False))
    return (
        '<details class="dev-detail">'
        "<summary>Developer detail</summary>"
        f"<pre>{body}</pre>"
        "</details>"
    )


def _step_card_html(
    node: dict,
    validators: list[dict],
    start_iso: str | None,
    step_num: int | None,
) -> str:
    cls = _step_class(node)
    dot_glyph = _glyph(cls)
    dot_aria = {"valid": "Success", "empty": "Returned nothing", "invalid": "Failed", "unknown": "Unknown"}[cls]
    title = _esc(node.get("label") or node.get("name"))
    outcome = _step_outcome_text(node)
    time_str = _time_offset(start_iso, node.get("ended_at"))

    parts: list[str] = []
    parts.append('<li class="step">')
    parts.append(f'<div class="step-dot step-dot--{cls}" aria-label="{_esc(dot_aria)}">{_esc(dot_glyph)}</div>')
    parts.append(f'<div class="step-card step-card--{cls}">')
    parts.append('<div class="step-header">')
    parts.append(f'<div class="step-title">{title}</div>')
    if time_str:
        parts.append(f'<div class="step-time">{_esc(time_str)}</div>')
    parts.append("</div>")
    parts.append(f'<p class="step-outcome">{_esc(outcome)}</p>')

    if cls == "invalid":
        parts.append(_failure_callout_html(node, validators))

    if validators:
        parts.append('<div class="substeps" aria-label="Validators for this step">')
        for v in validators:
            parts.append(_validator_substep_html(v))
        parts.append("</div>")

    parts.append(_dev_detail_html(node))
    parts.append("</div>")
    parts.append("</li>")
    return "".join(parts)


def _request_card_html(query: str) -> str:
    return (
        '<li class="step">'
        '<div class="step-dot step-dot--valid" aria-label="Received">&#x2713;</div>'
        '<div class="step-card step-card--valid">'
        '<div class="step-header">'
        '<div class="step-title">Received the request</div>'
        '<div class="step-time">0.00s</div>'
        "</div>"
        f'<p class="step-outcome">The agent was asked: "<strong>{_esc(query)}</strong>".</p>'
        "</div>"
        "</li>"
    )


def _finish_card_html(agent: dict, start_iso: str | None) -> str:
    cls = _step_class(agent)
    dot_glyph = _glyph(cls)
    time_str = _time_offset(start_iso, agent.get("ended_at"))
    outcome = _agent_finish_outcome(agent)
    return (
        '<li class="step">'
        f'<div class="step-dot step-dot--{cls}" aria-label="Finished">{_esc(dot_glyph)}</div>'
        f'<div class="step-card step-card--{cls}">'
        '<div class="step-header">'
        '<div class="step-title">Finished the run</div>'
        f'<div class="step-time">{_esc(time_str)}</div>'
        "</div>"
        f'<p class="step-outcome">{_esc(outcome)}</p>'
        "</div>"
        "</li>"
    )


def _timeline_html(payload: dict) -> str:
    agent = _agent_node(payload)
    start_iso = agent["started_at"] if agent else None
    validators = _validators_by_target(payload)

    parts: list[str] = ['<ol class="timeline" style="list-style:none; margin:0; padding-left:32px;">']

    query = (payload["run"].get("metadata") or {}).get("query")
    if query:
        parts.append(_request_card_html(query))

    for n in payload["graph"]["nodes"]:
        kind = n.get("kind")
        if kind in ("AGENT", "VALIDATION"):
            continue
        node_validators = validators.get(n["id"], [])
        parts.append(_step_card_html(n, node_validators, start_iso, None))

    if agent:
        parts.append(_finish_card_html(agent, start_iso))

    parts.append("</ol>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Summary card
# ---------------------------------------------------------------------------

def _summary_headline(payload: dict) -> str:
    failed = _first_failed(payload)
    if failed:
        validators = _validators_by_target(payload).get(failed["id"], [])
        invalid_v = next((v for v in validators if _cls(v.get("result_status")) == "invalid"), None)
        if invalid_v:
            vr = invalid_v.get("validation_result") or {}
            if vr.get("message"):
                return vr["message"]
        if failed.get("error", {}) and failed["error"].get("message"):
            return f'The agent stopped at "{failed.get("label")}": {failed["error"]["message"]}'
        return f'The agent failed at "{failed.get("label")}".'
    if payload["run"].get("result_status") == "VALID":
        agent = _agent_node(payload)
        return f'The agent completed "{agent.get("label", "the request")}" successfully.' if agent else "The agent completed successfully."
    return "The run finished."


def _summary_sub(payload: dict) -> str:
    query = (payload["run"].get("metadata") or {}).get("query")
    if query:
        return f'Request: "{query}"'
    return f'Run: {payload["run"].get("name", "")}'


def _run_facts(payload: dict) -> list[dict]:
    nodes = payload["graph"]["nodes"]
    step_nodes = [n for n in nodes if n.get("kind") != "AGENT"]
    agent = _agent_node(payload)
    total_ms = (agent or {}).get("duration_ms") or sum(n.get("duration_ms") or 0 for n in step_nodes)
    status = payload["run"].get("result_status") or "UNKNOWN"
    cls = _cls(status)
    facts = [
        {"label": "Outcome", "value": _OUTCOME_LABEL.get(status, status), "class": cls},
        {"label": "Steps", "value": str(len(step_nodes)), "class": "neutral"},
        {"label": "Duration", "value": _seconds(total_ms), "class": "neutral"},
    ]
    failed = _first_failed(payload)
    if failed:
        facts.append({
            "label": "Failed at",
            "value": failed.get("label") or failed.get("name") or "step",
            "class": "invalid",
        })
    return facts


def _facts_html(payload: dict) -> str:
    parts: list[str] = ['<div class="summary-facts">']
    for fact in _run_facts(payload):
        parts.append(
            f'<div class="summary-fact"><div class="label">{_esc(fact["label"])}</div>'
            f'<div class="value value--{fact["class"]}">{_esc(fact["value"])}</div></div>'
        )
    parts.append("</div>")
    return "".join(parts)


def _narrative_paragraph(payload: dict) -> str:
    query = (payload["run"].get("metadata") or {}).get("query")
    nodes = payload["graph"]["nodes"]
    failed = _first_failed(payload)
    step_nodes = [n for n in nodes if n.get("kind") not in ("AGENT", "VALIDATION")]

    if failed:
        validators = _validators_by_target(payload).get(failed["id"], [])
        invalid_v = next((v for v in validators if _cls(v.get("result_status")) == "invalid"), None)
        opener = f'The agent was asked "{query}".' if query else "The agent started the run."
        middle = f' It ran {len(step_nodes)} step{"s" if len(step_nodes) != 1 else ""}, but "{failed.get("label")}" did not pass its check.'
        if invalid_v:
            vr = invalid_v.get("validation_result") or {}
            if vr.get("message"):
                middle += f" The validator said: {vr['message']}"
        closer = " The overall run is recorded as a failure."
        return opener + middle + closer

    opener = f'The agent was asked "{query}".' if query else "The agent ran."
    middle = f' It completed {len(step_nodes)} step{"s" if len(step_nodes) != 1 else ""} cleanly.'
    return opener + middle


def _top_meta(payload: dict) -> str:
    run = payload["run"]
    started = run.get("started_at", "")
    return f"{_esc(run.get('name', ''))} &middot; {_esc(started)}"


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render(payload: dict[str, Any]) -> str:
    """Render a payload dict to a single self-contained HTML string."""
    template = _read(_TEMPLATE_PATH)
    css = _read(_ASSETS_DIR / "report.css")

    run = payload["run"]
    result_status = run.get("result_status") or "UNKNOWN"
    summary_cls = _cls(result_status)

    replacements = {
        "{{REPORT_TITLE}}": f"AgentView report: {run.get('name', 'agent run')}",
        "{{REPORT_CSS}}": css,
        "{{REPORT_NOTICE}}": payload["notice"],
        "{{TOP_META}}": _top_meta(payload),
        "{{SUMMARY_CLASS}}": summary_cls,
        "{{SUMMARY_GLYPH}}": _glyph(summary_cls),
        "{{SUMMARY_HEADLINE}}": _esc(_summary_headline(payload)),
        "{{SUMMARY_SUB}}": _esc(_summary_sub(payload)),
        "{{FACTS_HTML}}": _facts_html(payload),
        "{{TIMELINE_HTML}}": _timeline_html(payload),
        "{{NARRATIVE_PARAGRAPH}}": _esc(_narrative_paragraph(payload)),
        "{{REPORT_DATA}}": _safe_json(payload),
    }

    html_out = template
    for placeholder, value in replacements.items():
        html_out = html_out.replace(placeholder, value)
    return html_out


def render_to_file(payload: dict[str, Any], output_path: str | Path) -> Path:
    """Render and write a single HTML file. Returns the output path."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(payload), encoding="utf-8")
    return out
