"""Text-to-speech package for local and mock speech synthesis."""

from tts.service import MockTtsService, QueuedTtsService, TtsService, build_piper_tts_service

__all__ = ["MockTtsService", "QueuedTtsService", "TtsService", "build_piper_tts_service"]
