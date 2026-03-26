"""
End-to-end integration tests: OpenSageHttpBridge + OpenSageACPAgent ↔ mock opensage-web.

Architecture under test:

    test → OpenSageHttpBridge ──HTTP──▶ MockOpensageServer (FastAPI in thread)
    test → OpenSageACPAgent → OpenSageHttpBridge ──HTTP──▶ MockOpensageServer

The mock server runs in a background thread and mimics the opensage-web HTTP API.
No real opensage-web process, LLM backends, or Docker are required.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from opensage_acp.bridge import OpenSageHttpBridge
from opensage_acp.config import Config
from opensage_acp.server import OpenSageACPAgent
from tests.mock_opensage_server import MockOpensageServer

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def mock_server() -> MockOpensageServer:
    """Start a mock opensage-web server; yield it; stop it on teardown."""
    srv = MockOpensageServer(text_chunks=["Hello", " world", "!"])
    srv.start()

    # Poll until the server is responding (max 5 s)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5.0
    ready = False
    async with httpx.AsyncClient() as client:
        while loop.time() < deadline:
            try:
                r = await client.get(f"{srv.url}/", timeout=0.5)
                if r.status_code < 500:
                    ready = True
                    break
            except Exception:
                pass
            await asyncio.sleep(0.05)

    if not ready:
        srv.stop()
        pytest.fail("MockOpensageServer did not become ready within 5 s")

    yield srv

    srv.stop()


@pytest.fixture
async def bridge(mock_server: MockOpensageServer):
    """Return an OpenSageHttpBridge pointing at the mock server."""
    b = OpenSageHttpBridge(
        base_url=mock_server.url,
        session_id="test-session-123",
        timeout=5.0,
    )
    yield b
    await b.aclose()


# ---------------------------------------------------------------------------
# Mock server self-tests
# ---------------------------------------------------------------------------


async def test_mock_server_health_endpoint(mock_server: MockOpensageServer) -> None:
    """The mock server's health endpoint returns 200."""
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{mock_server.url}/")
    assert r.status_code == 200


async def test_mock_server_list_apps(mock_server: MockOpensageServer) -> None:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{mock_server.url}/list-apps")
    assert r.status_code == 200
    assert r.json() == ["opensage"]


async def test_mock_server_create_session(mock_server: MockOpensageServer) -> None:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{mock_server.url}/apps/opensage/users/user/sessions",
            json={"state": {}},
        )
    assert r.status_code == 200
    assert r.json()["id"] == "test-session-123"


async def test_mock_server_stop_turn(mock_server: MockOpensageServer) -> None:
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{mock_server.url}/control/stop_turn")
    assert r.status_code == 200
    # No turn is active, so stopped should be False
    assert r.json()["stopped"] is False


async def test_mock_server_turn_state(mock_server: MockOpensageServer) -> None:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{mock_server.url}/control/turn_state")
    assert r.status_code == 200
    assert r.json()["running"] is False


# ---------------------------------------------------------------------------
# OpenSageHttpBridge against mock server
# ---------------------------------------------------------------------------


async def test_bridge_health_check(bridge: OpenSageHttpBridge) -> None:
    """Bridge health_check returns True when server is responding."""
    assert await bridge.health_check() is True


async def test_bridge_create_session(bridge: OpenSageHttpBridge) -> None:
    """Bridge create_session does not raise against the mock server."""
    await bridge.create_session()  # should not raise


async def test_bridge_run_sse_collects_chunks(bridge: OpenSageHttpBridge) -> None:
    """Bridge run_sse yields all text chunks from the SSE stream."""
    chunks = [c async for c in bridge.run_sse("hello")]
    assert chunks == ["Hello", " world", "!"]


async def test_bridge_run_sse_full_text(bridge: OpenSageHttpBridge) -> None:
    """All chunks joined equal the complete response text."""
    chunks = [c async for c in bridge.run_sse("hello")]
    assert "".join(chunks) == "Hello world!"


async def test_bridge_cancel(bridge: OpenSageHttpBridge) -> None:
    """Bridge cancel() posts to /control/stop_turn without raising."""
    await bridge.cancel()  # should not raise


async def test_bridge_is_running_false(bridge: OpenSageHttpBridge) -> None:
    """Bridge is_running() returns False (mock server reports not running)."""
    assert await bridge.is_running() is False


