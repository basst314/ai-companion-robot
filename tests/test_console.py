"""Console formatting tests."""

from __future__ import annotations

import io

from shared.console import ConsoleFormatter


class FakeTty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_console_formatter_uses_color_when_tty(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)

    formatter = ConsoleFormatter(stream=FakeTty())

    assert formatter.transcript("hello").startswith("\033[36m")
    assert formatter.response("world").startswith("\033[32m")


def test_console_formatter_respects_no_color(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")

    formatter = ConsoleFormatter(stream=FakeTty())

    assert formatter.transcript("hello") == "hello"


def test_console_formatter_adds_timestamp_prefix(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)

    formatter = ConsoleFormatter(stream=FakeTty())
    stamped = formatter.stamp("message")

    assert stamped.startswith("\033[90m[")
    assert " message" in stamped
