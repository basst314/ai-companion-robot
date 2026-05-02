"""Tests for shared live audio capture helpers."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import wave

from audio.capture import SharedLiveSpeechState


class _FakeRecordingSession:
    def __init__(self) -> None:
        self.output_path = Path("fake.wav")
        self.pcm_path = Path("fake.pcm")
        self.returncode = None
        self.stop_calls = 0

    @property
    def stop_requested(self) -> bool:
        return False

    def mark_stop_requested(self) -> None:
        return None

    async def stop(self) -> None:
        self.stop_calls += 1
        self.returncode = 0

    async def wait(self) -> int:
        return 0


class _FakeStreamingCapture:
    sample_rate = 16000
    channels = 1
    sample_width = 2

    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self.chunks = chunks
        self.session = _FakeRecordingSession()

    async def start_capture(self, on_chunk):  # type: ignore[no-untyped-def]
        for chunk in self.chunks:
            on_chunk(chunk)
        return self.session


def test_shared_live_state_session_recording_writes_valid_wav(tmp_path: Path) -> None:
    chunks = (b"\x01\x00\x02\x00", b"\x03\x00\x04\x00")
    capture = _FakeStreamingCapture(chunks)
    state = SharedLiveSpeechState(
        audio_capture=capture,
        wake_buffer_seconds=1.0,
        sample_rate=16000,
        channels=1,
        sample_width=2,
        session_recording_enabled=True,
        session_recording_dir=tmp_path,
    )

    async def run() -> Path:
        path = state.start_session_recording()
        assert path is not None
        await state.ensure_session()
        assert state.session_recording_path is not None
        state.stop_session_recording()
        await state.close()
        return path

    recording_path = asyncio.run(run())

    assert capture.session.stop_calls == 1
    assert recording_path.exists()
    with wave.open(str(recording_path), "rb") as wav_file:
        assert wav_file.getframerate() == 16000
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.readframes(wav_file.getnframes()) == b"".join(chunks)


def test_shared_live_state_session_recording_writes_initial_pcm(tmp_path: Path) -> None:
    capture = _FakeStreamingCapture((b"\x03\x00\x04\x00",))
    state = SharedLiveSpeechState(
        audio_capture=capture,
        wake_buffer_seconds=1.0,
        sample_rate=16000,
        channels=1,
        sample_width=2,
        session_recording_enabled=True,
        session_recording_dir=tmp_path,
    )

    async def run() -> Path:
        path = state.start_session_recording(initial_pcm=b"\x01\x00\x02\x00")
        assert path is not None
        await state.ensure_session()
        state.stop_session_recording()
        await state.close()
        return path

    recording_path = asyncio.run(run())

    with wave.open(str(recording_path), "rb") as wav_file:
        assert wav_file.readframes(wav_file.getnframes()) == b"\x01\x00\x02\x00\x03\x00\x04\x00"


def test_shared_live_state_session_recording_rotation_keeps_last_five(tmp_path: Path) -> None:
    state = SharedLiveSpeechState(
        audio_capture=_FakeStreamingCapture(()),
        wake_buffer_seconds=1.0,
        session_recording_enabled=True,
        session_recording_dir=tmp_path,
        session_recording_keep_count=5,
    )

    for index in range(7):
        stale_path = tmp_path / f"ai-companion-session-old-{index}.wav"
        stale_path.write_bytes(b"old")
        stale_time = 1_700_000_000 + index
        os.utime(stale_path, (stale_time, stale_time))

    path = state.start_session_recording(initial_pcm=b"\x01\x00")
    assert path is not None
    state.stop_session_recording()

    recordings = sorted(tmp_path.glob("ai-companion-session-*.wav"))
    assert len(recordings) == 5
    assert path in recordings
    assert not (tmp_path / "ai-companion-session-old-0.wav").exists()


def test_shared_live_state_session_recording_is_optional(tmp_path: Path) -> None:
    capture = _FakeStreamingCapture((b"\x01\x00",))
    state = SharedLiveSpeechState(
        audio_capture=capture,
        wake_buffer_seconds=1.0,
        session_recording_enabled=False,
        session_recording_dir=tmp_path,
    )

    async def run() -> None:
        await state.ensure_session()
        assert state.start_session_recording(initial_pcm=b"\x02\x00") is None
        await state.close()

    asyncio.run(run())

    assert state.session_recording_path is None
    assert list(tmp_path.iterdir()) == []
