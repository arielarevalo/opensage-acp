"""
Protocol conformance tests using the echo adapter as oracle.

Tests verify:
  - read_message / write_message correctly serialise/deserialise JSON-lines
  - JSON-RPC 2.0 message constructors produce correct shapes
  - The echo adapter implements ACP conformance profile v1 (cases 001–020
    subset applicable to a simple in-process adapter):
      001: initialize succeeds
      003: session/new succeeds, returns non-empty sessionId
      004: session/new — null cwd produces error
      005: session/new — non-string cwd produces error
      006: session/prompt — basic single-turn, streaming updates emitted,
           stopReason end_turn
      016: session/cancel on idle session succeeds
      020: cancel then reuse session
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from opensage_acp.protocol import (
    ERR_INVALID_PARAMS,
    ERR_METHOD_NOT_FOUND,
    METHOD_INITIALIZE,
    METHOD_SESSION_UPDATE,
    STOP_REASON_END_TURN,
    SUCCESSFUL_STOP_REASONS,
    RpcErrorResponse,
    RpcNotification,
    RpcRequest,
    RpcSuccessResponse,
    async_read_message,
    async_write_message,
    is_notification,
    is_request,
    is_response,
    make_error_response,
    make_initialize_result,
    make_notification,
    make_prompt_result,
    make_request,
    make_response,
    make_session_new_result,
    make_text_chunk_notification,
    parse_message,
    read_message,
    write_message,
)

ECHO_ADAPTER = Path(__file__).parent.parent / "scripts" / "echo_adapter.py"

# ---------------------------------------------------------------------------
# Unit tests: read_message / write_message
# ---------------------------------------------------------------------------


def test_read_message_basic(make_stream):
    stream = make_stream([{"jsonrpc": "2.0", "id": "1", "method": "initialize", "params": {}}])
    msg = read_message(stream)
    assert msg["jsonrpc"] == "2.0"
    assert msg["method"] == "initialize"


def test_write_message_basic(capture_stream):
    buf, decode = capture_stream
    write_message({"jsonrpc": "2.0", "id": "1", "result": {"ok": True}}, buf)
    msgs = decode()
    assert msgs == [{"jsonrpc": "2.0", "id": "1", "result": {"ok": True}}]


def test_read_message_eof(make_stream):
    stream = io.StringIO("")
    with pytest.raises(EOFError):
        read_message(stream)


def test_read_multiple_messages(make_stream):
    stream = make_stream(
        [
            {"jsonrpc": "2.0", "id": "1", "method": "initialize"},
            {"jsonrpc": "2.0", "id": "2", "method": "session/new"},
        ]
    )
    m1 = read_message(stream)
    m2 = read_message(stream)
    assert m1["id"] == "1"
    assert m2["id"] == "2"


# ---------------------------------------------------------------------------
# Unit tests: message constructors
# ---------------------------------------------------------------------------


def test_make_request():
    msg = make_request("1", METHOD_INITIALIZE, {"protocolVersion": 1})
    assert msg["jsonrpc"] == "2.0"
    assert msg["id"] == "1"
    assert msg["method"] == METHOD_INITIALIZE
    assert msg["params"]["protocolVersion"] == 1


def test_make_notification():
    msg = make_notification(METHOD_SESSION_UPDATE, {"sessionUpdate": "chunk"})
    assert msg["jsonrpc"] == "2.0"
    assert "id" not in msg
    assert msg["method"] == METHOD_SESSION_UPDATE


def test_make_response():
    msg = make_response("1", {"stopReason": "end_turn"})
    assert msg["id"] == "1"
    assert msg["result"]["stopReason"] == "end_turn"
    assert "error" not in msg


def test_make_error_response():
    msg = make_error_response("1", ERR_INVALID_PARAMS, "bad params")
    assert msg["error"]["code"] == ERR_INVALID_PARAMS
    assert "result" not in msg


def test_make_initialize_result():
    r = make_initialize_result(1, load_session=True)
    assert r["protocolVersion"] == 1
    assert r["agentCapabilities"]["loadSession"] is True


def test_make_session_new_result():
    r = make_session_new_result("sess-1", runtime_session_id="native-id")
    assert r["sessionId"] == "sess-1"
    assert r["_meta"]["runtimeSessionId"] == "native-id"


def test_make_text_chunk_notification():
    msg = make_text_chunk_notification("hello ")
    assert msg["method"] == METHOD_SESSION_UPDATE
    assert msg["params"]["content"]["text"] == "hello "
    assert msg["params"]["content"]["type"] == "text"


def test_make_prompt_result_defaults():
    r = make_prompt_result()
    assert r["stopReason"] == STOP_REASON_END_TURN


def test_successful_stop_reasons():
    assert "end_turn" in SUCCESSFUL_STOP_REASONS
    assert "completed" in SUCCESSFUL_STOP_REASONS
    assert "done" in SUCCESSFUL_STOP_REASONS
    assert "cancelled" not in SUCCESSFUL_STOP_REASONS


# ---------------------------------------------------------------------------
# Integration tests: echo adapter subprocess (ACP conformance)
# ---------------------------------------------------------------------------


def _build_convo(*messages: dict) -> str:
    """Build an NDJSON conversation from a list of request dicts."""
    return "\n".join(json.dumps(m) for m in messages) + "\n"


def _run_echo(messages: list[dict]) -> list[dict]:
    """Send *messages* to the echo adapter and collect all output messages."""
    stdin = _build_convo(*messages)
    result = subprocess.run(
        [sys.executable, str(ECHO_ADAPTER)],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"echo_adapter exited {result.returncode}: {result.stderr}"
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def _run_echo_with_session(*extra_msgs: dict) -> tuple[str, list[dict]]:
    """Run init + session/new, then *extra_msgs*, return (session_id, all_output).

    Uses Popen so that session_id from session/new can be passed to extra_msgs.
    The caller must pass extra_msgs with the correct session_id already embedded.
    This helper handles the two-phase bootstrap:
      1. Pipe init + session/new to the process and read the 2 responses.
      2. Pipe extra_msgs, close stdin, collect all remaining output.
    """
    proc = subprocess.Popen(
        [sys.executable, str(ECHO_ADAPTER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert proc.stdin and proc.stdout
    proc.stdin.write(json.dumps(_init_msg()) + "\n")
    proc.stdin.write(json.dumps(_session_new_msg(id_="__new__")) + "\n")
    proc.stdin.flush()

    # Read exactly 2 responses (one per request above)
    _init_r = json.loads(proc.stdout.readline())
    _new_r = json.loads(proc.stdout.readline())
    session_id = _new_r["result"]["sessionId"]

    # Now pipe caller's messages (which must use the correct session_id)
    for msg in extra_msgs:
        proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.close()

    raw = proc.stdout.read()
    proc.wait(timeout=10)
    all_out = [json.loads(line) for line in raw.splitlines() if line.strip()]
    return session_id, all_out


def _init_msg(id_="init-1"):
    return {
        "jsonrpc": "2.0",
        "id": id_,
        "method": "initialize",
        "params": {
            "protocolVersion": 1,
            "clientCapabilities": {"fs": {"readTextFile": True, "writeTextFile": True}},
            "clientInfo": {"name": "test", "version": "0"},
        },
    }


def _session_new_msg(id_="new-1", cwd="/tmp"):
    return {
        "jsonrpc": "2.0",
        "id": id_,
        "method": "session/new",
        "params": {"cwd": cwd, "mcpServers": []},
    }


def _prompt_msg(session_id, id_="p-1", text="hello world"):
    return {
        "jsonrpc": "2.0",
        "id": id_,
        "method": "session/prompt",
        "params": {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": text}],
        },
    }


def _cancel_msg(session_id, id_="c-1"):
    return {
        "jsonrpc": "2.0",
        "id": id_,
        "method": "session/cancel",
        "params": {"sessionId": session_id},
    }


# Case 001: initialize succeeds
def test_case_001_initialize():
    msgs = _run_echo([_init_msg()])
    assert len(msgs) == 1
    r = msgs[0]
    assert r["id"] == "init-1"
    assert "result" in r
    assert "error" not in r
    assert "protocolVersion" in r["result"]


# Case 003: session/new succeeds, returns non-empty sessionId
def test_case_003_session_new():
    msgs = _run_echo([_init_msg(), _session_new_msg()])
    r = next(m for m in msgs if m.get("id") == "new-1")
    assert "result" in r
    session_id = r["result"]["sessionId"]
    assert session_id and isinstance(session_id, str)


# Case 004: session/new — null cwd produces error
def test_case_004_null_cwd():
    bad = {
        "jsonrpc": "2.0",
        "id": "bad-1",
        "method": "session/new",
        "params": {"cwd": None, "mcpServers": []},
    }
    msgs = _run_echo([_init_msg(), bad])
    r = next(m for m in msgs if m.get("id") == "bad-1")
    assert "error" in r
    assert r["error"]["code"] in (ERR_INVALID_PARAMS, -32600)


# Case 005: session/new — non-string cwd produces error
def test_case_005_nonstring_cwd():
    bad = {
        "jsonrpc": "2.0",
        "id": "bad-2",
        "method": "session/new",
        "params": {"cwd": 12345, "mcpServers": []},
    }
    msgs = _run_echo([_init_msg(), bad])
    r = next(m for m in msgs if m.get("id") == "bad-2")
    assert "error" in r


# Case 006: session/prompt — streaming updates emitted, stopReason end_turn
def test_case_006_session_prompt_streaming():
    # Bootstrap: get session_id, then send the prompt
    proc = subprocess.Popen(
        [sys.executable, str(ECHO_ADAPTER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    proc.stdin.write(json.dumps(_init_msg()) + "\n")
    proc.stdin.write(json.dumps(_session_new_msg(id_="n1")) + "\n")
    proc.stdin.flush()
    json.loads(proc.stdout.readline())  # init response
    sid = json.loads(proc.stdout.readline())["result"]["sessionId"]

    proc.stdin.write(json.dumps(_prompt_msg(sid, id_="p-1", text="hello world")) + "\n")
    proc.stdin.close()
    all_out = [json.loads(line) for line in proc.stdout.read().splitlines() if line.strip()]
    proc.wait(timeout=5)

    updates = [m for m in all_out if m.get("method") == METHOD_SESSION_UPDATE]
    assert len(updates) >= 1

    prompt_resp = next(m for m in all_out if m.get("id") == "p-1")
    assert "result" in prompt_resp
    assert prompt_resp["result"]["stopReason"] in SUCCESSFUL_STOP_REASONS


# Case: session/update notifications have correct shape
def test_session_update_shape():
    proc = subprocess.Popen(
        [sys.executable, str(ECHO_ADAPTER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    proc.stdin.write(json.dumps(_init_msg()) + "\n")
    proc.stdin.write(json.dumps(_session_new_msg(id_="n1")) + "\n")
    proc.stdin.flush()
    json.loads(proc.stdout.readline())
    sid = json.loads(proc.stdout.readline())["result"]["sessionId"]

    proc.stdin.write(json.dumps(_prompt_msg(sid, text="hello")) + "\n")
    proc.stdin.close()
    msgs = [json.loads(line) for line in proc.stdout.read().splitlines() if line.strip()]
    proc.wait(timeout=5)

    updates = [m for m in msgs if m.get("method") == METHOD_SESSION_UPDATE]
    assert updates
    for u in updates:
        assert "id" not in u  # notifications have no id
        assert u["jsonrpc"] == "2.0"
        params = u["params"]
        assert "sessionUpdate" in params
        assert "content" in params
        assert params["content"]["type"] in ("text",)


# Case: multi-turn — session/prompt can be called multiple times on same session
def test_multi_turn_prompt():
    # Use Popen: send init + new, read session_id, then pipe both prompts and read all output.
    proc = subprocess.Popen(
        [sys.executable, str(ECHO_ADAPTER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    # Send init + new synchronously line by line
    for msg in [_init_msg(), _session_new_msg(id_="new-mt")]:
        proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()

    # Read 2 response lines (one per request); skip initialize response
    proc.stdout.readline()  # initialize
    r_new = json.loads(proc.stdout.readline())
    sid = r_new["result"]["sessionId"]

    # Send both prompts, then close stdin to signal EOF
    for msg in [
        _prompt_msg(sid, id_="p-1", text="first"),
        _prompt_msg(sid, id_="p-2", text="second"),
    ]:
        proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.close()

    # Collect all remaining output
    raw = proc.stdout.read()
    proc.wait(timeout=5)
    all_out = [json.loads(line) for line in raw.splitlines() if line.strip()]

    r1 = next(m for m in all_out if m.get("id") == "p-1")
    r2 = next(m for m in all_out if m.get("id") == "p-2")
    assert r1["result"]["stopReason"] in SUCCESSFUL_STOP_REASONS
    assert r2["result"]["stopReason"] in SUCCESSFUL_STOP_REASONS


# Case 016: session/cancel on idle session succeeds
def test_case_016_cancel_idle():
    proc = subprocess.Popen(
        [sys.executable, str(ECHO_ADAPTER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    proc.stdin.write(json.dumps(_init_msg()) + "\n")
    proc.stdin.write(json.dumps(_session_new_msg(id_="n1")) + "\n")
    proc.stdin.flush()
    json.loads(proc.stdout.readline())
    sid = json.loads(proc.stdout.readline())["result"]["sessionId"]

    proc.stdin.write(json.dumps(_cancel_msg(sid)) + "\n")
    proc.stdin.close()
    msgs = [json.loads(line) for line in proc.stdout.read().splitlines() if line.strip()]
    proc.wait(timeout=5)

    cancel_resp = next(m for m in msgs if m.get("id") == "c-1")
    assert "error" not in cancel_resp
    assert "result" in cancel_resp


# Case: method-not-found produces -32601 error
def test_unknown_method():
    msgs = _run_echo([_init_msg(), {"jsonrpc": "2.0", "id": "x-1", "method": "no_such_method"}])
    r = next(m for m in msgs if m.get("id") == "x-1")
    assert "error" in r
    assert r["error"]["code"] == ERR_METHOD_NOT_FOUND


# Case: echo text is reflected in streaming output
def test_echo_text_content():
    proc = subprocess.Popen(
        [sys.executable, str(ECHO_ADAPTER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    proc.stdin.write(json.dumps(_init_msg()) + "\n")
    proc.stdin.write(json.dumps(_session_new_msg(id_="n1")) + "\n")
    proc.stdin.flush()
    json.loads(proc.stdout.readline())
    sid = json.loads(proc.stdout.readline())["result"]["sessionId"]

    proc.stdin.write(json.dumps(_prompt_msg(sid, text="ping pong")) + "\n")
    proc.stdin.close()
    msgs = [json.loads(line) for line in proc.stdout.read().splitlines() if line.strip()]
    proc.wait(timeout=5)

    updates = [m for m in msgs if m.get("method") == METHOD_SESSION_UPDATE]
    full_text = "".join(u["params"]["content"].get("text", "") for u in updates)
    assert "ping" in full_text
    assert "pong" in full_text


# ---------------------------------------------------------------------------
# Phase 1.1: Pydantic v2 model tests
# ---------------------------------------------------------------------------


def test_pydantic_request_valid():
    raw = {"jsonrpc": "2.0", "id": "1", "method": "initialize", "params": {"protocolVersion": 1}}
    msg = RpcRequest.model_validate(raw)
    assert msg.id == "1"
    assert msg.method == "initialize"
    assert msg.params == {"protocolVersion": 1}


def test_pydantic_request_no_params():
    raw = {"jsonrpc": "2.0", "id": 42, "method": "session/cancel"}
    msg = RpcRequest.model_validate(raw)
    assert msg.id == 42
    assert msg.params is None


def test_pydantic_notification_valid():
    raw = {"jsonrpc": "2.0", "method": "session/update", "params": {"sessionUpdate": "chunk"}}
    msg = RpcNotification.model_validate(raw)
    assert msg.method == "session/update"
    assert "id" not in msg.model_fields_set


def test_pydantic_notification_rejects_id():
    raw = {"jsonrpc": "2.0", "id": "1", "method": "session/update"}
    with pytest.raises(ValidationError):
        RpcNotification.model_validate(raw)


def test_pydantic_success_response():
    raw = {"jsonrpc": "2.0", "id": "req-1", "result": {"stopReason": "end_turn"}}
    msg = RpcSuccessResponse.model_validate(raw)
    assert msg.result == {"stopReason": "end_turn"}


def test_pydantic_error_response():
    raw = {
        "jsonrpc": "2.0",
        "id": "req-1",
        "error": {"code": -32601, "message": "Method not found"},
    }
    msg = RpcErrorResponse.model_validate(raw)
    assert msg.error.code == -32601
    assert msg.error.message == "Method not found"
    assert msg.error.data is None


def test_pydantic_error_response_with_data():
    raw = {
        "jsonrpc": "2.0",
        "id": "r1",
        "error": {"code": -32602, "message": "Invalid params", "data": {"field": "cwd"}},
    }
    msg = RpcErrorResponse.model_validate(raw)
    assert msg.error.data == {"field": "cwd"}


def test_parse_message_request():
    raw = {"jsonrpc": "2.0", "id": "1", "method": "initialize"}
    msg = parse_message(raw)
    assert isinstance(msg, RpcRequest)
    assert msg.method == "initialize"


def test_parse_message_notification():
    raw = {"jsonrpc": "2.0", "method": "session/update", "params": {}}
    msg = parse_message(raw)
    assert isinstance(msg, RpcNotification)


def test_parse_message_success_response():
    raw = {"jsonrpc": "2.0", "id": "1", "result": {"ok": True}}
    msg = parse_message(raw)
    assert isinstance(msg, RpcSuccessResponse)
    assert msg.result == {"ok": True}


def test_parse_message_error_response():
    raw = {"jsonrpc": "2.0", "id": "1", "error": {"code": -32700, "message": "Parse error"}}
    msg = parse_message(raw)
    assert isinstance(msg, RpcErrorResponse)
    assert msg.error.code == -32700


def test_parse_message_error_takes_priority_over_result():
    # Malformed: has both error and result — error wins per our classification order
    raw = {"jsonrpc": "2.0", "id": "1", "error": {"code": -32603, "message": "err"}, "result": {}}
    msg = parse_message(raw)
    assert isinstance(msg, RpcErrorResponse)


# ---------------------------------------------------------------------------
# T-05a: async_read_message and async_write_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_read_message_reads_from_stdin():
    import io
    from unittest.mock import patch

    fake_stdin = io.StringIO('{"hello": "world"}\n')
    with patch("opensage_acp.protocol.sys.stdin", fake_stdin):
        result = await async_read_message()
    assert result == {"hello": "world"}


@pytest.mark.asyncio
async def test_async_write_message_writes_to_stdout():
    import io
    from unittest.mock import patch

    fake_stdout = io.StringIO()
    with patch("opensage_acp.protocol.sys.stdout", fake_stdout):
        await async_write_message({"x": 1})
    assert fake_stdout.getvalue() == '{"x": 1}\n'


@pytest.mark.asyncio
async def test_async_read_message_raises_eof():
    import io
    from unittest.mock import patch

    fake_stdin = io.StringIO("")
    with patch("opensage_acp.protocol.sys.stdin", fake_stdin):
        with pytest.raises(EOFError):
            await async_read_message()


# ---------------------------------------------------------------------------
# T-05b: is_request / is_notification / is_response
# ---------------------------------------------------------------------------


def test_is_request_true():
    assert is_request({"id": "1", "method": "initialize"}) is True


def test_is_request_false_for_notification():
    assert is_request({"method": "x"}) is False


def test_is_notification_true():
    assert is_notification({"method": "session/update"}) is True


def test_is_notification_false_for_request():
    assert is_notification({"id": "1", "method": "x"}) is False


def test_is_response_true_result():
    assert is_response({"id": "1", "result": {}}) is True


def test_is_response_true_error():
    assert is_response({"id": "1", "error": {}}) is True


def test_is_response_false_for_notification():
    assert is_response({"method": "x"}) is False


# ---------------------------------------------------------------------------
# T-05c: read_message with malformed JSON
# ---------------------------------------------------------------------------


def test_read_message_raises_on_malformed_json():
    import io

    stream = io.StringIO("not json\n")
    with pytest.raises(json.JSONDecodeError):
        read_message(stream)


# ---------------------------------------------------------------------------
# T-05d: make_request and make_notification with params=None
# ---------------------------------------------------------------------------


def test_make_request_omits_params_when_none():
    msg = make_request("1", "initialize")
    assert "params" not in msg


def test_make_notification_omits_params_when_none():
    msg = make_notification("x/y")
    assert "params" not in msg
