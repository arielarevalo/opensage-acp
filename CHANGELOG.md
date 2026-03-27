# Changelog

All notable changes to opensage-acp are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0](https://github.com/arielarevalo/opensage-acp/compare/v0.1.0...v0.2.0) (2026-03-27)


### Features

* initial release opensage-acp v0.1.0 ([e2fd82a](https://github.com/arielarevalo/opensage-acp/commit/e2fd82ad0cc56e0d4dfe48094f10caea13e0458d))

## [0.1.0] - 2026-03-26

Initial release.

### Added

- **ACP adapter** — bridges JSON-RPC 2.0 on stdin/stdout to HTTP calls against
  `opensage web`, translating ACP sessions into opensage sessions with streaming
  SSE response forwarding.
- **One process per session** — spawns a dedicated `opensage web` instance per
  ACP session on a free localhost port, with automatic process lifecycle
  management.
- **Per-session TOML config** — generates session-specific opensage config files
  at runtime, merging a base template with MCP servers passed dynamically by
  acpx in `session/new`.
- **Session resume** — `session/load` restarts `opensage web --resume` from
  persisted session snapshots, enabling cross-restart resilience.
- **Echo mode** — set `OPENSAGE_ECHO_MODE=1` to run the adapter without opensage
  for protocol testing and CI.
- **ADK event translation** — parses opensage's ADK SSE events (text, function
  calls, errors, stop sentinels) and maps them to ACP `session/update`
  notifications.
- **opensage-web crash detection** — checks process liveness before streaming to
  surface failures early.
- **Example config** — `examples/default_config.toml` with all opensage TOML
  sections documented.
- **Example agent** — `examples/agent/agent.py` minimal echo agent for manual
  testing.
- **CI** — GitHub Actions for linting, type checking, testing, and PyPI
  publishing on release.
- **223 tests** — unit, integration, and end-to-end coverage across all modules.
