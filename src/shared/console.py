"""Terminal-friendly console formatting helpers."""

from __future__ import annotations

import os
import shutil
import sys
import textwrap
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, TextIO

_console_log_path: Path | None = None
_terminal_debug_screen: "TerminalDebugScreen | None" = None


def _colors_enabled(stream: TextIO) -> bool:
    """Return whether ANSI color should be used for this stream."""

    if os.environ.get("NO_COLOR"):
        return False
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def configure_console_log(path: Path | None) -> None:
    """Configure a plain-text console mirror file."""

    global _console_log_path
    _console_log_path = path
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)


def configure_terminal_debug_screen(screen: "TerminalDebugScreen | None") -> None:
    """Register the active terminal debug screen used for interactive console output."""

    global _terminal_debug_screen
    _terminal_debug_screen = screen


def _append_console_log(line: str) -> None:
    if _console_log_path is None:
        return
    with _console_log_path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes before mirroring text into plain log files."""

    stripped = []
    index = 0
    while index < len(text):
        if text[index] != "\033":
            stripped.append(text[index])
            index += 1
            continue
        index += 1
        if index < len(text) and text[index] == "[":
            index += 1
            while index < len(text) and text[index] not in "@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~":
                index += 1
            if index < len(text):
                index += 1
            continue
        if index < len(text):
            index += 1
    return "".join(stripped)


class TerminalDebugSink(Protocol):
    """Structured terminal-only debug updates for the interactive console."""

    def activate(self) -> None:
        """Enable terminal rendering for the current session."""

    def close(self) -> None:
        """Restore the terminal after the current session."""

    def update_runtime(
        self,
        *,
        lifecycle: str,
        emotion: str,
        language: str | None = None,
        route_summary: str | None = None,
        last_error: str | None = None,
    ) -> None:
        """Update high-level runtime fields shown in the sticky header."""

    def update_ai_status(
        self,
        *,
        backend: str | None = None,
        planning_active: bool | None = None,
        response_active: bool | None = None,
        plan_preview: str | None = None,
        response_preview: str | None = None,
    ) -> None:
        """Update AI backend activity shown in the sticky header."""

    def update_audio(
        self,
        *,
        current_noise: float | None = None,
        peak_energy: float | None = None,
        trailing_silence_seconds: float | None = None,
        speech_started: bool | None = None,
        vad_active: bool | None = None,
        partial_pending: bool | None = None,
    ) -> None:
        """Update live microphone telemetry shown in the sticky header."""

    def update_transcript(
        self,
        text: str,
        *,
        language: str | None = None,
        is_final: bool = False,
    ) -> None:
        """Update the live transcript row in the sticky header."""

    def update_whisper_status(self, status: str | None) -> None:
        """Update the whisper status indicator in the sticky header."""

    def update_wake_status(self, status: str, detail: str | None = None) -> None:
        """Update the wake-word indicator in the sticky header."""

    def update_ring_buffer(
        self,
        *,
        capacity_seconds: float | None = None,
        filled_seconds: float | None = None,
        wake_window_seconds: float | None = None,
        utterance_start_seconds: float | None = None,
        write_head_seconds: float | None = None,
    ) -> None:
        """Update the shared wake/utterance ring buffer indicator."""

    def update_tts_status(
        self,
        *,
        backend: str | None = None,
        phase: str | None = None,
        voice: str | None = None,
        style: str | None = None,
        speaker: str | None = None,
        queue_depth: int | None = None,
        preview: str | None = None,
    ) -> None:
        """Update TTS playback state shown in the sticky header."""


@dataclass(slots=True)
class TerminalDebugState:
    """Structured state rendered by the terminal-only debug screen."""

    lifecycle: str = "idle"
    emotion: str = "neutral"
    language: str | None = None
    route_summary: str | None = None
    last_error: str | None = None
    ai_backend: str | None = None
    ai_planning_active: bool = False
    ai_response_active: bool = False
    ai_planning_started_at: datetime | None = None
    ai_response_started_at: datetime | None = None
    ai_planning_last_duration: str | None = None
    ai_response_last_duration: str | None = None
    ai_plan_preview: str | None = None
    ai_response_preview: str | None = None
    transcript_text: str = ""
    transcript_language: str | None = None
    transcript_is_final: bool = False
    current_noise: float | None = None
    peak_energy: float | None = None
    trailing_silence_seconds: float | None = None
    speech_started: bool = False
    vad_active: bool = False
    partial_pending: bool = False
    stt_running: bool = False
    last_stt_duration: str | None = None
    wake_status: str = "off"
    wake_detail: str | None = None
    turn_started_at: datetime | None = None
    held_peak_energy: float | None = None
    held_peak_at: datetime | None = None
    ring_capacity_seconds: float | None = None
    ring_filled_seconds: float | None = None
    ring_wake_window_seconds: float | None = None
    ring_utterance_start_seconds: float | None = None
    ring_write_head_seconds: float | None = None
    tts_backend: str | None = None
    tts_phase: str = "idle"
    tts_voice: str | None = None
    tts_style: str | None = None
    tts_speaker: str | None = None
    tts_queue_depth: int = 0
    tts_preview: str | None = None
    tts_synth_started_at: datetime | None = None
    tts_play_started_at: datetime | None = None
    tts_last_synth_duration: str | None = None
    tts_last_play_duration: str | None = None


@dataclass(slots=True)
class TerminalDebugScreen(TerminalDebugSink):
    """Interactive terminal debug screen with a fixed header and scrolling logs."""

    stream: TextIO = field(default_factory=lambda: sys.stdout)
    state: TerminalDebugState = field(default_factory=TerminalDebugState)
    header_height: int = 6
    active: bool = False
    control_enabled: bool = False
    _last_terminal_size: os.terminal_size = field(
        default_factory=lambda: shutil.get_terminal_size(fallback=(100, 24))
    )
    _last_fallback_transcript: tuple[str, str | None, bool] | None = None

    def __post_init__(self) -> None:
        isatty = getattr(self.stream, "isatty", None)
        self.control_enabled = bool(isatty and isatty())

    def activate(self) -> None:
        """Enable the sticky header for the current terminal session."""

        self.active = True
        self._refresh_terminal_size()
        if self.control_enabled:
            self.stream.write("\033[?1049h")
            self.stream.write("\033[2J\033[H")
            self._set_scroll_region()
            self.stream.write(f"\033[{self.header_height + 1};1H")
        self.render()
        self.stream.flush()

    def close(self) -> None:
        """Restore the terminal after the interactive debug session finishes."""

        if not self.active:
            return
        if self.control_enabled:
            rows = self._refresh_terminal_size().lines
            self.stream.write("\033[r")
            self.stream.write(f"\033[{rows};1H\n")
            self.stream.write("\033[?1049l")
            self.stream.flush()
        self.active = False
        self._last_fallback_transcript = None

    def update_runtime(
        self,
        *,
        lifecycle: str,
        emotion: str,
        language: str | None = None,
        route_summary: str | None = None,
        last_error: str | None = None,
    ) -> None:
        self.state.lifecycle = lifecycle
        self.state.emotion = emotion
        if language is not None:
            self.state.language = language
        self.state.route_summary = route_summary
        self.state.last_error = last_error
        if lifecycle == "listening" and self.state.turn_started_at is None:
            self.state.turn_started_at = datetime.now(UTC)
        if lifecycle == "idle":
            self.state.turn_started_at = None
            self.state.transcript_is_final = False
            self.state.partial_pending = False
            self.state.current_noise = None
            self.state.stt_running = False
            self.state.peak_energy = None
            self.state.held_peak_energy = None
            self.state.held_peak_at = None
            self.state.trailing_silence_seconds = None
            self.state.speech_started = False
            self.state.vad_active = False
            self.state.ai_planning_active = False
            self.state.ai_response_active = False
            self.state.ai_planning_started_at = None
            self.state.ai_response_started_at = None
        self.render()

    def update_ai_status(
        self,
        *,
        backend: str | None = None,
        planning_active: bool | None = None,
        response_active: bool | None = None,
        plan_preview: str | None = None,
        response_preview: str | None = None,
    ) -> None:
        if backend is not None:
            self.state.ai_backend = backend
        now = datetime.now(UTC)
        if planning_active is not None:
            if planning_active and not self.state.ai_planning_active:
                self.state.ai_planning_started_at = now
                self.state.ai_planning_last_duration = None
            elif not planning_active and self.state.ai_planning_active and self.state.ai_planning_started_at is not None:
                elapsed = max(0.0, (now - self.state.ai_planning_started_at).total_seconds())
                self.state.ai_planning_last_duration = f"{elapsed:0.2f}s"
                self.state.ai_planning_started_at = None
            self.state.ai_planning_active = planning_active
        if response_active is not None:
            if response_active and not self.state.ai_response_active:
                self.state.ai_response_started_at = now
                self.state.ai_response_last_duration = None
            elif not response_active and self.state.ai_response_active and self.state.ai_response_started_at is not None:
                elapsed = max(0.0, (now - self.state.ai_response_started_at).total_seconds())
                self.state.ai_response_last_duration = f"{elapsed:0.2f}s"
                self.state.ai_response_started_at = None
            self.state.ai_response_active = response_active
        if plan_preview is not None:
            self.state.ai_plan_preview = plan_preview
        if response_preview is not None:
            self.state.ai_response_preview = response_preview
        self.render()

    def update_audio(
        self,
        *,
        current_noise: float | None = None,
        peak_energy: float | None = None,
        trailing_silence_seconds: float | None = None,
        speech_started: bool | None = None,
        vad_active: bool | None = None,
        partial_pending: bool | None = None,
    ) -> None:
        now = datetime.now(UTC)
        if current_noise is not None:
            self.state.current_noise = current_noise
        candidate_peak = current_noise if current_noise is not None else peak_energy
        if candidate_peak is not None:
            held_peak = self.state.held_peak_energy
            held_peak_at = self.state.held_peak_at
            if held_peak is None or candidate_peak >= held_peak:
                self.state.held_peak_energy = candidate_peak
                self.state.held_peak_at = now
            elif held_peak_at is None or (now - held_peak_at).total_seconds() >= 1.0:
                self.state.held_peak_energy = max(candidate_peak, self.state.current_noise or 0.0)
                self.state.held_peak_at = now
        if peak_energy is not None:
            self.state.peak_energy = peak_energy
        if trailing_silence_seconds is not None:
            self.state.trailing_silence_seconds = trailing_silence_seconds
        if speech_started is not None:
            self.state.speech_started = speech_started
        if vad_active is not None:
            self.state.vad_active = vad_active
        if partial_pending is not None:
            self.state.partial_pending = partial_pending
        self.render()

    def update_transcript(
        self,
        text: str,
        *,
        language: str | None = None,
        is_final: bool = False,
    ) -> None:
        self.state.transcript_text = text
        self.state.transcript_is_final = is_final
        if language is not None:
            self.state.transcript_language = language
            self.state.language = language
        if self.active and not self.control_enabled:
            transcript_key = (text, language, is_final)
            if transcript_key != self._last_fallback_transcript:
                formatter = ConsoleFormatter(stream=self.stream)
                label = "Final transcript" if is_final else "Listening"
                resolved_language = language or self.state.language or "--"
                plain_message = formatter.stamp(f"{label} [{resolved_language}]: {text or '...'}")
                styled_message = formatter.stamp(
                    f"{formatter.label(f'{label} [{resolved_language}]:')} {formatter.transcript(text or '...')}"
                )
                self.stream.write(styled_message + "\n")
                _append_console_log(plain_message + "\n")
                self._last_fallback_transcript = transcript_key
        self.render()

    def update_whisper_status(self, status: str | None) -> None:
        if status == "running":
            self.state.stt_running = True
        elif status is None:
            self.state.stt_running = False
        else:
            self.state.stt_running = False
            self.state.last_stt_duration = status
        self.render()

    def update_wake_status(self, status: str, detail: str | None = None) -> None:
        self.state.wake_status = status
        self.state.wake_detail = detail
        self.render()

    def update_ring_buffer(
        self,
        *,
        capacity_seconds: float | None = None,
        filled_seconds: float | None = None,
        wake_window_seconds: float | None = None,
        utterance_start_seconds: float | None = None,
        write_head_seconds: float | None = None,
    ) -> None:
        if capacity_seconds is not None:
            self.state.ring_capacity_seconds = capacity_seconds
        if filled_seconds is not None:
            self.state.ring_filled_seconds = filled_seconds
        if wake_window_seconds is not None:
            self.state.ring_wake_window_seconds = wake_window_seconds
        self.state.ring_utterance_start_seconds = utterance_start_seconds
        self.state.ring_write_head_seconds = write_head_seconds
        self.render()

    def update_tts_status(
        self,
        *,
        backend: str | None = None,
        phase: str | None = None,
        voice: str | None = None,
        style: str | None = None,
        speaker: str | None = None,
        queue_depth: int | None = None,
        preview: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        if backend is not None:
            self.state.tts_backend = backend
        if voice is not None:
            self.state.tts_voice = voice
        if style is not None:
            self.state.tts_style = style
        if speaker is not None:
            self.state.tts_speaker = speaker
        if queue_depth is not None:
            self.state.tts_queue_depth = max(0, queue_depth)
        if preview is not None:
            self.state.tts_preview = preview
        if phase is not None and phase != self.state.tts_phase:
            previous_phase = self.state.tts_phase
            if previous_phase == "synth" and self.state.tts_synth_started_at is not None:
                elapsed = max(0.0, (now - self.state.tts_synth_started_at).total_seconds())
                self.state.tts_last_synth_duration = f"{elapsed:0.2f}s"
                self.state.tts_synth_started_at = None
            if previous_phase == "play" and self.state.tts_play_started_at is not None:
                elapsed = max(0.0, (now - self.state.tts_play_started_at).total_seconds())
                self.state.tts_last_play_duration = f"{elapsed:0.2f}s"
                self.state.tts_play_started_at = None

            if phase == "synth":
                self.state.tts_synth_started_at = now
                self.state.tts_last_synth_duration = None
            if phase == "play":
                self.state.tts_play_started_at = now
                self.state.tts_last_play_duration = None
            self.state.tts_phase = phase
        self.render()

    def emit_log(self, styled_text: str, *, plain_text: str, end: str, flush: bool) -> None:
        """Write a scrolling log message while preserving the sticky header."""

        if not self.active:
            self.stream.write(styled_text + end)
            if flush:
                self.stream.flush()
            _append_console_log(plain_text + ("\n" if not end else end.replace("\r", "\n")))
            return

        if self.control_enabled:
            self._refresh_terminal_size()
            self.stream.write("\033[0m")
            self.stream.write(styled_text + end)
            self.stream.flush()
            self.render()
        else:
            self.stream.write(styled_text + end)
            if flush:
                self.stream.flush()
        _append_console_log(plain_text + ("\n" if not end else end.replace("\r", "\n")))

    def render(self) -> None:
        """Redraw the header in place when terminal control is available."""

        if not self.active:
            return
        if not self.control_enabled:
            return
        size = self._refresh_terminal_size()
        self.stream.write("\0337")
        self._set_scroll_region()
        rows = self._render_header_rows(width=size.columns)
        self.stream.write("\033[H")
        for index, row in enumerate(rows, start=1):
            self.stream.write("\033[2K")
            self.stream.write(row)
            if index < self.header_height:
                self.stream.write("\n")
        self.stream.write("\0338")
        self.stream.flush()

    def snapshot_rows(self, width: int) -> tuple[str, ...]:
        """Return plain-text header rows for tests and non-terminal inspection."""

        return tuple(_strip_ansi(row) for row in self._render_header_rows(width=width))

    def _refresh_terminal_size(self) -> os.terminal_size:
        self._last_terminal_size = shutil.get_terminal_size(
            fallback=(self._last_terminal_size.columns, self._last_terminal_size.lines)
        )
        return self._last_terminal_size

    def _set_scroll_region(self) -> None:
        if not self.control_enabled:
            return
        rows = max(self._last_terminal_size.lines, self.header_height + 2)
        self.stream.write(f"\033[{self.header_height + 1};{rows}r")

    def _render_header_rows(self, *, width: int) -> tuple[str, str, str, str, str, str]:
        status_row = self._status_row(width)
        audio_row = self._audio_row(width)
        ring_row = self._ring_row(width)
        ai_row = self._ai_row(width)
        tts_row = self._tts_row(width)
        transcript_row = self._transcript_row(width)
        return (status_row, audio_row, ring_row, ai_row, tts_row, transcript_row)

    def _status_row(self, width: int) -> str:
        current_time = datetime.now().strftime("%H:%M:%S")
        elapsed = "--"
        if self.state.turn_started_at is not None:
            elapsed_seconds = max(0.0, (datetime.now(UTC) - self.state.turn_started_at).total_seconds())
            elapsed = f"{elapsed_seconds:04.1f}s"
        parts = [
            self.label("[DBG]"),
            f"{self.label('state')} {self.status_value(self.state.lifecycle)}",
            f"{self.label('mood')} {self.emotion_value(self.state.emotion)}",
            f"{self.label('lang')} {self.value(self.state.language or '--')}",
            f"{self.label('time')} {self.value(current_time)}",
            f"{self.label('turn')} {self.value(elapsed)}",
        ]
        if self.state.route_summary:
            parts.append(f"{self.label('route')} {self.route_value(self.state.route_summary)}")
        if self.state.last_error:
            parts.append(f"{self.label('error')} {self.error(self._clip_plain(self.state.last_error, 24))}")
        return self._pad_row("  ".join(parts), width)

    def _audio_row(self, width: int) -> str:
        current_noise = self.state.current_noise or 0.0
        peak_energy = self.state.held_peak_energy or self.state.peak_energy or current_noise
        meter_width = max(8, min(24, width // 6))
        meter = self._build_meter(current_noise=current_noise, peak_energy=peak_energy, width=meter_width)
        parts = [
            self.stt_label("[MIC]"),
            meter,
            self.metric_value(f"{current_noise:4.0f}"),
            self.peak_value(f"[{peak_energy:4.0f}]"),
            self._wake_badge(),
            self._stt_badge(),
            self._vad_badge(),
        ]
        return self._pad_row("  ".join(parts), width)

    def _transcript_row(self, width: int) -> str:
        label = "final" if self.state.transcript_is_final else "live"
        language = self.state.transcript_language or self.state.language or "--"
        transcript = self.state.transcript_text or "..."
        prefix = f"{self.label(f'[TXT {language}/{label}]')} "
        available = max(0, width - len(_strip_ansi(prefix)))
        clipped = self.transcript(self._clip_transcript_tail(transcript, available))
        return self._pad_row(prefix + clipped, width)

    def _ring_row(self, width: int) -> str:
        capacity = self.state.ring_capacity_seconds
        filled = self.state.ring_filled_seconds
        wake_window = self.state.ring_wake_window_seconds
        utterance_start = self.state.ring_utterance_start_seconds
        write_head = self.state.ring_write_head_seconds
        if capacity is None or filled is None or wake_window is None or capacity <= 0:
            return self._pad_row(
                f"{self.label('[BUF]')} {self.subtle_value('ring buffer unavailable')}",
                width,
            )

        timeline_width = max(12, min(32, width // 5))
        timeline = self._build_ring_timeline(
            width=timeline_width,
            capacity_seconds=capacity,
            filled_seconds=filled,
            wake_window_seconds=wake_window,
            utterance_start_seconds=utterance_start,
            write_head_seconds=write_head,
        )
        parts = [
            self.label("[BUF]"),
            timeline,
            self.badge("fill", f"{filled:0.1f}/{capacity:0.1f}s", value_style=self.value),
        ]
        if utterance_start is None:
            parts.append(self.badge("start", "--", value_style=self.subtle_value))
        else:
            parts.append(self.badge("start", f"{utterance_start:0.1f}s", value_style=self.success_value))
        return self._pad_row("  ".join(parts), width)

    def _ai_row(self, width: int) -> str:
        backend = self.state.ai_backend or "--"
        planning_status = "active" if self.state.ai_planning_active else "idle"
        response_status = "active" if self.state.ai_response_active else "idle"
        planning_style = self.whisper if self.state.ai_planning_active else self.subtle_value
        response_style = self.success_value if self.state.ai_response_active else self.subtle_value
        planning_duration = self._ai_duration(self.state.ai_planning_active, self.state.ai_planning_started_at, self.state.ai_planning_last_duration)
        response_duration = self._ai_duration(self.state.ai_response_active, self.state.ai_response_started_at, self.state.ai_response_last_duration)
        plan_preview = self.state.ai_plan_preview or "no plan yet"
        response_preview = self.state.ai_response_preview or "no reply yet"
        parts = [
            self.label("[AI]"),
            self.badge("backend", backend, value_style=self.value),
            f"{self.label('[plan')} {planning_style(planning_status)}{self.label(']')}",
            self.badge("plan-t", planning_duration, value_style=self.value),
            f"{self.label('[reply')} {response_style(response_status)}{self.label(']')}",
            self.badge("reply-t", response_duration, value_style=self.value),
            f"{self.label('plan')} {self.route_value(self._clip_plain(plan_preview, 28))}",
            f"{self.label('say')} {self.transcript(self._clip_plain(response_preview, 34))}",
        ]
        return self._pad_row("  ".join(parts), width)

    def _tts_row(self, width: int) -> str:
        backend = self.state.tts_backend or "--"
        phase = self.state.tts_phase
        voice = self.state.tts_voice or "--"
        style = self.state.tts_style or "neutral"
        speaker = self.state.tts_speaker or "--"
        queue_depth = str(self.state.tts_queue_depth)
        preview = self.state.tts_preview or "no speech queued"
        phase_style = {
            "idle": self.subtle_value,
            "queued": self.warning_value,
            "synth": self.whisper,
            "play": self.success_value,
            "interrupted": self.warning_value,
            "failed": self.error,
        }.get(phase, self.value)
        synth_duration = self._tts_duration(
            phase == "synth",
            self.state.tts_synth_started_at,
            self.state.tts_last_synth_duration,
        )
        play_duration = self._tts_duration(
            phase == "play",
            self.state.tts_play_started_at,
            self.state.tts_last_play_duration,
        )
        parts = [
            self.label("[TTS]"),
            self.badge("backend", backend, value_style=self.value),
            f"{self.label('[phase')} {phase_style(phase)}{self.label(']')}",
            self.badge("queue", queue_depth, value_style=self.value),
            self.badge("synth-t", synth_duration, value_style=self.value),
            self.badge("play-t", play_duration, value_style=self.value),
            f"{self.label('voice')} {self.route_value(self._clip_plain(voice, 24))}",
            f"{self.label('style')} {self.value(style)}",
            f"{self.label('spk')} {self.value(self._clip_plain(speaker, 12))}",
            f"{self.label('say')} {self.transcript(self._clip_plain(preview, 26))}",
        ]
        return self._pad_row("  ".join(parts), width)

    def _ai_duration(
        self,
        active: bool,
        started_at: datetime | None,
        last_duration: str | None,
    ) -> str:
        if active and started_at is not None:
            elapsed = max(0.0, (datetime.now(UTC) - started_at).total_seconds())
            return f"{elapsed:0.2f}s"
        return last_duration or "--"

    def _tts_duration(
        self,
        active: bool,
        started_at: datetime | None,
        last_duration: str | None,
    ) -> str:
        if active and started_at is not None:
            elapsed = max(0.0, (datetime.now(UTC) - started_at).total_seconds())
            return f"{elapsed:0.2f}s"
        return last_duration or "--"

    def _build_meter(self, *, current_noise: float, peak_energy: float, width: int) -> str:
        current_index = self._meter_index(current_noise, width)
        peak_index = self._meter_index(peak_energy, width)
        chunks = [self.label("[")]
        for index in range(width):
            if index == peak_index:
                chunks.append(self.peak_value("|"))
            elif index <= current_index:
                chunks.append(self.metric_value("-"))
            else:
                chunks.append(self.subtle_value("-"))
        chunks.append(self.label("]"))
        return "".join(chunks)

    def _build_ring_timeline(
        self,
        *,
        width: int,
        capacity_seconds: float,
        filled_seconds: float,
        wake_window_seconds: float,
        utterance_start_seconds: float | None,
        write_head_seconds: float | None,
    ) -> str:
        if width <= 0 or capacity_seconds <= 0:
            return ""
        if write_head_seconds is None:
            write_head_seconds = min(capacity_seconds, filled_seconds)
        write_head_seconds = write_head_seconds % capacity_seconds
        slot_seconds = capacity_seconds / max(1, width)
        data_start_seconds = (write_head_seconds - min(filled_seconds, capacity_seconds)) % capacity_seconds
        wake_start_seconds = (write_head_seconds - min(wake_window_seconds, filled_seconds, capacity_seconds)) % capacity_seconds
        write_head_index = min(
            width - 1,
            max(0, int(write_head_seconds / max(slot_seconds, 1e-9))),
        )
        utterance_index = None
        if utterance_start_seconds is not None:
            utterance_index = min(
                width - 1,
                max(0, int((utterance_start_seconds % capacity_seconds) / max(slot_seconds, 1e-9))),
            )

        chunks = [self.label("[")]
        for index in range(width):
            slot_start = index * slot_seconds
            if index == write_head_index:
                chunks.append(self.peak_value(">"))
            elif utterance_index is not None and index == utterance_index:
                chunks.append(self.success_value("^"))
            elif not self._ring_interval_contains(
                slot_start,
                start_seconds=data_start_seconds,
                end_seconds=write_head_seconds,
                capacity_seconds=capacity_seconds,
                filled_seconds=filled_seconds,
            ):
                chunks.append(self.subtle_value("·"))
            elif self._ring_interval_contains(
                slot_start,
                start_seconds=wake_start_seconds,
                end_seconds=write_head_seconds,
                capacity_seconds=capacity_seconds,
                filled_seconds=min(wake_window_seconds, filled_seconds),
            ):
                chunks.append(self.whisper("="))
            else:
                chunks.append(self.value("-"))
        chunks.append(self.label("]"))
        return "".join(chunks)

    def _ring_interval_contains(
        self,
        slot_seconds: float,
        *,
        start_seconds: float,
        end_seconds: float,
        capacity_seconds: float,
        filled_seconds: float,
    ) -> bool:
        if filled_seconds <= 0.0:
            return False
        if filled_seconds >= capacity_seconds:
            return True
        if start_seconds <= end_seconds:
            return start_seconds <= slot_seconds < end_seconds
        return slot_seconds >= start_seconds or slot_seconds < end_seconds

    def _meter_index(self, value: float, width: int) -> int:
        if width <= 0:
            return 0
        normalized = max(0.0, min(1.0, value / 2500.0))
        return min(width - 1, max(0, int(round(normalized * (width - 1)))))

    def _stt_status_badge(self) -> tuple[str, str]:
        status = "running" if self.state.stt_running else "standby"
        duration = self.state.last_stt_duration or "--"
        return status, duration

    def _stt_badge(self) -> str:
        status, duration = self._stt_status_badge()
        status_style = self.whisper if status == "running" else self.subtle_value
        return (
            f"{self.label('[stt')} "
            f"{status_style(status)} "
            f"{self.value(duration)}"
            f"{self.label(']')}"
        )

    def _wake_badge(self) -> str:
        status = self.state.wake_status
        detail = self.state.wake_detail or "--"
        padded_status = status.ljust(len("listening"))
        if status == "awake":
            status_style = self.success_value
        elif status == "listening":
            status_style = self.whisper
        else:
            status_style = self.subtle_value
        return (
            f"{self.label('[wake')} "
            f"{status_style(padded_status)} "
            f"{self.value(detail)}"
            f"{self.label(']')}"
        )

    def _vad_badge(self) -> str:
        silence = self.state.trailing_silence_seconds
        if not self.state.speech_started:
            vad_label = "idle"
            label_style = self.subtle_value
            value_text = "0.00s"
        elif self.state.vad_active:
            vad_label = "live"
            label_style = self.success_value
            value_text = "0.00s"
        else:
            vad_label = "tail"
            label_style = self.warning_value
            value_text = f"{max(0.0, silence or 0.0):0.2f}s"
        return (
            f"{self.label('[')}"
            f"{label_style('vad')}"
            f" {label_style(vad_label)}"
            f" {self.value(value_text)}"
            f"{self.label(']')}"
        )

    def _pad_row(self, text: str, width: int) -> str:
        plain = _strip_ansi(text)
        if len(plain) > width:
            text = self._truncate_ansi(text, width)
            plain = _strip_ansi(text)
        return text + (" " * max(0, width - len(plain)))

    def _truncate_ansi(self, text: str, width: int) -> str:
        plain = _strip_ansi(text)
        clipped_plain = self._clip_plain(plain, width)
        if clipped_plain == plain:
            return text
        chunks = []
        remaining = len(clipped_plain)
        index = 0
        while index < len(text) and remaining > 0:
            if text[index] == "\033":
                start = index
                index += 1
                if index < len(text) and text[index] == "[":
                    index += 1
                    while index < len(text) and text[index] not in "@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~":
                        index += 1
                    if index < len(text):
                        index += 1
                    chunks.append(text[start:index])
                    continue
            chunks.append(text[index])
            index += 1
            remaining -= 1
        if "\033[" in text:
            chunks.append("\033[0m")
        return "".join(chunks)

    def _clip_plain(self, text: str, width: int) -> str:
        if width <= 0:
            return ""
        if len(text) <= width:
            return text
        if width <= 3:
            return text[:width]
        return textwrap.shorten(text, width=width, placeholder="...")

    def _clip_transcript_tail(self, text: str, width: int) -> str:
        if width <= 0:
            return ""
        if len(text) <= width:
            return text
        if width <= 3:
            return text[-width:]
        tail_width = width - 3
        return "..." + text[-tail_width:]

    def style(self, text: str, color_code: str) -> str:
        if not self.control_enabled:
            return text
        return f"\033[{color_code}m{text}\033[0m"

    def label(self, text: str) -> str:
        return self.style(text, "1;37")

    def status_value(self, text: str) -> str:
        color = {
            "idle": "32",
            "listening": "36",
            "processing": "33",
            "responding": "35",
            "error": "31",
        }.get(text, "37")
        return self.style(text, color)

    def emotion_value(self, text: str) -> str:
        color = {
            "neutral": "37",
            "listening": "36",
            "thinking": "33",
            "speaking": "35",
            "curious": "34",
            "happy": "32",
        }.get(text, "37")
        return self.style(text, color)

    def route_value(self, text: str) -> str:
        return self.style(text, "1;34")

    def value(self, text: str) -> str:
        return self.style(text, "37")

    def subtle_value(self, text: str) -> str:
        return self.style(text, "90")

    def metric_value(self, text: str) -> str:
        return self.style(text, "33")

    def peak_value(self, text: str) -> str:
        return self.style(text, "31")

    def success_value(self, text: str) -> str:
        return self.style(text, "32")

    def warning_value(self, text: str) -> str:
        return self.style(text, "33")

    def transcript(self, text: str) -> str:
        return self.style(text, "36")

    def whisper(self, text: str) -> str:
        return self.style(text, "33")

    def stt_label(self, text: str) -> str:
        return self.style(text, "1;36")

    def error(self, text: str) -> str:
        return self.style(text, "31")

    def badge(
        self,
        label: str,
        value: str,
        *,
        label_style=None,
        value_style,
    ) -> str:
        resolved_label_style = self.label if label_style is None else label_style
        return f"{resolved_label_style('[' + label)} {value_style(value)}{self.label(']')}"


@dataclass(slots=True)
class ConsoleFormatter:
    """Apply lightweight ANSI styling for interactive terminal output."""

    stream: TextIO = field(default_factory=lambda: sys.stdout)
    enabled: bool = False

    def __post_init__(self) -> None:
        self.enabled = _colors_enabled(self.stream)

    def style(self, text: str, color_code: str) -> str:
        if not self.enabled:
            return text
        return f"\033[{color_code}m{text}\033[0m"

    def transcript(self, text: str) -> str:
        return self.style(text, "36")

    def response(self, text: str) -> str:
        return self.style(text, "32")

    def error(self, text: str) -> str:
        return self.style(text, "31")

    def label(self, text: str) -> str:
        return self.style(text, "1;37")

    def ui_label(self, text: str) -> str:
        return self.style(text, "1;34")

    def tts_label(self, text: str) -> str:
        return self.style(text, "1;35")

    def stt_label(self, text: str) -> str:
        return self.style(text, "1;36")

    def route_label(self, text: str) -> str:
        return self.style(text, "1;34")

    def whisper(self, text: str) -> str:
        return self.style(text, "33")

    def timestamp(self) -> str:
        return self.style(datetime.now().strftime("[%H:%M:%S]"), "90")

    def stamp(self, text: str) -> str:
        return f"{self.timestamp()} {text}"

    def emit(
        self,
        styled_text: str,
        *,
        plain_text: str | None = None,
        end: str = "\n",
        flush: bool = False,
    ) -> None:
        if plain_text is None:
            plain_text = _strip_ansi(styled_text)
        if _terminal_debug_screen is not None and _terminal_debug_screen.stream is self.stream:
            _terminal_debug_screen.emit_log(styled_text, plain_text=plain_text, end=end, flush=flush)
            return
        self.stream.write(styled_text + end)
        if flush:
            self.stream.flush()
        _append_console_log(plain_text + ("\n" if not end else end.replace("\r", "\n")))
