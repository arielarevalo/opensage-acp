# acpx Protocol Reference

**Source**: https://github.com/openclaw/acpx
**Researched**: 2026-03-24
**Status**: Alpha (interfaces likely to change)

---

## Transport

JSON-RPC 2.0 over child-process stdio (NDJSON).  Each message is one
JSON line (`\n`-terminated).  The adapter process is spawned with
`stdio: ["pipe","pipe","pipe"]`.  All protocol traffic goes on
stdin/stdout; the adapter MAY write human-readable diagnostics to
stderr.

---

## Message Shapes

### Request (client → adapter)
```json
{
  "jsonrpc": "2.0",
  "id": "<string|number|null>",
  "method": "<string>",
  "params": { ... }
}
```

### Notification (adapter → client, no `id`)
```json
{
  "jsonrpc": "2.0",
  "method": "<string>",
  "params": { ... }
}
```

### Response (adapter → client, reply to a Request)
```json
{
  "jsonrpc": "2.0",
  "id": "<same as request>",
  "result": { ... }
}
```
or on error:
```json
{
  "jsonrpc": "2.0",
  "id": "<same as request>",
  "error": { "code": <int>, "message": "<string>" }
}
```
`result` and `error` are mutually exclusive.

---

## Methods

### `initialize` (required — first call)

Client → adapter.  Capability negotiation.

**Params:**
```json
{
  "protocolVersion": <number>,
  "clientCapabilities": {
    "fs": { "readTextFile": true, "writeTextFile": true },
    "terminal": true
  },
  "clientInfo": { "name": "acpx", "version": "0.1.0" }
}
```

**Result:**
```json
{
  "protocolVersion": <number>,
  "agentCapabilities": {
    "loadSession": <bool>
  },
  "authMethods": [ ... ]
}
```
`agentCapabilities` and `authMethods` are optional.

---

### `session/new`

Client → adapter.  Create a new session.

**Params:**
```json
{
  "cwd": "/absolute/path",
  "mcpServers": [],
  "_meta": { ... }
}
```
`cwd` MUST be a non-null string.  Passing `null` or a non-string
produces error `-32602` or `-32600`.

**Result:**
```json
{
  "sessionId": "<non-empty string>",
  "_meta": { "runtimeSessionId": "..." }
}
```

---

### `session/prompt`

Client → adapter.  Send a prompt turn.

**Params:**
```json
{
  "sessionId": "<string>",
  "prompt": [ <ContentBlock>, ... ]
}
```

**ContentBlock types:**
```json
{ "type": "text", "text": "string" }
{ "type": "image", "mimeType": "image/png", "data": "<base64>" }
{ "type": "resource_link", "uri": "file:///...", "title": "...", "name": "..." }
{ "type": "resource", "resource": { "uri": "file:///...", "text": "..." } }
```

A plain-text prompt is encoded as `[{"type":"text","text":"..."}]`.
Input starting with `[` is treated as a structured JSON block array.

**Result:**
```json
{ "stopReason": "end_turn" | "completed" | "done" | "cancelled" | "failed" }
```
Accepted successful stop reasons: `end_turn`, `completed`, `done`.
After cancel: `cancelled`.

**IMPORTANT**: During execution the adapter emits `session/update`
notifications (see below).  The `session/prompt` response arrives only
after the turn is complete.

---

### `session/update` (notification, adapter → client)

Emitted by the adapter during an active `session/prompt` turn.  No `id`.
At least 1 update MUST be emitted for non-empty prompts before the final
response.

**Params:**
```json
{
  "sessionUpdate": "agent_message_chunk",
  "content": { "type": "text", "text": "..." }
}
```

---

### `session/cancel`

Client → adapter.  Cancel the in-flight prompt.

**Params:**
```json
{ "sessionId": "<string>" }
```

**Result:** any (adapter-defined).

After cancel:
- The active turn's `session/prompt` response MUST have `stopReason: "cancelled"`.
- Cancel on an idle session MUST succeed gracefully.
- The session MUST remain usable after cancel — a follow-up `session/prompt`
  MUST succeed.

---

### `session/load` (optional — requires `loadSession` capability)

Client → adapter.  Reconnect to a saved session.

**Params:**
```json
{ "sessionId": "<previously-issued sessionId>" }
```

**Result:** Same shape as `session/new`.

Replay `session/update` notifications during load are typically suppressed
by the client to avoid re-emitting already-seen content.

---

### Adapter-initiated methods (adapter → client requests)

