"""Speech-to-text service interface and mock streaming implementation."""

from __future__ import annotations

import asyncio
import contextlib
import json
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from shared.models import Language, Transcript


class SttService(Protocol):
    """Interface for streaming transcript updates."""

    async def listen_once(self) -> Transcript:
        """Capture one utterance and return the final transcript."""

    async def stream_transcripts(self) -> AsyncIterator[Transcript]:
        """Yield partial and final transcript results."""


class AudioCaptureService(Protocol):
    """Interface for capturing microphone audio to a temporary WAV file."""

    async def capture_wav(self) -> Path:
        """Record one utterance and return a WAV file path."""


@dataclass(slots=True, frozen=True)
class CommandResult:
    """Minimal subprocess result used for dependency injection in tests."""

    args: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


SubprocessRunner = Callable[[Sequence[str]], Awaitable[CommandResult]]


async def _default_run_command(command: Sequence[str]) -> CommandResult:
    """Run a subprocess and capture text output."""

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return CommandResult(
        args=tuple(command),
        returncode=process.returncode,
        stdout=stdout.decode(),
        stderr=stderr.decode(),
    )


@dataclass(slots=True)
class MockSttService:
    """Deterministic transcript stream for tests and early development."""

    utterances: tuple[str, ...] = ()
    emit_partials: bool = True
    language: Language = Language.ENGLISH
    confidence: float = 0.98
    _sequences: tuple[tuple[Transcript, ...], ...] = field(default_factory=tuple)
    _listen_index: int = 0

    async def listen_once(self) -> Transcript:
        if self._sequences:
            if self._listen_index >= len(self._sequences):
                raise RuntimeError("mock STT has no remaining transcript sequences")

            sequence = self._sequences[self._listen_index]
            self._listen_index += 1
            for transcript in sequence:
                if transcript.is_final:
                    return transcript
            raise RuntimeError("mock STT has no final transcript configured")

        if not self.utterances:
            raise RuntimeError("mock STT has no utterances configured")
        if self._listen_index >= len(self.utterances):
            raise RuntimeError("mock STT has no remaining utterances configured")

        utterance = self.utterances[self._listen_index]
        self._listen_index += 1
        return next(
            transcript
            for transcript in self._build_sequence(utterance)
            if transcript.is_final
        )

    async def stream_transcripts(self) -> AsyncIterator[Transcript]:
        if self._sequences:
            for sequence in self._sequences:
                for transcript in sequence:
                    yield transcript
            return

        for utterance in self.utterances:
            for transcript in self._build_sequence(utterance):
                yield transcript

    def _build_sequence(self, utterance: str) -> Iterable[Transcript]:
        started_at = datetime.now(UTC)
        if self.emit_partials:
            words = utterance.split()
            if len(words) > 1:
                yield Transcript(
                    text=" ".join(words[:-1]),
                    language=self.language,
                    confidence=self.confidence,
                    is_final=False,
                    started_at=started_at,
                )

        yield Transcript(
            text=utterance,
            language=self.language,
            confidence=self.confidence,
            is_final=True,
            started_at=started_at,
            ended_at=datetime.now(UTC),
        )


@dataclass(slots=True)
class ShellAudioCaptureService:
    """Capture microphone audio by running a configured external recorder."""

    command_template: tuple[str, ...]
    record_seconds: int = 5
    output_dir: Path | None = None
    runner: SubprocessRunner = _default_run_command

    async def capture_wav(self) -> Path:
        output_dir = self.output_dir or Path(tempfile.gettempdir())
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"ai-companion-recording-{datetime.now(UTC).timestamp():.0f}.wav"
        command = self._render_command(output_path)
        result = await self.runner(command)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "audio capture failed")
        if not output_path.exists():
            raise RuntimeError("audio capture command finished without creating a WAV file")
        return output_path

    def _render_command(self, output_path: Path) -> tuple[str, ...]:
        if not self.command_template:
            raise RuntimeError(
                "audio_record_command is not configured; provide a recorder command such as arecord or ffmpeg"
            )

        replacements = {
            "{output_path}": str(output_path),
            "{duration_seconds}": str(self.record_seconds),
        }
        return tuple(_replace_many(token, replacements) for token in self.command_template)


def _replace_many(value: str, replacements: dict[str, str]) -> str:
    """Replace placeholder tokens inside a command template value."""

    rendered = value
    for key, replacement in replacements.items():
        rendered = rendered.replace(key, replacement)
    return rendered


