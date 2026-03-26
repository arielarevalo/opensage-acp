# 2. One opensage-web Instance Per ACP Session

Date: 2026-03-24

## Status

Accepted

## Context

ACP clients (e.g. acpx) may create multiple concurrent sessions against the
adapter. Each session is independent and expects isolated state.

`opensage web` is **single-session by design**: at startup it generates (or
accepts) a `fixed_session_id` UUID and all requests to that server instance
share that session. While `OpenSageSessionRegistry` internally supports
multiple session objects, the web server's routing layer ignores any
session identifier passed by the client and always routes to the one fixed
session created at startup.

This means a single `opensage web` instance cannot serve multiple independent
ACP sessions.

## Decision

Run **one `opensage web` process per ACP session**. When the adapter receives
`session/new`, it allocates a free port, spawns a fresh `opensage web` process
on that port, and maps the ACP session ID to that process+port. The adapter
maintains a process pool keyed by ACP session ID. On `session/cancel` or
session teardown, the corresponding process is terminated.

## Consequences

**Positive:**
- Perfect session isolation: each session has its own opensage process, its own
  in-memory state, its own Docker containers, and its own ADK session.
- No shared mutable state between sessions (except Neo4j, which is intentional
  — see ADR-0005).
- Naturally resolves the MCP configuration problem (see ADR-0003): each process
  gets its own generated TOML.
- Clean mapping: ACP session lifecycle = opensage-web process lifecycle.

**Negative:**
- Port allocation complexity: the adapter needs a port manager (scan for free
  ports, avoid conflicts with other services).
- Process lifecycle management overhead: spawn, health-check, monitor, and
  reap child processes.
- Resource cost: each opensage-web instance loads the full FastAPI stack,
  LiteLLM, ADK, and any MCP tools. Memory and startup time scale linearly
  with concurrent sessions.

**Potential OpenSage Contribution:** Multi-session support in `opensage web` —
accept a `session_id` parameter in API calls rather than fixing it at startup.
This would allow a single server to handle multiple independent sessions,
eliminating the process-per-session overhead.
