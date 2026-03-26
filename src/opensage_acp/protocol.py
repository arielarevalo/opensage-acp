"""
acpx / ACP protocol — JSON-RPC 2.0 over NDJSON (stdin/stdout).

Wire format (from acpx reference implementation):
  - Every message is one JSON line terminated by \\n.
  - Three shapes: Request (has id + method), Notification (no id, has method),
    Response (has id + result|error).
  - Standard JSON-RPC 2.0 error codes.

See docs/acpx_protocol.md for the full protocol reference.
"""

from __future__ import annotations

import json
import sys
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Raw I/O helpers (synchronous)
# ---------------------------------------------------------------------------


def read_message(stream=None) -> dict[str, Any]:
    """Read one JSON-lines message from *stream* (defaults to stdin)."""
    stream = stream or sys.stdin
    line = stream.readline()
    if not line:
        raise EOFError("stdin closed")
    return json.loads(line)


def write_message(msg: dict[str, Any], stream=None) -> None:
    """Write one JSON-lines message to *stream* (defaults to stdout)."""
    stream = stream or sys.stdout
    stream.write(json.dumps(msg) + "\n")
    stream.flush()


# ---------------------------------------------------------------------------
# Async I/O helpers (anyio)
# ---------------------------------------------------------------------------


async def async_read_message() -> dict[str, Any]:
    """Read one JSON-lines message from stdin asynchronously (anyio)."""
    import anyio

    line: str = await anyio.to_thread.run_sync(sys.stdin.readline)
    if not line:
        raise EOFError("stdin closed")
    return json.loads(line)


async def async_write_message(msg: dict[str, Any]) -> None:
    """Write one JSON-lines message to stdout asynchronously (anyio)."""
    import anyio

    serialised = json.dumps(msg) + "\n"
    await anyio.to_thread.run_sync(lambda: (sys.stdout.write(serialised), sys.stdout.flush()))


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 helpers (dict constructors — kept for backward compat)
# ---------------------------------------------------------------------------

JSONRPC = "2.0"


