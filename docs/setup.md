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
- it can optionally install Piper and download voice packs into `artifacts/piper-voices`
- it resolves and verifies the selected OpenWakeWord model before writing `.env.local`
- it asks before overwriting `.env.local`

## Generated Local Config

The setup script writes `.env.local`, which is loaded automatically by the runtime.

Current supported variables:
- `AI_COMPANION_INPUT_MODE`
- `AI_COMPANION_INTERACTIVE_CONSOLE`
- `AI_COMPANION_STT_BACKEND`
- `AI_COMPANION_USE_MOCK_AI`
- `AI_COMPANION_CLOUD_ENABLED`
- `AI_COMPANION_CLOUD_PROVIDER_NAME`
- `AI_COMPANION_OPENAI_API_KEY`
- `AI_COMPANION_OPENAI_BASE_URL`
- `AI_COMPANION_OPENAI_RESPONSE_MODEL`
- `AI_COMPANION_OPENAI_TIMEOUT_SECONDS`
- `AI_COMPANION_OPENAI_REPLY_MAX_OUTPUT_TOKENS`
- `AI_COMPANION_TTS_BACKEND`
- `AI_COMPANION_TTS_PIPER_BASE_URL`
- `AI_COMPANION_TTS_PIPER_SERVICE_MODE`
- `AI_COMPANION_TTS_PIPER_DATA_DIR`
- `AI_COMPANION_TTS_PIPER_COMMAND`
- `AI_COMPANION_TTS_DEFAULT_VOICE_EN`
- `AI_COMPANION_TTS_DEFAULT_VOICE_DE`
- `AI_COMPANION_TTS_DEFAULT_VOICE_ID`
- `AI_COMPANION_TTS_EXPRESSIVE_DE_VOICE`
- `AI_COMPANION_TTS_EXPRESSIVE_DE_ENABLED`
- `AI_COMPANION_TTS_AUDIO_PLAY_COMMAND`
- `AI_COMPANION_TTS_QUEUE_MAX`
- `AI_COMPANION_TTS_SAVE_ARTIFACTS`
- `AI_COMPANION_TTS_SYNTHESIS_TIMEOUT_SECONDS`
- `AI_COMPANION_TTS_PLAYBACK_TIMEOUT_SECONDS`
- `AI_COMPANION_WHISPER_BINARY_PATH`
- `AI_COMPANION_WHISPER_MODEL_PATH`
- `AI_COMPANION_AUDIO_RECORD_COMMAND`
- `AI_COMPANION_SPEECH_LATENCY_PROFILE`
- `AI_COMPANION_SPEECH_SILENCE_SECONDS`
- `AI_COMPANION_VAD_THRESHOLD`
- `AI_COMPANION_VAD_FRAME_MS`
- `AI_COMPANION_VAD_START_TRIGGER_FRAMES`
- `AI_COMPANION_VAD_END_TRIGGER_FRAMES`
- `AI_COMPANION_MAX_RECORDING_SECONDS`
- `AI_COMPANION_WAKE_WORD_ENABLED`
- `AI_COMPANION_WAKE_WORD_PHRASE`
- `AI_COMPANION_WAKE_WORD_MODEL`
- `AI_COMPANION_WAKE_WORD_THRESHOLD`
- `AI_COMPANION_WAKE_LOOKBACK_SECONDS`
- `AI_COMPANION_UTTERANCE_FINALIZE_TIMEOUT_SECONDS`
- `AI_COMPANION_UTTERANCE_TAIL_STABLE_POLLS`
- `AI_COMPANION_LANGUAGE_MODE`

You can also use `.env` if you want a shared local config, but `.env.local` is the expected generated file.
The `AI_COMPANION_AUDIO_RECORD_COMMAND` value intentionally contains the `{output_path}` placeholder. In the current streaming STT path, the runtime replaces that placeholder with `-` and captures raw PCM from the recorder's `stdout`. That lets the app inspect the live stream, create WAV snapshots for transcription, and stop after the bundled Silero VAD confirms trailing non-speech. Custom recorder commands therefore need to support raw PCM output to standard output.
`AI_COMPANION_SPEECH_LATENCY_PROFILE` sets the baseline STT endpoint tuning as a group. Use `fast` for a more reactive robot, or `balanced` if your mic/environment needs more conservative endpointing. Any explicit `AI_COMPANION_SPEECH_*`, `AI_COMPANION_VAD_*`, `AI_COMPANION_WAKE_LOOKBACK_SECONDS`, or utterance-finalization values still override the profile individually.
When wake-word mode is enabled, the runtime uses OpenWakeWord on that same live PCM stream. The generated setup can either configure the built-in `Hey Jarvis` pairing or prompt you for a custom phrase and matching model path/name. Setup now downloads the shared OpenWakeWord runtime models into the package resources directory used by the installed library and verifies that the selected model can initialize on the current machine before finishing.
If `AI_COMPANION_USE_MOCK_AI=false` and `AI_COMPANION_CLOUD_ENABLED=true`, the runtime expects explicit OpenAI credentials plus a response model name. `AI_COMPANION_OPENAI_REPLY_MAX_OUTPUT_TOKENS` sets the hard ceiling for each spoken cloud reply so the robot does not ramble. The cloud backend now uses a single response-model call for normal chat turns and can request a local camera snapshot when needed; speech output still stays local.
The interactive setup flow now asks whether you want the real OpenAI backend. If you enable it, setup prompts for the API key but accepts a blank value so you can fill it in later in `.env.local`.
If you enable Piper TTS, setup can also provision the English/German/Indonesian starter voices and optionally the expressive German pack. In `managed` mode, the generated config expects the app to start the Piper HTTP server itself; in `external` mode, the app connects to an already running Piper service.

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
./scripts/setup.sh --yes --tts-backend piper --tts-languages en,de,id
./scripts/setup.sh --yes --skip-system-packages
```

`--force` is the clean rebuild option. It recreates `.venv`, rewrites `.env.local`, rebuilds `whisper.cpp`, re-downloads the selected Whisper model, and reruns the OpenWakeWord model verification step instead of reusing prior generated artifacts.

## Manual Fallback

If the script cannot support your environment yet, install manually:

1. Install Python 3.11+, Git, CMake, and a recorder tool.
2. Create `.venv` and run `python -m pip install -e ".[dev]"`, or `python -m pip install -e ".[dev,tts]"` if you want local Piper TTS.
3. Clone and build `whisper.cpp`.
4. Download a model such as `base`.
5. Copy `.env.example` to `.env.local` and fill in the Whisper and recorder paths.
6. If you want real cloud replies, also fill in the OpenAI settings, set `AI_COMPANION_USE_MOCK_AI=false`, and prefer `gpt-5.2` for `AI_COMPANION_OPENAI_RESPONSE_MODEL` unless you are intentionally testing a different model.
7. Run `.venv/bin/pytest -q`.
8. Launch `.venv/bin/python src/main.py` and then either type a phrase, press Enter on an empty line to start listening immediately, or say the configured wake word.
