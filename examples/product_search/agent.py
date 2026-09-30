"""Runnable product-search demo agent.

Runs three paths that mirror the fixtures:
- success: query returns real results, model summarizes correctly
- failure_a: query returns nothing, model hallucinates a count (unsupported claim)
- failure_b: query returns results, model names a product that was not returned

Every path is fully self-contained. No network calls. Traces are written to
`traces/{run_id}.jsonl` and can be turned into a report via renderer.
"""

from __future__ import annotations

from agentview import (
    ValidationResult,
    current_run,
    observe_agent,
    observe_model,
    observe_tool,
    observe_validation,
)

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
def search_catalog(query: str) -> list[dict]:
    return list(CATALOG.get(query.lower(), []))


@observe_model(name="respond_to_user", label="Model wrote the reply")
def respond_to_user(query: str, results: list[dict], mode: str) -> str:
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
def validate_search_response(reply: str, results: list[dict]) -> ValidationResult:
    if "2 options" in reply and len(results) == 0:
        return ValidationResult(
            status="INVALID",
            category="unsupported_claim",
            message="The answer said 2 options were found, but the search returned no results.",
            details={"claimed_count": 2, "actual_count": 0},
        )
    return ValidationResult(
        status="VALID",
        category=None,
        message="The answer matched the search results.",
        details={"claimed_count": len(results), "actual_count": len(results)},
    )


@observe_validation(
    name="validate_search_reference",
    label="Check answer references real products",
)
def validate_search_reference(reply: str, results: list[dict]) -> ValidationResult:
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
def run_success(query: str) -> str:
    current_run().set_metadata({"query": query, "demo_path": "success"})
    results = search_catalog(query)
    reply = respond_to_user(query, results, mode="success")
    validate_search_response(reply, results)
    return reply


@observe_agent(name="product_search", label="Product search agent")
def run_failure_a(query: str) -> str:
    current_run().set_metadata({"query": query, "demo_path": "failure_a"})
    results = search_catalog(query)
    reply = respond_to_user(query, results, mode="hallucinate_count")
    validate_search_response(reply, results)
    return reply


@observe_agent(name="product_search", label="Product search agent")
def run_failure_b(query: str) -> str:
    current_run().set_metadata({"query": query, "demo_path": "failure_b"})
    results = search_catalog(query)
    reply = respond_to_user(query, results, mode="hallucinate_sku")
    validate_search_reference(reply, results)
    return reply


if __name__ == "__main__":
    print(run_success("wireless headphones"))
    print(run_failure_a("quantum flux capacitor"))
    print(run_failure_b("noise cancelling"))
