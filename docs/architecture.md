# Architecture

## Overview

opensage-acp sits between an ACP client (acpx) and opensage's web server,
translating between protocols.

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

## Session lifecycle

1. acpx spawns `opensage-acp` as a subprocess.
2. On `session/new`, the adapter allocates a free port, writes a per-session
   `config.toml` (merging any `mcpServers[]` from the ACP request), and spawns
   `opensage web --agent <dir> --config <generated_toml>` on that port.
3. On `session/prompt`, the adapter POSTs to opensage-web's `/run_sse`
   endpoint and streams the SSE response back as ACP `session/update`
   notifications.
4. On `session/cancel`, the adapter POSTs to `/control/stop_turn`.
5. On `session/load`, the adapter restarts opensage-web with `--resume`,
   restoring chat history from the persisted session snapshot.
6. On teardown, the adapter terminates the opensage-web process and cleans up
   the generated config file.

## Key modules

| Module | Responsibility |
|---|---|
| `server.py` | ACP agent protocol implementation, process management |
| `bridge.py` | HTTP/SSE communication with opensage-web |
| `config.py` | Configuration loading (TOML + env vars) |
| `protocol.py` | ACP JSON-RPC message types |
| `cli.py` | CLI entry point |

## Design decisions

Architecture Decision Records are in `docs/adr/`.
