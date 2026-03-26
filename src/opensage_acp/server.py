"""
ACP agent server — bridges the agent-client-protocol SDK to opensage-web.

Architecture:
  acpx (client)  ←JSON-RPC/stdio→  OpenSageACPAgent  ←HTTP/SSE→  opensage-web

For each ACP session the agent:
  1. Allocates a port from the configured range.
  2. Optionally generates a per-session TOML config (MCP injection: Phase 2).
  3. Spawns ``opensage web <agent_dir> --port <port> --session-id <id>``.
  4. Waits for the process to pass a health check.
  5. Creates an ``OpenSageHttpBridge`` pointing at it.
  6. Calls ``bridge.create_session()`` to initialise the ADK session.

When ``OPENSAGE_ECHO_MODE=1`` (tests) no subprocess is spawned; instead an
``_EchoBridge`` is used that returns the prompt text verbatim.
"""

from __future__ import annotations

import contextlib
import logging
import shlex
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import anyio
import tomli_w
from acp import (
    InitializeResponse,
    NewSessionResponse,
    PromptResponse,
    run_agent,
    update_agent_message_text,
)
from acp.exceptions import RequestError
from acp.schema import (
    AgentCapabilities,
    AudioContentBlock,
    ClientCapabilities,
    EmbeddedResourceContentBlock,
    ForkSessionResponse,
    HttpMcpServer,
    ImageContentBlock,
    Implementation,
    ListSessionsResponse,
    LoadSessionResponse,
    McpServerStdio,
    ResourceContentBlock,
    ResumeSessionResponse,
    SessionInfo,
    SseMcpServer,
    TextContentBlock,
)

from .bridge import OpenSageHttpBridge, _EchoBridge
from .config import Config

if TYPE_CHECKING:
    from acp.interfaces import Client

log = logging.getLogger(__name__)

# How long to wait for opensage-web to pass a health check after spawning (s)
_HEALTH_TIMEOUT = 30.0
# Polling interval while waiting for health (s)
_HEALTH_POLL = 0.5


# Default opensage session snapshot directory
_OPENSAGE_SESSIONS_ROOT = "~/.local/opensage/sessions"


def _session_dir(session_id: str) -> str:
    """Return the expected opensage session snapshot directory path."""
    return str(Path(_OPENSAGE_SESSIONS_ROOT).expanduser() / session_id)


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