@dataclass(slots=True)
class WhisperCppSttService:
    """One-shot `whisper.cpp` adapter backed by CLI invocations."""

    audio_capture: AudioCaptureService
    model_path: Path
    binary_path: Path | None = None
    language_mode: str = "auto"
    runner: SubprocessRunner = _default_run_command
    command_extra_args: tuple[str, ...] = ()
    keep_recent_recordings: int = 5

    async def listen_once(self) -> Transcript:
        audio_path = await self.audio_capture.capture_wav()
        started_at = datetime.now(UTC)
        output_path = audio_path.with_suffix("")
        command = self._build_command(audio_path, output_path)
        result = await self.runner(command)
        ended_at = datetime.now(UTC)
        if result.returncode != 0:
            error_text = result.stderr.strip() or result.stdout.strip() or "whisper.cpp transcription failed"
            raise RuntimeError(error_text)
        transcript_json = self._load_transcript_json(output_path, result.stdout)
        transcript = self._parse_transcript(transcript_json, started_at, ended_at)
        self._prune_recording_artifacts(audio_path)
        return transcript

    async def stream_transcripts(self) -> AsyncIterator[Transcript]:
        yield await self.listen_once()

    def _build_command(self, audio_path: Path, output_path: Path) -> tuple[str, ...]:
        if self.binary_path is None:
            raise RuntimeError("whisper binary path is not configured")

        command = [
            str(self.binary_path),
            "-m",
            str(self.model_path),
            "-f",
            str(audio_path),
            "--output-json",
            "--output-file",
            str(output_path),
            "-l",
            self.language_mode,
        ]
        command.extend(self.command_extra_args)
        return tuple(command)

    def _load_transcript_json(self, output_path: Path, stdout: str) -> str:
        """Load whisper output from the generated JSON file or stdout fallback."""

        for json_path in self._candidate_json_paths(output_path):
            if json_path.exists():
                return json_path.read_text()

        return stdout

    def _candidate_json_paths(self, output_path: Path) -> tuple[Path, ...]:
        """Support both `output.json` and `output.wav.json` whisper output names."""

        return (
            output_path.with_suffix(".json"),
            Path(f"{output_path}.wav.json"),
        )

    def _prune_recording_artifacts(self, latest_audio_path: Path) -> None:
        """Keep a small rolling history of recent WAV/JSON debugging artifacts."""

        if self.keep_recent_recordings <= 0:
            return

        pattern = "ai-companion-recording-*.wav"
        audio_paths = sorted(
            latest_audio_path.parent.glob(pattern),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for stale_audio_path in audio_paths[self.keep_recent_recordings :]:
            with contextlib.suppress(OSError):
                stale_audio_path.unlink()

            for json_path in self._recording_json_paths(stale_audio_path):
                with contextlib.suppress(OSError):
                    json_path.unlink()

    def _recording_json_paths(self, audio_path: Path) -> tuple[Path, ...]:
        """Return all JSON sidecar path variants that may exist for a WAV recording."""

        return (
            audio_path.with_suffix(".json"),
            Path(f"{audio_path}.json"),
        )

    def _parse_transcript(
        self,
        transcript_json: str,
        started_at: datetime,
        ended_at: datetime,
    ) -> Transcript:
        data = _extract_json_payload(transcript_json)
        transcript_text = _extract_transcript_text(data)
        result = data.get("result")
        language_code = result.get("language") if isinstance(result, dict) else None
        language = _map_language_code(language_code)
        return Transcript(
            text=transcript_text,
            language=language,
            confidence=1.0,
            is_final=True,
            started_at=started_at,
            ended_at=ended_at,
        )


def _extract_json_payload(stdout: str) -> dict[str, object]:
    """Parse stdout that may contain logs plus a final JSON document."""

    text = stdout.strip()
    if not text:
        raise RuntimeError("whisper.cpp returned no output")

    for start_index in range(len(text)):
        if text[start_index] != "{":
            continue
        try:
            parsed = json.loads(text[start_index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise RuntimeError("unable to parse whisper.cpp JSON output")


def _extract_transcript_text(data: dict[str, object]) -> str:
    """Support a couple of plausible whisper.cpp JSON result shapes."""

    result = data.get("result")
    if isinstance(result, dict):
        text = result.get("text")
        if isinstance(text, str):
            return text.strip()

        segments = result.get("segments")
        if isinstance(segments, list):
            pieces = []
            for segment in segments:
                if isinstance(segment, dict):
                    segment_text = segment.get("text")
                    if isinstance(segment_text, str):
                        pieces.append(segment_text.strip())
            if pieces:
                return " ".join(piece for piece in pieces if piece).strip()
            return ""

    transcription = data.get("transcription")
    if isinstance(transcription, str):
        return transcription.strip()
    if isinstance(transcription, list):
        pieces = []
        for item in transcription:
            if isinstance(item, dict):
                item_text = item.get("text")
                if isinstance(item_text, str):
                    pieces.append(item_text.strip())
        if pieces:
            return " ".join(piece for piece in pieces if piece).strip()
        return ""

    raise RuntimeError("whisper.cpp JSON output did not include transcript text")


def _map_language_code(code: object) -> Language:
    """Map a whisper language code to the project's supported enum."""

    if code == Language.GERMAN.value:
        return Language.GERMAN
    if code == Language.INDONESIAN.value:
        return Language.INDONESIAN
    return Language.ENGLISH
