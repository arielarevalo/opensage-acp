# Testing Guide

## Automated tests

```bash
# All tests (unit + integration, no external dependencies)
uv run pytest

# With coverage
uv run pytest --cov=opensage_acp

# E2E tests using echo mode (no opensage needed)
uv run pytest tests/test_e2e.py -v

# Echo adapter protocol oracle
printf '{"jsonrpc":"2.0","id":"1","method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{},"clientInfo":{"name":"test","version":"0"}}}\n' \
  | uv run python scripts/echo_adapter.py
```

## acpx smoke test

End-to-end validation with acpx spawning opensage-acp as a real agent runtime.

### Prerequisites

1. **opensage-acp** installed: `uv pip install -e .`
2. **opensage** installed: `uv pip install git+https://github.com/opensage-agent/opensage.git`
3. **acpx** installed: see [acpx docs](https://github.com/openclaw/acpx)
4. **Docker** running: `docker info`
5. An **opensage agent directory** with an `__init__.py` defining the agent
6. An **opensage config.toml** with valid LLM credentials (see `examples/default_config.toml`)

### Echo mode (no opensage needed)

```bash
OPENSAGE_ECHO_MODE=1 acpx --agent opensage-acp exec "hello"
```

Expected: acpx echoes back `"hello"` with `stop_reason=end_turn`.

### Full stack

```bash
OPENSAGE_AGENT_DIR=/path/to/agent \
OPENSAGE_CONFIG_TEMPLATE=/path/to/config.toml \
acpx --agent opensage-acp --verbose exec "hello, what can you do?"
```

**Verify:**
- No Python tracebacks on stderr
- Coherent response from the agent
- No orphaned `opensage` processes after session ends

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `opensage-acp: command not found` | Not installed | `uv pip install -e .` |
| Timeout on `session/new` | opensage-web failed to start | Check agent dir, Docker, port range |
| `ConnectionRefusedError` | opensage-web died | Check stderr for missing Docker image |
| Empty response | LLM API key missing | Set `ANTHROPIC_API_KEY`, etc. |
| `TOML decode error` | Invalid config template | Validate with `python -c "import tomllib; tomllib.load(open('config.toml','rb'))"` |