@dataclass
class _Session:
    """All state for one ACP/opensage session."""

    bridge: OpenSageHttpBridge | _EchoBridge
    process: subprocess.Popen[bytes] | None  # None in echo mode
    port: int | None  # None in echo mode
    cwd: str
    session_dir: str | None = None  # Path to opensage session snapshot dir
    config_dir: str | None = None  # Per-session temp config dir to clean up
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    turn_count: int = 0


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class OpenSageACPAgent:
    """ACP agent implementation that delegates to opensage-web via HTTP."""

    _conn: Client

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config.load()
        self._sessions: dict[str, _Session] = {}
        self._cancelled: set[str] = set()
        self._next_port: int = self._config.port_range_start

    # ------------------------------------------------------------------
    # ACP Agent Protocol methods
    # ------------------------------------------------------------------

    def on_connect(self, conn: Client) -> None:
        """Store the ACP client connection for sending session updates."""
        self._conn = conn

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        log.debug("initialize: protocol_version=%d", protocol_version)
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_capabilities=AgentCapabilities(),
        )

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        """Create a new session.

        In echo mode: creates an ``_EchoBridge`` with no subprocess.
        Otherwise:    spawns ``opensage web`` on a fresh port, waits for it to
                      be healthy, then wires up an ``OpenSageHttpBridge``.
        """
        session_id = uuid4().hex

        if self._config.echo_mode:
            bridge: OpenSageHttpBridge | _EchoBridge = _EchoBridge()
            session = _Session(bridge=bridge, process=None, port=None, cwd=cwd)
        else:
            port = self._alloc_port()
            process = self._spawn_opensage_web(session_id, port, mcp_servers)
            base_url = f"http://127.0.0.1:{port}"
            bridge = OpenSageHttpBridge(
                base_url=base_url,
                session_id=session_id,
                timeout=self._config.timeout,
            )
            await self._wait_healthy(bridge, session_id, port)
            await bridge.discover_app_name()
            await bridge.create_session()
            session_dir = _session_dir(session_id)
            config_dir = f"/tmp/opensage-acp-{session_id[:8]}"
            session = _Session(
                bridge=bridge,
                process=process,
                port=port,
                cwd=cwd,
                session_dir=session_dir,
                config_dir=config_dir,
            )

        self._sessions[session_id] = session
        log.debug("new_session: id=%s cwd=%s echo=%s", session_id, cwd, self._config.echo_mode)
        return NewSessionResponse(session_id=session_id)

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse | None:
        """Resume a session: from memory if active, or from disk snapshot.

        If the session is still in memory, returns immediately.  Otherwise,
        checks for a disk snapshot at ``~/.local/opensage/sessions/<session_id>/``.
        If found, spawns a new opensage-web process with ``--resume`` and
        re-registers the session.
        """
        if session_id in self._sessions:
            log.debug("load_session: %s found in memory", session_id)
            return LoadSessionResponse()

        # Check for disk snapshot
        session_dir = _session_dir(session_id)
        if not Path(session_dir).exists():
            log.debug("load_session: %s not found (no disk snapshot)", session_id)
            return None

        # Resume from disk: spawn opensage-web with --resume
        port = self._alloc_port()
        process = self._spawn_opensage_web(session_id, port, mcp_servers, resume=True)
        base_url = f"http://127.0.0.1:{port}"
        bridge = OpenSageHttpBridge(
            base_url=base_url,
            session_id=session_id,
            timeout=self._config.timeout,
        )
        await self._wait_healthy(bridge, session_id, port)
        await bridge.discover_app_name()
        await bridge.create_session()

        config_dir = f"/tmp/opensage-acp-{session_id[:8]}"
        session = _Session(
            bridge=bridge,
            process=process,
            port=port,
            cwd=cwd,
            session_dir=session_dir,
            config_dir=config_dir,
        )
        self._sessions[session_id] = session
        log.debug("load_session: %s resumed from disk", session_id)
        return LoadSessionResponse()

    async def list_sessions(
        self,
        cursor: str | None = None,
        cwd: str | None = None,
        **kwargs: Any,
    ) -> ListSessionsResponse:
        """Return all currently active (in-memory) sessions, optionally filtered by cwd."""
        sessions: list[SessionInfo] = []
        for sid, sess in self._sessions.items():
            if cwd is not None and sess.cwd != cwd:
                continue
            sessions.append(
                SessionInfo(
                    session_id=sid,
                    cwd=sess.cwd,
                    updated_at=sess.updated_at,
                )
            )
        log.debug("list_sessions: %d active", len(sessions))
        return ListSessionsResponse(sessions=sessions)

    async def set_session_mode(self, mode_id: str, session_id: str, **kwargs: Any) -> None:
        return None

    async def set_session_model(self, model_id: str, session_id: str, **kwargs: Any) -> None:
        return None

    async def set_config_option(
        self, config_id: str, session_id: str, value: str, **kwargs: Any
    ) -> None:
        return None

    async def authenticate(self, method_id: str, **kwargs: Any) -> None:
        return None

    async def fork_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> ForkSessionResponse:
        raise RequestError(-32601, "fork_session not supported")

    async def resume_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> ResumeSessionResponse:
        """Resume a session that is still active in this process.

        Cross-restart resume is Phase 2 work.
        """
        if session_id not in self._sessions:
            raise RequestError(-32602, f"Cannot resume unknown session: {session_id}")
        log.debug("resume_session: %s", session_id)
        return ResumeSessionResponse()

    async def prompt(
        self,
        prompt: list[
            TextContentBlock
            | ImageContentBlock
            | AudioContentBlock
            | ResourceContentBlock
            | EmbeddedResourceContentBlock
        ],
        session_id: str,
        **kwargs: Any,
    ) -> PromptResponse:
        """Run a prompt through opensage and stream the response back."""
        # Extract text from the content blocks
        parts: list[str] = []
        for block in prompt:
            if isinstance(block, TextContentBlock):
                parts.append(block.text)
        task = " ".join(parts).strip() or "(no text)"

        log.debug("prompt: session_id=%s task=%r", session_id, task[:80])

        session = self._sessions.get(session_id)
        if session is None:
            raise RequestError(-32602, f"Unknown session: {session_id}")

        # Check if opensage-web process has crashed
        if session.process is not None and session.process.poll() is not None:
            raise RequestError(
                -32603, f"opensage-web process died (exit code {session.process.returncode})"
            )

        # Check for a cancel that arrived before we started
        if session_id in self._cancelled:
            self._cancelled.discard(session_id)
            return PromptResponse(stop_reason="cancelled")

        try:
            async for chunk in session.bridge.run_sse(task):
                # Check for a cancel that arrived mid-stream
                if session_id in self._cancelled:
                    self._cancelled.discard(session_id)
                    await session.bridge.cancel()
                    return PromptResponse(stop_reason="cancelled")
                if chunk:
                    await self._conn.session_update(
                        session_id=session_id,
                        update=update_agent_message_text(chunk),
                    )
        except Exception as exc:
            import asyncio

            if isinstance(exc, (asyncio.CancelledError, anyio.get_cancelled_exc_class())):
                raise
            log.error("opensage error session=%s: %s", session_id, exc)
            raise RequestError(-32603, str(exc)) from exc

        # Update session metadata
        session.updated_at = datetime.now(UTC).isoformat()
        session.turn_count += 1

        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        """Mark session as cancelled; prompt() will notice on next chunk."""
        self._cancelled.add(session_id)
        log.debug("cancel: session_id=%s", session_id)

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise RequestError(-32601, f"Unknown method: {method}")

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        log.debug("ext_notification: %s (ignored)", method)

    async def shutdown_all(self) -> None:
        """Shut down all active sessions: close bridges and terminate processes."""
        for session_id, session in list(self._sessions.items()):
            with contextlib.suppress(Exception):
                await session.bridge.aclose()
            if session.process is not None:
                with contextlib.suppress(Exception):
                    session.process.terminate()
                with contextlib.suppress(Exception):
                    session.process.wait(timeout=5)
            if session.config_dir:
                shutil.rmtree(session.config_dir, ignore_errors=True)
            log.debug("shutdown: session_id=%s", session_id)
        self._sessions.clear()
        self._cancelled.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _alloc_port(self) -> int:
        """Allocate the next available port from the configured range."""
        port = self._next_port
        self._next_port += 1
        return port

    def _read_base_template(self) -> dict[str, Any]:
        """Read the base TOML config template, handling errors gracefully.

        Returns an empty dict if the template is missing, empty, or malformed.
        """
        template = self._config.opensage_config_template
        if not template:
            return {}

        template_path = Path(template)
        try:
            with template_path.open("rb") as f:
                return tomllib.load(f)
        except FileNotFoundError:
            log.warning("Config template not found: %s — using empty base", template_path)
            return {}
        except tomllib.TOMLDecodeError:
            log.warning("Malformed TOML in config template: %s — using empty base", template_path)
            return {}

    def _generate_config(
        self,
        session_id: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
    ) -> str:
        """Generate a per-session TOML config file.

        Reads the base template (if configured), applies session-specific
        overrides, and writes the result to a temp directory.

        Returns the path to the generated config file.
        """
        base = self._read_base_template()

        # T-19b: Inject MCP servers from acpx into [mcp.services.<name>]
        if mcp_servers:
            mcp_section: dict[str, Any] = base.setdefault("mcp", {})
            services: dict[str, Any] = mcp_section.setdefault("services", {})
            for server in mcp_servers:
                if isinstance(server, McpServerStdio):
                    services[server.name] = {
                        "command": server.command,
                        "args": server.args,
                    }
                else:
                    log.warning(
                        "Skipping non-stdio MCP server %r (only stdio is supported)",
                        type(server).__name__,
                    )

        # T-19c: Scope agent_storage_path per session
        config_dir = Path(f"/tmp/opensage-acp-{session_id[:8]}")
        agent_section: dict[str, Any] = base.setdefault("agent", {})
        agent_section["agent_storage_path"] = str(config_dir / "agents")
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "config.toml"

        with config_path.open("wb") as f:
            tomli_w.dump(base, f)

        return str(config_path)

    def _spawn_opensage_web(
        self,
        session_id: str,
        port: int,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None,
        *,
        resume: bool = False,
    ) -> subprocess.Popen[bytes]:
        """Spawn an ``opensage web`` subprocess for this session.

        If *resume* is True, adds ``--resume`` to restart from a disk snapshot.

        MCP server injection into the config is Phase 2 work; for now we pass
        the template config path (if configured) directly.  A warning is logged
        if MCP servers are supplied but cannot yet be injected.
        """
        cmd: list[str] = [
            self._config.opensage_command,
            "web",
            "--agent",
            self._config.agent_dir,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-reload",
        ]
        if resume:
            cmd.append("--resume")
        if self._config.opensage_config_template:
            cmd += ["--config", self._config.opensage_config_template]

        if mcp_servers:
            log.warning(
                "new_session: %d MCP server(s) provided but MCP TOML injection is "
                "not yet implemented (Phase 2).  MCP servers will be ignored.",
                len(mcp_servers),
            )

        log.info("spawning: %s", shlex.join(cmd))
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    async def _wait_healthy(
        self,
        bridge: OpenSageHttpBridge,
        session_id: str,
        port: int,
    ) -> None:
        """Poll the health endpoint until opensage-web is ready or timeout."""
        deadline = anyio.current_time() + _HEALTH_TIMEOUT
        while anyio.current_time() < deadline:
            if await bridge.health_check():
                log.debug("opensage-web healthy: session=%s port=%d", session_id, port)
                return
            await anyio.sleep(_HEALTH_POLL)
        raise RuntimeError(
            f"opensage-web did not become healthy within {_HEALTH_TIMEOUT}s "
            f"(session={session_id}, port={port})"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def serve(config: Config | None = None) -> None:
    """Start the ACP agent server on stdin/stdout."""
    agent = OpenSageACPAgent(config=config)
    try:
        await run_agent(agent, use_unstable_protocol=True)
    finally:
        await agent.shutdown_all()
