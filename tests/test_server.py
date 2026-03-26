"""
Unit tests for OpenSageACPAgent (server.py).

All tests use Config(echo_mode=True) so no opensage-web subprocess is spawned
and no HTTP calls are made.  The _EchoBridge is used transparently, letting us
test full ACP protocol semantics without any external dependencies.

For edge-case tests (bridge errors, cancel mid-stream) we directly replace the
session's bridge with a mock after session creation.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from opensage_acp.config import Config
from opensage_acp.server import OpenSageACPAgent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _echo_config() -> Config:
    return Config(echo_mode=True)


def _make_text_block(text: str):
    from acp.schema import TextContentBlock

    return TextContentBlock(type="text", text=text)


def _make_mock_conn() -> MagicMock:
    conn = MagicMock()
    conn.session_update = AsyncMock()
    return conn


async def _setup_session(agent: OpenSageACPAgent, cwd: str = "/tmp") -> str:
    """Run new_session and return the session_id."""
    resp = await agent.new_session(cwd=cwd)
    return resp.session_id


# ---------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initialize_returns_protocol_version():
    agent = OpenSageACPAgent(config=_echo_config())
    resp = await agent.initialize(protocol_version=1)
    assert resp.protocol_version == 1


@pytest.mark.asyncio
async def test_initialize_returns_agent_capabilities():
    agent = OpenSageACPAgent(config=_echo_config())
    resp = await agent.initialize(protocol_version=1)
    assert resp.agent_capabilities is not None


# ---------------------------------------------------------------------------
# new_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_session_returns_nonempty_session_id():
    agent = OpenSageACPAgent(config=_echo_config())
    resp = await agent.new_session(cwd="/tmp")
    assert resp.session_id and isinstance(resp.session_id, str)


@pytest.mark.asyncio
async def test_new_session_stores_cwd():
    agent = OpenSageACPAgent(config=_echo_config())
    resp = await agent.new_session(cwd="/workspace")
    assert agent._sessions[resp.session_id].cwd == "/workspace"


@pytest.mark.asyncio
async def test_new_session_ids_are_unique():
    agent = OpenSageACPAgent(config=_echo_config())
    r1 = await agent.new_session(cwd="/tmp")
    r2 = await agent.new_session(cwd="/tmp")
    assert r1.session_id != r2.session_id


@pytest.mark.asyncio
async def test_new_session_echo_mode_no_process():
    agent = OpenSageACPAgent(config=_echo_config())
    resp = await agent.new_session(cwd="/tmp")
    session = agent._sessions[resp.session_id]
    assert session.process is None
    assert session.port is None
    assert session.session_dir is None  # echo mode has no session dir


@pytest.mark.asyncio
async def test_new_session_stores_session_dir():
    """T-09a: Non-echo mode stores the session directory path."""
    from unittest.mock import patch

    config = Config(echo_mode=False, agent_dir="/test/agents")
    agent = OpenSageACPAgent(config=config)

    with patch.object(agent, "_spawn_opensage_web", return_value=MagicMock()):
        with patch.object(agent, "_wait_healthy", new_callable=AsyncMock):
            with patch("opensage_acp.server.OpenSageHttpBridge") as MockBridge:
                mock_bridge = MockBridge.return_value
                mock_bridge.discover_app_name = AsyncMock()
                mock_bridge.create_session = AsyncMock()
                resp = await agent.new_session(cwd="/tmp")

    session = agent._sessions[resp.session_id]
    assert session.session_dir is not None
    assert resp.session_id in session.session_dir
    assert ".local/opensage/sessions" in session.session_dir

    await agent.shutdown_all()


# ---------------------------------------------------------------------------
# load_session / resume_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_session_returns_none_for_unknown():
    agent = OpenSageACPAgent(config=_echo_config())
    result = await agent.load_session(cwd="/tmp", session_id="no-such")
    assert result is None


@pytest.mark.asyncio
async def test_load_session_returns_none_when_no_disk_snapshot():
    """T-09b: No in-memory session and no disk snapshot → returns None."""
    config = Config(echo_mode=False)
    agent = OpenSageACPAgent(config=config)
    result = await agent.load_session(cwd="/tmp", session_id="nonexistent-session-id-12345")
    assert result is None


@pytest.mark.asyncio
async def test_load_session_resumes_from_disk(tmp_path):
    """T-09b: Disk snapshot exists → spawns opensage-web with --resume."""
    from unittest.mock import patch

    config = Config(echo_mode=False, agent_dir="/test/agents")
    agent = OpenSageACPAgent(config=config)

    fake_session_id = "resume-test-session"

    # Create a fake session dir on disk
    fake_dir = tmp_path / fake_session_id
    fake_dir.mkdir()

    # Patch _session_dir to point to our tmp_path
    with patch("opensage_acp.server._session_dir", return_value=str(fake_dir)):
        with patch.object(agent, "_spawn_opensage_web", return_value=MagicMock()) as mock_spawn:
            with patch.object(agent, "_wait_healthy", new_callable=AsyncMock):
                with patch("opensage_acp.server.OpenSageHttpBridge") as MockBridge:
                    mock_bridge = MockBridge.return_value
                    mock_bridge.discover_app_name = AsyncMock()
                    mock_bridge.create_session = AsyncMock()

                    result = await agent.load_session(cwd="/tmp", session_id=fake_session_id)

    assert result is not None
    assert fake_session_id in agent._sessions

    # Verify --resume was passed
    mock_spawn.assert_called_once()
    assert mock_spawn.call_args.kwargs.get("resume") is True

    await agent.shutdown_all()


@pytest.mark.asyncio
async def test_load_session_returns_response_for_active():
    agent = OpenSageACPAgent(config=_echo_config())
    sid = await _setup_session(agent)
    result = await agent.load_session(cwd="/tmp", session_id=sid)
    assert result is not None


@pytest.mark.asyncio
async def test_resume_session_succeeds_for_active():
    agent = OpenSageACPAgent(config=_echo_config())
    sid = await _setup_session(agent)
    resp = await agent.resume_session(cwd="/tmp", session_id=sid)
    assert resp is not None


@pytest.mark.asyncio
async def test_resume_session_raises_for_unknown():
    from acp.exceptions import RequestError

    agent = OpenSageACPAgent(config=_echo_config())
    with pytest.raises(RequestError):
        await agent.resume_session(cwd="/tmp", session_id="no-such-session")


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sessions_empty_initially():
    agent = OpenSageACPAgent(config=_echo_config())
    resp = await agent.list_sessions()
    assert resp.sessions == []


@pytest.mark.asyncio
async def test_list_sessions_returns_active_sessions():
    agent = OpenSageACPAgent(config=_echo_config())
    r_a = await agent.new_session(cwd="/workspace/a")
    r_b = await agent.new_session(cwd="/workspace/b")

    resp = await agent.list_sessions()
    ids = {s.session_id for s in resp.sessions}
    assert r_a.session_id in ids
    assert r_b.session_id in ids


@pytest.mark.asyncio
async def test_list_sessions_filters_by_cwd():
    agent = OpenSageACPAgent(config=_echo_config())
    await agent.new_session(cwd="/workspace/a")
    await agent.new_session(cwd="/workspace/b")

    resp = await agent.list_sessions(cwd="/workspace/a")
    assert len(resp.sessions) == 1
    assert resp.sessions[0].cwd == "/workspace/a"


# ---------------------------------------------------------------------------
# prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_returns_end_turn():
    agent = OpenSageACPAgent(config=_echo_config())
    conn = _make_mock_conn()
    agent.on_connect(conn)
    sid = await _setup_session(agent)

    resp = await agent.prompt(prompt=[_make_text_block("hello")], session_id=sid)
    assert resp.stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_prompt_emits_session_update():
    agent = OpenSageACPAgent(config=_echo_config())
    conn = _make_mock_conn()
    agent.on_connect(conn)
    sid = await _setup_session(agent)

    await agent.prompt(prompt=[_make_text_block("hello")], session_id=sid)

    conn.session_update.assert_awaited_once()
    call_kwargs = conn.session_update.call_args.kwargs
    assert call_kwargs["session_id"] == sid


@pytest.mark.asyncio
async def test_prompt_echo_content_matches_input():
    """Echo mode: session_update text equals the prompt text."""
    from acp.schema import AgentMessageChunk

    updates: list[tuple[str, Any]] = []

    async def capture_update(session_id: str, update: Any, **kwargs: Any) -> None:
        updates.append((session_id, update))

    conn = MagicMock()
    conn.session_update = capture_update

    agent = OpenSageACPAgent(config=_echo_config())
    agent.on_connect(conn)
    sid = await _setup_session(agent)

    await agent.prompt(prompt=[_make_text_block("echo this")], session_id=sid)

    assert len(updates) == 1
    _sid, update = updates[0]
    assert _sid == sid
    assert isinstance(update, AgentMessageChunk)
    assert update.content.text == "echo this"


@pytest.mark.asyncio
async def test_prompt_unknown_session_raises():
    from acp.exceptions import RequestError

    agent = OpenSageACPAgent(config=_echo_config())
    conn = _make_mock_conn()
    agent.on_connect(conn)

    with pytest.raises(RequestError):
        await agent.prompt(prompt=[_make_text_block("hi")], session_id="no-such")


@pytest.mark.asyncio
async def test_prompt_bridge_error_raises_request_error():
    """If the bridge's run_sse raises, prompt() wraps it in RequestError."""
    from acp.exceptions import RequestError

    agent = OpenSageACPAgent(config=_echo_config())
    conn = _make_mock_conn()
    agent.on_connect(conn)
    sid = await _setup_session(agent)

    # Replace the echo bridge with one that errors
    async def _error_gen(message: str):
        raise RuntimeError("opensage exploded")
        yield  # makes this an async generator

    mock_bridge = MagicMock()
    mock_bridge.run_sse = _error_gen
    mock_bridge.cancel = AsyncMock()
    agent._sessions[sid].bridge = mock_bridge

    with pytest.raises(RequestError) as exc_info:
        await agent.prompt(prompt=[_make_text_block("hi")], session_id=sid)
    assert exc_info.value.code == -32603