async def test_bridge_run_sse_sends_correct_payload(
    mock_server: MockOpensageServer,
) -> None:
    """Bridge run_sse sends the right JSON body to /run_sse."""
    # Use the mock server's configured session_id
    b = OpenSageHttpBridge(
        base_url=mock_server.url,
        session_id=mock_server.session_id,
        timeout=5.0,
    )
    try:
        chunks = [c async for c in b.run_sse("tell me a joke")]
        # Chunks came back fine — the server accepted the payload
        assert len(chunks) == 3
    finally:
        await b.aclose()


# ---------------------------------------------------------------------------
# OpenSageACPAgent end-to-end with mock server
# ---------------------------------------------------------------------------


async def test_agent_new_session_via_mock_server(
    mock_server: MockOpensageServer,
) -> None:
    """Agent new_session health-checks and create_sessions against the mock."""
    config = Config(
        port_range_start=mock_server.port,
        echo_mode=False,
        timeout=5.0,
        agent_dir="/tmp/test-agent",
    )
    agent = OpenSageACPAgent(config=config)

    # Prevent a real opensage-web subprocess from being spawned
    fake_proc = MagicMock()
    fake_proc.terminate = MagicMock()
    fake_proc.wait = MagicMock(return_value=0)
    agent._spawn_opensage_web = MagicMock(return_value=fake_proc)

    conn = MagicMock()
    conn.session_update = AsyncMock()
    agent.on_connect(conn)

    resp = await agent.new_session(cwd="/tmp")
    assert resp.session_id and isinstance(resp.session_id, str)

    # Verify that the spawner was called and the port was allocated
    agent._spawn_opensage_web.assert_called_once()
    call_args = agent._spawn_opensage_web.call_args
    assert call_args.args[1] == mock_server.port  # port argument

    await agent.shutdown_all()


async def test_agent_prompt_streams_chunks_from_mock(
    mock_server: MockOpensageServer,
) -> None:
    """Agent prompt() streams session_update calls with text from mock server."""
    from acp.schema import AgentMessageChunk, TextContentBlock

    config = Config(
        port_range_start=mock_server.port,
        echo_mode=False,
        timeout=5.0,
        agent_dir="/tmp/test-agent",
    )
    agent = OpenSageACPAgent(config=config)

    fake_proc = MagicMock()
    fake_proc.terminate = MagicMock()
    fake_proc.wait = MagicMock(return_value=0)
    fake_proc.poll.return_value = None  # process is still running
    agent._spawn_opensage_web = MagicMock(return_value=fake_proc)

    updates: list[tuple[str, Any]] = []

    async def capture(session_id: str, update: Any, **kwargs: Any) -> None:
        updates.append((session_id, update))

    conn = MagicMock()
    conn.session_update = capture
    agent.on_connect(conn)

    sess = await agent.new_session(cwd="/tmp")
    result = await agent.prompt(
        prompt=[TextContentBlock(type="text", text="hello")],
        session_id=sess.session_id,
    )

    assert result.stop_reason == "end_turn"
    # Mock server streams 3 chunks: "Hello", " world", "!"
    assert len(updates) == 3
    texts = [u.content.text for _sid, u in updates if isinstance(u, AgentMessageChunk)]
    assert texts == ["Hello", " world", "!"]

    await agent.shutdown_all()


async def test_agent_prompt_multi_turn_via_mock(
    mock_server: MockOpensageServer,
) -> None:
    """Two consecutive prompts on the same session both return end_turn."""
    from acp.schema import TextContentBlock

    config = Config(
        port_range_start=mock_server.port,
        echo_mode=False,
        timeout=5.0,
        agent_dir="/tmp/test-agent",
    )
    agent = OpenSageACPAgent(config=config)

    fake_proc = MagicMock()
    fake_proc.poll.return_value = None  # process is still running
    agent._spawn_opensage_web = MagicMock(return_value=fake_proc)

    conn = MagicMock()
    conn.session_update = AsyncMock()
    agent.on_connect(conn)

    sess = await agent.new_session(cwd="/tmp")

    r1 = await agent.prompt(
        prompt=[TextContentBlock(type="text", text="turn one")],
        session_id=sess.session_id,
    )
    r2 = await agent.prompt(
        prompt=[TextContentBlock(type="text", text="turn two")],
        session_id=sess.session_id,
    )

    assert r1.stop_reason == "end_turn"
    assert r2.stop_reason == "end_turn"
    assert agent._sessions[sess.session_id].turn_count == 2

    await agent.shutdown_all()


# ---------------------------------------------------------------------------
# Config / spawn-command generation
# ---------------------------------------------------------------------------


