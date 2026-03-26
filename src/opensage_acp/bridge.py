"""
OpenSageHttpBridge — HTTP client for a single opensage-web instance.

Each ACP session gets one opensage-web process on a dedicated port.
This bridge connects to that process, creates the ADK session, streams
SSE responses from /run_sse, and can cancel running turns.

_EchoBridge is a test-only implementation that echoes the input back
without spawning any process or making any HTTP calls.  It is used when
OPENSAGE_ECHO_MODE=1.

OpenSage HTTP API summary:
  POST /run_sse                                  — stream prompt (SSE)
  POST /control/stop_turn?session_id=<id>        — cancel running turn
  GET  /control/turn_state?session_id=<id>       — check if turn is active
  POST /apps/{app}/users/{uid}/sessions          — create/get ADK session
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator

import httpx

log = logging.getLogger(__name__)

# Defaults that match what opensage-web uses internally
_APP_NAME = "opensage"
_USER_ID = "user"


def _extract_text_from_event(event: dict) -> str | None:
    """Pull text out of an ADK event JSON object.

    ADK event JSON shape (relevant fields)::

        {
          "content": {
            "role": "model",
            "parts": [{"text": "chunk of text"}]
          },
          "author": "root_agent",
          ...
        }

    Returns None if the event carries no text (tool calls, function results,
    cancel sentinels, etc.).
    """
    # Cancel sentinel sent by opensage when stop_turn is called
    if event.get("stopped"):
        return None

    content = event.get("content")
    if not isinstance(content, dict):
        return None

    parts = content.get("parts")
    if not isinstance(parts, list):
        return None

    chunks: list[str] = []
    for part in parts:
        if isinstance(part, dict):
            if "function_call" in part:
                fc = part["function_call"]
                log.debug("skipping function_call: %s", fc.get("name", "<unknown>"))
                continue
            if "function_response" in part:
                fr = part["function_response"]
                log.debug("skipping function_response: %s", fr.get("name", "<unknown>"))
                continue
            text = part.get("text")
            if text and isinstance(text, str):
                chunks.append(text)

    return "".join(chunks) if chunks else None


class OpenSageHttpBridge:
    """HTTP bridge to a single running opensage-web instance.

    Args:
        base_url:   Full URL of the opensage-web instance, e.g.
                    ``http://127.0.0.1:8100``.
        session_id: The ACP/opensage session ID passed to all API calls.
                    Should match the ``--session-id`` used when starting
                    the opensage-web process.
        timeout:    HTTP timeout in seconds (applied to non-streaming calls;
                    streaming calls use this as a read timeout).
        app_name:   opensage app name (default ``"opensage"``).
        user_id:    opensage user ID (default ``"user"``).
    """

    def __init__(
        self,
        base_url: str,
        session_id: str,
        timeout: float = 120.0,
        app_name: str = _APP_NAME,
        user_id: str = _USER_ID,
    ) -> None:
        self._base_url = base_url
        self._session_id = session_id
        self._app_name = app_name
        self._user_id = user_id
        self._client = httpx.AsyncClient(
            base_url=base_url,
            # Long read timeout for streaming; shorter connect timeout
            timeout=httpx.Timeout(timeout, connect=10.0),
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Return True if the opensage-web instance is responding."""
        try:
            resp = await self._client.get("/")
            return resp.status_code < 500
        except (httpx.ConnectError, httpx.TimeoutException):
            return False
        except Exception as exc:
            log.debug("health_check error: %s", exc)
            return False

    async def discover_app_name(self) -> None:
        """Auto-discover the app name registered in opensage-web.

        opensage-web derives the app name from the agent directory name, which
        varies by deployment.  This method queries ``GET /list-apps`` and uses
        the first result.  Only needed when the default ``app_name`` might not
        match (i.e. almost always in non-echo mode).
        """
        resp = await self._client.get("/list-apps")
        resp.raise_for_status()
        apps: list[str] = resp.json()
        if not apps:
            raise RuntimeError("opensage-web returned no apps from /list-apps")
        if self._app_name not in apps:
            log.info(
                "app_name override: %r not in %r, using %r",
                self._app_name,
                apps,
                apps[0],
            )
            self._app_name = apps[0]

    async def create_session(self) -> None:
        """Create (or retrieve) the ADK session in the opensage-web instance.

        Corresponds to:
            POST /apps/{app_name}/users/{user_id}/sessions

        Updates ``self._session_id`` to match the session ID returned by
        opensage-web, which may differ from the ACP session ID.
        """
        url = f"/apps/{self._app_name}/users/{self._user_id}/sessions"
        resp = await self._client.post(url, json={"state": {}})
        resp.raise_for_status()
        body = resp.json()
        if "id" in body:
            self._session_id = body["id"]
        log.debug("create_session %s → %d (session_id=%s)", url, resp.status_code, self._session_id)

    async def aclose(self) -> None:
        """Close the underlying HTTP client and release connections."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Turn execution
    # ------------------------------------------------------------------

    async def run_sse(self, message: str) -> AsyncGenerator[str, None]:
        """POST /run_sse and yield text chunks as they stream in.

        Parses the SSE stream, extracts text from ADK event payloads, and
        yields each non-empty text fragment.  Stops on a ``{"stopped": true}``
        sentinel or when the stream ends.

        Args:
            message: The user message text to send.

        Yields:
            str: Each text chunk extracted from ADK events.
        """
        payload: dict = {
            "app_name": self._app_name,
            "user_id": self._user_id,
            "session_id": self._session_id,
            "new_message": {
                "role": "user",
                "parts": [{"text": message}],
            },
            "streaming": True,
        }
        async with self._client.stream("POST", "/run_sse", json=payload) as resp:
            resp.raise_for_status()
            async for raw_line in resp.aiter_lines():
                line = raw_line.strip()
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if not data_str:
                    continue
                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    log.warning("SSE: failed to parse JSON: %r", data_str[:120])
                    continue
                if event.get("error"):
                    raise RuntimeError(f"opensage error: {event['error']}")
                if event.get("stopped"):
                    log.debug("SSE: received stop sentinel")
                    break
                text = _extract_text_from_event(event)
                if text:
                    yield text

    # ------------------------------------------------------------------
    # Cancel / status
    # ------------------------------------------------------------------

    async def cancel(self) -> None:
        """POST /control/stop_turn to cancel the currently running turn."""
        try:
            resp = await self._client.post(
                "/control/stop_turn",
                params={"session_id": self._session_id},
            )
            log.debug("cancel → %d", resp.status_code)
        except Exception as exc:
            log.warning("cancel failed: %s", exc)

    async def is_running(self) -> bool:
        """Return True if a turn is currently executing in opensage-web."""
        try:
            resp = await self._client.get(
                "/control/turn_state",
                params={"session_id": self._session_id},
            )
            resp.raise_for_status()
            return bool(resp.json().get("running", False))
        except Exception as exc:
            log.debug("is_running failed: %s", exc)
            return False


# ---------------------------------------------------------------------------
# Echo bridge (test-only)
# ---------------------------------------------------------------------------


class _EchoBridge:
    """Test-only bridge that echoes the input back without HTTP or subprocess.

    Used when ``OPENSAGE_ECHO_MODE=1``.  Presents the same async interface
    as ``OpenSageHttpBridge`` so ``OpenSageACPAgent`` can treat both uniformly.
    """

    async def health_check(self) -> bool:
        return True

    async def discover_app_name(self) -> None:
        pass

    async def create_session(self) -> None:
        pass

    async def run_sse(self, message: str) -> AsyncGenerator[str, None]:
        yield message

    async def cancel(self) -> None:
        pass

    async def is_running(self) -> bool:
        return False

    async def aclose(self) -> None:
        pass
