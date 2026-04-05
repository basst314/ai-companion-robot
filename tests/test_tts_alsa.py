"""Tests for the dedicated ALSA playback worker."""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time

from tts.alsa_backend import AlsaPcmConfig, AlsaPlaybackWorker, AlsaQueuedPcmJob


@dataclass(slots=True)
class _FakePcm:
    writes: list[bytes] = field(default_factory=list)
    drop_calls: int = 0
    close_calls: int = 0

    def write(self, data: bytes) -> int:
        self.writes.append(bytes(data))
        time.sleep(0.002)
        return len(data)

    def close(self) -> None:
        self.close_calls += 1

    def drop(self) -> None:
        self.drop_calls += 1

    def prepare(self) -> None:
        return None

    def setchannels(self, channels: int) -> None:
        del channels

    def setrate(self, rate: int) -> None:
        del rate

    def setformat(self, fmt: int) -> None:
        del fmt

    def setperiodsize(self, frames: int) -> None:
        del frames


@dataclass(slots=True)
class _FakeAlsaModule:
    PCM_PLAYBACK: int = 0
    PCM_NORMAL: int = 0
    PCM_FORMAT_S16_LE: int = 0
    last_pcm: _FakePcm | None = None

    def PCM(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        self.last_pcm = _FakePcm()
        return self.last_pcm


def test_alsa_playback_worker_plays_speech_job() -> None:
    fake_alsa = _FakeAlsaModule()
    started = threading.Event()
    finished = threading.Event()
    durations: list[int] = []
    worker = AlsaPlaybackWorker(
        AlsaPcmConfig(
            device="default",
            sample_rate_hz=16000,
            channels=1,
            period_frames=64,
            buffer_frames=256,
            keepalive_interval_ms=10,
        ),
        alsa_module_factory=lambda: fake_alsa,
    )

    worker.ensure_started()
    worker.enqueue(
        AlsaQueuedPcmJob(
            job_id="job-1",
            pcm_frames=b"\x01\x00" * 256,
            on_started=started.set,
            on_finished=lambda duration_ms: (durations.append(duration_ms), finished.set()),
            on_interrupted=lambda duration_ms: None,
            on_failed=lambda exc: (_ for _ in ()).throw(exc),
        )
    )

    assert started.wait(timeout=1.0)
    assert finished.wait(timeout=1.0)
    worker.shutdown()

    assert durations and durations[0] >= 0
    assert fake_alsa.last_pcm is not None
    assert any(chunk and set(chunk) != {0} for chunk in fake_alsa.last_pcm.writes)


def test_alsa_playback_worker_interrupts_current_job() -> None:
    fake_alsa = _FakeAlsaModule()
    started = threading.Event()
    interrupted = threading.Event()
    worker = AlsaPlaybackWorker(
        AlsaPcmConfig(
            device="default",
            sample_rate_hz=16000,
            channels=1,
            period_frames=64,
            buffer_frames=256,
            keepalive_interval_ms=10,
        ),
        alsa_module_factory=lambda: fake_alsa,
    )

    worker.ensure_started()
    worker.enqueue(
        AlsaQueuedPcmJob(
            job_id="job-2",
            pcm_frames=b"\x02\x00" * 4096,
            on_started=started.set,
            on_finished=lambda duration_ms: None,
            on_interrupted=lambda duration_ms: interrupted.set(),
            on_failed=lambda exc: (_ for _ in ()).throw(exc),
        )
    )

    assert started.wait(timeout=1.0)
    worker.interrupt(job_id="job-2")
    assert interrupted.wait(timeout=1.0)
    worker.shutdown()

    assert fake_alsa.last_pcm is not None
    assert fake_alsa.last_pcm.drop_calls >= 1