async def test_spawn_command_includes_agent_dir() -> None:
    """The opensage-web spawn command includes the configured agent directory."""
    config = Config(
        agent_dir="/path/to/my-agent",
        port_range_start=9000,
        echo_mode=False,
    )
    agent = OpenSageACPAgent(config=config)

    captured_cmds: list[list[str]] = []

    def fake_popen(cmd: list[str], **kwargs: Any) -> MagicMock:
        captured_cmds.append(cmd)
        return MagicMock()

    with patch("subprocess.Popen", side_effect=fake_popen):
        agent._spawn_opensage_web("sess-abc", 9000, None)

    assert len(captured_cmds) == 1
    cmd = captured_cmds[0]
    assert "--agent" in cmd
    assert "/path/to/my-agent" in cmd
    assert "--port" in cmd
    assert "9000" in cmd
    assert "--no-reload" in cmd


async def test_spawn_command_includes_config_template() -> None:
    """When a config template is set, --config is added to the spawn command."""
    config = Config(
        agent_dir="/agents",
        opensage_config_template="/etc/opensage/template.toml",
        port_range_start=9001,
        echo_mode=False,
    )
    agent = OpenSageACPAgent(config=config)

    captured_cmds: list[list[str]] = []

    def fake_popen(cmd: list[str], **kwargs: Any) -> MagicMock:
        captured_cmds.append(cmd)
        return MagicMock()

    with patch("subprocess.Popen", side_effect=fake_popen):
        agent._spawn_opensage_web("sess-xyz", 9001, None)

    cmd = captured_cmds[0]
    assert "--config" in cmd
    assert "/etc/opensage/template.toml" in cmd


async def test_spawn_command_omits_config_when_not_set() -> None:
    """When no config template is set, --config is absent from the spawn command."""
    config = Config(
        agent_dir="/agents",
        opensage_config_template="",
        port_range_start=9002,
        echo_mode=False,
    )
    agent = OpenSageACPAgent(config=config)

    captured_cmds: list[list[str]] = []

    def fake_popen(cmd: list[str], **kwargs: Any) -> MagicMock:
        captured_cmds.append(cmd)
        return MagicMock()

    with patch("subprocess.Popen", side_effect=fake_popen):
        agent._spawn_opensage_web("sess-noc", 9002, None)

    cmd = captured_cmds[0]
    assert "--config" not in cmd


# ---------------------------------------------------------------------------
# Helper: start a custom mock server
# ---------------------------------------------------------------------------


async def _start_custom_server(**kwargs: Any) -> MockOpensageServer:
    """Start a MockOpensageServer with custom params, wait until ready."""
    srv = MockOpensageServer(**kwargs)
    srv.start()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5.0
    async with httpx.AsyncClient() as client:
        while loop.time() < deadline:
            try:
                r = await client.get(f"{srv.url}/", timeout=0.5)
                if r.status_code < 500:
                    return srv
            except Exception:
                pass
            await asyncio.sleep(0.05)
    srv.stop()
    pytest.fail("Custom MockOpensageServer did not become ready")
    return srv  # unreachable, for type checker


# ---------------------------------------------------------------------------
# T-08a: Bridge vs mock returning 500
# ---------------------------------------------------------------------------


async def test_bridge_run_sse_raises_on_500() -> None:
    srv = await _start_custom_server(run_sse_status=500)
    try:
        b = OpenSageHttpBridge(
            base_url=srv.url,
            session_id="test-session-123",
            timeout=5.0,
        )
        try:
            with pytest.raises(Exception):
                _ = [c async for c in b.run_sse("hi")]
        finally:
            await b.aclose()
    finally:
        srv.stop()


# ---------------------------------------------------------------------------
# T-08b: Bridge vs mock emitting error SSE event
# ---------------------------------------------------------------------------


async def test_bridge_run_sse_raises_on_error_event() -> None:
    srv = await _start_custom_server(error_chunk="model overloaded")
    try:
        b = OpenSageHttpBridge(
            base_url=srv.url,
            session_id=srv.session_id,
            timeout=5.0,
        )
        try:
            with pytest.raises(RuntimeError, match="model overloaded"):
                _ = [c async for c in b.run_sse("hi")]
        finally:
            await b.aclose()
    finally:
        srv.stop()


# ---------------------------------------------------------------------------
# T-08c: Cancel mid-prompt against slow mock
# ---------------------------------------------------------------------------


