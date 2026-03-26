# opensage-acp

ACP adapter that exposes [opensage](https://github.com/opensage-agent/opensage)
as an external coding agent reachable via
[acpx](https://github.com/openclaw/acpx).

## What it does

opensage-acp bridges JSON-RPC 2.0 messages from acpx on stdin/stdout to HTTP
calls against a locally-managed `opensage web` server. It translates ACP
sessions into opensage sessions, forwards MCP server configs, and streams
SSE responses back as ACP `session/update` notifications.

```
acpx (or any ACP client)
  |  JSON-RPC 2.0 / NDJSON on stdin/stdout
  v
opensage-acp  (this adapter)
  |  HTTP + SSE to localhost:<port>
  v
opensage web  (one process per ACP session)
  |  ADK Runner + OpenSageSession
  v
Docker sandboxes, Neo4j (optional), LLM backends (via LiteLLM)
```

## Quick start

```bash
pip install opensage-acp
```

Verify the adapter works (no opensage needed):

```bash
OPENSAGE_ECHO_MODE=1 acpx --agent opensage-acp exec "hello"
```

See [Getting Started](getting-started.md) for full setup instructions.
