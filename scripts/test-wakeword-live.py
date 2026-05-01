#!/usr/bin/env python3
"""Continuously score live microphone audio against the configured wake-word model."""

from __future__ import annotations

import argparse
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from shared.config import load_app_config
from audio.wake import OpenWakeWordModelAdapter


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run live microphone audio through the configured openWakeWord model.",
    )
    parser.add_argument("--model", help="Override the wake-word model path.")
    parser.add_argument("--threshold", type=float, help="Override the wake-word threshold.")
    parser.add_argument(
        "--frame-ms",
        type=int,
        default=80,
        help="Scoring frame size in milliseconds. Defaults to 80 to match the app.",
    )
    parser.add_argument(
        "--print-every",
        type=int,
        default=0,
        help="Print every N scored frames regardless of score. Defaults to 0 to disable periodic low-score output.",
    )
    parser.add_argument(
        "--quiet-below",
        type=float,
        default=0.0,
        help="Suppress score lines below this confidence. Detection lines still print.",
    )
    parser.add_argument(
        "--debounce-seconds",
        type=float,
        default=1.0,
        help="Debounce detections by this many seconds.",
    )
    parser.add_argument("--wav-out", help="Optional path to save captured microphone audio as a WAV file.")
    parser.add_argument("--max-seconds", type=float, help="Optional maximum runtime before stopping.")
    return parser.parse_args()


def _render_command(command_template: tuple[str, ...]) -> list[str]:
    if not command_template:
        raise RuntimeError(
            "audio_record_command is not configured; set AI_COMPANION_AUDIO_RECORD_COMMAND in .env.local",
        )
    return [token.replace("{output_path}", "-") for token in command_template]


def _format_status(score: float, threshold: float, detected: bool) -> str:
    status = "DETECTED" if detected else "listening"
    return f"{status:<9} score={score:0.4f} threshold={threshold:0.4f}"


def _write_wav(path: Path, pcm_data: bytes, *, sample_rate: int, channels: int, sample_width: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_data)


def _extract_fallback_sample_rate(stderr_text: str) -> int | None:
    match = re.search(r"using\s+(\d+)", stderr_text)
    if match is None:
        return None
    try:
        parsed = int(match.group(1))
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _reader_thread(
    stream,
    target_queue: queue.Queue[bytes | None],
    stop_event: threading.Event,
) -> None:
    try:
        while not stop_event.is_set():
            chunk = stream.read(4096)
            if not chunk:
                break
            target_queue.put(chunk)
    finally:
        target_queue.put(None)


def _stderr_thread(
    stream,
    stderr_chunks: list[bytes],
    stop_event: threading.Event,
) -> None:
    try:
        while not stop_event.is_set():
            chunk = stream.read(4096)
            if not chunk:
                break
            stderr_chunks.append(chunk)
    finally:
        return