def make_request(
    id: str | int | None, method: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 request."""
    msg: dict[str, Any] = {"jsonrpc": JSONRPC, "id": id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def make_notification(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 notification (no id, adapter→client)."""
    msg: dict[str, Any] = {"jsonrpc": JSONRPC, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def make_response(id: str | int | None, result: Any) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 success response."""
    return {"jsonrpc": JSONRPC, "id": id, "result": result}


def make_error_response(
    id: str | int | None,
    code: int,
    message: str,
    data: Any = None,
) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 error response."""
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC, "id": id, "error": error}


def is_request(msg: dict[str, Any]) -> bool:
    return "id" in msg and "method" in msg


def is_notification(msg: dict[str, Any]) -> bool:
    return "id" not in msg and "method" in msg


def is_response(msg: dict[str, Any]) -> bool:
    return "id" in msg and ("result" in msg or "error" in msg)


# ---------------------------------------------------------------------------
# Standard JSON-RPC error codes
# ---------------------------------------------------------------------------

ERR_PARSE_ERROR = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL_ERROR = -32603


# ---------------------------------------------------------------------------
# ACP method names
# ---------------------------------------------------------------------------

METHOD_INITIALIZE = "initialize"
METHOD_SESSION_NEW = "session/new"
METHOD_SESSION_PROMPT = "session/prompt"
METHOD_SESSION_UPDATE = "session/update"  # notification (adapter → client)
METHOD_SESSION_CANCEL = "session/cancel"
METHOD_SESSION_LOAD = "session/load"
METHOD_SESSION_SET_MODE = "session/set_mode"
METHOD_SESSION_SET_CONFIG = "session/set_config_option"

# Adapter-initiated (adapter → client requests)
METHOD_FS_READ = "fs/read_text_file"
METHOD_FS_WRITE = "fs/write_text_file"
METHOD_TERMINAL_CREATE = "terminal/create"
METHOD_TERMINAL_OUTPUT = "terminal/output"
METHOD_TERMINAL_WAIT = "terminal/wait_for_exit"
METHOD_TERMINAL_KILL = "terminal/kill"
METHOD_TERMINAL_RELEASE = "terminal/release"
METHOD_REQUEST_PERMISSION = "request_permission"
METHOD_AUTHENTICATE = "authenticate"


# ---------------------------------------------------------------------------
# Pydantic v2 models — typed, validated message shapes
# ---------------------------------------------------------------------------


class RpcRequest(BaseModel):
    """JSON-RPC 2.0 Request (client → adapter, has id + method)."""

    jsonrpc: Literal["2.0"]
    id: str | int | None
    method: str
    params: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _must_have_id(cls, data: Any) -> Any:
        if isinstance(data, dict) and "id" not in data:
            raise ValueError("Request must have 'id'")
        return data


class RpcNotification(BaseModel):
    """JSON-RPC 2.0 Notification (no id, has method)."""

    jsonrpc: Literal["2.0"]
    method: str
    params: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _must_not_have_id(cls, data: Any) -> Any:
        if isinstance(data, dict) and "id" in data:
            raise ValueError("Notification must not have 'id'")
        return data


class RpcErrorObject(BaseModel):
    code: int
    message: str
    data: Any = None


class RpcSuccessResponse(BaseModel):
    """JSON-RPC 2.0 success response (id + result)."""

    jsonrpc: Literal["2.0"]
    id: str | int | None
    result: Any


class RpcErrorResponse(BaseModel):
    """JSON-RPC 2.0 error response (id + error)."""

    jsonrpc: Literal["2.0"]
    id: str | int | None
    error: RpcErrorObject


# Discriminated union for parse_message
AnyRpcMessage = Annotated[
    RpcRequest | RpcNotification | RpcSuccessResponse | RpcErrorResponse,
    Field(discriminator=None),  # manual dispatch below
]


def parse_message(
    raw: dict[str, Any],
) -> RpcRequest | RpcNotification | RpcSuccessResponse | RpcErrorResponse:
    """Parse and validate a raw dict into a typed JSON-RPC 2.0 message.

    Classification order (matches JSON-RPC 2.0 spec):
      1. Has 'error' key  → RpcErrorResponse
      2. Has 'result' key → RpcSuccessResponse
      3. Has 'id' key     → RpcRequest
      4. Otherwise        → RpcNotification
    """
    if "error" in raw:
        return RpcErrorResponse.model_validate(raw)
    if "result" in raw:
        return RpcSuccessResponse.model_validate(raw)
    if "id" in raw:
        return RpcRequest.model_validate(raw)
    return RpcNotification.model_validate(raw)


# ---------------------------------------------------------------------------
# ACP-specific message constructors
# ---------------------------------------------------------------------------


def make_initialize_result(
    protocol_version: int,
    load_session: bool = False,
    auth_methods: list | None = None,
) -> dict[str, Any]:
    """Build the result payload for an `initialize` response."""
    result: dict[str, Any] = {
        "protocolVersion": protocol_version,
        "agentCapabilities": {"loadSession": load_session},
    }
    if auth_methods:
        result["authMethods"] = auth_methods
    return result


def make_session_new_result(
    session_id: str, runtime_session_id: str | None = None
) -> dict[str, Any]:
    """Build the result payload for a `session/new` response."""
    result: dict[str, Any] = {"sessionId": session_id}
    if runtime_session_id:
        result["_meta"] = {"runtimeSessionId": runtime_session_id}
    return result


def make_session_update_notification(
    session_update: str,
    content: dict[str, Any],
) -> dict[str, Any]:
    """Build a `session/update` notification (streaming chunk)."""
    return make_notification(
        METHOD_SESSION_UPDATE,
        {"sessionUpdate": session_update, "content": content},
    )


def make_text_chunk_notification(text: str) -> dict[str, Any]:
    """Convenience: build a `session/update` text-chunk notification."""
    return make_session_update_notification(
        "agent_message_chunk",
        {"type": "text", "text": text},
    )


def make_prompt_result(stop_reason: str = "end_turn") -> dict[str, Any]:
    """Build the result payload for a `session/prompt` response."""
    return {"stopReason": stop_reason}


STOP_REASON_END_TURN = "end_turn"
STOP_REASON_COMPLETED = "completed"
STOP_REASON_DONE = "done"
STOP_REASON_CANCELLED = "cancelled"
STOP_REASON_FAILED = "failed"

SUCCESSFUL_STOP_REASONS = {STOP_REASON_END_TURN, STOP_REASON_COMPLETED, STOP_REASON_DONE}
