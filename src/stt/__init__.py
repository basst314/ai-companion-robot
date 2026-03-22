"""Speech-to-text package for future local transcription."""

from stt.service import MockSttService, ShellAudioCaptureService, SttService, WhisperCppSttService

__all__ = ["MockSttService", "ShellAudioCaptureService", "SttService", "WhisperCppSttService"]
