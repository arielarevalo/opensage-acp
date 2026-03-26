"""Minimal OpenSage agent that echoes user input back.

This is a test/development agent usable with opensage-web for manual
testing of the opensage-acp adapter without needing a real LLM backend
or Docker sandbox.

Usage:
    opensage web examples/agent --port 8100 --session-id test

Then point opensage-acp at it:
    OPENSAGE_AGENT_DIR=examples/agent opensage-acp
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from google.adk.agents.llm_agent import LlmAgent


def mk_agent(opensage_session_id: str | None = None) -> LlmAgent:
    """Create a minimal echo agent.

    The agent simply echoes back whatever the user sends.  This is useful
    for verifying that the opensage-web → opensage-acp pipeline works
    end-to-end without needing an API key or Docker.

    Args:
        opensage_session_id: Session ID passed by opensage (unused here).

    Returns:
        An ADK LlmAgent configured to echo input.
    """
    from google.adk.agents.llm_agent import LlmAgent

    return LlmAgent(
        name="echo_agent",
        model="echo",  # litellm echo provider — no API key needed
        instruction="You are an echo agent. Repeat exactly what the user says.",
    )
