# Event schema

Every agentview trace file is a stream of newline-delimited JSON envelopes. The
Pydantic models in `src/agentview/events.py` are the authoritative source of
truth. This document explains the compatibility rules and the optional MCP
metadata fields added for v1.

## Envelope shape

```json
{
  "schema_version": "0.1",
  "event_type": "run_start | span_end | run_end",
  "event_id": "evt_...",
  "run_id": "run_...",
  "recorded_at": "2026-10-02T18:00:00Z",
  "payload": { "...": "shape depends on event_type" },
  "mcp_metadata": null
}
```

- `schema_version` is a semantic hint, not a hard contract.
- `payload` is validated by one of `RunStartPayload`, `SpanEndPayload`, or
  `RunEndPayload`, selected by `event_type`.
- `mcp_metadata` is optional. Present when the event came from
  `agentview proxy`; absent when it came from the SDK decorator path.

## Option A compatibility rule (locked in v1.5)

The schema is designed to evolve without breaking existing consumers:

- **Schema version stays at `0.1` for the entire v1 line.**
- **New optional fields may be added within `0.1`.** Consumers written against
  earlier `0.1` traces must still parse newer traces.
- **Consumers MUST ignore unknown fields.** Every Pydantic model in `events.py`
  sets `model_config = ConfigDict(extra="ignore")` to enforce this on the
  reader side.
- A minor version bump to `0.2` is reserved for forward-incompatible changes
  (removing a field, changing a type, renaming). None are planned for v1.

If you build an external consumer, mirror this rule: parse what you recognise,
skip what you do not, do not fail on unknown keys.

## MCP metadata (optional, added in v1)

The `mcp_metadata` block attaches MCP protocol context to any envelope. All
fields are optional. The block is populated by `agentview proxy` when wrapping
a real MCP server; the SDK decorator path leaves it as `null`.

| Field               | Type                                            | Notes                                                |
| ------------------- | ----------------------------------------------- | ---------------------------------------------------- |
| `protocol`          | string, usually `"mcp"`                         | Future protocols reuse the same block.               |
| `transport`         | string, usually `"stdio"`                       | HTTP transport reserved for post-v1.                 |
| `session_id`        | string                                          | MCP session identifier the client assigned.          |
| `request_id`        | integer or string                               | JSON-RPC `id` field from the request.                |
| `message_direction` | `"client_to_server"` or `"server_to_client"`    | Direction the proxy observed.                        |
| `method`            | string                                          | JSON-RPC method name (e.g. `tools/call`).            |
| `tool_name`         | string                                          | For `tools/call`, the tool argument.                 |

### Why one unified schema, not two

An earlier draft proposed a separate MCP schema. We rejected it to keep the
JSONL spine unified: one event model, one reader, one dashboard. MCP captures
and SDK captures are visually and structurally the same event with different
provenance. The optional block is the whole seam.

## Reader contract

Any tool that reads an agentview JSONL file should:

1. Parse one line at a time; a malformed line should not stop the stream.
2. Validate against `EventEnvelope`. Unknown fields are silently ignored.
3. If `mcp_metadata` is present, surface it; if absent, do not synthesise it.
4. Treat `schema_version != "0.1"` as a signal to display a warning and
   continue in best-effort mode, not to abort.
