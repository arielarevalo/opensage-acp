# OpenSage API Reference

**Source**: https://github.com/opensage-agent/opensage
**Researched**: 2026-03-24
**Package**: `opensage` (GitHub-only; not on PyPI as of 2026-03-24)

---

## What OpenSage Is

OpenSage is an **AI-centric agent framework** (ADK — Agent Development Kit)
built on top of **Google ADK** (`google-adk`).  Key capabilities:

- Self-generating agent topology (LLM creates sub-agents on the fly)
- Dynamic tool synthesis (LLM writes its own tools)
- Hierarchical graph-based memory (Neo4j, optional)
- Docker-based sandboxed tool execution
- Multi-LLM backend via **LiteLLM** (`litellm`)
- Web UI + REST API (FastAPI + uvicorn)

---

## Installation

Not published to PyPI.  Install from source using `uv`:

```bash
git clone https://github.com/opensage-agent/opensage
cd opensage
uv sync
uv run opensage --help
```

Key dependencies:
- `google-adk` (Google Agent Development Kit — underlying agent framework)
- `litellm` (LLM backend routing — supports OpenAI, Anthropic, Gemini, etc.)
- `neomodel` / Neo4j (optional, for graph memory — off by default)
- `docker` / `opensandbox` (for sandboxed tool execution)
- `uvicorn` + `fastapi` (web server)
- Python >= 3.12

---

## Architecture

```
opensage-acp (ACP adapter)
        │ HTTP
        ▼
OpenSage Web Server (FastAPI / uvicorn)
        │
        ▼
Google ADK Runner
  runner.run_async(user_id, session_id, new_message, run_config)
        │
        ▼
OpenSageAgent (extends google.adk.agents.llm_agent.LlmAgent)
        │
        ▼
LiteLLM → any LLM backend
        │ tool calls
        ▼
Docker Sandbox
```

---

## Starting the Server

```bash
# Start with an agent directory
opensage web <agent_dir> [--config config.toml] [--host 127.0.0.1] [--port 8000]
```

The server starts at `http://{host}:{port}`.  A single **session_id** is fixed
per server instance (passed as `--session-id` or auto-generated).  All API
calls use this session id.

---

## REST API

### `POST /run`

Run a turn and wait for all events (non-streaming).

**Request:**
```json
{
  "app_name": "<string>",
  "user_id": "user",
  "session_id": "<session_id>",
  "new_message": {
    "role": "user",
    "parts": [{"text": "Hello, agent"}]
  }
}
```

`new_message` is a `google.genai.types.Content` dict.

**Response:** `list[Event]` (ADK event objects, JSON-encoded)

---

### `POST /run_sse`

Run a turn with Server-Sent Events streaming.

**Request:** Same as `/run`, plus optional:
```json
{
  ...,
  "streaming": true
}
```

**Response:** SSE stream, each event is:
```
data: <event_json>\n\n
```

Each `event_json` is a JSON-encoded ADK `Event`.  On cancel:
```json
{"stopped": true, "message": "Turn stopped by UI"}
```

---

### `POST /control/stop_turn?session_id=<id>`

Cancel the currently running turn for a session.

**Response:**
```json
{"stopped": true, "running": true, "session_id": "<id>"}
```
Returns `{"stopped": false}` if no turn is running.

---

### `GET /control/turn_state?session_id=<id>`

Check if a turn is currently running.

**Response:**
```json
{"running": true|false, "session_id": "<id>"}
```

---

### `POST /apps/{app_name}/users/{user_id}/sessions`

Create or retrieve the session.

**Request:** `{"state": {...}}` (optional)

**Response:** ADK `Session` object

---

## Programmatic Usage (Python)

The OpenSage web server wraps Google ADK's `Runner`.  To drive it
programmatically from the adapter (without HTTP), use the runner directly:

```python
from google.adk.runners import Runner
from google.adk.apps.app import App
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.genai import types

from opensage.features.opensage_in_memory_session_service import OpenSageInMemorySessionService

# 1. Create services
session_service = OpenSageInMemorySessionService()

# 2. Create agent (from an agent module's mk_agent function)
agent = mk_agent(opensage_session_id=session_id)

# 3. Build runner
app = App(name="myapp", root_agent=agent)
runner = Runner(
    app=app,
    artifact_service=InMemoryArtifactService(),
    session_service=session_service,
    memory_service=InMemoryMemoryService(),
)

# 4. Create session
await session_service.create_session(
    app_name="myapp",
    user_id="user",
    state={"opensage_session_id": session_id},
    session_id=session_id,
)

# 5. Run a turn (streaming)
new_message = types.Content(
    role="user",
    parts=[types.Part(text="Do some task")]
)
async with Aclosing(
    runner.run_async(
        user_id="user",
        session_id=session_id,
        new_message=new_message,
        run_config=RunConfig(streaming_mode=StreamingMode.SSE),
    )
) as agen:
    async for event in agen:
        # event.content.parts[i].text for text chunks
        # event.is_final_response() for completion
        process_event(event)

# 6. Cancel: task.cancel("reason") on the asyncio.Task running step 5
```

