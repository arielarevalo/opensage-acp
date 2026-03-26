# 7. Target opensage-agent/opensage (Not ianblenke/sageagent)

Date: 2026-03-24

## Status

Accepted

## Context

Early in the project, the wrong GitHub repository was used as the opensage
target. Two repositories exist with similar names:

| Repository | Description |
|---|---|
| `opensage-agent/opensage` | Google ADK-based agentic framework with FastAPI server, Docker sandboxes, Neo4j memory, LiteLLM backends, dynamic sub-agents. The real opensage. |
| `ianblenke/sageagent` | A simple CLI tool, completely different project, unrelated architecture. |

The initial implementation was built against `ianblenke/sageagent`, using its
`AgentEngine` and `EngineConfig` classes. This resulted in an entire
implementation cycle — bridge code, tests, config — built on the wrong
foundation.

The error was discovered when researching opensage's session API and finding
that the project had entirely different internals, HTTP interface, and
capabilities compared to what was expected.

## Decision

Target `opensage-agent/opensage` exclusively. All references to
`ianblenke/sageagent` have been removed from the codebase.

## Consequences

The codebase was fully corrected in commit `514a2af`. All bridge code,
configuration, tests, and documentation now reference `opensage-agent/opensage`
exclusively.

This was a factual error, not an architectural tradeoff. There are no ongoing
caveats or shortcomings from this decision — the correct target is clear and
unambiguous.

**Time lost:** Approximately one full implementation cycle building against
the wrong library. This is documented as a warning for future sessions and
contributors: always verify the repository target before beginning
implementation.
