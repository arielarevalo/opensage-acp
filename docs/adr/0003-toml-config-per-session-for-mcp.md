# 3. Generate Per-Session TOML Config for MCP Passthrough

Date: 2026-03-24

## Status

Accepted

## Context

ACP clients (acpx) pass MCP server configurations dynamically in `session/new`
as a `mcpServers[]` array. Each entry specifies a name, command, args, and
environment variables for an MCP server the agent should have access to.

OpenSage configures MCP servers via static TOML at startup:

```toml
[mcp.services.my_tool]
command = "npx"
args = ["-y", "@my/mcp-tool"]
env = { MY_TOKEN = "..." }
```

This configuration is read once when `opensage web` starts and cannot be
changed at runtime. There is no API to register or deregister MCP servers
while the server is running.

Because we run one opensage-web instance per ACP session (ADR-0002), each
instance has its own config file.

## Decision

At `session/new` time, **generate a per-session TOML config file** that
includes the MCP server entries from the ACP `mcpServers[]` payload.
The file is written to a temp directory before the opensage-web process is
spawned. The process is started with `--config <path_to_generated_toml>`.

On session teardown, the generated TOML is deleted.

## Consequences

**Positive:**
- MCP servers requested by acpx are faithfully passed to opensage at session
  startup.
- Each session can have a different set of MCP tools without any cross-session
  interference.
- Implementation is straightforward: serialize `mcpServers[]` to TOML and
  write to a temp file.

**Negative:**
- TOML template orchestration: the adapter must maintain a base config
  template and merge in per-session MCP entries.
- MCP servers **cannot be added or removed mid-session** — doing so would
  require restarting the opensage-web process, which loses all in-memory
  session state.
- Secrets in MCP env vars are written to disk (temp files) and must be
  cleaned up on session teardown.

**Potential OpenSage Contribution:** A runtime MCP server registration API
(e.g. `POST /mcp/servers`) that allows adding and removing MCP servers without
restarting opensage. This would allow dynamic MCP server management mid-session
and eliminate the need for per-session TOML generation.
