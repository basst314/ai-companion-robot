"""Speech-to-text package for future local transcription."""

from stt.service import (
    MockSttService,
    ShellAudioCaptureService,
    SttService,
    WakeWordService,
    WhisperCppSttService,
    WhisperCppWakeWordService,
)

__all__ = [
    "MockSttService",
    "ShellAudioCaptureService",
    "SttService",
    "WakeWordService",
    "WhisperCppSttService",
    "WhisperCppWakeWordService",
]
