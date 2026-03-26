"""
opensage-acp entry point.

Starts the ACP agent server over stdin/stdout.  The agent-client-protocol SDK
handles JSON-RPC 2.0 framing; opensage_acp.server handles business logic.
"""

from __future__ import annotations

import asyncio
import logging
import sys


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.WARNING),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
        force=True,
    )


def main() -> None:
    """Run the opensage-acp ACP adapter (reads JSON-RPC 2.0 on stdin)."""
    import os

    log_level = os.getenv("OPENSAGE_LOG_LEVEL", "WARNING")
    _setup_logging(log_level)

    from .config import Config
    from .server import serve

    config = Config.load()
    asyncio.run(serve(config))