def main() -> int:
    args = parse_args()
    config = load_app_config(base_dir=REPO_ROOT)
    runtime = config.runtime

    model_path = Path(args.model or runtime.wake_word_model).expanduser()
    if not model_path.exists():
        raise FileNotFoundError(f"wake-word model not found: {model_path}")

    threshold = args.threshold if args.threshold is not None else runtime.wake_word_threshold
    threshold = min(max(threshold, 0.0), 1.0)
    frame_duration_seconds = max(0.01, args.frame_ms / 1000.0)
    print_every = max(0, args.print_every)
    max_seconds = args.max_seconds if args.max_seconds is None else max(0.1, args.max_seconds)
    wav_out = Path(args.wav_out).expanduser() if args.wav_out else None

    command = _render_command(runtime.audio_record_command)
    adapter = OpenWakeWordModelAdapter(wake_word_model=str(model_path))

    expected_sample_rate = 16000
    saved_sample_rate = expected_sample_rate
    channels = 1
    sample_width = 2
    bytes_per_frame = max(1, int(expected_sample_rate * sample_width * frame_duration_seconds))

    print(f"Model     : {model_path}")
    print(f"Threshold : {threshold:0.4f}")
    print(f"Command   : {' '.join(command)}")
    if wav_out is not None:
        print(f"WAV out   : {wav_out}")
    if max_seconds is not None:
        print(f"Max time  : {max_seconds:0.1f}s")
    print("Listening : press Ctrl-C to stop")

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("audio recorder did not expose stdout/stderr")

    stop_event = threading.Event()
    chunk_queue: queue.Queue[bytes | None] = queue.Queue()
    stderr_chunks: list[bytes] = []
    stdout_reader = threading.Thread(
        target=_reader_thread,
        args=(process.stdout, chunk_queue, stop_event),
        daemon=True,
    )
    stderr_reader = threading.Thread(
        target=_stderr_thread,
        args=(process.stderr, stderr_chunks, stop_event),
        daemon=True,
    )
    stdout_reader.start()
    stderr_reader.start()

    stop_requested = False

    def _stop_process(*_: object) -> None:
        nonlocal stop_requested
        stop_requested = True
        stop_event.set()
        if process.poll() is None:
            process.terminate()

    previous_sigint = signal.signal(signal.SIGINT, _stop_process)

    frame_buffer = bytearray()
    captured_pcm = bytearray()
    frames_seen = 0
    debounce_until = 0.0
    last_detection_at: float | None = None
    started_at = time.monotonic()
    stdout_done = False
    peak_score = 0.0
    detection_count = 0
    detection_times: list[float] = []
    top_scores: list[float] = []

    try:
        while True:
            elapsed = time.monotonic() - started_at
            if max_seconds is not None and elapsed >= max_seconds and not stop_requested:
                _stop_process()

            try:
                item = chunk_queue.get(timeout=0.2)
            except queue.Empty:
                if process.poll() is not None and stdout_done:
                    break
                if stop_requested and process.poll() is not None:
                    break
                continue

            if item is None:
                stdout_done = True
                if process.poll() is not None or stop_requested:
                    break
                continue

            if wav_out is not None:
                captured_pcm.extend(item)
            frame_buffer.extend(item)

            while len(frame_buffer) >= bytes_per_frame:
                frame = bytes(frame_buffer[:bytes_per_frame])
                del frame_buffer[:bytes_per_frame]

                frames_seen += 1
                score = adapter.score_frame(frame)
                peak_score = max(peak_score, score)
                top_scores.append(score)
                top_scores.sort(reverse=True)
                del top_scores[5:]
                now = time.monotonic()
                detected = False
                if score >= threshold and now >= debounce_until:
                    detected = True
                    debounce_until = now + max(0.0, args.debounce_seconds)
                elapsed = time.monotonic() - started_at

                periodic_print = print_every > 0 and frames_seen % print_every == 0
                should_print = detected or score >= args.quiet_below or periodic_print
                if not should_print:
                    continue

                prefix = f"[{elapsed:7.2f}s]"
                if detected:
                    last_detection_at = elapsed
                    detection_count += 1
                    detection_times.append(elapsed)
                    print(f"{prefix} {_format_status(score, threshold, True)}", flush=True)
                    continue

                since_text = ""
                if last_detection_at is not None:
                    since_text = f" since_last_detect={elapsed - last_detection_at:0.2f}s"
                print(f"{prefix} {_format_status(score, threshold, False)}{since_text}", flush=True)

        try:
            return_code = process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            return_code = process.wait(timeout=2)

        stop_event.set()
        stdout_reader.join(timeout=1)
        stderr_reader.join(timeout=1)

        stderr_text = b"".join(stderr_chunks).decode(errors="replace").strip()
        fallback_sample_rate = _extract_fallback_sample_rate(stderr_text)
        if fallback_sample_rate is not None:
            saved_sample_rate = fallback_sample_rate

        sample_rate_warning = "can't set sample rate 16000" in stderr_text
        if sample_rate_warning:
            print("Warning   : recorder could not capture at 16000 Hz and fell back to a different sample rate.", file=sys.stderr)
            print("Warning   : wake-word scores may be inaccurate until AI_COMPANION_AUDIO_RECORD_COMMAND is fixed.", file=sys.stderr)
        elif stderr_text:
            print(f"Recorder  : {stderr_text}", file=sys.stderr)

        if wav_out is not None:
            _write_wav(
                wav_out,
                bytes(captured_pcm),
                sample_rate=saved_sample_rate,
                channels=channels,
                sample_width=sample_width,
            )
            print(f"Saved WAV : {wav_out}")

        duration_seconds = time.monotonic() - started_at
        top_score_text = ", ".join(f"{score:0.4f}" for score in top_scores) if top_scores else "--"
        first_detection_text = f"{detection_times[0]:0.2f}s" if detection_times else "--"
        print("Summary   :")
        print(f"  duration={duration_seconds:0.2f}s frames={frames_seen} peak={peak_score:0.4f}")
        print(f"  detections={detection_count} first_detection={first_detection_text}")
        print(f"  top_scores={top_score_text}")

        if return_code not in (0, -15) and not stop_requested:
            raise RuntimeError(stderr_text or f"audio recorder exited with status {return_code}")
        return 0
    except KeyboardInterrupt:
        _stop_process()
        return 130
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        stop_event.set()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