@pytest.mark.asyncio
async def test_prompt_increments_turn_count():
    agent = OpenSageACPAgent(config=_echo_config())
    conn = _make_mock_conn()
    agent.on_connect(conn)
    sid = await _setup_session(agent)

    await agent.prompt(prompt=[_make_text_block("turn 1")], session_id=sid)
    await agent.prompt(prompt=[_make_text_block("turn 2")], session_id=sid)

    assert agent._sessions[sid].turn_count == 2


@pytest.mark.asyncio
async def test_prompt_updates_updated_at():
    agent = OpenSageACPAgent(config=_echo_config())
    conn = _make_mock_conn()
    agent.on_connect(conn)
    sid = await _setup_session(agent)

    before = agent._sessions[sid].updated_at
    await agent.prompt(prompt=[_make_text_block("anything")], session_id=sid)
    after = agent._sessions[sid].updated_at

    assert after >= before


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_sets_cancelled_flag():
    agent = OpenSageACPAgent(config=_echo_config())
    assert "any" not in agent._cancelled
    await agent.cancel(session_id="any")
    assert "any" in agent._cancelled


@pytest.mark.asyncio
async def test_cancel_mid_prompt_returns_cancelled():
    """Simulates a cancel arriving while run_sse is yielding chunks."""
    agent_ref: list[OpenSageACPAgent] = []

    async def cancelling_gen(message: str):
        # Set the cancelled flag before yielding so prompt() sees it
        if agent_ref:
            agent_ref[0]._cancelled.add(sid)
        yield "partial"

    mock_bridge = MagicMock()
    mock_bridge.run_sse = cancelling_gen
    mock_bridge.cancel = AsyncMock()

    agent = OpenSageACPAgent(config=_echo_config())
    agent_ref.append(agent)
    conn = _make_mock_conn()
    agent.on_connect(conn)
    sid = await _setup_session(agent)
    agent._sessions[sid].bridge = mock_bridge

    resp = await agent.prompt(prompt=[_make_text_block("go")], session_id=sid)
    assert resp.stop_reason == "cancelled"


