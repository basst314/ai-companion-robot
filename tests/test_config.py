"""Tests for environment-driven application configuration."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from shared.config import load_app_config


def test_load_app_config_reads_env_local_file(tmp_path: Path) -> None:
    """Local env files should populate runtime settings for fresh checkouts."""

    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "\n".join(
            [
                "AI_COMPANION_INPUT_MODE=speech",
                "AI_COMPANION_INTERACTIVE_CONSOLE=true",
                "AI_COMPANION_STT_BACKEND=whisper_cpp",
                "AI_COMPANION_WHISPER_BINARY_PATH=/opt/whisper/whisper-cli",
                "AI_COMPANION_WHISPER_MODEL_PATH=/opt/whisper/models/ggml-base.en.bin",
                "AI_COMPANION_AUDIO_RECORD_COMMAND=rec -q -c 1 -r 16000 -b 16 -e signed-integer -t raw {output_path}",
                "AI_COMPANION_SPEECH_SILENCE_SECONDS=1.8",
                "AI_COMPANION_MAX_RECORDING_SECONDS=11.5",
                "AI_COMPANION_WAKE_WORD_ENABLED=true",
                "AI_COMPANION_WAKE_WORD_PHRASE=Oreo",
                "AI_COMPANION_WAKE_WINDOW_SECONDS=2.0",
                "AI_COMPANION_WAKE_STRIDE_SECONDS=0.8",
                "AI_COMPANION_UTTERANCE_FINALIZE_TIMEOUT_SECONDS=0.9",
                "AI_COMPANION_UTTERANCE_TAIL_STABLE_POLLS=3",
                "AI_COMPANION_LANGUAGE_MODE=de",
            ]
        )
    )

    config = load_app_config(base_dir=tmp_path)

    assert config.runtime.input_mode == "speech"
    assert config.runtime.interactive_console is True
    assert config.runtime.stt_backend == "whisper_cpp"
    assert config.runtime.whisper_binary_path == Path("/opt/whisper/whisper-cli")
    assert config.runtime.whisper_model_path == Path("/opt/whisper/models/ggml-base.en.bin")
    assert config.runtime.audio_record_command[:4] == ("rec", "-q", "-c", "1")
    assert config.runtime.speech_silence_seconds == 1.8
    assert config.runtime.max_recording_seconds == 11.5
    assert config.runtime.wake_word_enabled is True
    assert config.runtime.wake_word_phrase == "Oreo"
    assert config.runtime.wake_window_seconds == 2.0
    assert config.runtime.wake_stride_seconds == 0.8
    assert config.runtime.utterance_finalize_timeout_seconds == 0.9
    assert config.runtime.utterance_tail_stable_polls == 3
    assert config.runtime.language_mode == "de"


def test_process_environment_overrides_env_file(monkeypatch, tmp_path: Path) -> None:
    """Explicit process environment should win over the generated local file."""

    (tmp_path / ".env.local").write_text("AI_COMPANION_INPUT_MODE=manual\n")
    monkeypatch.setenv("AI_COMPANION_INPUT_MODE", "speech")

    config = load_app_config(base_dir=tmp_path)

    assert config.runtime.input_mode == "speech"


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
    assert "--skip-system-packages" in result.stdout
