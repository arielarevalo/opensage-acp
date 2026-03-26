# 11. Per-Session TOML Config Orchestration

Date: 2026-03-24

## Status

Accepted

## Context

OpenSage configures its agents, LLM backends, MCP servers, sandbox settings, and Neo4j connections via a static TOML config file loaded at startup (`opensage web --config config.toml`). This config cannot be changed at runtime.

The ACP protocol is dynamic — acpx sends `mcpServers[]` in `session/new`, and different sessions may need different agent configurations, LLM backends, or sandbox setups. Per ADR-0002 (one opensage-web instance per session) and ADR-0003 (per-session TOML for MCP passthrough), each session gets its own opensage-web process with its own config.

The adapter must therefore:
1. Accept a base TOML config template from the user
2. At session creation time, generate a per-session TOML by merging the base template with session-specific overrides (MCP servers from acpx, port assignments, session-scoped paths)
3. Pass the generated TOML to `opensage web --config`
4. Clean up generated configs when sessions end

## Decision

The adapter maintains a base config template path (`OPENSAGE_CONFIG_TEMPLATE` env var or `config_template` in adapter config). On `new_session`:

1. Read the base template (or use empty defaults if none provided)
2. Deep-merge session-specific overrides:
   - `[mcp.services.<name>]` sections for each MCP server from acpx's `mcpServers[]`
   - Port-specific sandbox config if needed to avoid conflicts
   - `agent_storage_path` scoped to the session to isolate dynamic agent metadata
3. Write the merged config to a temp directory (`/tmp/opensage-acp-<session_id[:8]>/config.toml`)
4. Pass this path to `opensage web --config`
5. On session cleanup, remove the temp directory

The base template is the user's responsibility — it contains their LLM API keys, Neo4j connection, default tools, agent instructions. The adapter only adds/overrides session-specific sections.

## Consequences

**Positive:**
- Users configure opensage once via their familiar TOML format
- MCP servers from acpx are transparently injected
- Each session is isolated (own config, own process)
- Base template can be version-controlled separately

**Negative:**
- TOML generation logic must handle edge cases (malformed base template, conflicting section names, special characters in MCP server URLs)
- Temp files accumulate if cleanup fails (unclean shutdown)
- No runtime config changes — if acpx adds an MCP server mid-session, it requires a session restart
- The adapter must understand opensage's TOML schema well enough to merge correctly

**Potential OpenSage contribution:**
- Runtime config reload endpoint (`POST /config/reload`) to avoid session restarts for config changes
- Programmatic MCP server registration API (add/remove without TOML)