# ---------------------------------------------------------------------------
# multi-turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_turn_prompt_returns_end_turn_each_time():
    agent = OpenSageACPAgent(config=_echo_config())
    conn = _make_mock_conn()
    agent.on_connect(conn)
    sid = await _setup_session(agent)

    r1 = await agent.prompt(prompt=[_make_text_block("turn 1")], session_id=sid)
    r2 = await agent.prompt(prompt=[_make_text_block("turn 2")], session_id=sid)

    assert r1.stop_reason == "end_turn"
    assert r2.stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_two_sessions_are_isolated():
    """Prompts on session A do not affect session B."""
    updates: dict[str, list[Any]] = {}

    async def capture(session_id: str, update: Any, **kwargs: Any) -> None:
        updates.setdefault(session_id, []).append(update)

    conn = MagicMock()
    conn.session_update = capture

    agent = OpenSageACPAgent(config=_echo_config())
    agent.on_connect(conn)

    r_a = await agent.new_session(cwd="/a")
    r_b = await agent.new_session(cwd="/b")

    await agent.prompt(prompt=[_make_text_block("hello A")], session_id=r_a.session_id)
    await agent.prompt(prompt=[_make_text_block("hello B")], session_id=r_b.session_id)

    assert len(updates.get(r_a.session_id, [])) == 1
    assert len(updates.get(r_b.session_id, [])) == 1


# ---------------------------------------------------------------------------
# shutdown_all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_all_closes_bridges():
    agent = OpenSageACPAgent(config=_echo_config())
    sid = await _setup_session(agent)

    mock_bridge = MagicMock()
    mock_bridge.aclose = AsyncMock()
    agent._sessions[sid].bridge = mock_bridge

    await agent.shutdown_all()
    mock_bridge.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_all_clears_sessions():
    agent = OpenSageACPAgent(config=_echo_config())
    await agent.new_session(cwd="/tmp")
    await agent.new_session(cwd="/tmp")

    await agent.shutdown_all()

    assert len(agent._sessions) == 0


@pytest.mark.asyncio
async def test_shutdown_all_tolerates_bridge_errors():
    agent = OpenSageACPAgent(config=_echo_config())
    sid = await _setup_session(agent)

    mock_bridge = MagicMock()
    mock_bridge.aclose = AsyncMock(side_effect=Exception("close failed"))
    agent._sessions[sid].bridge = mock_bridge

    await agent.shutdown_all()  # should not raise


