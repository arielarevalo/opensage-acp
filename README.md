# opensage-acp

`opensage-acp` is an [Agent Client Protocol (ACP)](https://github.com/openclaw/acpx)
adapter that exposes [opensage](https://github.com/opensage-agent/opensage) as
an external coding agent reachable via
[acpx](https://github.com/openclaw/acpx). It bridges the JSON-RPC 2.0 messages
acpx sends on stdin/stdout to HTTP calls against a locally-managed `opensage
web` server, translating ACP sessions into opensage sessions, forwarding MCP
server configs, and streaming opensage's SSE responses back as ACP
`session/update` notifications.

## Architecture

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

acpx spawns `opensage-acp` as a subprocess and speaks JSON-RPC 2.0 over
NDJSON on its stdin/stdout. For each ACP session, the adapter spawns a
dedicated `opensage web` process on a free localhost port, generates a
per-session TOML config (including any MCP servers the client requested),
and proxies all prompts and responses.

## Prerequisites

- **Python >= 3.12**
- **Docker** — required by opensage for its sandbox backend
- **opensage** — installed from source (not yet on PyPI):
  ```bash
  pip install git+https://github.com/opensage-agent/opensage.git
  ```
- **acpx >= 0.3** — the ACP client that spawns this adapter (Node.js >= 22):
  ```bash
  npm install -g acpx@latest
  ```
- **Neo4j** (optional) — for persistent memory across sessions

## Installation

```bash
pip install opensage-acp
```

Or, from source:

```bash
pip install git+https://github.com/arielarevalo/opensage-acp.git
```

For local development:

```bash
pip install -e ".[dev]"
```

This installs the `opensage-acp` binary on your PATH.

> **Note:** opensage itself is not on PyPI and must be installed separately
> (see Prerequisites above). The adapter communicates with `opensage web` via
> HTTP — it does not import opensage as a Python library.

## Quick Start

### 1. Verify the adapter works (no opensage needed)

```bash
OPENSAGE_ECHO_MODE=1 acpx --agent opensage-acp exec "hello"
```

This runs the adapter in echo mode — it echoes back whatever you send. If you
see `hello` echoed back with `[done] end_turn`, the ACP protocol layer works.

### 2. Run with a real opensage agent

```bash
# Prepare a config (see examples/default_config.toml)
cp examples/default_config.toml /tmp/my_config.toml
# Edit: set your LLM model and API key env vars

# Run
OPENSAGE_AGENT_DIR=/path/to/your/agent \
OPENSAGE_CONFIG_TEMPLATE=/tmp/my_config.toml \
acpx --agent opensage-acp --verbose exec "hello, what can you do?"
```

The adapter spawns `opensage web` on a free port, auto-discovers the app name,
creates a session, and streams the response back through acpx.

## Configuration

### Config file

Create `~/.config/opensage-acp/config.toml` (or set `OPENSAGE_CONFIG_FILE` to
an alternate path):

```toml
[opensage-acp]
agent_dir = "/path/to/your/opensage/agent"
opensage_config_template = "/path/to/your/opensage/config.toml"
echo_mode = false
```

### Environment variables

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

### opensage config.toml

The opensage `config.toml` is the agent's own configuration — LLM backend,
API keys, Neo4j connection, MCP servers. A fully-documented template is
provided at [`examples/default_config.toml`](examples/default_config.toml).
Copy it, fill in the placeholders, and point the adapter at it:

```bash
OPENSAGE_CONFIG_TEMPLATE=/path/to/your/config.toml opensage-acp
```

The adapter deep-merges session-specific overrides on top of this file at
runtime. Fields it manages automatically:
- `agent_storage_path` — scoped per session to avoid collisions
- `[mcp.services.*]` — injected from the `mcpServers[]` in `session/new`

MCP servers configured in the template are available to all sessions; MCP
servers passed dynamically by acpx in `session/new` are merged in at session
creation time.

See the [opensage documentation](https://github.com/opensage-agent/opensage)
for details on all config fields.

## Usage

### Register with acpx

Add `opensage-acp` as an agent in your acpx configuration:

```json
{
  "agents": {
    "opensage": {
      "command": "opensage-acp",
      "env": {
        "OPENSAGE_AGENT_DIR": "/path/to/agent",
        "OPENSAGE_CONFIG_TEMPLATE": "/path/to/config.toml"
      }
    }
  }
}
```

### Run directly (for debugging)

```bash
OPENSAGE_AGENT_DIR=/path/to/agent \
OPENSAGE_CONFIG_TEMPLATE=/path/to/config.toml \
opensage-acp
```

The adapter reads JSON-RPC 2.0 messages from stdin and writes responses to
stdout. In normal use this is managed entirely by acpx.

## How It Works

1. acpx spawns `opensage-acp` as a subprocess.
2. On `session/new`, the adapter allocates a free port, writes a per-session
   `config.toml` (merging any `mcpServers[]` from the ACP request), and spawns
   `opensage web --agent <dir> --config <generated_toml>` on that port.
3. On `session/prompt`, the adapter POSTs to `opensage web`'s `/run_sse`
   endpoint and streams the SSE response back to acpx as ACP `session/update`
   notifications.
4. On `session/cancel`, the adapter POSTs to `/control/stop_turn`.
5. On `session/load`, the adapter restarts `opensage web` with `--resume`,
   restoring chat history from the persisted session snapshot.
6. On session teardown, the adapter terminates the opensage-web process and
   cleans up the generated config file.

## Limitations & Known Issues

- **One opensage-web process per session.** opensage's web server is
  single-session by design. The adapter manages a pool of processes, one per
  ACP session. Concurrent sessions require proportional memory and ports.

- **MCP servers are fixed at session creation.** MCP server configurations
  passed in `session/new` are baked into the per-session TOML. They cannot be
  added or removed while the session is running without restarting the opensage
  process (which loses in-memory state).

- **Dynamic sub-agents are lost on adapter restart.** opensage writes sub-agent
  metadata to disk but the reload code is not implemented in upstream opensage.
  After an adapter restart and `session/load` resume, the LLM must recreate
  its agent topology from chat history context.

- **Neo4j memory is shared across all sessions.** opensage writes memories
  tagged with a session ID but reads all memories without a session filter.
  Two concurrent sessions see each other's memories. This is a feature for
  a persistent long-lived assistant but a potential concern for multi-user
  deployments.

- **Dynamic agent reload not implemented upstream.** The
  `DynamicAgentManager._load_persisted_agents_on_demand()` method in opensage
  is commented out. Until opensage fixes this, sub-agent state does not survive
  process boundaries.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `opensage-acp: command not found` | Not installed or not on PATH | `pip install opensage-acp` |
| Timeout on `session/new` | opensage-web failed to start | Check `OPENSAGE_AGENT_DIR` is valid, Docker is running, port range is free |
| `ConnectionRefusedError` | opensage-web process died | Check stderr; often a missing Docker image or invalid agent dir |
| `Model X not found` | LLM model name not recognized by litellm | Check `config.toml` model name matches litellm format (`provider/model`) |
| `App not found` on session creation | App name mismatch | The adapter auto-discovers the app name; ensure opensage-web starts cleanly |
| Empty response | LLM API key missing | Set `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc. per your config |
| `TOML decode error` | Invalid config template | Validate: `python -c "import tomllib; tomllib.load(open('config.toml','rb'))"` |

For verbose logging, set `OPENSAGE_LOG_LEVEL=DEBUG` to see spawn commands,
health check attempts, app name discovery, and session ID mapping.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). For architectural context, see
[docs/adr/](docs/adr/).

## License

MIT
