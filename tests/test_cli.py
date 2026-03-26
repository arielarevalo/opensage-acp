"""
Unit tests for cli.py — _setup_logging and main().
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from opensage_acp.cli import _setup_logging


@pytest.fixture(autouse=True)
def _reset_logging():
    """Reset root logger state so basicConfig works in each test."""
    root = logging.getLogger()
    old_level = root.level
    old_handlers = root.handlers[:]
    root.handlers.clear()
    yield
    root.handlers = old_handlers
    root.level = old_level


# ---------------------------------------------------------------------------
# T-06a: _setup_logging
# ---------------------------------------------------------------------------


def test_setup_logging_debug_sets_debug_level():
    _setup_logging("DEBUG")
    assert logging.root.level == logging.DEBUG


def test_setup_logging_unknown_level_falls_back_to_warning():
    _setup_logging("NONSENSE")
    assert logging.root.level == logging.WARNING


def test_setup_logging_case_insensitive():
    _setup_logging("info")
    assert logging.root.level == logging.INFO


# ---------------------------------------------------------------------------
# T-06b: main() integration
# ---------------------------------------------------------------------------


def test_main_calls_config_load_and_serve():
    from opensage_acp.cli import main
    from opensage_acp.config import Config

    fake_config = Config(echo_mode=True)

    with patch("opensage_acp.config.Config.load", return_value=fake_config) as mock_load:
        with patch("opensage_acp.cli.asyncio.run") as mock_run:
            main()

    mock_load.assert_called_once()
    mock_run.assert_called_once()
