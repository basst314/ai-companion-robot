"""Dedicated ALSA playback worker used for low-latency Pi speech output."""

from __future__ import annotations

import contextlib
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
import queue
import threading
import time
from typing import Protocol


class AlsaModuleProtocol(Protocol):
    PCM_PLAYBACK: int
    PCM_NORMAL: int
    PCM_FORMAT_S16_LE: int

    def PCM(self, *args, **kwargs): ...  # type: ignore[no-untyped-def]


class PcmHandleProtocol(Protocol):
    def write(self, data: bytes): ...  # type: ignore[no-untyped-def]

    def close(self) -> None: ...

    def setchannels(self, channels: int) -> None: ...

    def setrate(self, rate: int) -> None: ...

    def setformat(self, fmt: int) -> None: ...

    def setperiodsize(self, frames: int) -> None: ...


@dataclass(slots=True, frozen=True)
class AlsaPcmConfig:
    device: str
    sample_rate_hz: int
    channels: int
    period_frames: int
    buffer_frames: int
    keepalive_interval_ms: int


@dataclass(slots=True, frozen=True)
class AlsaQueuedPcmJob:
    job_id: str
    pcm_frames: bytes
    on_started: Callable[[], None]
    on_finished: Callable[[int], None]
    on_interrupted: Callable[[int], None]
    on_failed: Callable[[Exception], None]


@dataclass(slots=True)
class _PlayCommand:
    job: AlsaQueuedPcmJob


@dataclass(slots=True)
class _InterruptCommand:
    job_id: str | None


@dataclass(slots=True)
class _ActiveJob:
    job: AlsaQueuedPcmJob
    offset_bytes: int = 0
    started_at: float | None = None
    started_emitted: bool = False


class _ShutdownCommand:
    pass