# ---------------------------------------------------------------------------
# ext_method
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ext_method_raises_not_found():
    from acp.exceptions import RequestError

    agent = OpenSageACPAgent(config=_echo_config())
    with pytest.raises(RequestError) as exc_info:
        await agent.ext_method("unknown/method", {})
    assert exc_info.value.code == -32601


# ---------------------------------------------------------------------------
# T-01c regression: CancelledError propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_cancelled_error_propagates():
    """CancelledError must propagate, not be wrapped in RequestError."""
    import asyncio

    agent = OpenSageACPAgent(config=_echo_config())
    conn = _make_mock_conn()
    agent.on_connect(conn)
    sid = await _setup_session(agent)

    async def _cancel_gen(message: str):
        raise asyncio.CancelledError()
        yield  # noqa: RUF027 — makes this an async generator

    mock_bridge = MagicMock()
    mock_bridge.run_sse = _cancel_gen
    mock_bridge.cancel = AsyncMock()
    agent._sessions[sid].bridge = mock_bridge

    with pytest.raises(asyncio.CancelledError):
        await agent.prompt(prompt=[_make_text_block("hi")], session_id=sid)


# ---------------------------------------------------------------------------
# T-03a: Port allocation and health-check loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alloc_port_sequential():
    agent = OpenSageACPAgent(config=Config(echo_mode=True, port_range_start=8100))
    assert agent._alloc_port() == 8100
    assert agent._alloc_port() == 8101
    assert agent._alloc_port() == 8102


@pytest.mark.asyncio
async def test_alloc_port_uses_config_start():
    agent = OpenSageACPAgent(config=Config(echo_mode=True, port_range_start=9100))
    assert agent._alloc_port() == 9100


@pytest.mark.asyncio
async def test_wait_healthy_raises_on_timeout():
    from unittest.mock import patch

    agent = OpenSageACPAgent(config=_echo_config())
    mock_bridge = MagicMock()
    mock_bridge.health_check = AsyncMock(return_value=False)

    # Simulate time advancing past deadline: first call returns 0, second returns 100
    call_count = 0

    def _fake_current_time():
        nonlocal call_count
        call_count += 1
        return 0.0 if call_count <= 1 else 100.0

    with patch("opensage_acp.server.anyio.current_time", side_effect=_fake_current_time):
        with patch("opensage_acp.server.anyio.sleep", new_callable=AsyncMock):
            with pytest.raises(RuntimeError, match="healthy"):
                await agent._wait_healthy(mock_bridge, "test-session", 8100)


@pytest.mark.asyncio
async def test_wait_healthy_succeeds_after_initial_failures():
    from unittest.mock import patch

    agent = OpenSageACPAgent(config=_echo_config())
    mock_bridge = MagicMock()
    mock_bridge.health_check = AsyncMock(side_effect=[False, False, True])

    # Time always within deadline
    with patch("opensage_acp.server.anyio.current_time", return_value=0.0):
        with patch("opensage_acp.server.anyio.sleep", new_callable=AsyncMock):
            await agent._wait_healthy(mock_bridge, "test-session", 8100)


# ---------------------------------------------------------------------------
# T-03b: new_session error propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_session_propagates_wait_healthy_error():
    from unittest.mock import patch

    config = Config(echo_mode=False)
    agent = OpenSageACPAgent(config=config)

    with patch.object(agent, "_spawn_opensage_web", return_value=MagicMock()):
        with patch.object(
            agent, "_wait_healthy", new_callable=AsyncMock, side_effect=RuntimeError("timeout")
        ):
            with pytest.raises(RuntimeError, match="timeout"):
                await agent.new_session(cwd="/tmp")


@pytest.mark.asyncio
async def test_new_session_propagates_create_session_error():
    from unittest.mock import patch

    import httpx

    config = Config(echo_mode=False)
    agent = OpenSageACPAgent(config=config)

    with patch.object(agent, "_spawn_opensage_web", return_value=MagicMock()):
        with patch.object(agent, "_wait_healthy", new_callable=AsyncMock):
            with patch("opensage_acp.server.OpenSageHttpBridge") as MockBridge:
                mock_bridge = MockBridge.return_value
                mock_bridge.discover_app_name = AsyncMock()
                mock_bridge.create_session = AsyncMock(
                    side_effect=httpx.HTTPStatusError(
                        "Server Error",
                        request=MagicMock(),
                        response=MagicMock(status_code=500),
                    )
                )
                with pytest.raises(httpx.HTTPStatusError):
                    await agent.new_session(cwd="/tmp")


# ---------------------------------------------------------------------------
# T-03c: prompt() edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_empty_list_sends_no_text():
    agent = OpenSageACPAgent(config=_echo_config())
    conn = _make_mock_conn()
    agent.on_connect(conn)
    sid = await _setup_session(agent)

    captured: list[str] = []

    async def _capture_gen(message: str):
        captured.append(message)
        yield message

    mock_bridge = MagicMock()
    mock_bridge.run_sse = _capture_gen
    mock_bridge.cancel = AsyncMock()
    agent._sessions[sid].bridge = mock_bridge

    await agent.prompt(prompt=[], session_id=sid)
    assert captured == ["(no text)"]


