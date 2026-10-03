"""Pydantic v2 event model: the JSONL contract for agentview traces.

Every trace file is a stream of newline-delimited JSON envelopes. Each envelope
carries a `schema_version`, `event_type`, `event_id`, `run_id`, `recorded_at`,
and a `payload` whose shape depends on the event type.

External consumers can import these models to validate a JSONL file without
importing the rest of the SDK.

Schema compatibility rule (Option A, locked in v1.5):
- Schema version stays at "0.1".
- Optional fields may be added within 0.1.
- Consumers MUST ignore unknown fields. Pydantic models here are configured
  with `extra="ignore"` to enforce this.
- A minor version bump (0.2) is reserved for forward-incompatible changes
  (removing a field, changing a type, renaming). None are planned for v1.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "0.1"

SpanKind = Literal["AGENT", "TOOL", "MODEL", "RETRIEVAL", "VALIDATION"]
ExecutionStatus = Literal["OK", "CANCELLED", "TIMEOUT", "ERROR", "UNKNOWN"]
ResultStatus = Literal["VALID", "UNKNOWN", "PARTIAL", "EMPTY", "INVALID"]
EvaluationStatus = Literal["PASS", "NOT_RUN", "WARN", "FAIL"]


class ErrorInfo(BaseModel):
    type: str
    message: str
    traceback: str | None = None


class ValidationResult(BaseModel):
    status: ResultStatus
    category: str | None = None
    message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class RunStartPayload(BaseModel):
    id: str
    name: str
    started_at: str
    execution_status: ExecutionStatus = "OK"
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunEndPayload(BaseModel):
    id: str
    ended_at: str
    execution_status: ExecutionStatus = "OK"


class SpanEndPayload(BaseModel):
    id: str
    run_id: str
    parent_id: str | None = None
    kind: SpanKind
    name: str
    label: str
    started_at: str
    ended_at: str
    execution_status: ExecutionStatus = "OK"
    result_status: ResultStatus | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    error: ErrorInfo | None = None
    validation_result: ValidationResult | None = None
    validates: str | None = None


MessageDirection = Literal["client_to_server", "server_to_client"]


class MCPMetadata(BaseModel):
    """Optional MCP-specific context attached to a trace event.

    All fields are optional. Captured by the `agentview proxy` command when
    wrapping an MCP server; absent when traces come from the SDK decorator path.
    The JSONL spine stays unified: MCP captures reuse the same envelope and
    attach this block alongside the standard payload.
    """

    model_config = ConfigDict(extra="ignore")

    protocol: str | None = None
    transport: str | None = None
    session_id: str | None = None
    request_id: int | str | None = None
    message_direction: MessageDirection | None = None
    method: str | None = None
    tool_name: str | None = None


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: str = SCHEMA_VERSION
    event_type: Literal["run_start", "span_end", "run_end"]
    event_id: str
    run_id: str
    recorded_at: str
    payload: dict[str, Any]
    mcp_metadata: MCPMetadata | None = None
