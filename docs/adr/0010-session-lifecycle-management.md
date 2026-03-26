# 10. Session Lifecycle Management via ProcessManager

Date: 2026-03-24

## Status

Accepted

## Context

Each ACP session requires its own `opensage-web` process (per ADR-0002). The
adapter must manage these processes across the full session lifetime:

1. **Spawn**: Start `opensage-web` on a free port when `session/new` arrives.
2. **Health-check**: Poll `GET /` until the server is responding before creating
   the ADK session and returning the `session_id` to the client.
3. **Monitor**: Detect if the process dies mid-session (crash or OOM).
4. **Cancel**: Terminate the process when the ACP session ends.
5. **Resume**: Restart the process with `--resume` when `session/load` arrives
   after an adapter restart (per ADR-0004).

The current implementation in `server.py` handles spawn, health-check, and
termination inline in `OpenSageACPAgent`. As Phase 1 adds resume and crash
detection, this logic will grow and needs a clear structure.

Additional constraints:
- **Port conflicts**: If two adapter instances run on the same host, they may
  compete for the same port range. Port allocation must be resilient.
- **Orphaned processes**: If the adapter crashes without running `shutdown_all`,
  `opensage-web` children are left running. On restart, new instances will be
  spawned on different ports; old ones are orphaned until the OS reaps them.
- **acpx queue owner TTL**: acpx disconnects and reconnects sessions when a
  queue owner lease expires. The adapter must accept `session/load` and
  reattach to an already-running process (or restart it with `--resume`).

## Decision

The lifecycle logic lives in `OpenSageACPAgent` (current) and will grow into
a `ProcessManager` helper class (Phase 2). The agreed behaviour per phase:

### Phase 1 (current)
- `_alloc_port()`: increment `_next_port` counter from `port_range_start`. No
  OS probe — simple sequential allocation from a configurable base port.
- `_spawn_opensage_web()`: `subprocess.Popen` with `stdout=PIPE`, `stderr=PIPE`.
  MCP server injection is a Phase 2 item; log a warning if `mcp_servers` is
  non-empty.
- `_wait_healthy()`: poll `bridge.health_check()` every `_HEALTH_POLL` seconds
  up to `_HEALTH_TIMEOUT` seconds. Raise `RuntimeError` on timeout. This error
  propagates through `new_session()` back to the ACP client as a `RequestError`.
- `shutdown_all()`: call `bridge.aclose()` + `process.terminate()` +
  `process.wait(timeout=5)` for each session. Suppress all exceptions.
  Clear `_sessions` and `_cancelled`.
- `session/load` (Phase 1 stub): return `LoadSessionResponse` only if the
  session is still in `_sessions` (i.e., the process is still alive in memory).
  Cross-restart resume is deferred.

### Phase 2 (planned)
- Wrap spawn/health/terminate logic into a `ProcessManager` class.
- `session/load` (Phase 2): if `session_id` not in `_sessions`, check for a
  persisted opensage session dir at `~/.local/opensage/sessions/<id>/`. If it
  exists, restart opensage-web with `--resume <id>` on a fresh port. Wait for
  healthy. Reconnect bridge. Re-register in `_sessions`.
- Crash detection: a background task periodically polls `process.poll()`. If
  a process dies, mark the session as errored; any subsequent `prompt()` for
  that session returns `RequestError(-32603, "opensage-web process died")`.
- Port allocation: probe the OS with `socket.bind(("", 0))` before committing
  to a port, avoiding conflicts with other services on the same host.

## Consequences

**Positive:**
- Clear phase boundary: Phase 1 keeps it simple; Phase 2 adds resilience.
- `shutdown_all()` provides a clean teardown hook called from `serve()`.
- The health-check loop prevents the adapter from returning a `session_id` to
  the ACP client before opensage-web is actually ready.

**Negative:**
- **Orphaned processes** on unclean shutdown (adapter crash). No mitigation in
  Phase 1. Phase 2 should write a PID file per session to detect and clean up
  orphans on restart.
- **Port conflicts** with other services are possible with sequential
  allocation. Mitigated in Phase 2 by OS-probe allocation.
- **Startup latency**: each `session/new` blocks until opensage-web passes its
  health check. With a heavy agent, this could be 5–30 seconds. The
  `_HEALTH_TIMEOUT` (30 s default) must be configurable.
- **Resource leak on spawn failure**: if `_wait_healthy` times out, the
  partially-started process is not explicitly terminated. Phase 1 fix: if
  `_wait_healthy` raises, call `process.terminate()` before propagating.

**Potential OpenSage Contribution:** A `GET /ready` endpoint that returns 200
only after the ADK runner is fully initialised (not just FastAPI up) would make
health-checking more precise and reduce the risk of `create_session` calls
arriving before opensage is ready.
