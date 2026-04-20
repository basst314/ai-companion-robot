from __future__ import annotations

import struct

import pytest

from stt.respeaker_capture import extract_interleaved_channel


def test_extract_interleaved_channel_returns_requested_channel() -> None:
    frames = [
        (100, 200, 300, 400, 500, 600),
        (101, 201, 301, 401, 501, 601),
        (102, 202, 302, 402, 502, 602),
    ]
    pcm = struct.pack("<18h", *(sample for frame in frames for sample in frame))

    extracted = extract_interleaved_channel(pcm, channels=6, channel_index=0)

    assert struct.unpack("<3h", extracted) == (100, 101, 102)


def test_extract_interleaved_channel_ignores_trailing_partial_frame() -> None:
    pcm = struct.pack("<12h", *range(12)) + b"\xAA\xBB"

    extracted = extract_interleaved_channel(pcm, channels=6, channel_index=1)

    assert struct.unpack("<2h", extracted) == (1, 7)


def test_extract_interleaved_channel_validates_inputs() -> None:
    with pytest.raises(ValueError):
        extract_interleaved_channel(b"", channels=0, channel_index=0)
    with pytest.raises(ValueError):
        extract_interleaved_channel(b"", channels=6, channel_index=6)
