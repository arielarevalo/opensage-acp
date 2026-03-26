#!/usr/bin/env python3
"""
Echo adapter — ACP (JSON-RPC 2.0 over NDJSON) protocol test oracle.

Implements the minimum ACP conformance profile v1 required methods:
  - initialize
  - session/new
  - session/prompt  (echoes last user text as streaming session/update chunks)
  - session/cancel  (gracefully acknowledges)

Used as a test oracle to verify protocol conformance before wiring up
OpenSage.

Usage:
    echo '{"jsonrpc":"2.0","id":"1","method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{},"clientInfo":{"name":"test","version":"0"}}}' | python scripts/echo_adapter.py
"""

from __future__ import annotations

import json
import sys
import uuid

JSONRPC = "2.0"
PROTOCOL_VERSION = 1


def read_msg():
    line = sys.stdin.readline()
    if not line:
        raise EOFError
    return json.loads(line)


def write_msg(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def response(id_, result):
    write_msg({"jsonrpc": JSONRPC, "id": id_, "result": result})


def error_response(id_, code, message):
    write_msg({"jsonrpc": JSONRPC, "id": id_, "error": {"code": code, "message": message}})


def notification(method, params=None):
    msg = {"jsonrpc": JSONRPC, "method": method}
    if params is not None:
        msg["params"] = params
    write_msg(msg)


# In-memory session store: sessionId → {"messages": [...], "cancelled": bool}
_sessions: dict[str, dict] = {}
_active_prompt: dict[str, str] = {}   # sessionId → pending prompt request id


def handle_initialize(id_, params):
    response(id_, {
        "protocolVersion": PROTOCOL_VERSION,
        "agentCapabilities": {"loadSession": False},
    })


def handle_session_new(id_, params):
    cwd = params.get("cwd") if params else None
    if not isinstance(cwd, str):
        error_response(id_, -32602, "cwd must be a non-null string")
        return
    session_id = str(uuid.uuid4())
    _sessions[session_id] = {"messages": [], "cancelled": False}
    response(id_, {"sessionId": session_id})


def handle_session_prompt(id_, params):
    session_id = params.get("sessionId", "") if params else ""
    if session_id not in _sessions:
        error_response(id_, -32602, f"unknown sessionId: {session_id!r}")
        return

    session = _sessions[session_id]
    session["cancelled"] = False
    prompt_blocks = params.get("prompt", []) if params else []

    # Collect text from all text blocks
    text = " ".join(
        b.get("text", "") for b in prompt_blocks if b.get("type") == "text"
    ).strip() or "(empty)"

    session["messages"].append({"role": "user", "content": text})

    # Check if cancelled before we start
    if session["cancelled"]:
        response(id_, {"stopReason": "cancelled"})
        return

    # Stream the echo back word by word
    words = text.split()
    for i, word in enumerate(words):
        if session["cancelled"]:
            response(id_, {"stopReason": "cancelled"})
            return
        chunk_text = word + (" " if i < len(words) - 1 else "")
        notification("session/update", {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": chunk_text},
        })

    # If no words, still emit at least one update
    if not words:
        notification("session/update", {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": "(empty)"},
        })

    session["messages"].append({"role": "assistant", "content": text})
    response(id_, {"stopReason": "end_turn"})


def handle_session_cancel(id_, params):
    session_id = params.get("sessionId", "") if params else ""
    if session_id in _sessions:
        _sessions[session_id]["cancelled"] = True
    response(id_, {})


def main():
    initialized = False

    while True:
        try:
            msg = read_msg()
        except EOFError:
            break
        except json.JSONDecodeError as exc:
            sys.stderr.write(f"JSON decode error: {exc}\n")
            continue

        id_ = msg.get("id")
        method = msg.get("method")
        params = msg.get("params")

        # Notifications (no id, no response needed)
        if id_ is None and method:
            sys.stderr.write(f"echo_adapter: notification '{method}' (ignored)\n")
            continue

        # Must initialize first
        if method != "initialize" and not initialized:
            error_response(id_, -32600, "Must call initialize first")
            continue

        if method == "initialize":
            handle_initialize(id_, params)
            initialized = True
        elif method == "session/new":
            handle_session_new(id_, params)
        elif method == "session/prompt":
            handle_session_prompt(id_, params)
        elif method == "session/cancel":
            handle_session_cancel(id_, params)
        elif method is None:
            sys.stderr.write("echo_adapter: received a response (ignored)\n")
        else:
            error_response(id_, -32601, f"Method not found: {method!r}")


if __name__ == "__main__":
    main()