class AlsaPlaybackWorker:
    """Own one ALSA playback device in a dedicated worker thread."""

    def __init__(
        self,
        config: AlsaPcmConfig,
        *,
        alsa_module_factory: Callable[[], AlsaModuleProtocol] | None = None,
        startup_timeout_seconds: float = 5.0,
        shutdown_timeout_seconds: float = 5.0,
    ) -> None:
        self.config = config
        self._alsa_module_factory = alsa_module_factory or _import_alsa_module
        self._startup_timeout_seconds = startup_timeout_seconds
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._command_queue: queue.SimpleQueue[object] = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._shutdown_complete = threading.Event()
        self._startup_error: Exception | None = None

    def ensure_started(self) -> None:
        thread = self._thread
        if thread is not None and thread.is_alive():
            return

        self._ready.clear()
        self._shutdown_complete.clear()
        self._startup_error = None
        self._thread = threading.Thread(target=self._run, name="alsa-playback-worker", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=self._startup_timeout_seconds):
            raise RuntimeError("ALSA playback worker did not start in time")
        if self._startup_error is not None:
            raise RuntimeError(f"failed to start ALSA playback worker: {self._startup_error}") from self._startup_error

    def enqueue(self, job: AlsaQueuedPcmJob) -> None:
        self.ensure_started()
        self._command_queue.put(_PlayCommand(job))

    def interrupt(self, *, job_id: str | None = None) -> None:
        self._command_queue.put(_InterruptCommand(job_id=job_id))

    def shutdown(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._command_queue.put(_ShutdownCommand())
        self._shutdown_complete.wait(timeout=self._shutdown_timeout_seconds)
        thread.join(timeout=self._shutdown_timeout_seconds)
        self._thread = None

    def _run(self) -> None:
        pcm = None
        current: _ActiveJob | None = None
        pending_jobs: deque[AlsaQueuedPcmJob] = deque()
        try:
            pcm = self._open_pcm_handle()
            self._ready.set()
            silence_chunk = b"\x00" * self._keepalive_chunk_bytes()
            speech_chunk_bytes = self._period_chunk_bytes()

            while True:
                shutdown_requested = False
                for command in self._drain_commands():
                    if isinstance(command, _PlayCommand):
                        pending_jobs.append(command.job)
                    elif isinstance(command, _InterruptCommand):
                        current = self._interrupt_jobs(pcm, current, pending_jobs, command.job_id)
                    else:
                        shutdown_requested = True
                        break

                if shutdown_requested:
                    current = self._interrupt_jobs(pcm, current, pending_jobs, None)
                    return

                if current is None and pending_jobs:
                    current = _ActiveJob(job=pending_jobs.popleft())

                if current is None:
                    pcm.write(silence_chunk)
                    continue

                if not current.started_emitted:
                    current.started_emitted = True
                    current.started_at = time.monotonic()
                    _safe_callback(current.job.on_started)

                next_chunk = current.job.pcm_frames[current.offset_bytes : current.offset_bytes + speech_chunk_bytes]
                pcm.write(next_chunk)
                current.offset_bytes += len(next_chunk)
                if current.offset_bytes >= len(current.job.pcm_frames):
                    duration_ms = int(max(0.0, time.monotonic() - (current.started_at or time.monotonic())) * 1000)
                    _safe_finished_callback(current.job.on_finished, duration_ms)
                    current = None
        except Exception as exc:
            self._startup_error = exc
            self._fail_current_and_pending(current, pending_jobs, exc)
        finally:
            self._ready.set()
            self._shutdown_complete.set()
            if pcm is not None:
                with contextlib.suppress(Exception):
                    pcm.close()

    def _open_pcm_handle(self) -> PcmHandleProtocol:
        alsa = self._alsa_module_factory()
        periods = max(2, self.config.buffer_frames // self.config.period_frames)
        try:
            pcm = alsa.PCM(
                type=alsa.PCM_PLAYBACK,
                mode=alsa.PCM_NORMAL,
                device=self.config.device,
                channels=self.config.channels,
                rate=self.config.sample_rate_hz,
                format=alsa.PCM_FORMAT_S16_LE,
                periodsize=self.config.period_frames,
                periods=periods,
            )
        except TypeError:
            pcm = alsa.PCM(
                type=alsa.PCM_PLAYBACK,
                mode=alsa.PCM_NORMAL,
                device=self.config.device,
            )
            pcm.setchannels(self.config.channels)
            pcm.setrate(self.config.sample_rate_hz)
            pcm.setformat(alsa.PCM_FORMAT_S16_LE)
            pcm.setperiodsize(self.config.period_frames)
        return pcm

    def _drain_commands(self) -> list[object]:
        commands: list[object] = []
        while True:
            try:
                commands.append(self._command_queue.get_nowait())
            except queue.Empty:
                return commands

    def _interrupt_jobs(
        self,
        pcm: PcmHandleProtocol,
        current: _ActiveJob | None,
        pending_jobs: deque[AlsaQueuedPcmJob],
        job_id: str | None,
    ) -> _ActiveJob | None:
        if current is not None and (job_id is None or current.job.job_id == job_id):
            _drop_pcm_if_supported(pcm)
            duration_ms = 0
            if current.started_at is not None:
                duration_ms = int(max(0.0, time.monotonic() - current.started_at) * 1000)
            _safe_finished_callback(current.job.on_interrupted, duration_ms)
            current = None

        kept: deque[AlsaQueuedPcmJob] = deque()
        while pending_jobs:
            pending = pending_jobs.popleft()
            if job_id is None or pending.job_id == job_id:
                _safe_finished_callback(pending.on_interrupted, 0)
            else:
                kept.append(pending)
        pending_jobs.extend(kept)
        return current

    def _fail_current_and_pending(
        self,
        current: _ActiveJob | None,
        pending_jobs: deque[AlsaQueuedPcmJob],
        exc: Exception,
    ) -> None:
        if current is not None:
            _safe_callback(lambda: current.job.on_failed(exc))
        while pending_jobs:
            pending = pending_jobs.popleft()
            _safe_callback(lambda pending=pending: pending.on_failed(exc))

    def _period_chunk_bytes(self) -> int:
        return self.config.period_frames * self.config.channels * 2

    def _keepalive_chunk_bytes(self) -> int:
        keepalive_frames = max(
            1,
            min(
                self.config.period_frames,
                int(self.config.sample_rate_hz * (self.config.keepalive_interval_ms / 1000.0)),
            ),
        )
        return keepalive_frames * self.config.channels * 2


def _safe_callback(callback: Callable[[], None]) -> None:
    with contextlib.suppress(Exception):
        callback()


def _safe_finished_callback(callback: Callable[[int], None], duration_ms: int) -> None:
    with contextlib.suppress(Exception):
        callback(duration_ms)


def _drop_pcm_if_supported(pcm: PcmHandleProtocol) -> None:
    drop = getattr(pcm, "drop", None)
    if callable(drop):
        with contextlib.suppress(Exception):
            drop()
    prepare = getattr(pcm, "prepare", None)
    if callable(prepare):
        with contextlib.suppress(Exception):
            prepare()


def _import_alsa_module() -> AlsaModuleProtocol:
    import alsaaudio  # type: ignore[import-not-found]

    return alsaaudio
