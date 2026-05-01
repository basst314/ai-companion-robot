#!/usr/bin/env python3
"""Capture one channel from a multichannel ReSpeaker stream as raw mono PCM."""

from __future__ import annotations

import argparse
from pathlib import Path
import signal
import subprocess
import sys
from typing import Sequence

REPO_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from audio.respeaker_capture import extract_interleaved_channel


def _build_arecord_command(
    *,
    device: str,
    sample_rate: int,
    channels: int,
) -> list[str]:
    return [
        "arecord",
        "-D",
        device,
        "-t",
        "raw",
        "-f",
        "S16_LE",
        "-r",
        str(sample_rate),
        "-c",
        str(channels),
        "-q",
        "-",
    ]


def run(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", required=True)
    parser.add_argument("--input-channels", type=int, default=6)
    parser.add_argument("--channel-index", type=int, default=0)
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument("--sample-width", type=int, default=2)
    parser.add_argument("output_path", nargs="?", default="-")
    args = parser.parse_args(argv)

    if args.output_path not in {"", "-"}:
        parser.error("only stdout output is supported")

    process = subprocess.Popen(
        _build_arecord_command(
            device=args.device,
            sample_rate=args.sample_rate,
            channels=args.input_channels,
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    def _forward_signal(signum, _frame) -> None:  # type: ignore[no-untyped-def]
        if process.poll() is None:
            process.terminate()

    signal.signal(signal.SIGTERM, _forward_signal)
    signal.signal(signal.SIGINT, _forward_signal)

    try:
        if process.stdout is None:
            raise RuntimeError("arecord stdout pipe was not created")
        while True:
            chunk = process.stdout.read(4092)
            if not chunk:
                break
            mono_chunk = extract_interleaved_channel(
                chunk,
                channels=args.input_channels,
                channel_index=args.channel_index,
                sample_width=args.sample_width,
            )
            if mono_chunk:
                sys.stdout.buffer.write(mono_chunk)
                sys.stdout.buffer.flush()
        return process.wait()
    finally:
        stderr = b""
        if process.stderr is not None:
            stderr = process.stderr.read()
        if stderr:
            sys.stderr.buffer.write(stderr)
            sys.stderr.buffer.flush()
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=2.0)


if __name__ == "__main__":
    raise SystemExit(run())