---

## Session Management

**Session ID**: OpenSage uses two layers of session IDs:
- `opensage_session_id` — OpenSage's own session ID, used as a key for the
  `OpenSageSessionRegistry` and Docker container state.
- `adk_session_id` — Google ADK's session ID, used by the ADK runner.  In
  OpenSage's web mode, these are the same value.

**Session persistence**: The web server can persist the ADK session snapshot to
JSON on disk (`adk_session.json`) and restore it on resume via
`_load_adk_session_into_service_async`.

---

## Configuration

Config file: TOML format, loaded via `OpenSageConfig.from_toml(path)`.

Key config sections:
```toml
[llm.model_configs.main]
model_name = "anthropic/claude-sonnet-4-6"  # any liteLLM model string

[neo4j]
user = "neo4j"
password = "password"
bolt_port = 7687

[sandbox]
backend = "native"  # native (Docker), remotedocker, opensandbox, local, k8s
[sandbox.sandboxes.main]
image = "opensage-main:latest"

[memory]
enabled = false  # optional Neo4j graph memory
```

Environment variables override config values (via template expansion `${VAR}`).

Common env vars:
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` (LiteLLM routing)
- `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` (if memory is enabled)

---

## LLM Backends

LiteLLM model strings accepted (examples):
- `"openai/gpt-4o"`
- `"anthropic/claude-sonnet-4-6"`
- `"gemini/gemini-2.5-pro"`
- Any other liteLLM provider (Bedrock, Azure, etc.)

---

## Neo4j

**Required for**:
- Graph-based long-term memory (`config.memory.enabled = true`)
- Static analysis tools (joern/CodeQL sandbox, if used)

**Optional for**: basic agent operation (memory defaults to `enabled = false`).

When Neo4j is not available, set `config.memory.enabled = false`.  The adapter
can start OpenSage without Neo4j for basic use cases.

---

## Cancel / Abort

**HTTP approach** (preferred for the ACP adapter):
- `POST /control/stop_turn?session_id=<id>` cancels via `asyncio.Task.cancel()`
- The running turn gets a `CancelledError`; SSE returns `{"stopped": true}`

**In-process approach**:
- Hold the `asyncio.Task` and call `task.cancel("reason")`

After cancel, the session remains valid and the next `POST /run` or `/run_sse`
will succeed.

---

## ADK Event Format

`runner.run_async` yields ADK `Event` objects.  Relevant fields:
```python
event.content             # google.genai.types.Content
event.content.parts       # list of Part
event.content.parts[i].text  # text chunk (for text responses)
event.is_final_response() # True for the last event of a turn
event.author              # "model" for agent responses
```

---

## opensage-acp Integration Approach

`opensage-acp` imports opensage **directly as a Python library** (no HTTP).
Each ACP session maps to one `OpenSageSession` + one ADK `Runner` session.

Mapping:
| ACP method | opensage-acp action |
|---|---|
| `initialize` | Respond immediately with capabilities |
| `session/new` | Create `OpenSageSession`, build ADK `Runner`, create ADK session |
| `session/prompt` | `runner.run_async()` → collect final-response events → stream text back |
| `session/cancel` | `asyncio.Task.cancel()` on the running `run_async` task |
| `session/load` | Check if session exists in `_bridges` dict (in-memory only) |

**Config env vars**:
```
OPENSAGE_AGENT_DIR=/path/to/agent   # directory containing agent.py with mk_agent()
OPENSAGE_CONFIG_PATH=/path/to/config.toml   # opensage TOML config (optional)
OPENSAGE_ECHO_MODE=1   # skip opensage, echo task back (for tests)
```

---

## Dead Ends / Limitations

1. **Not on PyPI**: Must install from source.  Dependency pinned as:
   `opensage @ git+https://github.com/opensage-agent/opensage.git`

2. **Docker required for tools**: The `native` sandbox backend requires Docker.
   The adapter itself does NOT need Docker for echo-mode tests.

3. **No cross-restart session resume**: `OpenSageInMemorySessionService` is
   in-memory only.  Session state is lost when the process exits.

4. **ADK coupling**: The bridge imports `google.adk` types directly.  All ADK
   imports are deferred inside `if not echo_mode` blocks so tests run without
   any opensage/ADK installation.