@pytest.mark.asyncio
async def test_prompt_non_text_blocks_only_sends_no_text():
    from acp.schema import ImageContentBlock

    agent = OpenSageACPAgent(config=_echo_config())
    conn = _make_mock_conn()
    agent.on_connect(conn)
    sid = await _setup_session(agent)

    captured: list[str] = []

    async def _capture_gen(message: str):
        captured.append(message)
        yield message

    mock_bridge = MagicMock()
    mock_bridge.run_sse = _capture_gen
    mock_bridge.cancel = AsyncMock()
    agent._sessions[sid].bridge = mock_bridge

    img = ImageContentBlock(type="image", data="base64data", mimeType="image/png")
    await agent.prompt(prompt=[img], session_id=sid)
    assert captured == ["(no text)"]


@pytest.mark.asyncio
async def test_prompt_joins_multiple_text_blocks_with_space():
    agent = OpenSageACPAgent(config=_echo_config())
    conn = _make_mock_conn()
    agent.on_connect(conn)
    sid = await _setup_session(agent)

    captured: list[str] = []

    async def _capture_gen(message: str):
        captured.append(message)
        yield message

    mock_bridge = MagicMock()
    mock_bridge.run_sse = _capture_gen
    mock_bridge.cancel = AsyncMock()
    agent._sessions[sid].bridge = mock_bridge

    await agent.prompt(
        prompt=[_make_text_block("hello"), _make_text_block("world")],
        session_id=sid,
    )
    assert captured == ["hello world"]


@pytest.mark.asyncio
async def test_prompt_pre_cancel_returns_cancelled():
    agent = OpenSageACPAgent(config=_echo_config())
    conn = _make_mock_conn()
    agent.on_connect(conn)
    sid = await _setup_session(agent)

    # Pre-cancel before calling prompt
    agent._cancelled.add(sid)

    resp = await agent.prompt(prompt=[_make_text_block("go")], session_id=sid)
    assert resp.stop_reason == "cancelled"


# ---------------------------------------------------------------------------
# T-11: opensage-web crash detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_raises_if_process_exited():
    """prompt() raises RequestError(-32603) when opensage-web process has died."""
    from acp.exceptions import RequestError

    agent = OpenSageACPAgent(config=_echo_config())
    conn = _make_mock_conn()
    agent.on_connect(conn)
    sid = await _setup_session(agent)

    # Inject a fake process that reports as exited
    fake_proc = MagicMock()
    fake_proc.poll.return_value = 1  # non-None = exited
    fake_proc.returncode = 1
    agent._sessions[sid].process = fake_proc

    with pytest.raises(RequestError) as exc_info:
        await agent.prompt(prompt=[_make_text_block("hello")], session_id=sid)
    assert exc_info.value.code == -32603
    assert "process died" in str(exc_info.value)


# ---------------------------------------------------------------------------
# T-03d: No-op methods, shutdown_all, list_sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fork_session_raises_32601():
    from acp.exceptions import RequestError

    agent = OpenSageACPAgent(config=_echo_config())
    with pytest.raises(RequestError) as exc_info:
        await agent.fork_session(cwd="/tmp", session_id="any")
    assert exc_info.value.code == -32601


@pytest.mark.asyncio
async def test_set_session_mode_returns_none():
    agent = OpenSageACPAgent(config=_echo_config())
    assert await agent.set_session_mode("x", "y") is None


@pytest.mark.asyncio
async def test_set_session_model_returns_none():
    agent = OpenSageACPAgent(config=_echo_config())
    assert await agent.set_session_model("x", "y") is None


@pytest.mark.asyncio
async def test_set_config_option_returns_none():
    agent = OpenSageACPAgent(config=_echo_config())
    assert await agent.set_config_option("key", "sid", "val") is None


@pytest.mark.asyncio
async def test_authenticate_returns_none():
    agent = OpenSageACPAgent(config=_echo_config())
    assert await agent.authenticate("oauth") is None


@pytest.mark.asyncio
async def test_shutdown_all_clears_cancelled_set():
    agent = OpenSageACPAgent(config=_echo_config())
    agent._cancelled.add("some-session")
    await agent.shutdown_all()
    assert len(agent._cancelled) == 0


@pytest.mark.asyncio
async def test_shutdown_all_tolerates_terminate_raising():
    agent = OpenSageACPAgent(config=_echo_config())
    sid = await _setup_session(agent)

    mock_proc = MagicMock()
    mock_proc.terminate = MagicMock(side_effect=OSError("no such process"))
    mock_proc.wait = MagicMock()
    agent._sessions[sid].process = mock_proc

    mock_bridge = MagicMock()
    mock_bridge.aclose = AsyncMock()
    agent._sessions[sid].bridge = mock_bridge

    await agent.shutdown_all()  # should not raise


