"""
Configuration loading for opensage-acp.

Config is resolved from (highest-priority first):
  1. Environment variables (OPENSAGE_*)
  2. ~/.config/opensage-acp/config.toml  (or OPENSAGE_CONFIG_FILE override)
  3. Built-in defaults

This config describes the *adapter itself*, not opensage internals.
opensage-web is spawned as a subprocess; its own config is generated per
session from the optional OPENSAGE_CONFIG_TEMPLATE file.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_DEFAULT_CONFIG = "~/.config/opensage-acp/config.toml"


def _default_config_path() -> Path:
    return Path(os.getenv("OPENSAGE_CONFIG_FILE", _DEFAULT_CONFIG)).expanduser()


def _load_toml(path: Path) -> dict[str, object]:
    """Return parsed TOML dict, or {} if the file doesn't exist or is malformed."""
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError:
        import logging

        logging.getLogger(__name__).warning("Failed to parse TOML config: %s", path)
        return {}


@dataclass
class Config:
    # Path (or name) of the opensage CLI binary
    opensage_command: str = field(default_factory=lambda: os.getenv("OPENSAGE_COMMAND", "opensage"))

    # Path to the opensage agent directory (contains agent.py)
    agent_dir: str = field(default_factory=lambda: os.getenv("OPENSAGE_AGENT_DIR", "agents"))

    # Optional path to a base TOML config template for opensage-web.
    # If set, each session copies this file (injecting MCP servers if provided).
    # If unset, opensage-web is started without --config.
    opensage_config_template: str = field(
        default_factory=lambda: os.getenv("OPENSAGE_CONFIG_TEMPLATE", "")
    )

    # First port in the range used for opensage-web instances (one per session)
    port_range_start: int = field(
        default_factory=lambda: int(os.getenv("OPENSAGE_PORT_RANGE_START", "8100"))
    )

    # HTTP request timeout in seconds (used by OpenSageHttpBridge)
    timeout: float = field(default_factory=lambda: float(os.getenv("OPENSAGE_TIMEOUT", "120")))

    # Testing — skips opensage subprocess entirely and echoes the task back
    echo_mode: bool = field(default_factory=lambda: os.getenv("OPENSAGE_ECHO_MODE") == "1")

    # Logging level
    log_level: str = field(default_factory=lambda: os.getenv("OPENSAGE_LOG_LEVEL", "WARNING"))

    @classmethod
    def load(cls, config_file: Path | None = None) -> Config:
        """Load config: TOML file provides defaults, env vars override.

        TOML keys mirror field names (snake_case) under the [opensage-acp] section::

            [opensage-acp]
            opensage_command = "/usr/local/bin/opensage"
            agent_dir = "/home/user/myagent"
            port_range_start = 9000
            log_level = "DEBUG"
        """
        path = config_file or _default_config_path()
        raw = _load_toml(path)
        toml_section: dict[str, object] = {}
        if isinstance(raw.get("opensage-acp"), dict):
            toml_section = raw["opensage-acp"]  # type: ignore[assignment]

        def _str(toml_key: str, env_suffix: str, default: str) -> str:
            env_val = os.getenv(f"OPENSAGE_{env_suffix}")
            if env_val is not None:
                return env_val
            toml_val = toml_section.get(toml_key)
            return str(toml_val) if toml_val is not None else default

        def _int(toml_key: str, env_suffix: str, default: int) -> int:
            env_val = os.getenv(f"OPENSAGE_{env_suffix}")
            if env_val is not None:
                return int(env_val)
            toml_val = toml_section.get(toml_key)
            return int(str(toml_val)) if toml_val is not None else default

        def _float(toml_key: str, env_suffix: str, default: float) -> float:
            env_val = os.getenv(f"OPENSAGE_{env_suffix}")
            if env_val is not None:
                return float(env_val)
            toml_val = toml_section.get(toml_key)
            return float(str(toml_val)) if toml_val is not None else default

        def _bool(toml_key: str, env_suffix: str, default: bool) -> bool:
            env_val = os.getenv(f"OPENSAGE_{env_suffix}")
            if env_val is not None:
                return env_val == "1"
            toml_val = toml_section.get(toml_key)
            if isinstance(toml_val, bool):
                return toml_val
            return default

        return cls(
            opensage_command=_str("opensage_command", "COMMAND", "opensage"),
            agent_dir=_str("agent_dir", "AGENT_DIR", "agents"),
            opensage_config_template=_str("opensage_config_template", "CONFIG_TEMPLATE", ""),
            port_range_start=_int("port_range_start", "PORT_RANGE_START", 8100),
            timeout=_float("timeout", "TIMEOUT", 120.0),
            echo_mode=_bool("echo_mode", "ECHO_MODE", False),
            log_level=_str("log_level", "LOG_LEVEL", "WARNING"),
        )
