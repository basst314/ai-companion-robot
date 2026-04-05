"""Tests for the dedicated ALSA playback worker."""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time

import pytest

from tts.alsa_backend import AlsaPcmConfig, AlsaPlaybackWorker, AlsaQueuedPcmJob


@dataclass(slots=True)
class _FakePcm:
    writes: list[bytes] = field(default_factory=list)
    drop_calls: int = 0
    close_calls: int = 0
    channels: int | None = None
    rate: int | None = None
    fmt: int | None = None
    periodsize: int | None = None

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
        self.channels = channels

    def setrate(self, rate: int) -> None:
        self.rate = rate

    def setformat(self, fmt: int) -> None:
        self.fmt = fmt

    def setperiodsize(self, frames: int) -> None:
        self.periodsize = frames


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


@dataclass(slots=True)
class _FallbackAlsaModule(_FakeAlsaModule):
    pcm_calls: int = 0

    def PCM(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.pcm_calls += 1
        if self.pcm_calls == 1:
            raise TypeError("legacy constructor")
        return _FakeAlsaModule.PCM(self, *args, **kwargs)


@dataclass(slots=True)
class _FailingPcm(_FakePcm):
    fail_after_writes: int = 1

    def write(self, data: bytes) -> int:
        result = _FakePcm.write(self, data)
        if len(self.writes) >= self.fail_after_writes:
            raise RuntimeError("pcm write failed")
        return result


@dataclass(slots=True)
class _FailingAlsaModule(_FakeAlsaModule):
    fail_after_writes: int = 1

    def PCM(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        self.last_pcm = _FailingPcm(fail_after_writes=self.fail_after_writes)
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


def test_alsa_playback_worker_supports_legacy_pcm_constructor() -> None:
    fake_alsa = _FallbackAlsaModule()
    worker = AlsaPlaybackWorker(
        AlsaPcmConfig(
            device="default",
            sample_rate_hz=22050,
            channels=1,
            period_frames=128,
            buffer_frames=512,
            keepalive_interval_ms=20,
        ),
        alsa_module_factory=lambda: fake_alsa,
    )

    worker.ensure_started()
    worker.shutdown()

    assert fake_alsa.last_pcm is not None
    assert fake_alsa.last_pcm.channels == 1
    assert fake_alsa.last_pcm.rate == 22050
    assert fake_alsa.last_pcm.fmt == fake_alsa.PCM_FORMAT_S16_LE
    assert fake_alsa.last_pcm.periodsize == 128


def test_alsa_playback_worker_reports_startup_error() -> None:
    worker = AlsaPlaybackWorker(
        AlsaPcmConfig(
            device="default",
            sample_rate_hz=16000,
            channels=1,
            period_frames=64,
            buffer_frames=256,
            keepalive_interval_ms=10,
        ),
        alsa_module_factory=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="failed to start ALSA playback worker: boom"):
        worker.ensure_started()


def test_alsa_playback_worker_fails_current_and_pending_jobs() -> None:
    fake_alsa = _FailingAlsaModule(fail_after_writes=2)
    failed: list[tuple[str, str]] = []
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

    def make_job(job_id: str) -> AlsaQueuedPcmJob:
        return AlsaQueuedPcmJob(
            job_id=job_id,
            pcm_frames=b"\x01\x00" * 512,
            on_started=lambda: None,
            on_finished=lambda duration_ms: None,
            on_interrupted=lambda duration_ms: None,
            on_failed=lambda exc, job_id=job_id: failed.append((job_id, str(exc))),
        )

    worker.ensure_started()
    worker.enqueue(make_job("job-1"))
    worker.enqueue(make_job("job-2"))

    deadline = time.monotonic() + 1.0
    while len(failed) < 2:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    worker.shutdown()

    assert ("job-1", "pcm write failed") in failed
    assert ("job-2", "pcm write failed") in failed
