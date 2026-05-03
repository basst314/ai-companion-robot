# AI Companion Robot

An event-driven AI companion robot runtime for Raspberry Pi. The robot combines wake-word activation, streamed OpenAI Realtime conversation, local tool validation, a browser-backed animated face, camera/vision hooks, memory, and hardware-control surfaces into one local coordinator.

## Overview

The current speech pipeline is:

```text
Wake word
-> shared microphone stream
-> OpenAI Realtime audio input
-> Pi-validated local tools
-> streamed model audio
-> local command/ALSA playback
```

The Pi remains the authority for local behavior. It owns microphone capture, wake detection, tool validation, UI state, hardware actions, camera snapshots, and audio playback. OpenAI Realtime receives active user audio and returns streamed assistant audio plus tool requests.

## Key Capabilities

- Low-latency voice interaction through OpenAI Realtime
- Local OpenWakeWord wake detection
- ReSpeaker multichannel channel extraction
- Shared live audio buffering for wake handoff and realtime streaming
- Pi-validated local tools for UI, hardware, memory, and vision
- Browser-backed animated face in Chromium kiosk/windowed modes
- Local event bus for lifecycle, playback, listening, and UI reactions
- Manual text/dev loop for lightweight local checks

## Hardware Target

The runtime is designed around a Raspberry Pi robot stack:

- Raspberry Pi 5
- ReSpeaker mic array or another raw PCM-capable microphone
- HDMI or ALSA speaker output
- Small display for the browser face
- Optional camera module
- Optional future head/servo hardware

The same code can run on macOS for development, with command-based audio playback and a browser window for the face.

## Package Map

- `ai`: OpenAI cloud and realtime clients
- `audio`: microphone capture, ReSpeaker channel extraction, shared live buffering, wake detection
- `orchestrator`: runtime coordination, local tool validation, realtime tool execution
- `ui`: mock and browser-backed robot face UI
- `hardware`: local action surface
- `memory`: active-user and interaction memory surface
- `vision`: local camera/vision surface
- `shared`: config, events, models, process helpers, console UI

## Setup

Create a local development environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env.local
```

For a real realtime speech session, configure:

```bash
AI_COMPANION_INTERACTION_BACKEND=openai_realtime
AI_COMPANION_INPUT_MODE=speech
AI_COMPANION_CLOUD_ENABLED=true
AI_COMPANION_USE_MOCK_AI=false
AI_COMPANION_OPENAI_API_KEY=...
AI_COMPANION_AUDIO_RECORD_COMMAND=...
AI_COMPANION_WAKE_WORD_ENABLED=true
AI_COMPANION_WAKE_WORD_PHRASE=Hey Oreo
AI_COMPANION_WAKE_WORD_MODEL=/path/to/hey_oreo.onnx
```

The recorder command must write raw PCM to stdout. The runtime replaces `{output_path}` with `-` so commands can be written as templates:

```bash
AI_COMPANION_AUDIO_RECORD_COMMAND=arecord -D plughw:2,0 -f S16_LE -r 16000 -c 6 -t raw {output_path}
AI_COMPANION_AUDIO_INPUT_CHANNELS=6
AI_COMPANION_AUDIO_CHANNEL_INDEX=0
```

For multichannel devices such as the ReSpeaker array, `AI_COMPANION_AUDIO_INPUT_CHANNELS` describes the device stream and `AI_COMPANION_AUDIO_CHANNEL_INDEX` selects the processed channel that is passed to wake detection and realtime streaming.

## Audio Output

On macOS, realtime playback defaults to `afplay` if `AI_COMPANION_AUDIO_PLAY_COMMAND` is empty.

On Raspberry Pi, prefer persistent ALSA playback:

```bash
AI_COMPANION_AUDIO_OUTPUT_BACKEND=alsa_persistent
AI_COMPANION_AUDIO_ALSA_DEVICE=default:CARD=vc4hdmi1
AI_COMPANION_OPENAI_REALTIME_AUDIO_SAMPLE_RATE=24000
```

If needed, command playback can be configured instead:

```bash
AI_COMPANION_AUDIO_OUTPUT_BACKEND=command
AI_COMPANION_AUDIO_PLAY_COMMAND=aplay {input_path}
```

## Face UI

The browser-backed face is the main display path:

```bash
AI_COMPANION_UI_BACKEND=browser
AI_COMPANION_UI_BROWSER_LAUNCH_MODE=windowed
```

On the Pi, kiosk mode is usually preferred:

```bash
AI_COMPANION_UI_BROWSER_LAUNCH_MODE=kiosk
```

The Python app starts the local browser bridge and launches Chromium when configured to do so. The robot face badge and animations are driven by the current robot status: `idle`, `listening`, or `speaking`.

## Run

With a configured `.env.local`:

```bash
python -m main
```

For SSH sessions on the Pi, the start helper can select the active graphical session before launching:

```bash
./scripts/start-robot.sh
```

## Pi Sync

Pi-specific secrets and runtime settings live in the ignored local file `.env.local.rpi`. The sync script copies it to the Pi as `.env.local` automatically when present:

```bash
./scripts/sync-to-pi.sh --host <hostname> --user <user>
```

Useful options:

```bash
./scripts/sync-to-pi.sh --host <hostname> --user <user> --env-file path/to/env
./scripts/sync-to-pi.sh --host <hostname> --user <user> --no-env-file
./scripts/sync-to-pi.sh --host <hostname> --user <user> --copy-wake-model /absolute/path/to/model.onnx
```

The sync helper skips `.git`, virtualenvs, logs, artifacts, and local `.env.local` files.

## Validation

Run the test suite locally:

```bash
pytest
```

Useful focused checks:

```bash
pytest tests/test_realtime.py tests/test_config.py tests/test_respeaker_capture.py
```

On the Pi, a quick source compile check is:

```bash
python -m py_compile src/main.py src/ai/realtime.py src/audio/capture.py src/audio/wake.py
```

For live wake-word scoring:

```bash
.venv/bin/python scripts/test-wakeword-live.py --max-seconds 15
```

## Tools And Playgrounds

- `docs/robot-face-playground.html`: tune face timing, expressions, idle behaviors, and exportable settings.
- `docs/wakeword-model-visualizer.html`: inspect wake-word model structure and threshold behavior locally in the browser.