@pytest.mark.asyncio
async def test_list_sessions_ignores_cursor_param():
    agent = OpenSageACPAgent(config=_echo_config())
    await agent.new_session(cwd="/tmp")

    resp = await agent.list_sessions(cursor="some-token")
    assert len(resp.sessions) == 1


# ---------------------------------------------------------------------------
# T-03e: _spawn_opensage_web details
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_includes_host_flag():
    from unittest.mock import patch

    config = Config(echo_mode=False, agent_dir="/test/agents")
    agent = OpenSageACPAgent(config=config)

    with patch("opensage_acp.server.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        agent._spawn_opensage_web("sid", 8100, None)

        cmd = mock_popen.call_args.args[0]
        assert "--host" in cmd
        assert "127.0.0.1" in cmd


# ---------------------------------------------------------------------------
# T-19a: _generate_config — malformed base template handling
# ---------------------------------------------------------------------------


def test_generate_config_malformed_template_falls_back_to_empty(tmp_path):
    """Malformed TOML template → no crash, valid (empty) config written."""
    import shutil
    import tomllib

    bad_toml = tmp_path / "bad.toml"
    bad_toml.write_text("[llm\nkey = [unclosed")

    config = Config(echo_mode=True, opensage_config_template=str(bad_toml))
    agent = OpenSageACPAgent(config=config)

    result_path = agent._generate_config("test-session-1234")

    with open(result_path, "rb") as f:
        parsed = tomllib.load(f)

    # With malformed base, the generated config should have only session overrides
    assert isinstance(parsed, dict)
    assert "llm" not in parsed  # base template keys not present
    assert "agent" in parsed  # session-scoped agent_storage_path is always set

    shutil.rmtree(result_path.rsplit("/", 1)[0], ignore_errors=True)


def test_generate_config_malformed_template_logs_warning(tmp_path, caplog):
    """Malformed TOML template logs a WARNING."""
    import logging

    bad_toml = tmp_path / "bad.toml"
    bad_toml.write_text("[llm\nkey = [unclosed")

    config = Config(echo_mode=True, opensage_config_template=str(bad_toml))
    agent = OpenSageACPAgent(config=config)

    with caplog.at_level(logging.WARNING):
        result_path = agent._generate_config("test-session-5678")

    assert any("Malformed TOML" in r.message for r in caplog.records)

    # Cleanup
    import shutil

    config_dir = result_path.rsplit("/", 1)[0]
    shutil.rmtree(config_dir, ignore_errors=True)


def test_generate_config_valid_template_preserved(tmp_path):
    """Valid base template keys are preserved in the generated config."""
    import tomllib

    good_toml = tmp_path / "good.toml"
    good_toml.write_text('[llm]\nmodel = "claude-3"\n')

    config = Config(echo_mode=True, opensage_config_template=str(good_toml))
    agent = OpenSageACPAgent(config=config)

    result_path = agent._generate_config("test-session-abcd")

    with open(result_path, "rb") as f:
        parsed = tomllib.load(f)

    assert parsed["llm"]["model"] == "claude-3"

    # Cleanup
    import shutil

    config_dir = result_path.rsplit("/", 1)[0]
    shutil.rmtree(config_dir, ignore_errors=True)


def test_generate_config_no_template_returns_only_overrides(tmp_path):
    """No template configured → generates valid TOML with only session overrides."""
    import shutil
    import tomllib

    config = Config(echo_mode=True, opensage_config_template="")
    agent = OpenSageACPAgent(config=config)

    result_path = agent._generate_config("test-session-0000")

    with open(result_path, "rb") as f:
        parsed = tomllib.load(f)

    # Only session overrides, no base template keys
    assert list(parsed.keys()) == ["agent"]
    assert "agent_storage_path" in parsed["agent"]

    shutil.rmtree(result_path.rsplit("/", 1)[0], ignore_errors=True)


# ---------------------------------------------------------------------------
# T-18c: _generate_config — no MCP servers
# ---------------------------------------------------------------------------


def test_generate_config_no_mcp_servers():
    """mcp_servers=[] → no [mcp.services] section; agent_storage_path still set."""
    import shutil
    import tomllib

    config = Config(echo_mode=True, opensage_config_template="")
    agent = OpenSageACPAgent(config=config)

    result_path = agent._generate_config("test18c-none", mcp_servers=[])

    with open(result_path, "rb") as f:
        parsed = tomllib.load(f)

    assert "mcp" not in parsed
    assert "agent_storage_path" in parsed["agent"]
    assert "test18c-" in parsed["agent"]["agent_storage_path"]

    shutil.rmtree(result_path.rsplit("/", 1)[0], ignore_errors=True)


# ---------------------------------------------------------------------------
# T-18b: _generate_config — empty/missing base template
# ---------------------------------------------------------------------------


def test_generate_config_empty_base_template():
    """No template configured → valid TOML with only session overrides."""
    import shutil
    import tomllib

    config = Config(echo_mode=True, opensage_config_template="")
    agent = OpenSageACPAgent(config=config)

    result_path = agent._generate_config("test18b-empt")

    with open(result_path, "rb") as f:
        parsed = tomllib.load(f)

    # Only session overrides (agent_storage_path), no base template keys
    assert "agent" in parsed
    assert "agent_storage_path" in parsed["agent"]
    assert "llm" not in parsed

    shutil.rmtree(result_path.rsplit("/", 1)[0], ignore_errors=True)


def test_generate_config_with_existing_base_template(tmp_path):
    """Existing base template keys preserved; session overrides merged on top."""
    import shutil
    import tomllib

    base = tmp_path / "base.toml"
    base.write_text('[llm]\nmodel = "gpt-4"\napi_key = "sk-test"\n')

    config = Config(echo_mode=True, opensage_config_template=str(base))
    agent = OpenSageACPAgent(config=config)

    result_path = agent._generate_config("test18b-base")

    with open(result_path, "rb") as f:
        parsed = tomllib.load(f)

    # Base template keys preserved
    assert parsed["llm"]["model"] == "gpt-4"
    assert parsed["llm"]["api_key"] == "sk-test"
    # Session overrides merged on top
    assert "agent" in parsed
    assert "agent_storage_path" in parsed["agent"]

    shutil.rmtree(result_path.rsplit("/", 1)[0], ignore_errors=True)


# ---------------------------------------------------------------------------
# T-18a: _generate_config — MCP server injection into TOML
# ---------------------------------------------------------------------------


def test_generate_config_injects_mcp_server():
    """Single McpServerStdio → [mcp.services.<name>] with command and args."""
    import shutil
    import tomllib

    from acp.schema import McpServerStdio

    config = Config(echo_mode=True, opensage_config_template="")
    agent = OpenSageACPAgent(config=config)

    mcp = McpServerStdio(command="npx", args=["-y", "@example/mcp"], env=[], name="my-mcp")

    result_path = agent._generate_config("test18a-sing", mcp_servers=[mcp])

    with open(result_path, "rb") as f:
        parsed = tomllib.load(f)

    entry = parsed["mcp"]["services"]["my-mcp"]
    assert entry["command"] == "npx"
    assert entry["args"] == ["-y", "@example/mcp"]

    shutil.rmtree(result_path.rsplit("/", 1)[0], ignore_errors=True)


def test_generate_config_injects_multiple_mcp_servers():
    """Two McpServerStdio entries → both appear without clobbering."""
    import shutil
    import tomllib

    from acp.schema import McpServerStdio

    config = Config(echo_mode=True, opensage_config_template="")
    agent = OpenSageACPAgent(config=config)

    servers = [
        McpServerStdio(command="server-a", args=["--a"], env=[], name="svc-a"),
        McpServerStdio(command="server-b", args=["--b"], env=[], name="svc-b"),
    ]

    result_path = agent._generate_config("test18a-mult", mcp_servers=servers)

    with open(result_path, "rb") as f:
        parsed = tomllib.load(f)

    services = parsed["mcp"]["services"]
    assert services["svc-a"]["command"] == "server-a"
    assert services["svc-b"]["command"] == "server-b"
    assert services["svc-a"]["args"] == ["--a"]
    assert services["svc-b"]["args"] == ["--b"]

    shutil.rmtree(result_path.rsplit("/", 1)[0], ignore_errors=True)


# ---------------------------------------------------------------------------
# T-19b: _generate_config — MCP server injection with special characters
# ---------------------------------------------------------------------------


def test_generate_config_mcp_url_with_spaces(tmp_path):
    """MCP server command with spaces round-trips through TOML correctly."""
    import shutil
    import tomllib

    from acp.schema import McpServerStdio

    config = Config(echo_mode=True, opensage_config_template="")
    agent = OpenSageACPAgent(config=config)

    mcp = McpServerStdio(
        command="/path/to/my server",
        args=["--flag", "value with spaces"],
        env=[],
        name="spacy-mcp",
    )

    result_path = agent._generate_config("test-mcp-space", mcp_servers=[mcp])

    with open(result_path, "rb") as f:
        parsed = tomllib.load(f)

    entry = parsed["mcp"]["services"]["spacy-mcp"]
    assert entry["command"] == "/path/to/my server"
    assert entry["args"] == ["--flag", "value with spaces"]

    shutil.rmtree(result_path.rsplit("/", 1)[0], ignore_errors=True)


def test_generate_config_mcp_args_with_quotes(tmp_path):
    """MCP server arg with double-quotes round-trips through TOML correctly."""
    import shutil
    import tomllib

    from acp.schema import McpServerStdio

    config = Config(echo_mode=True, opensage_config_template="")
    agent = OpenSageACPAgent(config=config)

    mcp = McpServerStdio(
        command="npx",
        args=["-y", '@example/"mcp-server"'],
        env=[],
        name="quotey-mcp",
    )

    result_path = agent._generate_config("test-mcp-quote", mcp_servers=[mcp])

    with open(result_path, "rb") as f:
        parsed = tomllib.load(f)

    entry = parsed["mcp"]["services"]["quotey-mcp"]
    assert entry["command"] == "npx"
    assert entry["args"][1] == '@example/"mcp-server"'

    shutil.rmtree(result_path.rsplit("/", 1)[0], ignore_errors=True)


def test_generate_config_multiple_mcp_servers(tmp_path):
    """Multiple MCP servers don't clobber each other."""
    import shutil
    import tomllib

    from acp.schema import McpServerStdio

    config = Config(echo_mode=True, opensage_config_template="")
    agent = OpenSageACPAgent(config=config)

    servers = [
        McpServerStdio(command="cmd1", args=["a"], env=[], name="mcp-a"),
        McpServerStdio(command="cmd2", args=["b"], env=[], name="mcp-b"),
    ]

    result_path = agent._generate_config("test-multi", mcp_servers=servers)

    with open(result_path, "rb") as f:
        parsed = tomllib.load(f)

    assert parsed["mcp"]["services"]["mcp-a"]["command"] == "cmd1"
    assert parsed["mcp"]["services"]["mcp-b"]["command"] == "cmd2"

    shutil.rmtree(result_path.rsplit("/", 1)[0], ignore_errors=True)


def test_generate_config_mcp_merges_with_existing(tmp_path):
    """MCP servers merge with existing [mcp.services] in the base template."""
    import shutil
    import tomllib

    from acp.schema import McpServerStdio

    good_toml = tmp_path / "base.toml"
    good_toml.write_text('[mcp.services.existing]\ncommand = "static-cmd"\nargs = ["x"]\n')

    config = Config(echo_mode=True, opensage_config_template=str(good_toml))
    agent = OpenSageACPAgent(config=config)

    mcp = McpServerStdio(command="dynamic-cmd", args=["y"], env=[], name="dynamic")

    result_path = agent._generate_config("test-merge", mcp_servers=[mcp])

    with open(result_path, "rb") as f:
        parsed = tomllib.load(f)

    services = parsed["mcp"]["services"]
    assert services["existing"]["command"] == "static-cmd"
    assert services["dynamic"]["command"] == "dynamic-cmd"

    shutil.rmtree(result_path.rsplit("/", 1)[0], ignore_errors=True)


# ---------------------------------------------------------------------------
# T-19c: _generate_config — session-scoped agent_storage_path
# ---------------------------------------------------------------------------


def test_generate_config_agent_storage_path_is_session_scoped():
    """Two sessions get distinct agent_storage_path values."""
    import shutil
    import tomllib

    config = Config(echo_mode=True, opensage_config_template="")
    agent = OpenSageACPAgent(config=config)

    path_a = agent._generate_config("aaaa1111rest")
    path_b = agent._generate_config("bbbb2222rest")

    with open(path_a, "rb") as f:
        parsed_a = tomllib.load(f)
    with open(path_b, "rb") as f:
        parsed_b = tomllib.load(f)

    storage_a = parsed_a["agent"]["agent_storage_path"]
    storage_b = parsed_b["agent"]["agent_storage_path"]

    assert storage_a != storage_b
    assert "aaaa1111" in storage_a
    assert "bbbb2222" in storage_b

    shutil.rmtree(path_a.rsplit("/", 1)[0], ignore_errors=True)
    shutil.rmtree(path_b.rsplit("/", 1)[0], ignore_errors=True)


# ---------------------------------------------------------------------------
# T-19d: Temp directory cleanup on session end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_cleanup_removes_temp_config_dir(tmp_path):
    """shutdown_all() removes the per-session config temp directory."""
    agent = OpenSageACPAgent(config=_echo_config())
    sid = await _setup_session(agent)

    # Create a real temp dir and attach it to the session
    config_dir = tmp_path / "opensage-acp-test1234"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text("")
    agent._sessions[sid].config_dir = str(config_dir)

    assert config_dir.exists()
    await agent.shutdown_all()
    assert not config_dir.exists()


@pytest.mark.asyncio
async def test_session_cleanup_tolerates_missing_config_dir():
    """shutdown_all() doesn't crash if config_dir was already removed."""
    agent = OpenSageACPAgent(config=_echo_config())
    sid = await _setup_session(agent)

    agent._sessions[sid].config_dir = "/tmp/opensage-acp-nonexist"
    await agent.shutdown_all()  # should not raise


@pytest.mark.asyncio
async def test_spawn_logs_warning_for_mcp_servers(caplog):
    import logging
    from unittest.mock import patch

    from acp.schema import McpServerStdio

    config = Config(echo_mode=False, agent_dir="/test/agents")
    agent = OpenSageACPAgent(config=config)

    mcp = McpServerStdio(command="echo", args=["hi"], env=[], name="test-mcp")

    with patch("opensage_acp.server.subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        with caplog.at_level(logging.WARNING):
            agent._spawn_opensage_web("sid", 8100, [mcp])

    assert any("MCP" in record.message for record in caplog.records)
