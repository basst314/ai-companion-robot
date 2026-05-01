# Setup

This guide covers the current OpenAI Realtime runtime: local wake detection, local microphone capture, local tool validation, local audio playback, and streamed model audio.

## Install

From a fresh checkout:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env.local
```

The runtime loads `.env.local` automatically. You can also use `.env` for shared local settings, but `.env.local` is the normal per-machine file.

## Required Realtime Settings

For a real speech session:

- `AI_COMPANION_INTERACTION_BACKEND=openai_realtime`
- `AI_COMPANION_INPUT_MODE=speech`
- `AI_COMPANION_CLOUD_ENABLED=true`
- `AI_COMPANION_USE_MOCK_AI=false`
- `AI_COMPANION_OPENAI_API_KEY`
- `AI_COMPANION_OPENAI_REALTIME_MODEL`
- `AI_COMPANION_OPENAI_REALTIME_VOICE`
- `AI_COMPANION_OPENAI_REALTIME_TURN_DETECTION`
- `AI_COMPANION_AUDIO_RECORD_COMMAND`
- `AI_COMPANION_WAKE_WORD_ENABLED=true`
- `AI_COMPANION_WAKE_WORD_PHRASE`
- `AI_COMPANION_WAKE_WORD_MODEL`

Realtime defaults use `semantic_vad` and `AI_COMPANION_OPENAI_REALTIME_TURN_EAGERNESS=auto`. If turn endings feel slow in a quiet room, try `high`; if the model cuts you off, move toward `medium`, `low`, or `auto`.

## Microphone Input

The recorder command must emit raw PCM to stdout. The runtime replaces `{output_path}` with `-`.

macOS example:

```bash
AI_COMPANION_AUDIO_RECORD_COMMAND=rec -q -c 1 -r 16000 -b 16 -e signed-integer -t raw {output_path}
AI_COMPANION_AUDIO_INPUT_CHANNELS=1
AI_COMPANION_AUDIO_CHANNEL_INDEX=0
```

Raspberry Pi / ReSpeaker example:

```bash
AI_COMPANION_AUDIO_RECORD_COMMAND=arecord -D plughw:2,0 -f S16_LE -r 16000 -c 6 -t raw {output_path}
AI_COMPANION_AUDIO_INPUT_CHANNELS=6
AI_COMPANION_AUDIO_CHANNEL_INDEX=0
```

For ReSpeaker-style devices, `audio.capture` extracts the configured channel from the interleaved stream before wake detection and realtime streaming.

## Audio Output

Configure realtime playback with:

- `AI_COMPANION_AUDIO_OUTPUT_BACKEND`
- `AI_COMPANION_AUDIO_PLAY_COMMAND`
- `AI_COMPANION_AUDIO_ALSA_DEVICE`
- `AI_COMPANION_AUDIO_ALSA_SAMPLE_RATE`
- `AI_COMPANION_AUDIO_ALSA_PERIOD_FRAMES`
- `AI_COMPANION_AUDIO_ALSA_BUFFER_FRAMES`
- `AI_COMPANION_AUDIO_ALSA_KEEPALIVE_INTERVAL_MS`

On macOS, leave `AI_COMPANION_AUDIO_PLAY_COMMAND` empty to use `afplay`.

On Raspberry Pi, prefer:

```bash
AI_COMPANION_AUDIO_OUTPUT_BACKEND=alsa_persistent
AI_COMPANION_AUDIO_ALSA_DEVICE=default:CARD=vc4hdmi1
AI_COMPANION_OPENAI_REALTIME_AUDIO_SAMPLE_RATE=24000
```

## Wake Word

Wake-word mode uses OpenWakeWord on the same live PCM stream used for realtime audio.

Important settings:

- `AI_COMPANION_WAKE_WORD_ENABLED`
- `AI_COMPANION_WAKE_WORD_PHRASE`
- `AI_COMPANION_WAKE_WORD_MODEL`
- `AI_COMPANION_WAKE_WORD_THRESHOLD`
- `AI_COMPANION_WAKE_LOOKBACK_SECONDS`

`AI_COMPANION_WAKE_LOOKBACK_SECONDS` controls how much recent audio is included when the realtime session starts after a wake hit.

## UI

The supported display path is the browser-backed face renderer:

```bash
AI_COMPANION_UI_BACKEND=browser
AI_COMPANION_UI_BROWSER_LAUNCH_MODE=windowed
```

On Raspberry Pi, use kiosk mode:

```bash
AI_COMPANION_UI_BROWSER_LAUNCH_MODE=kiosk
```

The runtime starts the local face bridge and launches Chromium when configured. Use `AI_COMPANION_UI_BROWSER_EXECUTABLE`, `AI_COMPANION_UI_BROWSER_PROFILE_DIR`, and `AI_COMPANION_UI_BROWSER_EXTRA_ARGS` for machine-specific browser tuning.

## Pi Config Sync

Keep Pi-specific secrets and runtime settings in `.env.local.rpi` on the development machine. That file is ignored by git. `scripts/sync-to-pi.sh` automatically copies it to the Pi as `.env.local` when present.

Examples:

```bash
./scripts/sync-to-pi.sh --host <hostname> --user <user>
./scripts/sync-to-pi.sh --host <hostname> --user <user> --env-file .env.local.rpi
./scripts/sync-to-pi.sh --host <hostname> --user <user> --no-env-file
```

## Setup Script

`scripts/setup.sh` creates a virtualenv, installs the package with dev dependencies, and writes a realtime-oriented `.env.local`. It is useful for a fresh machine:

```bash
./scripts/setup.sh --yes
```

Use `--force` to overwrite an existing `.env.local`.

## Manual Validation

```bash
python -m py_compile src/main.py src/ai/realtime.py src/audio/capture.py src/audio/wake.py
pytest
```

For live wake-word scoring:

```bash
.venv/bin/python scripts/test-wakeword-live.py --max-seconds 15
```
