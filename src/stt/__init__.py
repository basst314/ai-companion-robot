"""Speech-to-text package for future local transcription."""

from stt.service import (
    MockSttService,
    OpenWakeWordWakeWordService,
    ShellAudioCaptureService,
    SttService,
    WakeWordService,
    WhisperCppSttService,
)

__all__ = [
    "MockSttService",
    "OpenWakeWordWakeWordService",
    "ShellAudioCaptureService",
    "SttService",
    "WakeWordService",
    "WhisperCppSttService",
]
