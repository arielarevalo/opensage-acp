# 5. Accept Shared Neo4j Memory Across Sessions

Date: 2026-03-24

## Status

Accepted

## Context

OpenSage supports Neo4j as an optional persistent memory backend. Research
into its implementation revealed an asymmetry in how memory is scoped:

- Memory is **written** with `opensage_session_id` as a node property on each
  memory entry.
- Memory is **read** with **no session filter**: vector similarity search and
  keyword search query the entire Neo4j database, returning results from all
  sessions regardless of which session created them.

As a result, when two ACP sessions share the same Neo4j instance, they see
each other's memories.

## Decision

Accept shared memory as a **feature**, not a bug. For a persistent Claw
(OpenClaw AI assistant) accumulating knowledge across many sessions and
interactions, cross-session memory access is desirable: learnings from one
session improve performance in subsequent sessions, and no knowledge is siloed.

## Consequences

**Positive:**
- Knowledge accumulates globally: every session benefits from the collective
  memory of all past sessions.
- No configuration required — this is the default opensage behavior.
- Persistent Claw use case is well-served: a long-lived assistant gets smarter
  over time across all its sessions.

**Negative:**
- No isolation between sessions: if one session stores incorrect, misleading,
  or contradictory information, all sessions are affected.
- No way to selectively clear one session's memories without custom Neo4j
  queries targeting `opensage_session_id`.
- In multi-user or multi-tenant deployments, session A can read session B's
  potentially sensitive memory entries.

**Potential OpenSage Contribution:** Optional session-scoped memory queries —
add a `WHERE m.opensage_session_id = $session_id` filter to the Neo4j search
queries, controlled by a config flag (e.g. `[memory] session_isolated = true`).
This would allow operators to choose between shared (default) and isolated
memory semantics.