async def test_agent_cancel_mid_prompt_returns_cancelled() -> None:
    srv = await _start_custom_server(
        text_chunks=["chunk1", "chunk2", "chunk3", "chunk4", "chunk5"],
        chunk_delay_ms=200,
    )
    try:
        config = Config(
            port_range_start=srv.port,
            echo_mode=False,
            timeout=10.0,
            agent_dir="/tmp/test-agent",
        )
        agent = OpenSageACPAgent(config=config)

        fake_proc = MagicMock()
        fake_proc.terminate = MagicMock()
        fake_proc.wait = MagicMock(return_value=0)
        fake_proc.poll.return_value = None  # process is still running
        agent._spawn_opensage_web = MagicMock(return_value=fake_proc)

        conn = MagicMock()
        conn.session_update = AsyncMock()
        agent.on_connect(conn)

        from acp.schema import TextContentBlock

        sess = await agent.new_session(cwd="/tmp")

        # Start prompt in a task, cancel after 50ms
        async def _cancel_after_delay() -> None:
            await asyncio.sleep(0.05)
            await agent.cancel(session_id=sess.session_id)

        cancel_task = asyncio.create_task(_cancel_after_delay())

        result = await agent.prompt(
            prompt=[TextContentBlock(type="text", text="slow prompt")],
            session_id=sess.session_id,
        )

        await cancel_task
        assert result.stop_reason == "cancelled"

        await agent.shutdown_all()
    finally:
        srv.stop()


# ---------------------------------------------------------------------------
# T-08d: Verify exact JSON body reaches mock server
# ---------------------------------------------------------------------------


async def test_bridge_run_sse_body_contains_session_id(
    mock_server: MockOpensageServer,
    bridge: OpenSageHttpBridge,
) -> None:
    _ = [c async for c in bridge.run_sse("hello")]
    assert mock_server.last_run_sse_body is not None
    assert mock_server.last_run_sse_body["session_id"] == "test-session-123"


async def test_bridge_run_sse_body_contains_app_name(
    mock_server: MockOpensageServer,
    bridge: OpenSageHttpBridge,
) -> None:
    _ = [c async for c in bridge.run_sse("hello")]
    assert mock_server.last_run_sse_body is not None
    assert mock_server.last_run_sse_body["app_name"] == "opensage"


async def test_bridge_run_sse_body_contains_message_text(
    mock_server: MockOpensageServer,
    bridge: OpenSageHttpBridge,
) -> None:
    _ = [c async for c in bridge.run_sse("hello")]
    assert mock_server.last_run_sse_body is not None
    assert mock_server.last_run_sse_body["new_message"]["parts"][0]["text"] == "hello"


# ---------------------------------------------------------------------------
# T-09c: Integration test for session resume after simulated restart
# ---------------------------------------------------------------------------


async def test_agent_load_session_after_simulated_restart(
    mock_server: MockOpensageServer,
    tmp_path: Any,
) -> None:
    """After simulated restart, load_session resumes from disk snapshot."""
    from pathlib import Path

    config = Config(
        port_range_start=mock_server.port,
        echo_mode=False,
        timeout=5.0,
        agent_dir="/tmp/test-agent",
    )
    agent = OpenSageACPAgent(config=config)

    fake_proc = MagicMock()
    fake_proc.terminate = MagicMock()
    fake_proc.wait = MagicMock(return_value=0)
    agent._spawn_opensage_web = MagicMock(return_value=fake_proc)

    conn = MagicMock()
    conn.session_update = AsyncMock()
    agent.on_connect(conn)

    # Step 1: Create a session normally
    resp = await agent.new_session(cwd="/tmp")
    session_id = resp.session_id
    assert session_id in agent._sessions

    # Step 2: Simulate restart — close bridge, delete session from memory
    old_bridge = agent._sessions[session_id].bridge
    await old_bridge.aclose()
    del agent._sessions[session_id]
    assert session_id not in agent._sessions

    # Step 3: Create fake session dir on disk
    fake_session_dir = str(tmp_path / session_id)
    Path(fake_session_dir).mkdir(parents=True)

    # Reset port so load_session allocates the mock server port again
    agent._next_port = mock_server.port

    # Step 4: Call load_session with patched _session_dir
    with patch("opensage_acp.server._session_dir", return_value=fake_session_dir):
        load_resp = await agent.load_session(cwd="/tmp", session_id=session_id)

    # Assert success: session is re-registered with a new bridge
    assert load_resp is not None
    assert session_id in agent._sessions
    new_bridge = agent._sessions[session_id].bridge
    assert new_bridge is not None
    assert new_bridge is not old_bridge

    # Verify spawn was called with resume=True
    spawn_calls = [c for c in agent._spawn_opensage_web.call_args_list if c.kwargs.get("resume")]
    assert len(spawn_calls) == 1

    await agent.shutdown_all()
