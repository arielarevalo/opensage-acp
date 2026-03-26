# 6. Accept No Cross-Session Agent Sharing

Date: 2026-03-24

## Status

Accepted

## Context

OpenSage supports dynamic sub-agent creation: the LLM can call a
`create_subagent` tool to spin up a specialized agent (e.g. a Python
code-review agent, a shell-execution agent) that lives in
`DynamicAgentManager` for the duration of the session.

The question was whether a sub-agent created in session A could be referenced
or reused by session B.

Research findings:
- `DynamicAgentManager` is instantiated per `opensage web` process. Each
  process (and thus each ACP session, per ADR-0002) has its own isolated
  manager.
- Agent metadata (names, system prompts, tool configs) is written to
  `~/.local/opensage/dynamic_agents/` — a **global directory** shared across
  processes.
- However, the code that would reload agents from this directory
  (`_load_persisted_agents_on_demand`) is **commented out**. The metadata
  files are written but never read back.
- There is no API to query, import, or instantiate an agent defined in another
  session.

## Decision

Accept this limitation. Sessions cannot share dynamic agents. Each session
builds its own agent topology independently.

## Consequences

**Positive:**
- No action required — this is the current behavior.
- Session isolation is preserved: agents in session A cannot accidentally
  affect session B.

**Negative:**
- If multiple sessions need the same type of specialized sub-agent (e.g. a
  domain-specific code reviewer), each session must invoke the LLM's
  `create_subagent` tool independently, paying the cost in LLM calls and
  latency each time.
- The global metadata directory fills up with orphaned agent definitions from
  terminated sessions.

**Potential OpenSage Contribution:** A cross-session agent registry with
working persistence and reload. If `_load_persisted_agents_on_demand()` were
un-commented and made reliable, agents defined in previous sessions could be
reused without recreation. A further extension would be an explicit agent
library concept: agents can be "published" globally and "imported" by any
session.
