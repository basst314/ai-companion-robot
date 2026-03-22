"""Terminal-friendly console formatting helpers."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TextIO

_console_log_path: Path | None = None


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


def _append_console_log(line: str) -> None:
    if _console_log_path is None:
        return
    with _console_log_path.open("a", encoding="utf-8") as handle:
        handle.write(line)


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
        self.stream.write(styled_text + end)
        if flush:
            self.stream.flush()
        if plain_text is None:
            plain_text = styled_text
        _append_console_log(plain_text + ("\n" if not end else end.replace("\r", "\n")))
