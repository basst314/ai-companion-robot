"""Console formatting tests."""

from __future__ import annotations

import io
from datetime import timedelta
from pathlib import Path

from shared.console import (
    ConsoleFormatter,
    TerminalDebugScreen,
    configure_console_log,
    configure_terminal_debug_screen,
)


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


def test_terminal_debug_screen_formats_rows_with_meter_and_transcript() -> None:
    screen = TerminalDebugScreen(stream=FakeTty())
    screen.update_runtime(
        lifecycle="listening",
        emotion="listening",
        language="en",
        route_summary="local action",
        last_error=None,
    )
    screen.update_audio(
        current_noise=180.0,
        peak_energy=420.0,
        trailing_silence_seconds=0.35,
        speech_started=True,
        partial_pending=True,
    )
    screen.update_transcript("open your eyes please", language="en", is_final=False)

    rows = screen.snapshot_rows(width=120)

    assert len(rows) == 3
    assert "[DBG]" in rows[0]
    assert "route local action" in rows[0]
    assert "[MIC]" in rows[1]
    assert "[silence 0.35s]" in rows[1]
    assert "[speech yes]" in rows[1]
    assert "[stt standby --]" in rows[1]
    assert "[ 180]" in rows[1]
    assert "[TXT en/live]" in rows[2]
    assert "open your eyes please" in rows[2]


def test_terminal_debug_screen_truncates_transcript_for_narrow_width() -> None:
    screen = TerminalDebugScreen(stream=FakeTty())
    screen.update_transcript(
        "this is a much longer transcript that should be clipped on narrow terminals",
        language="en",
        is_final=False,
    )

    rows = screen.snapshot_rows(width=36)

    assert len(rows[2]) == 36
    assert rows[2].startswith("[TXT en/live]")
    assert "..." in rows[2]
    assert "narrow terminals" in rows[2]


def test_terminal_debug_screen_holds_peak_and_shows_current_noise() -> None:
    screen = TerminalDebugScreen(stream=FakeTty())
    screen.update_audio(current_noise=360.0, peak_energy=360.0, partial_pending=False)
    screen.update_audio(current_noise=40.0, peak_energy=40.0, partial_pending=False)

    rows = screen.snapshot_rows(width=120)

    assert "  40" in rows[1]
    assert "[ 360]" in rows[1]


def test_terminal_debug_screen_peak_decays_after_hold_interval() -> None:
    screen = TerminalDebugScreen(stream=FakeTty())
    screen.update_audio(current_noise=360.0, peak_energy=360.0, partial_pending=False)
    assert screen.state.held_peak_at is not None
    screen.state.held_peak_at -= timedelta(seconds=2)

    screen.update_audio(current_noise=40.0, peak_energy=360.0, partial_pending=False)

    rows = screen.snapshot_rows(width=120)

    assert "  40" in rows[1]
    assert "[  40]" in rows[1]


def test_terminal_debug_screen_shows_whisper_status_badge() -> None:
    screen = TerminalDebugScreen(stream=FakeTty())
    screen.update_whisper_status("0.85s")

    rows = screen.snapshot_rows(width=120)

    assert "[stt standby 0.85s]" in rows[1]


def test_terminal_debug_screen_shows_running_status_with_last_duration() -> None:
    screen = TerminalDebugScreen(stream=FakeTty())
    screen.update_whisper_status("0.85s")
    screen.update_whisper_status("running")

    rows = screen.snapshot_rows(width=120)

    assert "[stt running 0.85s]" in rows[1]


def test_console_log_mirror_stays_plain_text_with_terminal_debug(tmp_path: Path) -> None:
    stream = io.StringIO()
    screen = TerminalDebugScreen(stream=stream)
    screen.active = True
    configure_console_log(tmp_path / "console.log")
    configure_terminal_debug_screen(screen)

    formatter = ConsoleFormatter(stream=stream)
    formatter.emit("\033[31mhello\033[0m", plain_text="hello")

    configure_terminal_debug_screen(None)
    configure_console_log(None)

    assert (tmp_path / "console.log").read_text() == "hello\n"
