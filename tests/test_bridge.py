"""
Unit tests for OpenSageHttpBridge and _EchoBridge (bridge.py).

OpenSageHttpBridge tests mock httpx.AsyncClient so no real network calls are
made.  _EchoBridge tests verify the test-only echo implementation.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from opensage_acp.bridge import OpenSageHttpBridge, _EchoBridge, _extract_text_from_event

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sse_lines(*texts: str) -> list[str]:
    """Build SSE data lines for events that each contain one text part."""
    lines = []
    for text in texts:
        event = {"content": {"role": "model", "parts": [{"text": text}]}}
        lines.append(f"data: {json.dumps(event)}")
    return lines


def _stopped_line() -> str:
    return 'data: {"stopped": true, "message": "Turn stopped by UI"}'


class _FakeStreamResponse:
    """Mimics the httpx streaming response context."""

    def __init__(self, lines: list[str], status_code: int = 200) -> None:
        self._lines = lines
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self._lines:
            yield line


@asynccontextmanager
async def _fake_stream_ctx(lines: list[str], status_code: int = 200):
    yield _FakeStreamResponse(lines, status_code)


def _make_bridge(mock_client: AsyncMock) -> OpenSageHttpBridge:
    """Return a bridge whose internal httpx client is replaced by mock_client."""
    with patch("opensage_acp.bridge.httpx.AsyncClient", return_value=mock_client):
        return OpenSageHttpBridge(
            base_url="http://127.0.0.1:8100",
            session_id="testsession",
        )


# ---------------------------------------------------------------------------
# _extract_text_from_event
# ---------------------------------------------------------------------------


def test_extract_text_basic():
    event = {"content": {"role": "model", "parts": [{"text": "Hello"}]}}
    assert _extract_text_from_event(event) == "Hello"


def test_extract_text_multiple_parts():
    event = {
        "content": {
            "role": "model",
            "parts": [{"text": "Hello"}, {"text": " World"}],
        }
    }
    assert _extract_text_from_event(event) == "Hello World"


def test_extract_text_no_content():
    assert _extract_text_from_event({}) is None


def test_extract_text_stopped_sentinel():
    assert _extract_text_from_event({"stopped": True}) is None


def test_extract_text_tool_call_no_text():
    # Tool-call events have function_call parts, not text parts
    event = {
        "content": {
            "role": "model",
            "parts": [{"function_call": {"name": "do_thing", "args": {}}}],
        }
    }
    assert _extract_text_from_event(event) is None


def test_extract_text_empty_parts():
    event = {"content": {"role": "model", "parts": []}}
    assert _extract_text_from_event(event) is None


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_true_on_200():
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=MagicMock(status_code=200))
    bridge = _make_bridge(mock_client)
    assert await bridge.health_check() is True


@pytest.mark.asyncio
async def test_health_check_true_on_404():
    # Any non-5xx response means the server is up
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=MagicMock(status_code=404))
    bridge = _make_bridge(mock_client)
    assert await bridge.health_check() is True


@pytest.mark.asyncio
async def test_health_check_false_on_500():
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=MagicMock(status_code=500))
    bridge = _make_bridge(mock_client)
    assert await bridge.health_check() is False


@pytest.mark.asyncio
async def test_health_check_false_on_connect_error():
    import httpx

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    bridge = _make_bridge(mock_client)
    assert await bridge.health_check() is False


# ---------------------------------------------------------------------------
# create_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_posts_correct_url():
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    bridge = _make_bridge(mock_client)

    await bridge.create_session()

    mock_client.post.assert_awaited_once()
    url_arg = mock_client.post.call_args.args[0]
    assert "/apps/opensage/users/user/sessions" in url_arg


# ---------------------------------------------------------------------------
# run_sse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_sse_yields_text_chunks():
    mock_client = AsyncMock()
    sse_lines = _make_sse_lines("Hello", " World")
    # stream() is a sync method on httpx.AsyncClient that returns an async ctx manager
    mock_client.stream = MagicMock(return_value=_fake_stream_ctx(sse_lines))
    bridge = _make_bridge(mock_client)

    chunks = [c async for c in bridge.run_sse("test message")]
    assert chunks == ["Hello", " World"]


@pytest.mark.asyncio
async def test_run_sse_stops_on_stopped_sentinel():
    mock_client = AsyncMock()
    sse_lines = _make_sse_lines("before stop") + [_stopped_line()] + _make_sse_lines("after stop")
    mock_client.stream = MagicMock(return_value=_fake_stream_ctx(sse_lines))
    bridge = _make_bridge(mock_client)

    chunks = [c async for c in bridge.run_sse("hi")]
    assert chunks == ["before stop"]


@pytest.mark.asyncio
async def test_run_sse_skips_non_data_lines():
    mock_client = AsyncMock()
    # SSE spec allows comment lines and blank lines
    sse_lines = [
        ": this is a comment",
        "",
        'data: {"content": {"role": "model", "parts": [{"text": "hi"}]}}',
    ]
    mock_client.stream = MagicMock(return_value=_fake_stream_ctx(sse_lines))
    bridge = _make_bridge(mock_client)

    chunks = [c async for c in bridge.run_sse("prompt")]
    assert chunks == ["hi"]


@pytest.mark.asyncio
async def test_run_sse_skips_events_without_text():
    mock_client = AsyncMock()
    # Tool-call event (no text) followed by a text event
    tool_event = {
        "content": {
            "role": "model",
            "parts": [{"function_call": {"name": "run_code", "args": {}}}],
        }
    }
    text_event = {"content": {"role": "model", "parts": [{"text": "done"}]}}
    sse_lines = [
        f"data: {json.dumps(tool_event)}",
        f"data: {json.dumps(text_event)}",
    ]
    mock_client.stream = MagicMock(return_value=_fake_stream_ctx(sse_lines))
    bridge = _make_bridge(mock_client)

    chunks = [c async for c in bridge.run_sse("go")]
    assert chunks == ["done"]


@pytest.mark.asyncio
async def test_run_sse_posts_correct_payload():
    mock_client = AsyncMock()
    mock_client.stream = MagicMock(return_value=_fake_stream_ctx([]))
    bridge = _make_bridge(mock_client)

    _ = [c async for c in bridge.run_sse("hello opensage")]

    mock_client.stream.assert_called_once()
    call_kwargs = mock_client.stream.call_args.kwargs
    payload = call_kwargs["json"]
    assert payload["new_message"]["parts"][0]["text"] == "hello opensage"
    assert payload["session_id"] == "testsession"
    assert payload["streaming"] is True


@pytest.mark.asyncio
async def test_run_sse_raises_on_http_error():
    mock_client = AsyncMock()
    mock_client.stream = MagicMock(return_value=_fake_stream_ctx([], status_code=500))
    bridge = _make_bridge(mock_client)

    with pytest.raises(Exception):
        _ = [c async for c in bridge.run_sse("hi")]


@pytest.mark.asyncio
async def test_run_sse_raises_on_error_event():
    """T-01b regression: SSE error events must raise, not be silently swallowed."""
    mock_client = AsyncMock()
    sse_lines = ['data: {"error": "model overloaded"}']
    mock_client.stream = MagicMock(return_value=_fake_stream_ctx(sse_lines))
    bridge = _make_bridge(mock_client)

    with pytest.raises(RuntimeError, match="model overloaded"):
        _ = [c async for c in bridge.run_sse("hi")]


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_posts_to_stop_turn():
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))
    bridge = _make_bridge(mock_client)

    await bridge.cancel()

    mock_client.post.assert_awaited_once()
    url_arg = mock_client.post.call_args.args[0]
    assert "stop_turn" in url_arg


@pytest.mark.asyncio
async def test_cancel_does_not_raise_on_error():
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=Exception("network error"))
    bridge = _make_bridge(mock_client)

    await bridge.cancel()  # should not raise


# ---------------------------------------------------------------------------
# is_running
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_running_true():
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value={"running": True})
    mock_client.get = AsyncMock(return_value=mock_resp)
    bridge = _make_bridge(mock_client)

    assert await bridge.is_running() is True


@pytest.mark.asyncio
async def test_is_running_false_on_error():
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=Exception("timeout"))
    bridge = _make_bridge(mock_client)

    assert await bridge.is_running() is False


# ---------------------------------------------------------------------------
# aclose
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aclose_closes_client():
    mock_client = AsyncMock()
    bridge = _make_bridge(mock_client)
    await bridge.aclose()
    mock_client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# _EchoBridge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_echo_bridge_health_check_true():
    bridge = _EchoBridge()
    assert await bridge.health_check() is True


@pytest.mark.asyncio
async def test_echo_bridge_run_sse_yields_message():
    bridge = _EchoBridge()
    chunks = [c async for c in bridge.run_sse("hello world")]
    assert chunks == ["hello world"]


@pytest.mark.asyncio
async def test_echo_bridge_run_sse_empty_string():
    bridge = _EchoBridge()
    chunks = [c async for c in bridge.run_sse("")]
    assert chunks == [""]


@pytest.mark.asyncio
async def test_echo_bridge_cancel_is_noop():
    bridge = _EchoBridge()
    await bridge.cancel()  # should not raise


@pytest.mark.asyncio
async def test_echo_bridge_is_running_false():
    bridge = _EchoBridge()
    assert await bridge.is_running() is False


@pytest.mark.asyncio
async def test_echo_bridge_aclose_is_noop():
    bridge = _EchoBridge()
    await bridge.aclose()  # should not raise


@pytest.mark.asyncio
async def test_echo_bridge_create_session_is_noop():
    bridge = _EchoBridge()
    await bridge.create_session()  # should not raise


# ---------------------------------------------------------------------------
# T-02a: health_check error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_false_on_timeout_exception():
    import httpx

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("read timed out"))
    bridge = _make_bridge(mock_client)
    assert await bridge.health_check() is False


@pytest.mark.asyncio
async def test_health_check_false_on_generic_exception():
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=ValueError("unexpected"))
    bridge = _make_bridge(mock_client)
    assert await bridge.health_check() is False


# ---------------------------------------------------------------------------
# T-02b: create_session HTTP error propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_raises_on_4xx():
    import httpx

    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "Client Error", request=MagicMock(), response=MagicMock(status_code=400)
        )
    )
    mock_client.post = AsyncMock(return_value=mock_resp)
    bridge = _make_bridge(mock_client)

    with pytest.raises(httpx.HTTPStatusError):
        await bridge.create_session()


@pytest.mark.asyncio
async def test_create_session_raises_on_5xx():
    import httpx

    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=MagicMock(status_code=500)
        )
    )
    mock_client.post = AsyncMock(return_value=mock_resp)
    bridge = _make_bridge(mock_client)

    with pytest.raises(httpx.HTTPStatusError):
        await bridge.create_session()


# ---------------------------------------------------------------------------
# T-02c: run_sse data parsing edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_sse_skips_empty_data_string():
    mock_client = AsyncMock()
    sse_lines = ["data: ", 'data: {"content":{"role":"model","parts":[{"text":"ok"}]}}']
    mock_client.stream = MagicMock(return_value=_fake_stream_ctx(sse_lines))
    bridge = _make_bridge(mock_client)

    chunks = [c async for c in bridge.run_sse("msg")]
    assert chunks == ["ok"]


@pytest.mark.asyncio
async def test_run_sse_logs_warning_on_malformed_json():
    mock_client = AsyncMock()
    sse_lines = ["data: {not json}"]
    mock_client.stream = MagicMock(return_value=_fake_stream_ctx(sse_lines))
    bridge = _make_bridge(mock_client)

    chunks = [c async for c in bridge.run_sse("msg")]
    assert chunks == []


@pytest.mark.asyncio
async def test_run_sse_does_not_yield_empty_string_text():
    mock_client = AsyncMock()
    event = {"content": {"role": "model", "parts": [{"text": ""}]}}
    sse_lines = [f"data: {json.dumps(event)}"]
    mock_client.stream = MagicMock(return_value=_fake_stream_ctx(sse_lines))
    bridge = _make_bridge(mock_client)

    chunks = [c async for c in bridge.run_sse("msg")]
    assert chunks == []


# ---------------------------------------------------------------------------
# T-02d: run_sse payload correctness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_sse_sends_app_name_in_payload():
    mock_client = AsyncMock()
    mock_client.stream = MagicMock(return_value=_fake_stream_ctx([]))
    bridge = _make_bridge(mock_client)

    _ = [c async for c in bridge.run_sse("hi")]

    payload = mock_client.stream.call_args.kwargs["json"]
    assert payload["app_name"] == "opensage"


@pytest.mark.asyncio
async def test_run_sse_sends_user_id_in_payload():
    mock_client = AsyncMock()
    mock_client.stream = MagicMock(return_value=_fake_stream_ctx([]))
    bridge = _make_bridge(mock_client)

    _ = [c async for c in bridge.run_sse("hi")]

    payload = mock_client.stream.call_args.kwargs["json"]
    assert payload["user_id"] == "user"


@pytest.mark.asyncio
async def test_run_sse_sends_custom_app_name():
    mock_client = AsyncMock()
    mock_client.stream = MagicMock(return_value=_fake_stream_ctx([]))
    with patch("opensage_acp.bridge.httpx.AsyncClient", return_value=mock_client):
        bridge = OpenSageHttpBridge(
            base_url="http://127.0.0.1:8100",
            session_id="testsession",
            app_name="myapp",
        )

    _ = [c async for c in bridge.run_sse("hi")]

    payload = mock_client.stream.call_args.kwargs["json"]
    assert payload["app_name"] == "myapp"


# ---------------------------------------------------------------------------
# T-02e: cancel session_id query param
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_sends_session_id_as_query_param():
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))
    bridge = _make_bridge(mock_client)

    await bridge.cancel()

    assert mock_client.post.call_args.kwargs["params"]["session_id"] == "testsession"


# ---------------------------------------------------------------------------
# T-02f: is_running edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_running_false_when_running_key_missing():
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value={})
    mock_client.get = AsyncMock(return_value=mock_resp)
    bridge = _make_bridge(mock_client)

    assert await bridge.is_running() is False


@pytest.mark.asyncio
async def test_is_running_sends_session_id_as_query_param():
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value={"running": False})
    mock_client.get = AsyncMock(return_value=mock_resp)
    bridge = _make_bridge(mock_client)

    await bridge.is_running()

    assert mock_client.get.call_args.kwargs["params"]["session_id"] == "testsession"


# ---------------------------------------------------------------------------
# T-02g: _extract_text_from_event non-dict content
# ---------------------------------------------------------------------------


def test_extract_text_with_string_content():
    event = {"content": "plain string"}
    assert _extract_text_from_event(event) is None


def test_extract_text_with_list_content():
    event = {"content": [{"text": "x"}]}
    assert _extract_text_from_event(event) is None


def test_extract_text_with_null_content():
    event = {"content": None}
    assert _extract_text_from_event(event) is None


# ---------------------------------------------------------------------------
# T-10b: Function call / function response events — log and skip
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).parent / "fixtures" / "adk_events"


def test_extract_text_function_call_returns_none():
    """Function call events yield no text."""
    event = json.loads((_FIXTURES / "function_call.json").read_text())
    assert _extract_text_from_event(event) is None


def test_extract_text_function_response_returns_none():
    """Function response events yield no text."""
    event = json.loads((_FIXTURES / "function_response.json").read_text())
    assert _extract_text_from_event(event) is None


# ---------------------------------------------------------------------------
# T-10c: Partial / final flag handling
# ---------------------------------------------------------------------------


def test_extract_text_partial_event_yields_text():
    """Partial text events yield their text content."""
    event = json.loads((_FIXTURES / "text_partial.json").read_text())
    assert event["partial"] is True
    assert _extract_text_from_event(event) == "The vulnerability exists in the"


def test_extract_text_final_event_yields_text():
    """Final text events yield their text content."""
    event = json.loads((_FIXTURES / "text_final.json").read_text())
    assert event["partial"] is False
    assert _extract_text_from_event(event) == " input validation module."
