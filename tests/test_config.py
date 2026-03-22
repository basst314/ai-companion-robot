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
                "AI_COMPANION_AUDIO_RECORD_COMMAND=ffmpeg -y -f avfoundation -i :0 -ar 16000 -ac 1 -f s16le {output_path}",
                "AI_COMPANION_SPEECH_SILENCE_SECONDS=1.8",
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
    assert config.runtime.audio_record_command[:4] == ("ffmpeg", "-y", "-f", "avfoundation")
    assert config.runtime.speech_silence_seconds == 1.8
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
