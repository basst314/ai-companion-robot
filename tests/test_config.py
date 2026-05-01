"""Configuration tests for the realtime-first runtime."""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.config import load_app_config


def test_load_app_config_reads_realtime_audio_settings(tmp_path: Path) -> None:
    (tmp_path / ".env.local").write_text(
        "\n".join(
            [
                "AI_COMPANION_INTERACTION_BACKEND=openai_realtime",
                "AI_COMPANION_INPUT_MODE=speech",
                "AI_COMPANION_CLOUD_ENABLED=true",
                "AI_COMPANION_USE_MOCK_AI=false",
                "AI_COMPANION_OPENAI_API_KEY=test-key",
                "AI_COMPANION_OPENAI_REALTIME_MODEL=gpt-realtime-test",
                "AI_COMPANION_OPENAI_REALTIME_VOICE=echo",
                "AI_COMPANION_AUDIO_RECORD_COMMAND=rec -q -c 1 -r 16000 -b 16 -e signed-integer -t raw {output_path}",
                "AI_COMPANION_AUDIO_INPUT_CHANNELS=6",
                "AI_COMPANION_AUDIO_CHANNEL_INDEX=0",
                "AI_COMPANION_AUDIO_OUTPUT_BACKEND=alsa_persistent",
                "AI_COMPANION_AUDIO_PLAY_COMMAND=aplay {input_path}",
                "AI_COMPANION_AUDIO_ALSA_DEVICE=default:CARD=vc4hdmi1",
                "AI_COMPANION_AUDIO_ALSA_SAMPLE_RATE=24000",
                "AI_COMPANION_AUDIO_ALSA_PERIOD_FRAMES=256",
                "AI_COMPANION_AUDIO_ALSA_BUFFER_FRAMES=1024",
                "AI_COMPANION_AUDIO_ALSA_KEEPALIVE_INTERVAL_MS=15",
                "AI_COMPANION_WAKE_WORD_ENABLED=true",
                "AI_COMPANION_WAKE_WORD_PHRASE=Hey Oreo",
                "AI_COMPANION_WAKE_WORD_MODEL=/models/hey_oreo.onnx",
                "AI_COMPANION_WAKE_WORD_THRESHOLD=0.6",
                "AI_COMPANION_WAKE_LOOKBACK_SECONDS=0.7",
            ]
        )
    )

    config = load_app_config(tmp_path)

    assert config.runtime.interaction_backend == "openai_realtime"
    assert config.runtime.audio_record_command[:2] == ("rec", "-q")
    assert config.runtime.audio_input_channels == 6
    assert config.runtime.audio_output_backend == "alsa_persistent"
    assert config.runtime.audio_play_command == ("aplay", "{input_path}")
    assert config.runtime.audio_alsa_device == "default:CARD=vc4hdmi1"
    assert config.runtime.audio_alsa_period_frames == 256
    assert config.runtime.audio_alsa_buffer_frames == 1024
    assert config.runtime.audio_alsa_keepalive_interval_ms == 15
    assert config.runtime.wake_word_phrase == "Hey Oreo"
    assert config.runtime.wake_word_model == "/models/hey_oreo.onnx"
    assert config.runtime.wake_word_threshold == 0.6
    assert config.runtime.wake_lookback_seconds == 0.7


def test_load_app_config_rejects_invalid_alsa_audio_buffer(tmp_path: Path) -> None:
    (tmp_path / ".env.local").write_text(
        "\n".join(
            [
                "AI_COMPANION_AUDIO_OUTPUT_BACKEND=alsa_persistent",
                "AI_COMPANION_AUDIO_ALSA_PERIOD_FRAMES=512",
                "AI_COMPANION_AUDIO_ALSA_BUFFER_FRAMES=128",
            ]
        )
    )

    with pytest.raises(ValueError, match="AI_COMPANION_AUDIO_ALSA_BUFFER_FRAMES"):
        load_app_config(tmp_path)
