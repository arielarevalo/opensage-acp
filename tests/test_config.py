"""
Unit tests for Config.load() — TOML + env-var resolution.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from opensage_acp.config import Config

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(textwrap.dedent(content))
    return p


# ---------------------------------------------------------------------------
# defaults
# ---------------------------------------------------------------------------


def test_load_returns_defaults_when_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in (
        "OPENSAGE_COMMAND",
        "OPENSAGE_AGENT_DIR",
        "OPENSAGE_CONFIG_TEMPLATE",
        "OPENSAGE_PORT_RANGE_START",
        "OPENSAGE_TIMEOUT",
        "OPENSAGE_ECHO_MODE",
        "OPENSAGE_LOG_LEVEL",
    ):
        monkeypatch.delenv(key, raising=False)

    cfg = Config.load(config_file=tmp_path / "nonexistent.toml")
    assert cfg.opensage_command == "opensage"
    assert cfg.agent_dir == "agents"
    assert cfg.opensage_config_template == ""
    assert cfg.port_range_start == 8100
    assert cfg.timeout == 120.0
    assert cfg.echo_mode is False
    assert cfg.log_level == "WARNING"


# ---------------------------------------------------------------------------
# TOML file
# ---------------------------------------------------------------------------


def test_toml_overrides_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSAGE_AGENT_DIR", raising=False)
    monkeypatch.delenv("OPENSAGE_LOG_LEVEL", raising=False)
    monkeypatch.delenv("OPENSAGE_PORT_RANGE_START", raising=False)
    p = _write_toml(
        tmp_path,
        """\
        [opensage-acp]
        agent_dir = "/opt/myagent"
        log_level = "DEBUG"
        port_range_start = 9000
        """,
    )
    cfg = Config.load(config_file=p)
    assert cfg.agent_dir == "/opt/myagent"
    assert cfg.log_level == "DEBUG"
    assert cfg.port_range_start == 9000


def test_toml_opensage_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSAGE_COMMAND", raising=False)
    p = _write_toml(
        tmp_path,
        """\
        [opensage-acp]
        opensage_command = "/usr/local/bin/opensage"
        """,
    )
    cfg = Config.load(config_file=p)
    assert cfg.opensage_command == "/usr/local/bin/opensage"


def test_toml_echo_mode_bool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSAGE_ECHO_MODE", raising=False)
    p = _write_toml(
        tmp_path,
        """\
        [opensage-acp]
        echo_mode = true
        """,
    )
    cfg = Config.load(config_file=p)
    assert cfg.echo_mode is True


def test_toml_ignored_without_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSAGE_AGENT_DIR", raising=False)
    p = _write_toml(
        tmp_path,
        """\
        [other-section]
        agent_dir = "/ignored"
        """,
    )
    cfg = Config.load(config_file=p)
    assert cfg.agent_dir == "agents"


def test_missing_toml_file_uses_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSAGE_AGENT_DIR", raising=False)
    cfg = Config.load(config_file=tmp_path / "does_not_exist.toml")
    assert cfg.agent_dir == "agents"


# ---------------------------------------------------------------------------
# env var precedence
# ---------------------------------------------------------------------------


def test_env_var_overrides_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSAGE_AGENT_DIR", "/from-env/agent")
    p = _write_toml(
        tmp_path,
        """\
        [opensage-acp]
        agent_dir = "/from-toml/agent"
        """,
    )
    cfg = Config.load(config_file=p)
    assert cfg.agent_dir == "/from-env/agent"


def test_env_echo_mode_overrides_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSAGE_ECHO_MODE", "1")
    p = _write_toml(
        tmp_path,
        """\
        [opensage-acp]
        echo_mode = false
        """,
    )
    cfg = Config.load(config_file=p)
    assert cfg.echo_mode is True


def test_env_timeout_overrides_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSAGE_TIMEOUT", "30")
    p = _write_toml(
        tmp_path,
        """\
        [opensage-acp]
        timeout = 999.0
        """,
    )
    cfg = Config.load(config_file=p)
    assert cfg.timeout == 30.0


def test_env_port_range_start_overrides_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENSAGE_PORT_RANGE_START", "9500")
    p = _write_toml(
        tmp_path,
        """\
        [opensage-acp]
        port_range_start = 8100
        """,
    )
    cfg = Config.load(config_file=p)
    assert cfg.port_range_start == 9500


def test_env_command_overrides_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSAGE_COMMAND", "/custom/opensage")
    p = _write_toml(
        tmp_path,
        """\
        [opensage-acp]
        opensage_command = "/default/opensage"
        """,
    )
    cfg = Config.load(config_file=p)
    assert cfg.opensage_command == "/custom/opensage"


# ---------------------------------------------------------------------------
# T-01a regression: malformed TOML returns defaults
# ---------------------------------------------------------------------------


def test_invalid_toml_returns_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed TOML must not crash — should return defaults."""
    for key in (
        "OPENSAGE_COMMAND",
        "OPENSAGE_AGENT_DIR",
        "OPENSAGE_CONFIG_TEMPLATE",
        "OPENSAGE_PORT_RANGE_START",
        "OPENSAGE_TIMEOUT",
        "OPENSAGE_ECHO_MODE",
        "OPENSAGE_LOG_LEVEL",
    ):
        monkeypatch.delenv(key, raising=False)

    p = tmp_path / "bad.toml"
    p.write_text("agent_dir = [unclosed\n")
    cfg = Config.load(config_file=p)
    assert cfg.agent_dir == "agents"
    assert cfg.port_range_start == 8100


# ---------------------------------------------------------------------------
# T-04a: ECHO_MODE edge cases and non-dict section
# ---------------------------------------------------------------------------


def test_echo_mode_zero_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSAGE_ECHO_MODE", "0")
    cfg = Config.load(config_file=Path("/nonexistent/path.toml"))
    assert cfg.echo_mode is False


def test_echo_mode_empty_string_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSAGE_ECHO_MODE", "")
    cfg = Config.load(config_file=Path("/nonexistent/path.toml"))
    assert cfg.echo_mode is False


def test_non_dict_opensage_acp_section_uses_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in (
        "OPENSAGE_COMMAND",
        "OPENSAGE_AGENT_DIR",
        "OPENSAGE_CONFIG_TEMPLATE",
        "OPENSAGE_PORT_RANGE_START",
        "OPENSAGE_TIMEOUT",
        "OPENSAGE_ECHO_MODE",
        "OPENSAGE_LOG_LEVEL",
    ):
        monkeypatch.delenv(key, raising=False)

    p = _write_toml(tmp_path, '"opensage-acp" = "not a dict"\n')
    cfg = Config.load(config_file=p)
    assert cfg.agent_dir == "agents"
    assert cfg.port_range_start == 8100
    assert cfg.echo_mode is False
