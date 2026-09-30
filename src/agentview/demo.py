"""Runnable product-search demo. Same logic as examples/product_search/agent.py,
kept inside the package so `agentview demo` works regardless of cwd.

Three paths that make the report tell three different stories:
- success: query returns real results, model summarizes correctly (all green)
- failure_a: query returns nothing, model hallucinates a count (unsupported claim)
- failure_b: query returns results, model names a product that was not returned
"""

from __future__ import annotations

import os
from pathlib import Path

from agentview import (
    ValidationResult,
    current_run,
    observe_agent,
    observe_model,
    observe_tool,
    observe_validation,
)
from agentview.report import build_payload, render_to_file

CATALOG = {
    "wireless headphones": [
        {"sku": "sku_010", "name": "AirWave Pro"},
        {"sku": "sku_011", "name": "SoundHalo 2"},
        {"sku": "sku_012", "name": "BassCloud"},
    ],
    "noise cancelling": [
        {"sku": "sku_100", "name": "Silent Room"},
        {"sku": "sku_200", "name": "QuietPeak"},
    ],
}


@observe_tool(name="search_catalog", label="Search product catalog")
def _search_catalog(query: str) -> list[dict]:
    return list(CATALOG.get(query.lower(), []))


@observe_model(name="respond_to_user", label="Model wrote the reply")
def _respond(query: str, results: list[dict], mode: str) -> str:
    if mode == "success":
        return f"Found {len(results)} matches for '{query}'."
    if mode == "hallucinate_count":
        return f"I found 2 options for '{query}'."
    if mode == "hallucinate_sku":
        return f"For '{query}' I recommend sku_999."
    raise ValueError(f"unknown mode {mode}")


@observe_validation(
    name="validate_search_response",
    label="Check answer against search results",
)
def _validate_count(reply: str, results: list[dict]) -> ValidationResult:
    if "2 options" in reply and len(results) == 0:
        return ValidationResult(
            status="INVALID",
            category="unsupported_claim",
            message="The answer said 2 options were found, but the search returned no results.",
            details={"claimed_count": 2, "actual_count": 0},
        )
    return ValidationResult(
        status="VALID",
        message="The answer matched the search results.",
        details={"claimed_count": len(results), "actual_count": len(results)},
    )


@observe_validation(
    name="validate_search_reference",
    label="Check answer references real products",
)
def _validate_reference(reply: str, results: list[dict]) -> ValidationResult:
    returned_skus = [r["sku"] for r in results]
    for token in reply.replace(".", " ").split():
        if token.startswith("sku_") and token not in returned_skus:
            return ValidationResult(
                status="INVALID",
                category="mismatched_reference",
                message="The answer named a product the search did not return.",
                details={"claimed_sku": token, "returned_skus": returned_skus},
            )
    return ValidationResult(
        status="VALID",
        message="All product references matched the search results.",
        details={"returned_skus": returned_skus},
    )


@observe_agent(name="product_search", label="Product search agent")
def run_success(query: str = "wireless headphones") -> str:
    current_run().set_metadata({"query": query, "demo_path": "success"})
    results = _search_catalog(query)
    reply = _respond(query, results, mode="success")
    _validate_count(reply, results)
    return reply


@observe_agent(name="product_search", label="Product search agent")
def run_failure_a(query: str = "quantum flux capacitor") -> str:
    current_run().set_metadata({"query": query, "demo_path": "failure_a"})
    results = _search_catalog(query)
    reply = _respond(query, results, mode="hallucinate_count")
    _validate_count(reply, results)
    return reply


@observe_agent(name="product_search", label="Product search agent")
def run_failure_b(query: str = "noise cancelling") -> str:
    current_run().set_metadata({"query": query, "demo_path": "failure_b"})
    results = _search_catalog(query)
    reply = _respond(query, results, mode="hallucinate_sku")
    _validate_reference(reply, results)
    return reply


def run_all(output_dir: str | Path = "agentview_demo") -> list[Path]:
    """Run all three demo paths, render three reports, return their paths."""
    out = Path(output_dir)
    trace_dir = out / "traces"
    report_dir = out / "reports"
    trace_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    reports: list[Path] = []
    for name, fn in [
        ("success", run_success),
        ("failure_a", run_failure_a),
        ("failure_b", run_failure_b),
    ]:
        trace_path = trace_dir / f"{name}.jsonl"
        # Make each run write to its own predictable file.
        os.environ["AGENTVIEW_TRACE_PATH"] = str(trace_path)
        try:
            fn()
        finally:
            os.environ.pop("AGENTVIEW_TRACE_PATH", None)
        payload = build_payload(trace_path)
        report_path = render_to_file(payload, report_dir / f"{name}.html")
        reports.append(report_path)
    return reports
