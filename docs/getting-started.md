# Getting Started

## Prerequisites

- **Python >= 3.12**
- **Docker** — required by opensage for its sandbox backend
- **opensage** — installed from source:
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

Or from source:

```bash
pip install git+https://github.com/arielarevalo/opensage-acp.git
```

## Verify with echo mode

```bash
OPENSAGE_ECHO_MODE=1 acpx --agent opensage-acp exec "hello"
```

If you see `hello` echoed back with `[done] end_turn`, the ACP protocol layer
works.

## Run with a real agent

```bash
# Prepare a config (see examples/default_config.toml)
cp examples/default_config.toml /tmp/my_config.toml
# Edit: set your LLM model and API key env vars

# Run
OPENSAGE_AGENT_DIR=/path/to/your/agent \
OPENSAGE_CONFIG_TEMPLATE=/tmp/my_config.toml \
acpx --agent opensage-acp --verbose exec "hello, what can you do?"
```

## Register with acpx

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