| Method | Purpose |
|---|---|
| `fs/read_text_file` | Agent asks client to read a file |
| `fs/write_text_file` | Agent asks client to write a file |
| `terminal/create` | Agent asks client to spawn a terminal |
| `terminal/output` | Get terminal output |
| `terminal/wait_for_exit` | Wait for process exit |
| `terminal/kill` | Kill terminal process |
| `terminal/release` | Release terminal handle |
| `request_permission` | Agent requests permission for an operation |
| `authenticate` | Auth handshake |

---

## Session Persistence

acpx stores session records as JSON files at `~/.acpx/sessions/*.json`
(schema `acpx.session.v1`).  Key fields:

```
acpxRecordId       local UUID
acpSessionId       ACP session id (from session/new)
agentSessionId     adapter-native session id (from _meta)
agentCommand       command string used to spawn the adapter
cwd                working directory
name               optional session name (-s flag)
messages           conversation history
eventLog           NDJSON segment rotation config
closed             bool
```

The raw ACP NDJSON stream is also stored alongside the JSON record as an
event log with segment rotation.  Segments: `<id>.ndjson`,
`<id>.1.ndjson`, …

Scope key for auto-routing: `(agentCommand, absoluteCwd, name?)`.

---

## Error Codes

Standard JSON-RPC codes:
| Code | Meaning |
|---|---|
| -32700 | Parse error |
| -32600 | Invalid request |
| -32601 | Method not found |
| -32602 | Invalid params |
| -32603 | Internal error |

---

## Exit Codes (acpx client)

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Agent/protocol/runtime error |
| 2 | CLI usage error |
| 3 | Timeout |
| 4 | No session found |
| 5 | Permission denied |
| 130 | SIGINT/SIGTERM |

---

## Quirks and Gotchas

1. **Prompt text starting with `[`** is parsed as a JSON ContentBlock
   array.  If it parses but is not a valid `ContentBlock[]`, a
   `PromptInputValidationError` is thrown.  If it fails JSON parse, it
   falls back to treating the whole string as plain text.

2. **`initialize` is always the first call**.  No other method may be
   sent before `initialize` completes.

3. **`session/update` is a notification** (no `id`).  It must be handled
   outside the request/response queue.

4. **Interleaving**: The client may receive `session/update` notifications
   from an in-flight `session/prompt` while also receiving unrelated
   responses.  The client matches responses by `id`.

5. **Cancel→reuse**: After `session/cancel`, the session MUST remain
   usable (conformance case 020).

6. **`session/load` fallback**: If `session/load` fails (e.g. adapter
   doesn't support `loadSession`), the client falls back to `session/new`.

7. **`runtimeSessionId`**: Adapters may return an adapter-native session
   id in `result._meta.runtimeSessionId` from `session/new` /
   `session/load`.

8. **Method-not-found for optional methods**: `-32601`, `-32602`, or
   `-32603` with "invalid params" in the detail string are all treated as
   "adapter doesn't support this optional method".

9. **Conformance profile v1** defines 20 required test cases (cases
   001–020).  These cover: initialize, session/new validation, single-turn
   prompt, multi-turn, cancel, session/load (if capability advertised).

---

## Conformance Test Cases (summary)

| Case | Description |
|---|---|
| 001 | initialize succeeds |
| 002 | initialize — unknown capability ignored |
| 003 | session/new succeeds, returns non-empty sessionId |
| 004 | session/new — null cwd produces error |
| 005 | session/new — non-string cwd produces error |
| 006–010 | session/prompt — basic, multi-turn, content blocks |
| 011–015 | session/prompt — streaming updates, stopReasons |
| 016–018 | session/cancel |
| 019 | session/load (if loadSession capability) |
| 020 | cancel then reuse session |

---

## opensage-acp Implementation Notes

`opensage-acp` must:

1. Respond to `initialize` with a valid result immediately (even before
   connecting to OpenSage).
2. On `session/new`, create an OpenSage session and return the OpenSage
   session id as the `sessionId`.
3. On `session/prompt`, call OpenSage's agent API and stream back
   `session/update` notifications, then respond with
   `{ "stopReason": "end_turn" }` when done.
4. On `session/cancel`, call OpenSage's cancel API and respond with
   `{ "stopReason": "cancelled" }` from the pending `session/prompt`.
5. Optionally support `session/load` and advertise `loadSession` in
   `agentCapabilities`.
6. Handle adapter-initiated `fs/*`, `terminal/*`, `request_permission` by
   delegating to acpx's default handlers or returning appropriate errors.
