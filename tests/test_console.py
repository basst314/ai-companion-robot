"""Console formatting tests."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

from shared.console import (
    ConsoleFormatter,
    TerminalDebugScreen,
    _strip_ansi,
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
    screen.update_ai_status(
        backend="openai",
        planning_active=True,
        response_active=False,
        plan_preview="look_at_user -> cloud_reply",
        response_preview="I am looking at you now.",
    )
    screen.update_audio(
        current_noise=180.0,
        peak_energy=420.0,
        trailing_silence_seconds=0.35,
        speech_started=True,
        vad_active=False,
        partial_pending=True,
    )
    screen.update_transcript("open your eyes please", language="en", is_final=False)
    screen.update_ring_buffer(
        capacity_seconds=3.5,
        filled_seconds=2.5,
        wake_window_seconds=1.5,
        utterance_start_seconds=0.8,
    )

    rows = screen.snapshot_rows(width=160)

    assert len(rows) == 5
    assert "[DBG]" in rows[0]
    assert "route local action" in rows[0]
    assert "[MIC]" in rows[1]
    assert "[vad 0.35s]" in rows[1]
    assert "[stt standby --]" in rows[1]
    assert rows[1].index("[wake off") < rows[1].index("[stt standby --]") < rows[1].index("[vad 0.35s]")
    assert "[ 180]" in rows[1]
    assert "[BUF]" in rows[2]
    assert "[fill 2.5/3.5s]" in rows[2]
    assert "[start 0.8s]" in rows[2]
    assert "^" in rows[2]
    assert ">" in rows[2]
    assert "[AI]" in rows[3]
    assert "[backend openai]" in rows[3]
    assert "[plan active]" in rows[3]
    assert "[plan-t" in rows[3]
    assert "[reply-t" in rows[3]
    assert "look_at_user -> cloud_reply" in rows[3]
    assert "say" in rows[3]
    assert "[TXT en/live]" in rows[4]
    assert "open your eyes please" in rows[4]


def test_terminal_debug_screen_truncates_transcript_for_narrow_width() -> None:
    screen = TerminalDebugScreen(stream=FakeTty())
    screen.update_transcript(
        "this is a much longer transcript that should be clipped on narrow terminals",
        language="en",
        is_final=False,
    )

    rows = screen.snapshot_rows(width=36)

    assert len(rows[4]) == 36
    assert rows[4].startswith("[TXT en/live]")
    assert "..." in rows[4]
    assert "narrow terminals" in rows[4]


def test_terminal_debug_screen_shows_ai_phase_durations() -> None:
    screen = TerminalDebugScreen(stream=FakeTty())
    now = datetime.now(UTC)
    screen.state.ai_backend = "openai"
    screen.state.ai_planning_active = False
    screen.state.ai_planning_last_duration = "0.42s"
    screen.state.ai_response_active = True
    screen.state.ai_response_started_at = now - timedelta(seconds=1.25)
    screen.state.ai_plan_preview = "cloud_reply"
    screen.state.ai_response_preview = "hello there"

    row = screen.snapshot_rows(width=160)[3]

    assert "[plan-t 0.42s]" in row
    assert "[reply active]" in row
    assert "[reply-t 1.25s]" in row or "[reply-t 1.24s]" in row or "[reply-t 1.26s]" in row


def test_terminal_debug_screen_holds_peak_and_shows_current_noise() -> None:
    screen = TerminalDebugScreen(stream=FakeTty())
    screen.update_audio(current_noise=360.0, peak_energy=360.0, partial_pending=False)
    screen.update_audio(current_noise=40.0, peak_energy=40.0, partial_pending=False)

    rows = screen.snapshot_rows(width=120)

    assert "  40" in rows[1]
    assert "[ 360]" in rows[1]


def test_terminal_debug_screen_shows_inactive_vad_badge_before_speech_starts() -> None:
    screen = TerminalDebugScreen(stream=FakeTty())
    screen.update_audio(
        current_noise=20.0,
        peak_energy=20.0,
        trailing_silence_seconds=0.50,
        speech_started=False,
        vad_active=False,
        partial_pending=False,
    )

    rows = screen.snapshot_rows(width=120)

    assert "[vad 0.00s]" in rows[1]


def test_terminal_debug_screen_shows_active_vad_badge_when_voice_is_live() -> None:
    screen = TerminalDebugScreen(stream=FakeTty())
    screen.update_audio(
        current_noise=140.0,
        peak_energy=220.0,
        trailing_silence_seconds=0.0,
        speech_started=True,
        vad_active=True,
        partial_pending=False,
    )

    rows = screen.snapshot_rows(width=120)

    assert "[vad 0.00s]" in rows[1]


def test_terminal_debug_screen_colors_vad_badge_by_state() -> None:
    inactive = TerminalDebugScreen(stream=FakeTty())
    inactive.update_audio(
        current_noise=20.0,
        peak_energy=20.0,
        trailing_silence_seconds=0.50,
        speech_started=False,
        vad_active=False,
        partial_pending=False,
    )
    inactive_badge = inactive._vad_badge()

    active = TerminalDebugScreen(stream=FakeTty())
    active.update_audio(
        current_noise=140.0,
        peak_energy=220.0,
        trailing_silence_seconds=0.0,
        speech_started=True,
        vad_active=True,
        partial_pending=False,
    )
    active_badge = active._vad_badge()

    trailing = TerminalDebugScreen(stream=FakeTty())
    trailing.update_audio(
        current_noise=80.0,
        peak_energy=220.0,
        trailing_silence_seconds=0.35,
        speech_started=True,
        vad_active=False,
        partial_pending=False,
    )
    trailing_badge = trailing._vad_badge()

    assert "\033[90mvad" in inactive_badge
    assert "\033[37m0.00s" in inactive_badge
    assert "\033[32mvad" in active_badge
    assert "\033[37m0.00s" in active_badge
    assert "\033[33mvad" in trailing_badge
    assert "\033[37m0.35s" in trailing_badge


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


def test_terminal_debug_screen_shows_wake_status_badge() -> None:
    screen = TerminalDebugScreen(stream=FakeTty())
    screen.update_wake_status("listening", "Oreo")

    rows = screen.snapshot_rows(width=120)

    assert "[wake listening Oreo]" in rows[1]


def test_terminal_debug_screen_keeps_wake_badge_width_stable_across_states() -> None:
    screen = TerminalDebugScreen(stream=FakeTty())
    screen.update_wake_status("listening", "Oreo")
    listening_badge = _strip_ansi(screen._wake_badge())

    screen.update_wake_status("awake", "Oreo")
    awake_badge = _strip_ansi(screen._wake_badge())

    assert len(listening_badge) == len(awake_badge)


def test_terminal_debug_screen_shows_running_status_with_last_duration() -> None:
    screen = TerminalDebugScreen(stream=FakeTty())
    screen.update_whisper_status("0.85s")
    screen.update_whisper_status("running")

    rows = screen.snapshot_rows(width=120)

    assert "[stt running 0.85s]" in rows[1]


def test_terminal_debug_screen_uses_alternate_screen_buffer() -> None:
    stream = FakeTty()
    screen = TerminalDebugScreen(stream=stream)

    screen.activate()
    screen.close()

    output = stream.getvalue()
    assert "\033[?1049h" in output
    assert "\033[?1049l" in output


def test_terminal_debug_screen_shows_ring_buffer_when_available() -> None:
    screen = TerminalDebugScreen(stream=FakeTty())
    screen.update_ring_buffer(
        capacity_seconds=3.0,
        filled_seconds=1.5,
        wake_window_seconds=1.0,
        utterance_start_seconds=0.4,
    )

    rows = screen.snapshot_rows(width=120)

    assert "[BUF]" in rows[2]
    assert "[fill 1.5/3.0s]" in rows[2]
    assert "[start 0.4s]" in rows[2]
    assert ">" in rows[2]


def test_terminal_debug_screen_shows_ring_buffer_unavailable_by_default() -> None:
    screen = TerminalDebugScreen(stream=FakeTty())

    rows = screen.snapshot_rows(width=120)

    assert "[BUF]" in rows[2]
    assert "ring buffer unavailable" in rows[2]


def test_terminal_debug_screen_ring_buffer_head_moves_after_wrap() -> None:
    screen = TerminalDebugScreen(stream=FakeTty())
    screen.update_ring_buffer(
        capacity_seconds=3.0,
        filled_seconds=3.0,
        wake_window_seconds=1.0,
        utterance_start_seconds=None,
        write_head_seconds=0.2,
    )
    first_rows = screen.snapshot_rows(width=120)
    screen.update_ring_buffer(
        capacity_seconds=3.0,
        filled_seconds=3.0,
        wake_window_seconds=1.0,
        utterance_start_seconds=None,
        write_head_seconds=1.7,
    )
    second_rows = screen.snapshot_rows(width=120)

    assert first_rows[2] != second_rows[2]


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
