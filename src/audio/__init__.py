"""Audio package for future local audio coordination."""
"""Audio capture, playback, and wake-word helpers."""

from audio.capture import AudioWindow, SharedLiveSpeechState, ShellAudioCaptureService
from audio.respeaker_capture import InterleavedChannelExtractor, extract_interleaved_channel
from audio.wake import OpenWakeWordWakeWordService, WakeDetectionResult, WakeWordService

__all__ = [
    "AudioWindow",
    "InterleavedChannelExtractor",
    "OpenWakeWordWakeWordService",
    "SharedLiveSpeechState",
    "ShellAudioCaptureService",
    "WakeDetectionResult",
    "WakeWordService",
    "extract_interleaved_channel",
]
