# Configuration

## Adapter config file

Create `~/.config/opensage-acp/config.toml` (or set `OPENSAGE_CONFIG_FILE`):

```toml
[opensage-acp]
agent_dir = "/path/to/your/opensage/agent"
opensage_config_template = "/path/to/your/opensage/config.toml"
echo_mode = false
```

## Environment variables

All config values can be overridden via environment variables (take precedence
over the TOML file):

| Variable | Description |
|---|---|
| `OPENSAGE_AGENT_DIR` | Path to the opensage agent directory (required) |
| `OPENSAGE_CONFIG_TEMPLATE` | Path to the base opensage `config.toml` (required for real usage) |
| `OPENSAGE_ECHO_MODE` | Set to `1` to skip opensage and echo prompts back (for testing) |
| `OPENSAGE_CONFIG_FILE` | Override path to the opensage-acp adapter config TOML |
| `OPENSAGE_PORT_RANGE_START` | First port for opensage-web instances (default: `8100`) |
| `OPENSAGE_TIMEOUT` | HTTP timeout in seconds for bridge calls (default: `120`) |
| `OPENSAGE_LOG_LEVEL` | Logging level: `DEBUG`, `INFO`, `WARNING` (default: `WARNING`) |

## opensage config.toml

The opensage `config.toml` is the agent's own configuration — LLM backend,
API keys, Neo4j connection, MCP servers. A fully-documented template is
provided at `examples/default_config.toml`.

The adapter deep-merges session-specific overrides on top of this file at
runtime. Fields it manages automatically:

- `agent_storage_path` — scoped per session to avoid collisions
- `[mcp.services.*]` — injected from the `mcpServers[]` in `session/new`

MCP servers configured in the template are available to all sessions; MCP
servers passed dynamically by acpx in `session/new` are merged in at session
creation time.
