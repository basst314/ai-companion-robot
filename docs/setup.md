# Setup Guide

This document explains the automated bootstrap flow introduced for the current STT-enabled prototype.

## Preferred Path

From a fresh checkout:

```bash
./scripts/setup.sh
```

The script is designed to be idempotent:
- it reuses `.venv` when possible
- it reuses an existing `whisper.cpp` checkout and build unless `--force` is used
- it reuses downloaded Whisper models unless `--force` is used
- it resolves and verifies the selected OpenWakeWord model before writing `.env.local`
- it asks before overwriting `.env.local`

## Generated Local Config

The setup script writes `.env.local`, which is loaded automatically by the runtime.

Current supported variables:
- `AI_COMPANION_INPUT_MODE`
- `AI_COMPANION_INTERACTIVE_CONSOLE`
- `AI_COMPANION_STT_BACKEND`
- `AI_COMPANION_WHISPER_BINARY_PATH`
- `AI_COMPANION_WHISPER_MODEL_PATH`
- `AI_COMPANION_AUDIO_RECORD_COMMAND`
- `AI_COMPANION_SPEECH_SILENCE_SECONDS`
- `AI_COMPANION_WAKE_WORD_ENABLED`
- `AI_COMPANION_WAKE_WORD_PHRASE`
- `AI_COMPANION_WAKE_WORD_MODEL`
- `AI_COMPANION_WAKE_WORD_THRESHOLD`
- `AI_COMPANION_WAKE_LOOKBACK_SECONDS`
- `AI_COMPANION_UTTERANCE_FINALIZE_TIMEOUT_SECONDS`
- `AI_COMPANION_UTTERANCE_TAIL_STABLE_POLLS`
- `AI_COMPANION_LANGUAGE_MODE`

You can also use `.env` if you want a shared local config, but `.env.local` is the expected generated file.
The `AI_COMPANION_AUDIO_RECORD_COMMAND` value intentionally contains the `{output_path}` placeholder. In the current streaming STT path, the runtime replaces that placeholder with `-` and captures raw PCM from the recorder's `stdout`. That lets the app inspect the live stream, create WAV snapshots for transcription, and stop after it detects trailing silence. Custom recorder commands therefore need to support raw PCM output to standard output.
When wake-word mode is enabled, the runtime uses OpenWakeWord on that same live PCM stream. The generated setup can either configure the built-in `Hey Jarvis` pairing or prompt you for a custom phrase and matching model path/name. Setup now downloads the shared OpenWakeWord runtime models into the package resources directory used by the installed library and verifies that the selected model can initialize on the current machine before finishing.

## Platform-Specific Defaults

Raspberry Pi:
- package manager: `apt`
- recorder command: `arecord -t raw -f S16_LE -r 16000 -c 1 {output_path}` (`{output_path}` becomes `-` at runtime)
- intended target: Raspberry Pi OS or another Debian-family Raspberry Pi image

macOS:
- package manager: `brew`
- recorder command: `rec -q -c 1 -r 16000 -b 16 -e signed-integer -t raw {output_path}` (`{output_path}` becomes `-` at runtime)
- this uses `sox`/`rec`, which has been more reliable than `ffmpeg`/`avfoundation` for clean mic capture on some Macs
- remember to grant microphone permissions to Terminal/iTerm

## Non-Interactive Examples

Use these when you want repeatable automation:

```bash
./scripts/setup.sh --yes
./scripts/setup.sh --yes --platform rpi --model base --language-mode auto
./scripts/setup.sh --yes --skip-system-packages
```

`--force` is the clean rebuild option. It recreates `.venv`, rewrites `.env.local`, rebuilds `whisper.cpp`, re-downloads the selected Whisper model, and reruns the OpenWakeWord model verification step instead of reusing prior generated artifacts.

## Manual Fallback

If the script cannot support your environment yet, install manually:

1. Install Python 3.11+, Git, CMake, and a recorder tool.
2. Create `.venv` and run `python -m pip install -e ".[dev]"`.
3. Clone and build `whisper.cpp`.
4. Download a model such as `base`.
5. Copy `.env.example` to `.env.local` and fill in the Whisper and recorder paths.
6. Run `.venv/bin/pytest -q`.
7. Launch `.venv/bin/python src/main.py` and then either type a phrase, press Enter on an empty line to start listening immediately, or say the configured wake word.
