"""Helpers for extracting one channel from the ReSpeaker multichannel stream."""

from __future__ import annotations

from dataclasses import dataclass, field


def extract_interleaved_channel(
    pcm_chunk: bytes,
    *,
    channels: int,
    channel_index: int,
    sample_width: int = 2,
) -> bytes:
    """Return one channel from interleaved PCM frames."""

    if channels <= 0:
        raise ValueError("channels must be positive")
    if sample_width <= 0:
        raise ValueError("sample_width must be positive")
    if not 0 <= channel_index < channels:
        raise ValueError("channel_index out of range")

    frame_bytes = channels * sample_width
    complete_bytes = len(pcm_chunk) - (len(pcm_chunk) % frame_bytes)
    if complete_bytes <= 0:
        return b""

    source = memoryview(pcm_chunk)[:complete_bytes]
    channel_offset = channel_index * sample_width
    extracted = bytearray((complete_bytes // frame_bytes) * sample_width)
    write_offset = 0
    for frame_offset in range(0, complete_bytes, frame_bytes):
        extracted[write_offset : write_offset + sample_width] = source[
            frame_offset + channel_offset : frame_offset + channel_offset + sample_width
        ]
        write_offset += sample_width
    return bytes(extracted)


@dataclass(slots=True)
class InterleavedChannelExtractor:
    """Incrementally extract one PCM channel from a multichannel byte stream."""

    channels: int
    channel_index: int
    sample_width: int = 2
    _remainder: bytearray = field(default_factory=bytearray, init=False, repr=False)

    def feed(self, pcm_chunk: bytes) -> bytes:
        """Return extracted mono bytes while keeping incomplete frames buffered."""

        if not pcm_chunk:
            return b""
        frame_bytes = self.channels * self.sample_width
        if frame_bytes <= 0:
            raise ValueError("frame size must be positive")

        self._remainder.extend(pcm_chunk)
        complete_bytes = len(self._remainder) - (len(self._remainder) % frame_bytes)
        if complete_bytes <= 0:
            return b""

        complete_chunk = bytes(self._remainder[:complete_bytes])
        del self._remainder[:complete_bytes]
        return extract_interleaved_channel(
            complete_chunk,
            channels=self.channels,
            channel_index=self.channel_index,
            sample_width=self.sample_width,
        )

    def flush(self) -> bytes:
        """Drop any incomplete trailing frame bytes and finish the current stream."""

        self._remainder.clear()
        return b""
