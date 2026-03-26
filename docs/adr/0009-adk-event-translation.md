# 9. ADK Event → ACP Content Block Translation

Date: 2026-03-24

## Status

Accepted

## Context

`opensage-web` emits a stream of ADK `Event` objects via SSE on `POST /run_sse`.
These are Pydantic model dumps of Google ADK's `Event` class. The relevant
fields (as observed from opensage source and live captures) are:

```json
{
  "content": {
    "role": "model",
    "parts": [
      {"text": "chunk of output text"},
      {"function_call": {"name": "create_subagent", "args": {...}}},
      {"function_response": {"name": "create_subagent", "response": {...}}}
    ]
  },
  "author": "root_agent",
  "partial": true,
  "error_code": null,
  "error_message": null,
  "stopped": false
}
```

ACP clients (acpx / OpenClaw) expect content blocks:
- `TextContentBlock`: `{"type": "text", "text": "..."}`
- `ToolUseContentBlock`: `{"type": "tool_use", "name": "...", "input": {...}}`

The two data models do not map 1:1. Key differences:
- ADK events are **partial** (streaming fragments) or **final** (complete turn).
  ACP `session/update` sends chunks as they arrive; the final `PromptResponse`
  carries the `stop_reason`.
- ADK events include **function calls** (agent tools like `create_subagent`,
  `run_code`). These have no direct ACP equivalent today.
- ADK events can carry **error information** via `error_code` / `error_message`
  fields, or as top-level `{"error": "..."}` objects. The current
  `_extract_text_from_event` silently ignores these.
- ADK events for **sub-agent invocations** carry author metadata that could be
  useful for OpenClaw routing but are not part of the ACP spec.

## Decision

Implement translation in `bridge.py`'s `_extract_text_from_event` and
`run_sse`, following these rules:

1. **Text extraction**: Collect `part.text` from `content.parts[]`. Ignore
   parts without a `text` key. Join all text parts and yield as a single
   string. Do not yield empty strings.

2. **Function calls**: Skip for now (Phase 1). Log at DEBUG level so callers
   can see them in traces. Phase 2 will map them to ACP `ToolUseContentBlock`
   when the ACP schema supports it.

3. **Error events**: Detect `{"error": "..."}` top-level events and
   `event.error_message` (non-null). Raise a `RuntimeError` from `run_sse` so
   `server.py`'s `prompt()` can wrap it in a `RequestError(-32603, ...)` and
   return it to the ACP client. **Never silently swallow error events.**

4. **Stop sentinel**: `{"stopped": true}` terminates the stream — break out of
   `run_sse`. This is the current behaviour and is correct.

5. **Partial vs final**: Both partial and final events yield text if present.
   The `partial` flag is not forwarded to ACP (ACP always treats
   `session/update` as incremental); it is logged at DEBUG level only.

6. **Author / sub-agent events**: Ignored (no ACP equivalent). Logged at DEBUG.

## Consequences

**Positive:**
- Error events surface to ACP clients as `RequestError` instead of silent gaps
  in the response. This is the correct user-facing behaviour.
- Simple, predictable mapping — easy to unit test with fixture JSON files.
- Phase 2 function-call mapping is additive (no existing behaviour changes).

**Negative:**
- We lose sub-agent topology visibility: OpenClaw cannot currently see which
  sub-agent is responding. This is acceptable for Phase 1 (text responses only).
- Function call events during a turn cause a gap in the streamed text — the
  user sees only the final text response, not the tool-use steps in between.
  This may feel like latency.
- The translation layer is the **most likely place for bugs** because the ADK
  event schema is not formally versioned and may change across opensage releases.

**Testing obligation (per ADR-0008):**
- Maintain `tests/fixtures/adk_events/` with JSON files of real ADK events
  captured from a live opensage-web session. These are the source of truth for
  unit tests of `_extract_text_from_event`.
- Each new event type observed in production must have a fixture and a unit
  test before the translation code for it is merged.

**Potential OpenSage Contribution:** A versioned ADK event schema (JSON Schema
or Pydantic export) published alongside opensage releases would allow the
adapter to validate incoming events and detect breaking changes automatically.
