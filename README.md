# agentview

**See what your AI agent actually did, and why it failed, in one picture.**

Agent traceability and observability for teams that need to show their work, not just ship it.

`agentview` is a Python SDK that wraps your agent code, records what happened, and turns the recording into a single self-contained HTML page. The page shows every step your agent took as a colored node in a graph. Green worked. Yellow returned nothing. Red failed or made an unsupported claim. A non-technical viewer can look at the report and understand what went wrong in about thirty seconds, without asking anyone.

The library ships with three demo runs. Two of them fail on purpose, in different ways, so the failure story is visible on first open.

## What the report looks like

A single HTML file. The graph is the first thing you see. Every node has a plain-language label, not a function name. Every failure has a sentence explaining what happened. There is a legend, a step-by-step timeline, and a details panel that shows exactly what the validator said. The file is offline, self-contained, and safe to open in any browser. It never loads anything from the internet.

Run `agentview demo` and open one of the three reports it writes. That is faster than any screenshot.

## Quickstart

```bash
pip install agentview
agentview demo
# Open any of the three files printed under agentview_demo/reports/
```

To wrap your own agent, use four decorators:

```python
from agentview import (
    observe_agent, observe_tool, observe_model, observe_validation,
    current_run, ValidationResult,
)

@observe_tool(name="search_catalog", label="Search product catalog")
def search_catalog(query: str) -> list[dict]:
    return db.search(query)

@observe_model(name="respond_to_user", label="Model wrote the reply")
def respond(query: str, results: list[dict]) -> str:
    return llm.complete(f"Summarize {results} for {query}")

@observe_validation(name="check_answer", label="Check answer against search results")
def check_answer(reply: str, results: list[dict]) -> ValidationResult:
    if not results and "found" in reply.lower():
        return ValidationResult(
            status="INVALID",
            category="unsupported_claim",
            message="The answer claimed matches but the search returned nothing.",
        )
    return ValidationResult(status="VALID", message="The answer matched the search results.")

@observe_agent(name="product_search", label="Product search agent")
def run_agent(query: str) -> str:
    current_run().set_metadata({"query": query})
    results = search_catalog(query)
    reply = respond(query, results)
    check_answer(reply, results)
    return reply

run_agent("wireless headphones")
```

Every run writes a JSONL trace file. Render it:

```bash
agentview report traces/run_abc123.jsonl -o report.html
open report.html
```

## What you are looking at (for a non-technical reader)

The graph shows every step the agent took, in order. Colors tell you what happened.

- **Green** worked as expected.
- **Yellow** returned nothing or partial results.
- **Red** failed, or made a claim that a validator marked as unsupported.
- **Grey** was not judged here.

A dashed orange edge labelled *validates* points from a validation step to the step it judged. If a validator turned a step red, you will see the dashed edge running to whichever step failed judgment. Click any node for the plain-English explanation and the detail the validator returned.

## What it captures

- Which tools ran, in what order, with what shape of input and output (types and lengths, not values, by default).
- Which model calls happened, and how long each took.
- Which validators fired, what they judged, and which step they judged.
- The final status of the run, rolled up from every step.

## What it does not capture (by default)

- Prompt text and model responses. Off by default. Opt in per-run if you need it.
- Secrets. Redacted before storage.
- Anything network-loaded in the report. The HTML file is fully offline.

These defaults are deliberate. A report you can hand to a stakeholder is worth more than a report you cannot share because it leaks data.

## How it differs from Langfuse, Phoenix, MLflow, LangSmith

Those platforms are trace databases. They store spans well and show them in dashboards for developers. They do not produce a portable, layperson-legible HTML file, and they treat validation as an afterthought.

`agentview` is a small library with an opinionated product decision: **the primary artefact is a single HTML page a non-technical stakeholder can understand.** Everything else, including the storage format, is in service of that.

Concretely:

| | agentview | Trace platforms |
|-|-|-|
| Primary artefact | Single self-contained HTML report | Server-hosted dashboard |
| Layperson-first UX | Yes, colored graph plus sentence explanations | No, developer table view |
| Portable | One file, opens anywhere, offline | Requires access to the platform |
| Validation as a first-class span kind | Yes | No, treated as attribute or eval score |
| Server required | No | Usually yes |
| Cost | Zero | Usually usage-based or seat-based |

`agentview` is not a replacement for those platforms. It is a complementary tool: the JSONL trace format is stable and you can also emit to OpenTelemetry, so you can use `agentview` locally and export to a platform separately.

## Design principles

1. **Capture once, interpret many ways.** The JSONL trace file is the source of truth. The HTML report is one interpretation. Evaluators, dashboards, and CI gates can read the same file.
2. **Layperson-first UX.** Every design decision, from node color to sentence-level explanations, is judged against whether a non-technical viewer can understand the report without help. A screenshot of the report has to be legible to someone who has never used the library.
3. **Claim-versus-evidence validation.** A validator is a first-class span kind. It records what claim was made, what evidence was actually available, and what it judged. The report shows both the claim and the evidence, side by side.
4. **Privacy by default.** Prompts and responses off, secrets scrubbed. Users opt in to content capture. This is a baseline, not a differentiator.
5. **Portable single file.** The HTML report is self-contained. No network. No server. No dependencies at open time. It works on an airplane.

## Roadmap

MVP 1 (this release) ships the capture and report loop. Future work builds on it without breaking the API:

- **MVP 2: Evaluate.** Rule-based, policy-based, and LLM-as-judge evaluators that read JSONL traces and annotate spans with pass or fail verdicts.
- **MVP 3: Enforce.** CI quality gate as a reusable GitHub Action. Baseline comparison across runs. Platform adapters (Langfuse, Phoenix, MLflow) via OpenTelemetry.
- **MVP 4: Scale.** Multi-agent orchestration semantics, live-tail UI, token and cost attribution.

## What is inside

- `src/agentview/decorators.py` — the public API: `@observe_agent`, `@observe_tool`, `@observe_model`, `@observe_validation`.
- `src/agentview/events.py` — the JSONL event contract, as Pydantic v2 models. Any external tool can validate a trace file without depending on the SDK.
- `src/agentview/report/` — the report builder and renderer. Vendored [Cytoscape.js](https://js.cytoscape.org/) (MIT) is inlined so the report is one file.
- `examples/product_search/` — the runnable demo agent.

## Requirements

- Python 3.10 or newer.
- Pydantic v2.

Nothing else at runtime. No server. No network.

## License

Apache-2.0. See [LICENSE](LICENSE).

The report inlines a vendored copy of Cytoscape.js, which is MIT-licensed. Its license is embedded in every rendered report and is available at [src/agentview/report/assets/cytoscape.LICENSE](src/agentview/report/assets/cytoscape.LICENSE).
