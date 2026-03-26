# 4. Session Resume via opensage --resume

Date: 2026-03-24

## Status

Accepted

## Context

When the adapter process dies and restarts (crash, deployment update, etc.),
ACP clients may attempt to resume existing sessions via `session/load`. The
adapter must decide how to handle this.

Research into opensage's persistence model revealed the following:

- On **clean shutdown** (with `auto_cleanup=False`), opensage saves ADK session
  snapshots — including full chat history and session state — to
  `~/.local/opensage/sessions/<session_id>/`.
- Docker containers used by the session can be **left running** (with
  `auto_cleanup=False`) and re-attached on resume.
- `opensage web --resume <session_id>` restores the ADK session from the
  snapshot and reconnects to running containers.
- **Neo4j memory** survives independently of the process — it is an external
  Docker container that persists across restarts.
- **Dynamic sub-agents** are NOT recoverable. `DynamicAgentManager` writes
  agent metadata (JSON files) to `~/.local/opensage/dynamic_agents/` but the
  reload code (`_load_persisted_agents_on_demand`) is **commented out** and
  does not run. Sub-agents are lost when the process dies.

## Decision

Implement ACP `session/load` to **restart `opensage web` with `--resume`**,
pointing it at the persisted session directory. This restores chat history,
session state, and Neo4j memory access.

Accept the loss of dynamic sub-agents on restart as a known limitation. The
LLM retains full chat history from the ADK session snapshot and can recreate
sub-agents by re-running its agent-creation tool calls when it next needs them.

## Consequences

**Positive:**
- Conversations survive adapter restarts: chat history and session state are
  fully restored.
- Neo4j memory (accumulated knowledge) is always available regardless of
  restart.
- Docker containers can be reused if left running, avoiding cold-start overhead.

**Negative:**
- Sub-agent loss on restart: the LLM must re-discover and recreate its agent
  topology after a resume. This costs time and LLM tokens.
- For long-running sessions with many specialized sub-agents, the re-creation
  cost may be significant.
- Requires opensage to have been shut down cleanly with `auto_cleanup=False`.
  Crash recovery (unclean shutdown) may leave a partial snapshot.

**Potential OpenSage Contribution:** Fix the commented-out dynamic agent reload
code in `DynamicAgentManager._load_persisted_agents_on_demand()`. If sub-agents
were reloaded from their metadata files on resume, the post-restart
re-discovery cost would be eliminated.
