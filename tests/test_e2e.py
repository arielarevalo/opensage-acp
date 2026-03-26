"""
End-to-end smoke tests for opensage-acp.

These tests spawn the real ``opensage-acp`` binary as a subprocess and drive it
via the ACP client SDK.  ``OPENSAGE_ECHO_MODE=1`` is injected so the bridge
returns echoed responses without calling any LLM backend or requiring API keys.

The tests verify:
- The process starts and responds to the ACP protocol.
- ``initialize`` returns the negotiated protocol version.
- ``new_session`` returns a non-empty session ID.
- ``prompt`` (echo mode) streams a session_update and returns stop_reason "end_turn".
- Multiple sessions are independent.
"""

from __future__ import annotations

import os
import sys
from typing import Any

import acp
import pytest
from acp.schema import AgentMessageChunk, TextContentBlock

# ---------------------------------------------------------------------------
# Minimal Client implementation (receives server → client notifications)
# ---------------------------------------------------------------------------


class _RecordingClient:
    """Captures session_update notifications for assertions in tests."""

    def __init__(self) -> None:
        self.updates: list[tuple[str, Any]] = []  # [(session_id, update), ...]

    def on_connect(self, conn: acp.Agent) -> None:  # noqa: ARG002
        pass

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.updates.append((session_id, update))

    async def request_permission(
        self, options: Any, session_id: str, tool_call: Any, **kwargs: Any
    ) -> Any:
        from acp.schema import RequestPermissionResponse

        return RequestPermissionResponse(result="allow")

    async def write_text_file(
        self, content: str, path: str, session_id: str, **kwargs: Any
    ) -> None:
        return None

    async def read_text_file(self, path: str, session_id: str, **kwargs: Any) -> Any:
        from acp.schema import ReadTextFileResponse

        return ReadTextFileResponse(content="")

    async def create_terminal(self, command: str, session_id: str, **kwargs: Any) -> None:
        return None

    async def terminal_output(self, session_id: str, terminal_id: str, **kwargs: Any) -> None:
        return None

    async def release_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> None:
        return None

    async def wait_for_terminal_exit(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> None:
        return None

    async def kill_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> None:
        return None

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

AGENT_BINARY = str(__import__("pathlib").Path(sys.executable).parent / "opensage-acp")

_ECHO_ENV = {**os.environ, "OPENSAGE_ECHO_MODE": "1", "OPENSAGE_LOG_LEVEL": "ERROR"}
_CONN_KWARGS: dict = {"use_unstable_protocol": True}


def _make_client() -> _RecordingClient:
    return _RecordingClient()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initialize_returns_protocol_version():
    client = _make_client()
    async with acp.spawn_agent_process(client, AGENT_BINARY, env=_ECHO_ENV, **_CONN_KWARGS) as (
        conn,
        _proc,
    ):
        resp = await conn.initialize(protocol_version=1)
        assert resp.protocol_version == 1


@pytest.mark.asyncio
async def test_initialize_reflects_negotiated_version():
    client = _make_client()
    async with acp.spawn_agent_process(client, AGENT_BINARY, env=_ECHO_ENV, **_CONN_KWARGS) as (
        conn,
        _proc,
    ):
        resp = await conn.initialize(protocol_version=1)
        assert resp.agent_capabilities is not None


@pytest.mark.asyncio
async def test_new_session_returns_session_id():
    client = _make_client()
    async with acp.spawn_agent_process(client, AGENT_BINARY, env=_ECHO_ENV, **_CONN_KWARGS) as (
        conn,
        _proc,
    ):
        await conn.initialize(protocol_version=1)
        resp = await conn.new_session(cwd="/tmp")
        assert resp.session_id and isinstance(resp.session_id, str)


@pytest.mark.asyncio
async def test_two_sessions_have_distinct_ids():
    client = _make_client()
    async with acp.spawn_agent_process(client, AGENT_BINARY, env=_ECHO_ENV, **_CONN_KWARGS) as (
        conn,
        _proc,
    ):
        await conn.initialize(protocol_version=1)
        r1 = await conn.new_session(cwd="/tmp")
        r2 = await conn.new_session(cwd="/tmp")
        assert r1.session_id != r2.session_id


@pytest.mark.asyncio
async def test_list_sessions_returns_empty():
    client = _make_client()
    async with acp.spawn_agent_process(client, AGENT_BINARY, env=_ECHO_ENV, **_CONN_KWARGS) as (
        conn,
        _proc,
    ):
        await conn.initialize(protocol_version=1)
        resp = await conn.list_sessions()
        assert resp.sessions == []


@pytest.mark.asyncio
async def test_prompt_echo_returns_end_turn():
    """In echo mode, prompt() should return stop_reason='end_turn'."""
    client = _make_client()
    async with acp.spawn_agent_process(client, AGENT_BINARY, env=_ECHO_ENV, **_CONN_KWARGS) as (
        conn,
        _proc,
    ):
        await conn.initialize(protocol_version=1)
        sess = await conn.new_session(cwd="/tmp")
        resp = await conn.prompt(
            prompt=[TextContentBlock(type="text", text="hello world")],
            session_id=sess.session_id,
        )
        assert resp.stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_prompt_echo_streams_session_update():
    """In echo mode, the agent should emit a session_update notification."""
    client = _make_client()
    async with acp.spawn_agent_process(client, AGENT_BINARY, env=_ECHO_ENV, **_CONN_KWARGS) as (
        conn,
        _proc,
    ):
        await conn.initialize(protocol_version=1)
        sess = await conn.new_session(cwd="/tmp")
        await conn.prompt(
            prompt=[TextContentBlock(type="text", text="ping")],
            session_id=sess.session_id,
        )
        assert len(client.updates) >= 1
        sid, update = client.updates[0]
        assert sid == sess.session_id
        assert isinstance(update, AgentMessageChunk)


@pytest.mark.asyncio
async def test_prompt_echo_content_matches_input():
    """Echo mode: the session_update content equals the prompt text."""
    client = _make_client()
    async with acp.spawn_agent_process(client, AGENT_BINARY, env=_ECHO_ENV, **_CONN_KWARGS) as (
        conn,
        _proc,
    ):
        await conn.initialize(protocol_version=1)
        sess = await conn.new_session(cwd="/tmp")
        await conn.prompt(
            prompt=[TextContentBlock(type="text", text="echo this")],
            session_id=sess.session_id,
        )
        assert client.updates, "expected at least one session_update"
        _sid, update = client.updates[0]
        assert isinstance(update, AgentMessageChunk)
        assert isinstance(update.content, TextContentBlock)
        assert update.content.text == "echo this"


@pytest.mark.asyncio
async def test_prompt_unknown_session_raises():
    """Prompting with a non-existent session should raise RequestError."""
    client = _make_client()
    async with acp.spawn_agent_process(client, AGENT_BINARY, env=_ECHO_ENV, **_CONN_KWARGS) as (
        conn,
        _proc,
    ):
        await conn.initialize(protocol_version=1)
        with pytest.raises(Exception):
            await conn.prompt(
                prompt=[TextContentBlock(type="text", text="hi")],
                session_id="no-such-session",
            )


# ---------------------------------------------------------------------------
# Multi-turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_turn_each_prompt_returns_end_turn():
    """Two consecutive prompts on the same session both return end_turn."""
    client = _make_client()
    async with acp.spawn_agent_process(client, AGENT_BINARY, env=_ECHO_ENV, **_CONN_KWARGS) as (
        conn,
        _proc,
    ):
        await conn.initialize(protocol_version=1)
        sess = await conn.new_session(cwd="/tmp")

        resp1 = await conn.prompt(
            prompt=[TextContentBlock(type="text", text="turn one")],
            session_id=sess.session_id,
        )
        resp2 = await conn.prompt(
            prompt=[TextContentBlock(type="text", text="turn two")],
            session_id=sess.session_id,
        )

        assert resp1.stop_reason == "end_turn"
        assert resp2.stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_multi_turn_updates_accumulate():
    """Each prompt on the same session adds a session_update notification."""
    client = _make_client()
    async with acp.spawn_agent_process(client, AGENT_BINARY, env=_ECHO_ENV, **_CONN_KWARGS) as (
        conn,
        _proc,
    ):
        await conn.initialize(protocol_version=1)
        sess = await conn.new_session(cwd="/tmp")

        await conn.prompt(
            prompt=[TextContentBlock(type="text", text="first")],
            session_id=sess.session_id,
        )
        await conn.prompt(
            prompt=[TextContentBlock(type="text", text="second")],
            session_id=sess.session_id,
        )

        assert len(client.updates) == 2
        texts = [u.content.text for _, u in client.updates if isinstance(u, AgentMessageChunk)]
        assert texts == ["first", "second"]


@pytest.mark.asyncio
async def test_two_sessions_receive_independent_updates():
    """Updates from session A don't pollute session B."""
    client = _make_client()
    async with acp.spawn_agent_process(client, AGENT_BINARY, env=_ECHO_ENV, **_CONN_KWARGS) as (
        conn,
        _proc,
    ):
        await conn.initialize(protocol_version=1)
        sess_a = await conn.new_session(cwd="/tmp")
        sess_b = await conn.new_session(cwd="/tmp")

        await conn.prompt(
            prompt=[TextContentBlock(type="text", text="hello from A")],
            session_id=sess_a.session_id,
        )
        await conn.prompt(
            prompt=[TextContentBlock(type="text", text="hello from B")],
            session_id=sess_b.session_id,
        )

        sid_a_updates = [u for sid, u in client.updates if sid == sess_a.session_id]
        sid_b_updates = [u for sid, u in client.updates if sid == sess_b.session_id]
        assert len(sid_a_updates) == 1
        assert len(sid_b_updates) == 1
