"""agentview: open agent quality layer.

Wrap your agent code with @observe_agent, @observe_tool, @observe_model, and
@observe_validation. Runs write a JSONL trace; the report tools turn that
trace into a single self-contained HTML file with a colored node graph.
"""

from agentview.context import RunHandle, current_run, current_span_id
from agentview.decorators import (
    observe_agent,
    observe_model,
    observe_tool,
    observe_validation,
)
from agentview.events import ValidationResult

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "observe_agent",
    "observe_tool",
    "observe_model",
    "observe_validation",
    "current_run",
    "current_span_id",
    "RunHandle",
    "ValidationResult",
]
