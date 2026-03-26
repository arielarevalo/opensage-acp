"""Shared pytest fixtures."""

from __future__ import annotations

import io
import json

import pytest


@pytest.fixture
def make_stream():
    """Return a helper that wraps a list of dicts as a readable JSON-lines stream."""

    def _make(messages: list[dict]) -> io.StringIO:
        lines = "\n".join(json.dumps(m) for m in messages) + "\n"
        return io.StringIO(lines)

    return _make


@pytest.fixture
def capture_stream():
    """Return a StringIO that captures written output and a helper to decode it."""
    buf = io.StringIO()

    def decode():
        buf.seek(0)
        return [json.loads(line) for line in buf if line.strip()]

    return buf, decode
