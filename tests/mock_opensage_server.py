"""
Mock opensage-web HTTP server for end-to-end integration testing.

Mimics the HTTP API that OpenSageHttpBridge talks to:

  GET  /                                          — health check (returns 200)
  GET  /list-apps                                 — list apps
  POST /apps/{app_name}/users/{user_id}/sessions  — create / get session
  POST /run_sse                                   — SSE stream of ADK text events
  POST /control/stop_turn                         — cancel running turn
  GET  /control/turn_state                        — query turn status
"""

from __future__ import annotations

import json
import logging
import socket
import threading
from typing import Any

import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

log = logging.getLogger(__name__)


def create_app(
    *,
    text_chunks: list[str] | None = None,
    session_id: str = "test-session-123",
    app_name: str = "opensage",
    error_chunk: str | None = None,
    inject_stop_sentinel: bool = False,
    chunk_delay_ms: int = 0,
    run_sse_status: int = 200,
) -> FastAPI:
    """Return a FastAPI app that mimics opensage-web.

    Args:
        text_chunks: The text fragments to stream from ``/run_sse``.
                     Each fragment is wrapped in an ADK event envelope.
                     Defaults to ``["Hello", " world", "!"]``.
        session_id:  The session ID returned by ``/apps/.../sessions``.
        app_name:    Expected app name for validation.
        error_chunk: If set, emit an error SSE event before text chunks.
        inject_stop_sentinel: If True, emit a stop sentinel as the last event.
        chunk_delay_ms: Optional delay between chunks (milliseconds).
        run_sse_status: HTTP status code for /run_sse (default 200).
    """
    if text_chunks is None:
        text_chunks = ["Hello", " world", "!"]

    _app = FastAPI()
    _app.state.turn_active = False
    _app.state.last_run_sse_body: dict[str, Any] | None = None

    @_app.get("/")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @_app.get("/list-apps")
    async def list_apps() -> list[str]:
        return [app_name]

    @_app.post("/apps/{app_name_param}/users/{user_id}/sessions")
    async def create_session(app_name_param: str, user_id: str) -> dict[str, str]:
        return {"id": session_id}

    @_app.post("/run_sse", response_model=None)
    async def run_sse(request: Request) -> StreamingResponse | JSONResponse:
        """Stream ADK text events as SSE, one chunk at a time."""
        body = await request.json()
        _app.state.last_run_sse_body = body

        # Validate session_id if present in body (log mismatch for debugging)
        body_session_id = body.get("session_id")
        if body_session_id is not None and body_session_id != session_id:
            log.debug(
                "run_sse: session_id mismatch: body=%s configured=%s",
                body_session_id,
                session_id,
            )

        # Validate app_name if present in body
        body_app_name = body.get("app_name")
        if body_app_name is not None and body_app_name != app_name:
            return JSONResponse(
                status_code=404,
                content={"error": "app not found"},
            )

        # Return error status if configured
        if run_sse_status != 200:
            return JSONResponse(
                status_code=run_sse_status,
                content={"error": "server error"},
            )

        async def _stream() -> Any:
            import asyncio

            _app.state.turn_active = True

            # Emit error event if configured
            if error_chunk is not None:
                yield f"data: {json.dumps({'error': error_chunk})}\n\n"

            total = len(text_chunks)
            for i, chunk in enumerate(text_chunks):
                if chunk_delay_ms > 0:
                    await asyncio.sleep(chunk_delay_ms / 1000.0)

                is_last = i == total - 1
                event: dict[str, Any] = {
                    "content": {"role": "model", "parts": [{"text": chunk}]},
                    "author": "root_agent",
                    "partial": not is_last,
                }
                yield f"data: {json.dumps(event)}\n\n"

            # Emit stop sentinel if configured
            if inject_stop_sentinel:
                sentinel = {"stopped": True, "message": "Turn stopped by UI"}
                yield f"data: {json.dumps(sentinel)}\n\n"

            _app.state.turn_active = False

        return StreamingResponse(_stream(), media_type="text/event-stream")

    @_app.post("/control/stop_turn")
    async def stop_turn(
        session_id_param: str | None = Query(default=None, alias="session_id"),
    ) -> dict[str, Any]:
        log.debug("stop_turn: session_id=%s", session_id_param)
        if not _app.state.turn_active:
            return {"stopped": False, "message": "no active turn"}
        return {"stopped": True}

    @_app.get("/control/turn_state")
    async def turn_state(
        session_id_param: str | None = Query(default=None, alias="session_id"),
    ) -> dict[str, bool]:
        return {"running": _app.state.turn_active}

    return _app


def find_free_port() -> int:
    """Find an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class MockOpensageServer:
    """Manages a mock opensage-web server running in a background daemon thread.

    Usage::

        srv = MockOpensageServer()
        srv.start()
        # ... make HTTP calls to srv.url ...
        srv.stop()

    Or use the ``mock_server`` fixture in ``test_e2e_integration.py``.
    """

    def __init__(
        self,
        *,
        text_chunks: list[str] | None = None,
        session_id: str = "test-session-123",
        app_name: str = "opensage",
        error_chunk: str | None = None,
        inject_stop_sentinel: bool = False,
        chunk_delay_ms: int = 0,
        run_sse_status: int = 200,
    ) -> None:
        self.port: int = find_free_port()
        self.url: str = f"http://127.0.0.1:{self.port}"
        self.session_id: str = session_id
        self._app: FastAPI = create_app(
            text_chunks=text_chunks,
            session_id=session_id,
            app_name=app_name,
            error_chunk=error_chunk,
            inject_stop_sentinel=inject_stop_sentinel,
            chunk_delay_ms=chunk_delay_ms,
            run_sse_status=run_sse_status,
        )
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def last_run_sse_body(self) -> dict[str, Any] | None:
        """Return the last request body received by /run_sse."""
        return self._app.state.last_run_sse_body

    def start(self) -> None:
        """Start the server in a background daemon thread."""
        config = uvicorn.Config(
            self._app,
            host="127.0.0.1",
            port=self.port,
            log_level="error",
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the server to exit and wait for the thread to finish."""
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
