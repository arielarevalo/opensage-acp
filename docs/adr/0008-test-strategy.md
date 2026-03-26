# 8. Three-Tier Test Strategy

Date: 2026-03-24

## Status

Accepted

## Context

`opensage-acp` bridges two complex systems: the `acpx` ACP client (JSON-RPC 2.0
over stdio) and `opensage-web` (FastAPI/SSE HTTP server with ADK event format).
Both sides have rich, non-trivial protocols. We need high confidence that the
bridge logic is correct, but we cannot run `opensage-web` in CI because it
requires Docker, Neo4j, GPU-capable LLM backends, and Python packages with
C extensions.

We need a test strategy that:
- Gives fast, reliable feedback on logic bugs without external deps
- Validates the translation layer against realistic opensage-web behaviour
- Keeps CI lightweight and runnable without any infrastructure

Three questions drove the design:
1. How do we test `OpenSageHttpBridge` without a real opensage-web?
2. How do we test `OpenSageACPAgent` without spawning processes?
3. How do we trust our mock server accurately represents real opensage-web?

## Decision

Adopt a **three-tier test strategy** with strict naming conventions and
explicitly different dependency footprints per tier:

### Tier 1 — Unit tests (`test_<module>.py`)

Test a **single class in isolation**. All other classes and all I/O are mocked.

- `test_bridge.py`: `OpenSageHttpBridge` and `_EchoBridge` with `httpx.AsyncClient` replaced by `AsyncMock`.
- `test_server.py`: `OpenSageACPAgent` using `Config(echo_mode=True)` (no subprocess, no HTTP). Edge-case tests inject a mock bridge directly into `agent._sessions[sid].bridge`.
- `test_config.py`: `Config.load()` with `tmp_path`-backed TOML files and monkeypatched env vars.
- `test_protocol.py`: protocol helpers (`read_message`, `make_request`, etc.) with `io.StringIO` streams.
- `test_cli.py`: `_setup_logging` and `main()` with patched `Config.load` and `serve`.

No network. No subprocesses. No filesystem side-effects beyond `tmp_path`.

### Tier 2 — Integration tests (`test_integration_<flow>.py`)

Test a **single flow across multiple real classes**. The only mock is the
external HTTP server, replaced by `MockOpensageServer` (a FastAPI app in a
background thread that faithfully replicates the opensage-web HTTP API and SSE
event format).

- `test_e2e_integration.py` (existing): bridge → mock server, agent → mock server.
- Future: `test_integration_cancel.py`, `test_integration_errors.py`, etc.

No real `opensage-web`. No subprocess spawning (agent's `_spawn_opensage_web`
is patched out; the mock server port is pre-allocated). No LLM backends.

### Tier 3 — End-to-end tests (`test_e2e_<scenario>.py`)

Test the **complete application** with no mocks. Spawns the real binary, drives
it via the ACP client SDK, requires a real `opensage-web` instance running with
a real agent directory. **Not run in CI.** Gated by `OPENSAGE_AGENT_DIR` env
var (test is skipped if not set).

- `test_e2e.py` (existing): binary launched with `OPENSAGE_ECHO_MODE=1` —
  does not require a real opensage-web but validates the binary + ACP protocol
  end-to-end.

## Consequences

**Positive:**
- CI runs tiers 1 and 2 with no external deps: fast and hermetic.
- Mock server fidelity is the single most important correctness lever — if it
  matches real opensage-web, tier 2 gives very high confidence.
- Each tier has a clear scope; bugs found in tier 1 are cheaper to diagnose.
- The `_EchoBridge` lets tier 1 tests of `OpenSageACPAgent` verify full ACP
  protocol semantics without HTTP.

**Negative:**
- **Mock server fidelity is critical.** If `MockOpensageServer` diverges from
  real opensage-web behaviour (different SSE event shapes, different status
  codes, missing fields), tier 2 tests give false confidence.
- We must maintain a `tests/fixtures/` directory with real ADK event JSON
  samples captured from a live opensage-web. These are the ground truth for
  the mock server's event format.
- Tier 3 tests are manual; there is no automated smoke test of the full
  opensage stack.

**Mock server fidelity contract:**
- SSE events must include `content.role`, `content.parts`, and (once added)
  `author` and `partial` fields matching real ADK event shapes.
- `stop_turn` and `turn_state` must accept and validate the `session_id` query
  parameter (opensage-web requires this).
- Error scenarios (`{"error": "..."}` events, 500 responses) must be
  exercisable via constructor flags on `MockOpensageServer`.
