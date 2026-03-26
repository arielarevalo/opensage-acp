# 1. HTTP Bridge to opensage web

Date: 2026-03-24

## Status

Accepted

## Context

We need opensage-acp to drive opensage task execution on behalf of ACP clients.
Two approaches were considered:

1. **Direct Python import** — import opensage internals, instantiate
   `OpenSageAgent`, wire up an ADK `Runner` and `OpenSageInMemorySessionService`
   in-process.
2. **HTTP bridge** — launch `opensage web` as a subprocess and talk to its
   FastAPI/uvicorn HTTP API from the adapter process.

Initial implementation used the direct-import approach (via the now-dead
`ianblenke/sageagent` path, and then against `opensage-agent/opensage`).
Research into opensage's actual architecture revealed the following: opensage
exposes a fully-featured REST+SSE API (`POST /run_sse`, `POST /run`,
`POST /control/stop_turn`, `GET /control/turn_state`) covering the entire
prompt/stream/cancel lifecycle. There is **no `opensage run` CLI command** for
headless execution. The only stable interface opensage provides to external
consumers is `opensage web`.

Importing opensage internals required understanding and coupling to ADK Runner,
`OpenSageSessionRegistry`, `OpenSageSession`, and `DynamicAgentManager` — all
of which are internal implementation details that are not stable and not
designed for external consumption.

## Decision

Use the **HTTP bridge pattern**: opensage-acp launches `opensage web` as a
subprocess and communicates with it via HTTP/SSE on localhost. The adapter is
a thin translation layer between ACP JSON-RPC (on stdio) and the opensage HTTP
API (on a local port).

## Consequences

**Positive:**
- Stable API surface — we depend on opensage's documented HTTP interface, not
  its internals.
- Opensage upgrades do not break the adapter as long as the HTTP API is stable.
- Clean process boundary makes debugging easier (can curl the opensage server
  independently).
- Streaming via SSE maps naturally to ACP `session/update` notifications.

**Negative:**
- Extra process to manage per session: the adapter must spawn, monitor, and
  terminate `opensage web` processes.
- Added latency from the HTTP hop vs in-process calls.
- Port allocation and process lifecycle management add complexity to the adapter.

**Potential OpenSage Contribution:** An `opensage run <agent_dir> --prompt "..."` CLI
command for headless one-shot execution would allow simpler integration for
tools that don't need a persistent server. This would eliminate the process
management overhead.
