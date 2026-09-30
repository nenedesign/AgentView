"""JSONL trace file -> report payload dict.

Reads a JSONL trace file that follows the v0.1 file-format contract, aggregates
derived status fields (conservative diagnostic aggregation, worst-child wins),
and produces the payload dict consumed by the renderer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "0.1"

REPORT_NOTICE = (
    "This report may contain captured agent inputs and outputs. "
    "Open it only in a trusted environment and share it according to your "
    "organization's data policies."
)

EXECUTION_SEVERITY = {"OK": 0, "CANCELLED": 1, "TIMEOUT": 2, "ERROR": 3, "UNKNOWN": 0}
RESULT_SEVERITY = {"VALID": 0, "UNKNOWN": 1, "PARTIAL": 2, "EMPTY": 3, "INVALID": 4}
EVAL_SEVERITY = {"PASS": 0, "NOT_RUN": 1, "WARN": 2, "FAIL": 3}

DEFAULT_EXPLANATIONS = {
    ("TOOL", "EMPTY"): "This tool returned no results.",
    ("TOOL", "INVALID"): "This tool returned data in an unexpected shape.",
    ("TOOL", "PARTIAL"): "This tool returned partial results.",
    ("MODEL", "UNKNOWN"): "The model returned a reply; its content was not judged here.",
    ("VALIDATION", "VALID"): "The answer matched the evidence.",
    ("AGENT", "INVALID"): "The agent produced an answer that a validator marked invalid.",
    ("AGENT", "EMPTY"): "A tool this agent depends on returned no results.",
}


@dataclass
class Span:
    id: str
    parent_id: str | None
    kind: str
    name: str
    label: str
    started_at: str
    ended_at: str
    execution_status: str
    result_status: str | None
    attributes: dict
    events: list
    error: dict | None
    validation_result: dict | None = None
    validates: str | None = None
    parent_status_reason: dict | None = field(default=None)


@dataclass
class Run:
    id: str
    name: str
    started_at: str
    ended_at: str | None
    execution_status: str
    metadata: dict
    result_status: str | None = None
    evaluation_status: str = "NOT_RUN"


def _worse_result(a: str | None, b: str | None) -> str | None:
    if a is None:
        return b
    if b is None:
        return a
    return a if RESULT_SEVERITY[a] >= RESULT_SEVERITY[b] else b


def _worse_execution(a: str, b: str) -> str:
    return a if EXECUTION_SEVERITY.get(a, 0) >= EXECUTION_SEVERITY.get(b, 0) else b


def _duration_ms(started_at: str, ended_at: str) -> int:
    # RFC 3339 with ms precision; use simple string parsing via datetime.
    from datetime import datetime

    start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    end = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    return int((end - start).total_seconds() * 1000)


def _explain(span: Span) -> str:
    if span.validation_result and span.validation_result.get("message"):
        return span.validation_result["message"]
    if span.parent_status_reason:
        r = span.parent_status_reason
        child_name = r.get("inherited_from", "a child step")
        child_status = r.get("child_status", "").lower()
        if r.get("reason") == "validation_verdict":
            return f"A validator ({child_name}) judged this step's output as {child_status}."
        return f"This step inherited its status from {child_name}: {child_status}."
    key = (span.kind, span.result_status or "")
    if key in DEFAULT_EXPLANATIONS:
        return DEFAULT_EXPLANATIONS[key]
    if span.execution_status == "OK" and span.result_status in (None, "VALID"):
        return "This step completed normally."
    return "See details for more information."


def parse_trace(path: str | Path) -> tuple[Run, list[Span]]:
    """Read a JSONL trace file. Returns (run, spans in insertion order)."""
    run: Run | None = None
    run_end_payload: dict | None = None
    spans: list[Span] = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        event = json.loads(raw)
        etype = event["event_type"]
        payload = event["payload"]
        if etype == "run_start":
            run = Run(
                id=payload["id"],
                name=payload["name"],
                started_at=payload["started_at"],
                ended_at=None,
                execution_status=payload.get("execution_status", "OK"),
                metadata=payload.get("metadata", {}),
            )
        elif etype == "span_end":
            spans.append(
                Span(
                    id=payload["id"],
                    parent_id=payload.get("parent_id"),
                    kind=payload["kind"],
                    name=payload["name"],
                    label=payload.get("label", payload["name"]),
                    started_at=payload["started_at"],
                    ended_at=payload["ended_at"],
                    execution_status=payload.get("execution_status", "OK"),
                    result_status=payload.get("result_status"),
                    attributes=payload.get("attributes", {}),
                    events=payload.get("events", []),
                    error=payload.get("error"),
                    validation_result=payload.get("validation_result"),
                    validates=payload.get("validates"),
                )
            )
        elif etype == "run_end":
            run_end_payload = payload
    if run is None:
        raise ValueError(f"Trace {path} has no run_start event")
    if run_end_payload is not None:
        run.ended_at = run_end_payload.get("ended_at")
        run.execution_status = _worse_execution(
            run.execution_status, run_end_payload.get("execution_status", "OK")
        )
        final_metadata = run_end_payload.get("metadata")
        if isinstance(final_metadata, dict):
            run.metadata = {**run.metadata, **final_metadata}
    return run, spans


def aggregate(run: Run, spans: list[Span]) -> None:
    """Conservative diagnostic aggregation: worst-child wins on execution and result status.

    Records parent_status_reason on each parent whose status changed due to a child.
    Runs bottom-up so grandchildren propagate through parents.
    """
    by_id = {s.id: s for s in spans}
    children_of: dict[str, list[Span]] = {}
    for s in spans:
        if s.parent_id:
            children_of.setdefault(s.parent_id, []).append(s)

    # Depth-first post-order to guarantee children resolve before parents.
    roots = [s for s in spans if s.parent_id is None or s.parent_id not in by_id]
    order: list[Span] = []
    visited: set[str] = set()

    def visit(node: Span) -> None:
        if node.id in visited:
            return
        visited.add(node.id)
        for child in children_of.get(node.id, []):
            visit(child)
        order.append(node)

    for root in roots:
        visit(root)

    for span in order:
        children = children_of.get(span.id, [])
        if not children:
            continue
        # Aggregate execution
        worst_exec = span.execution_status
        for c in children:
            worst_exec = _worse_execution(worst_exec, c.execution_status)
        span.execution_status = worst_exec
        # Aggregate result: worst-child, and record why if it beat what we had
        prior = span.result_status
        worst_child = None
        worst_child_status: str | None = None
        for c in children:
            candidate = _worse_result(worst_child_status, c.result_status)
            if candidate != worst_child_status:
                worst_child_status = candidate
                worst_child = c
        if worst_child_status is not None:
            aggregated = _worse_result(prior, worst_child_status)
            # Only annotate inheritance when the parent picked up a non-VALID
            # status from a child. All-VALID chains do not need an inheritance
            # note; there is no story to explain.
            if (
                aggregated != prior
                and aggregated != "VALID"
                and worst_child is not None
            ):
                span.parent_status_reason = {
                    "inherited_from": worst_child.name,
                    "span_id": worst_child.id,
                    "child_status": worst_child_status,
                }
            span.result_status = aggregated

    # Second pass: propagate validation verdicts to the spans they judged.
    # A VALIDATION span with `validates=<span_id>` pushes its result_status
    # onto that span (worst-child style), then re-propagates up the ancestor
    # chain so the report shows the model/tool node in the color of its
    # verdict.
    for v in spans:
        if v.kind != "VALIDATION" or not v.validates:
            continue
        target = by_id.get(v.validates)
        if target is None:
            continue
        prior = target.result_status
        new = _worse_result(prior, v.result_status)
        if new != prior and new is not None and new != "VALID":
            target.result_status = new
            target.parent_status_reason = {
                "inherited_from": v.name,
                "span_id": v.id,
                "child_status": new,
                "reason": "validation_verdict",
            }
            # Walk up: parent, grandparent, ... re-applying worst-child.
            cursor = target
            while cursor.parent_id and cursor.parent_id in by_id:
                anc = by_id[cursor.parent_id]
                prev = anc.result_status
                bumped = _worse_result(prev, cursor.result_status)
                if bumped == prev:
                    break
                anc.result_status = bumped
                if bumped != "VALID":
                    anc.parent_status_reason = {
                        "inherited_from": cursor.name,
                        "span_id": cursor.id,
                        "child_status": bumped,
                    }
                cursor = anc

    # Aggregate to the run
    top_level = [s for s in spans if s.parent_id is None]
    for s in top_level:
        run.execution_status = _worse_execution(run.execution_status, s.execution_status)
        run.result_status = _worse_result(run.result_status, s.result_status)
    if run.result_status is None:
        run.result_status = "VALID"


def build_payload(path: str | Path) -> dict[str, Any]:
    """Full pipeline: parse -> aggregate -> report payload dict."""
    run, spans = parse_trace(path)
    aggregate(run, spans)

    nodes = []
    for s in spans:
        nodes.append(
            {
                "id": s.id,
                "label": s.label or s.name,
                "kind": s.kind,
                "name": s.name,
                "execution_status": s.execution_status,
                "result_status": s.result_status or "UNKNOWN",
                "evaluation_status": "NOT_RUN",
                "started_at": s.started_at,
                "ended_at": s.ended_at,
                "duration_ms": _duration_ms(s.started_at, s.ended_at),
                "explanation": _explain(s),
                "attributes": s.attributes,
                "validation_result": s.validation_result,
                "parent_status_reason": s.parent_status_reason,
                "parent_id": s.parent_id,
                "validates": s.validates,
            }
        )

    edges = [
        {"source": s.parent_id, "target": s.id, "type": "parent"}
        for s in spans
        if s.parent_id is not None
    ]
    for s in spans:
        if s.kind == "VALIDATION" and s.validates:
            edges.append({"source": s.id, "target": s.validates, "type": "validates"})

    # Timeline in span-end order (which is what the JSONL preserves).
    timeline = []
    query = run.metadata.get("query")
    step = 1
    if query is not None:
        timeline.append(
            {
                "step": step,
                "span_id": None,
                "label": f"User request: \"{query}\"",
                "at": run.started_at,
            }
        )
        step += 1
    for s in spans:
        timeline.append(
            {
                "step": step,
                "span_id": s.id,
                "kind": s.kind,
                "label": _timeline_label(s),
                "at": s.ended_at,
                "result_status": s.result_status or "UNKNOWN",
            }
        )
        step += 1
    timeline.append(
        {
            "step": step,
            "span_id": None,
            "label": _run_summary(run),
            "at": run.ended_at or run.started_at,
            "result_status": run.result_status or "UNKNOWN",
        }
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "notice": REPORT_NOTICE,
        "run": {
            "id": run.id,
            "name": run.name,
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "execution_status": run.execution_status,
            "result_status": run.result_status,
            "evaluation_status": run.evaluation_status,
            "metadata": run.metadata,
        },
        "graph": {"nodes": nodes, "edges": edges},
        "timeline": timeline,
        "legend": {
            "VALID": "Worked as expected",
            "PARTIAL": "Some results, but not everything",
            "EMPTY": "Returned nothing",
            "INVALID": "Failed or made an unsupported claim",
            "UNKNOWN": "Not judged here",
        },
    }


def _timeline_label(s: Span) -> str:
    if s.kind == "TOOL":
        if s.result_status == "EMPTY":
            return f"{s.label} returned no results"
        if s.result_status == "VALID":
            length = s.attributes.get("return_length")
            if isinstance(length, int):
                return f"{s.label} returned {length} result(s)"
        return f"{s.label} completed"
    if s.kind == "MODEL":
        return f"{s.label} produced a reply"
    if s.kind == "VALIDATION":
        vr = s.validation_result or {}
        cat = vr.get("category")
        if s.result_status == "VALID":
            return f"Validation: passed"
        if cat:
            return f"Validation: {cat.replace('_', ' ')} ({(s.result_status or '').lower()})"
        return f"Validation: {(s.result_status or '').lower()}"
    if s.kind == "AGENT":
        return f"{s.label} finished"
    return f"{s.label}"


def _run_summary(run: Run) -> str:
    rs = run.result_status or "UNKNOWN"
    if rs == "VALID":
        return "Final result: worked as expected"
    if rs == "EMPTY":
        return "Final result: no results"
    if rs == "INVALID":
        return "Final result: answer was marked invalid by a validator"
    if rs == "PARTIAL":
        return "Final result: partial"
    return f"Final result: {rs.lower()}"
