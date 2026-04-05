"""Tests for environment-driven application configuration."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from shared.config import load_app_config


def test_load_app_config_reads_env_local_file(tmp_path: Path) -> None:
    """Local env files should populate runtime settings for fresh checkouts."""

    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "\n".join(
            [
                "AI_COMPANION_CLOUD_ENABLED=true",
                "AI_COMPANION_CLOUD_PROVIDER_NAME=openai",
                "AI_COMPANION_OPENAI_API_KEY=test-key",
                "AI_COMPANION_OPENAI_BASE_URL=https://api.openai.com/v1/responses",
                "AI_COMPANION_OPENAI_RESPONSE_MODEL=gpt-test-response",
                "AI_COMPANION_OPENAI_TIMEOUT_SECONDS=18.5",
                "AI_COMPANION_OPENAI_REPLY_MAX_OUTPUT_TOKENS=96",
                "AI_COMPANION_TTS_BACKEND=piper",
                "AI_COMPANION_TTS_PIPER_BASE_URL=http://127.0.0.1:5001",
                "AI_COMPANION_TTS_PIPER_SERVICE_MODE=external",
                "AI_COMPANION_TTS_PIPER_DATA_DIR=/opt/piper/voices",
                "AI_COMPANION_TTS_DEFAULT_VOICE_EN=en_US-hfc_female-medium",
                "AI_COMPANION_TTS_DEFAULT_VOICE_DE=de_DE-thorsten-medium",
                "AI_COMPANION_TTS_DEFAULT_VOICE_ID=id_ID-news_tts-medium",
                "AI_COMPANION_TTS_EXPRESSIVE_DE_VOICE=de_DE-thorsten_emotional-medium",
                "AI_COMPANION_TTS_EXPRESSIVE_DE_ENABLED=true",
                "AI_COMPANION_TTS_AUDIO_BACKEND=alsa_persistent",
                "AI_COMPANION_TTS_AUDIO_PLAY_COMMAND=aplay {input_path}",
                "AI_COMPANION_TTS_ALSA_DEVICE=default:CARD=vc4hdmi1",
                "AI_COMPANION_TTS_ALSA_SAMPLE_RATE=22050",
                "AI_COMPANION_TTS_ALSA_PERIOD_FRAMES=256",
                "AI_COMPANION_TTS_ALSA_BUFFER_FRAMES=1024",
                "AI_COMPANION_TTS_ALSA_KEEPALIVE_INTERVAL_MS=15",
                "AI_COMPANION_TTS_QUEUE_MAX=3",
                "AI_COMPANION_TTS_SAVE_ARTIFACTS=true",
                "AI_COMPANION_TTS_SYNTHESIS_TIMEOUT_SECONDS=11.5",
                "AI_COMPANION_TTS_PLAYBACK_TIMEOUT_SECONDS=21.0",
                "AI_COMPANION_UI_BACKEND=fb0",
                "AI_COMPANION_UI_FULLSCREEN=false",
                "AI_COMPANION_UI_ACTIVE_FPS=36",
                "AI_COMPANION_UI_IDLE_FPS=10",
                "AI_COMPANION_UI_IDLE_SLEEP_SECONDS=180",
                "AI_COMPANION_UI_SLEEPING_EYES_GRACE_SECONDS=9",
                "AI_COMPANION_UI_SHOW_TEXT_OVERLAY=false",
                "AI_COMPANION_UI_SLEEP_COMMAND=vcgencmd display_power 0",
                "AI_COMPANION_UI_WAKE_COMMAND=vcgencmd display_power 1",
                "AI_COMPANION_UI_SDL_VIDEODRIVER=kmsdrm",
                "AI_COMPANION_UI_THEME_NAME=retro_bot",
                "AI_COMPANION_UI_FB_PATH=/dev/fb0",
                "AI_COMPANION_INPUT_MODE=speech",
                "AI_COMPANION_SPEECH_LATENCY_PROFILE=balanced",
                "AI_COMPANION_INTERACTIVE_CONSOLE=true",
                "AI_COMPANION_STT_BACKEND=whisper_cpp",
                "AI_COMPANION_WHISPER_BINARY_PATH=/opt/whisper/whisper-cli",
                "AI_COMPANION_WHISPER_MODEL_PATH=/opt/whisper/models/ggml-base.en.bin",
                "AI_COMPANION_AUDIO_RECORD_COMMAND=rec -q -c 1 -r 16000 -b 16 -e signed-integer -t raw {output_path}",
                "AI_COMPANION_SPEECH_SILENCE_SECONDS=1.8",
                "AI_COMPANION_VAD_THRESHOLD=0.55",
                "AI_COMPANION_VAD_FRAME_MS=20",
                "AI_COMPANION_VAD_START_TRIGGER_FRAMES=3",
                "AI_COMPANION_VAD_END_TRIGGER_FRAMES=6",
                "AI_COMPANION_MAX_RECORDING_SECONDS=11.5",
                "AI_COMPANION_WAKE_WORD_ENABLED=true",
                "AI_COMPANION_FOLLOW_UP_MODE_ENABLED=true",
                "AI_COMPANION_FOLLOW_UP_LISTEN_TIMEOUT_SECONDS=3.3",
                "AI_COMPANION_FOLLOW_UP_MAX_TURNS=5",
                "AI_COMPANION_WAKE_WORD_PHRASE=Oreo",
                "AI_COMPANION_WAKE_WORD_MODEL=/models/oreo.tflite",
                "AI_COMPANION_WAKE_WORD_THRESHOLD=0.65",
                "AI_COMPANION_WAKE_LOOKBACK_SECONDS=0.9",
                "AI_COMPANION_UTTERANCE_FINALIZE_TIMEOUT_SECONDS=0.9",
                "AI_COMPANION_UTTERANCE_TAIL_STABLE_POLLS=3",
                "AI_COMPANION_LANGUAGE_MODE=de",
                "AI_COMPANION_USE_MOCK_AI=false",
            ]
        )
    )

    config = load_app_config(base_dir=tmp_path)

    assert config.cloud.enabled is True
    assert config.cloud.provider_name == "openai"
    assert config.cloud.openai_api_key == "test-key"
    assert config.cloud.openai_base_url == "https://api.openai.com/v1/responses"
    assert config.cloud.openai_response_model == "gpt-test-response"
    assert config.cloud.openai_timeout_seconds == 18.5
    assert config.cloud.openai_reply_max_output_tokens == 96
    assert config.tts.backend == "piper"
    assert config.tts.piper_base_url == "http://127.0.0.1:5001"
    assert config.tts.piper_service_mode == "external"
    assert config.tts.piper_data_dir == Path("/opt/piper/voices")
    assert config.tts.default_voice_en == "en_US-hfc_female-medium"
    assert config.tts.default_voice_de == "de_DE-thorsten-medium"
    assert config.tts.default_voice_id == "id_ID-news_tts-medium"
    assert config.tts.expressive_de_voice == "de_DE-thorsten_emotional-medium"
    assert config.tts.expressive_de_enabled is True
    assert config.tts.audio_backend == "alsa_persistent"
    assert config.tts.audio_play_command == ("aplay", "{input_path}")
    assert config.tts.alsa_device == "default:CARD=vc4hdmi1"
    assert config.tts.alsa_sample_rate == 22050
    assert config.tts.alsa_period_frames == 256
    assert config.tts.alsa_buffer_frames == 1024
    assert config.tts.alsa_keepalive_interval_ms == 15
    assert config.tts.queue_max == 3
    assert config.tts.save_artifacts is True
    assert config.tts.synthesis_timeout_seconds == 11.5
    assert config.tts.playback_timeout_seconds == 21.0
    assert config.ui.backend == "fb0"
    assert config.ui.fullscreen is False
    assert config.ui.active_fps == 36
    assert config.ui.idle_fps == 10
    assert config.ui.idle_sleep_seconds == 180
    assert config.ui.sleeping_eyes_grace_seconds == 9
    assert config.ui.show_text_overlay is False
    assert config.ui.sleep_command == ("vcgencmd", "display_power", "0")
    assert config.ui.wake_command == ("vcgencmd", "display_power", "1")
    assert config.ui.sdl_videodriver == "kmsdrm"
    assert config.ui.theme_name == "retro_bot"
    assert config.ui.fb_path == "/dev/fb0"
    assert config.runtime.input_mode == "speech"
    assert config.runtime.speech_latency_profile == "balanced"
    assert config.runtime.interactive_console is True
    assert config.runtime.stt_backend == "whisper_cpp"
    assert config.runtime.whisper_binary_path == Path("/opt/whisper/whisper-cli")
    assert config.runtime.whisper_model_path == Path("/opt/whisper/models/ggml-base.en.bin")
    assert config.runtime.audio_record_command[:4] == ("rec", "-q", "-c", "1")
    assert config.runtime.speech_silence_seconds == 1.8
    assert config.runtime.vad_threshold == 0.55
    assert config.runtime.vad_frame_ms == 20
    assert config.runtime.vad_start_trigger_frames == 3
    assert config.runtime.vad_end_trigger_frames == 6
    assert config.runtime.max_recording_seconds == 11.5
    assert config.runtime.wake_word_enabled is True
    assert config.runtime.follow_up_mode_enabled is True
    assert config.runtime.follow_up_listen_timeout_seconds == 3.3
    assert config.runtime.follow_up_max_turns == 5
    assert config.runtime.wake_word_phrase == "Oreo"
    assert config.runtime.wake_word_model == "/models/oreo.tflite"
    assert config.runtime.wake_word_threshold == 0.65
    assert config.runtime.wake_lookback_seconds == 0.9
    assert config.runtime.utterance_finalize_timeout_seconds == 0.9
    assert config.runtime.utterance_tail_stable_polls == 3
    assert config.runtime.language_mode == "de"
    assert config.runtime.use_mock_ai is False


def test_process_environment_overrides_env_file(monkeypatch, tmp_path: Path) -> None:
    """Explicit process environment should win over the generated local file."""

    (tmp_path / ".env.local").write_text("AI_COMPANION_INPUT_MODE=manual\n")
    monkeypatch.setenv("AI_COMPANION_INPUT_MODE", "speech")

    config = load_app_config(base_dir=tmp_path)

    assert config.runtime.input_mode == "speech"


def test_load_app_config_rejects_enabled_wake_word_without_model(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "\n".join(
            [
                "AI_COMPANION_WAKE_WORD_ENABLED=true",
                "AI_COMPANION_WAKE_WORD_PHRASE=Hey Jarvis",
            ]
        )
    )

    with pytest.raises(ValueError, match="WAKE_WORD_MODEL"):
        load_app_config(base_dir=tmp_path)


def test_load_app_config_rejects_real_cloud_without_required_openai_fields(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "\n".join(
            [
                "AI_COMPANION_CLOUD_ENABLED=true",
                "AI_COMPANION_USE_MOCK_AI=false",
                "AI_COMPANION_OPENAI_API_KEY=test-key",
            ]
        )
    )

    with pytest.raises(ValueError, match="OPENAI_RESPONSE_MODEL"):
        load_app_config(base_dir=tmp_path)


def test_load_app_config_rejects_non_positive_reply_token_cap(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "\n".join(
            [
                "AI_COMPANION_CLOUD_ENABLED=true",
                "AI_COMPANION_USE_MOCK_AI=false",
                "AI_COMPANION_OPENAI_API_KEY=test-key",
                "AI_COMPANION_OPENAI_RESPONSE_MODEL=gpt-test-response",
                "AI_COMPANION_OPENAI_REPLY_MAX_OUTPUT_TOKENS=0",
            ]
        )
    )

    with pytest.raises(ValueError, match="OPENAI_REPLY_MAX_OUTPUT_TOKENS"):
        load_app_config(base_dir=tmp_path)


def test_load_app_config_applies_fast_speech_profile_defaults(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "\n".join(
            [
                "AI_COMPANION_INPUT_MODE=speech",
                "AI_COMPANION_SPEECH_LATENCY_PROFILE=fast",
            ]
        )
    )

    config = load_app_config(base_dir=tmp_path)

    assert config.runtime.speech_latency_profile == "fast"
    assert config.runtime.speech_silence_seconds == 0.55
    assert config.runtime.wake_lookback_seconds == 0.5
    assert config.runtime.utterance_finalize_timeout_seconds == 0.25
    assert config.runtime.utterance_tail_stable_polls == 1


def test_load_app_config_normalizes_invalid_vad_frame_size(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text("\n".join(["AI_COMPANION_VAD_FRAME_MS=25", "AI_COMPANION_VAD_START_TRIGGER_FRAMES=0"]))

    config = load_app_config(base_dir=tmp_path)

    assert config.runtime.vad_frame_ms == 30
    assert config.runtime.vad_start_trigger_frames == 1


def test_load_app_config_defaults_to_alsa_backend_for_aplay_on_linux(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "\n".join(
            [
                "AI_COMPANION_TTS_BACKEND=piper",
                "AI_COMPANION_TTS_AUDIO_BACKEND=alsa_persistent",
                "AI_COMPANION_TTS_PIPER_BASE_URL=http://127.0.0.1:5001",
                "AI_COMPANION_TTS_DEFAULT_VOICE_EN=en_US-hfc_female-medium",
                "AI_COMPANION_TTS_DEFAULT_VOICE_DE=de_DE-thorsten-medium",
                "AI_COMPANION_TTS_DEFAULT_VOICE_ID=id_ID-news_tts-medium",
                "AI_COMPANION_TTS_AUDIO_PLAY_COMMAND=aplay {input_path}",
            ]
        )
    )
    monkeypatch.setattr("shared.config.sys.platform", "linux")

    config = load_app_config(base_dir=tmp_path)

    assert config.tts.audio_backend == "alsa_persistent"


def test_load_app_config_rejects_alsa_buffer_smaller_than_period(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "\n".join(
            [
                "AI_COMPANION_TTS_BACKEND=piper",
                "AI_COMPANION_TTS_AUDIO_BACKEND=alsa_persistent",
                "AI_COMPANION_TTS_PIPER_BASE_URL=http://127.0.0.1:5001",
                "AI_COMPANION_TTS_DEFAULT_VOICE_EN=en_US-hfc_female-medium",
                "AI_COMPANION_TTS_DEFAULT_VOICE_DE=de_DE-thorsten-medium",
                "AI_COMPANION_TTS_DEFAULT_VOICE_ID=id_ID-news_tts-medium",
                "AI_COMPANION_TTS_ALSA_PERIOD_FRAMES=512",
                "AI_COMPANION_TTS_ALSA_BUFFER_FRAMES=128",
            ]
        )
    )

    with pytest.raises(ValueError, match="ALSA_BUFFER_FRAMES"):
        load_app_config(base_dir=tmp_path)


def test_load_app_config_command_backend_ignores_invalid_alsa_values(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "\n".join(
            [
                "AI_COMPANION_TTS_BACKEND=piper",
                "AI_COMPANION_TTS_AUDIO_BACKEND=command",
                "AI_COMPANION_TTS_PIPER_BASE_URL=http://127.0.0.1:5001",
                "AI_COMPANION_TTS_DEFAULT_VOICE_EN=en_US-hfc_female-medium",
                "AI_COMPANION_TTS_DEFAULT_VOICE_DE=de_DE-thorsten-medium",
                "AI_COMPANION_TTS_DEFAULT_VOICE_ID=id_ID-news_tts-medium",
                "AI_COMPANION_TTS_AUDIO_PLAY_COMMAND=aplay {input_path}",
                "AI_COMPANION_TTS_ALSA_DEVICE=",
                "AI_COMPANION_TTS_ALSA_SAMPLE_RATE=0",
                "AI_COMPANION_TTS_ALSA_PERIOD_FRAMES=0",
                "AI_COMPANION_TTS_ALSA_BUFFER_FRAMES=1",
                "AI_COMPANION_TTS_ALSA_KEEPALIVE_INTERVAL_MS=0",
            ]
        )
    )

    config = load_app_config(base_dir=tmp_path)

    assert config.tts.audio_backend == "command"


def test_load_app_config_rejects_non_positive_alsa_keepalive_interval(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "\n".join(
            [
                "AI_COMPANION_TTS_BACKEND=piper",
                "AI_COMPANION_TTS_AUDIO_BACKEND=alsa_persistent",
                "AI_COMPANION_TTS_PIPER_BASE_URL=http://127.0.0.1:5001",
                "AI_COMPANION_TTS_DEFAULT_VOICE_EN=en_US-hfc_female-medium",
                "AI_COMPANION_TTS_DEFAULT_VOICE_DE=de_DE-thorsten-medium",
                "AI_COMPANION_TTS_DEFAULT_VOICE_ID=id_ID-news_tts-medium",
                "AI_COMPANION_TTS_ALSA_KEEPALIVE_INTERVAL_MS=0",
            ]
        )
    )

    with pytest.raises(ValueError, match="ALSA_KEEPALIVE_INTERVAL_MS"):
        load_app_config(base_dir=tmp_path)


def test_load_app_config_rejects_non_positive_ui_frame_rate(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text("AI_COMPANION_UI_ACTIVE_FPS=0\n")

    with pytest.raises(ValueError, match="UI_ACTIVE_FPS"):
        load_app_config(base_dir=tmp_path)


def test_setup_script_help_is_available() -> None:
    """The bootstrap script should expose a stable help surface."""

    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["bash", "scripts/setup.sh", "--help"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )

    assert result.returncode == 0
    assert "--platform <macos|rpi>" in result.stdout
    assert "--model <tiny|base|small>" in result.stdout
    assert "--tts-backend <mock|piper>" in result.stdout
    assert "--tts-languages <en,de,id>" in result.stdout
    assert "--skip-system-packages" in result.stdout
