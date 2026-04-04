# AI Companion Robot

[![CI](https://github.com/basst314/ai-companion-robot/actions/workflows/ci.yml/badge.svg)](https://github.com/basst314/ai-companion-robot/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/basst314/ai-companion-robot/branch/main/graph/badge.svg)](https://codecov.io/gh/basst314/ai-companion-robot)


A desktop AI companion robot built on a Raspberry Pi, designed to be conversational, expressive, and personality-driven.

The system combines voice interaction, computer vision, a screen-based face, and a lightweight local control layer with cloud-based AI to create a responsive and engaging companion.

The current runtime uses a local-first turn director: fast local reactive behaviors during listening and thinking, a typed local capability registry for actions and queries, a single cloud reply path with optional tool calls, and local speech output on the robot.

---

## Overview

The robot listens, routes, acts, and responds using a pipeline:
```
Microphone → Speech-to-Text → Orchestrator/Turn Director → Local Actions + Cloud Reply Text → Text-to-Speech → Speaker
```
It also uses a camera for basic awareness and a display to show animated facial expressions.

---

## Architecture

The system is split between local execution on the Raspberry Pi and cloud services.

### Runs on Raspberry Pi (Local)

- Audio input (microphone)
- Speech-to-text (Whisper / whisper.cpp)
- Text-to-speech (mock or local Piper)
- Camera processing (face detection)
- Display rendering (eyes and expressions)
- Hybrid orchestrator and capability executor
- Memory (local storage)
- Hardware control (future)

### Runs in the Cloud

- OpenAI-backed response text generation with optional tool calls
- Optional fallback speech-to-text
- No cloud speech output in the current architecture

---

## Key Capabilities

- Voice interaction with low latency
- Multilingual support (English, German, Indonesian)
- Animated face with expressions and reactions
- Recognition of known individuals
- Personality-driven responses (humor, sound effects)
- Hybrid local/cloud execution

---

## Hardware

- Raspberry Pi 5 (8GB)
- Camera Module 3 (Wide)
- Microphone (ReSpeaker array or alternative)
- 5" HDMI display
- Speaker (via display board)
- USB power bank

---

## Software Stack

- STT: whisper.cpp (local)
- TTS: local output pipeline with mock and Piper-backed implementations
- AI reply/tool-calling: OpenAI Responses API for the first real cloud backend
- Vision: OpenCV (initial)
- Orchestrator: Python service running on the Pi

---

## Setup

Use the automated bootstrap on a fresh machine such as a Raspberry Pi or your local MacBook.

### Quick Start

```bash
./scripts/setup.sh
```

The script:
- detects macOS or Raspberry Pi
- installs system dependencies with `brew` or `apt`
- creates `.venv`
- installs Python project dependencies
- optionally installs the Piper HTTP TTS runtime
- resolves the OpenWakeWord runtime for the current platform
- downloads and validates the selected OpenWakeWord runtime models when wake-word mode is enabled
- clones and builds `whisper.cpp`
- downloads a default Whisper model
- optionally downloads Piper voice packs for English/German/Indonesian
- generates `.env.local` with the local STT/TTS runtime settings
- runs the test suite

After setup finishes:

```bash
.venv/bin/python src/main.py
```

The runtime will read `.env.local` automatically if it exists.

### Automated Setup

The bootstrap script is interactive by default and uses sensible defaults. You can also override key choices:

```bash
./scripts/setup.sh --platform macos --model base --language-mode auto
```

Supported flags:
- `--platform <macos|rpi>`
- `--model <tiny|base|small>`
- `--language-mode <auto|en|de|id>`
- `--tts-backend <mock|piper>`
- `--tts-languages <en,de,id>`
- `--tts-service-mode <managed|external>`
- `--tts-expressive-de`
- `--yes`
- `--force`
- `--skip-system-packages`

`--force` recreates the generated local environment instead of reusing it. In practice that means:
- rebuild `.venv`
- rewrite `.env.local`
- rebuild `whisper.cpp`
- re-download the selected Whisper model
- re-resolve and re-verify the wake-word model setup

The generated `.env.local` file is user-editable and contains:
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
- `AI_COMPANION_FOLLOW_UP_MODE_ENABLED`
- `AI_COMPANION_FOLLOW_UP_LISTEN_TIMEOUT_SECONDS`
- `AI_COMPANION_FOLLOW_UP_MAX_TURNS`
- `AI_COMPANION_WAKE_WORD_PHRASE`
- `AI_COMPANION_WAKE_WORD_MODEL`
- `AI_COMPANION_WAKE_WORD_THRESHOLD`
- `AI_COMPANION_WAKE_LOOKBACK_SECONDS`
- `AI_COMPANION_UTTERANCE_FINALIZE_TIMEOUT_SECONDS`
- `AI_COMPANION_UTTERANCE_TAIL_STABLE_POLLS`
- `AI_COMPANION_LANGUAGE_MODE`

### What The Script Installs

On Raspberry Pi:
- `python3`, `python3-venv`, `python3-pip`
- build tools for `whisper.cpp`
- `alsa-utils` for `arecord`

On macOS:
- `python@3.11`
- `cmake`
- `sox`
- `git`

Project-local artifacts:
- `.venv/`
- `artifacts/whisper.cpp/`
- `.env.local`

### Platform Notes

Raspberry Pi:
- the script expects a Debian-family Raspberry Pi environment
- audio capture defaults to `arecord`

macOS:
- the script uses Homebrew for dependencies
- audio capture defaults to `rec`, which has been more reliable than `ffmpeg`/`avfoundation` on some Macs
- you may need to grant microphone access to Terminal or your shell app

### Manual Fallback

If you prefer to install everything yourself, you can still use the manual path below.

### 1. Create a virtual environment

```bash
python3 -m venv .venv
```

### 2. Activate the virtual environment

macOS / Linux:

```bash
source .venv/bin/activate
```

### 3. Install the project

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

### 4. Verify the setup

```bash
.venv/bin/pytest -q
```

Expected result right now:

```text
all tests pass
```

---

## Run

Start the app with:

```bash
.venv/bin/python src/main.py
```

If the virtual environment is already activated, you can also run:

```bash
python src/main.py
```

With the generated speech config in place, the interactive console supports typed phrases, Enter-to-talk, and wake-word activation in the same session. Without `.env.local`, the app falls back to the default manual text prompt at `You>`.

### Speech Input Prototype

The bootstrap script now configures the current local speech path automatically: `whisper.cpp` for STT, `OpenWakeWord` for wake-word detection, shared live-stream handoff, and typed/Enter/manual fallback in the interactive console.

Speech mode also supports wake-free follow-up turns. After the robot finishes a spoken reply, it opens a short follow-up listen window so you can continue naturally without repeating the wake word. That follow-up path only proceeds when VAD confirms real speech, which makes it much less likely that TV audio, music, or placeholder Whisper outputs such as `[BLANK AUDIO]` accidentally trigger a second turn.

The interaction layer now supports multi-step turns. A single utterance can trigger local actions or queries before the cloud reply is generated, for example turning toward the user and then speaking a cloud-generated answer with local TTS.

You need:
- a built `whisper.cpp` binary such as `whisper-cli`
- a Whisper model file in ggml format
- a local recording command that writes 16 kHz mono raw PCM to `stdout`
- an OpenWakeWord model name or model file path when wake-word mode is enabled

Example `whisper.cpp` setup:

```bash
git clone https://github.com/ggml-org/whisper.cpp.git
cd whisper.cpp
cmake -B build
cmake --build build -j --config Release
./models/download-ggml-model.sh base
```

Example one-shot transcription experiment on Raspberry Pi or Linux:

```bash
arecord -t raw -f S16_LE -r 16000 -c 1 /tmp/robot-test.pcm
ffmpeg -f s16le -ar 16000 -ac 1 -i /tmp/robot-test.pcm /tmp/robot-test.wav
./build/bin/whisper-cli -m models/ggml-base.bin -f /tmp/robot-test.wav --output-json
```

To wire this into the app, configure:
- `AI_COMPANION_INPUT_MODE=speech`
- `AI_COMPANION_STT_BACKEND=whisper_cpp`
- `AI_COMPANION_WHISPER_BINARY_PATH`
- `AI_COMPANION_WHISPER_MODEL_PATH`
- `AI_COMPANION_AUDIO_RECORD_COMMAND`
- `AI_COMPANION_MAX_RECORDING_SECONDS`
- `AI_COMPANION_WAKE_WORD_ENABLED=true`
- `AI_COMPANION_FOLLOW_UP_MODE_ENABLED=true`
- `AI_COMPANION_FOLLOW_UP_LISTEN_TIMEOUT_SECONDS=3.0`
- `AI_COMPANION_FOLLOW_UP_MAX_TURNS=10`
- `AI_COMPANION_WAKE_WORD_PHRASE=Hey Jarvis`
- `AI_COMPANION_WAKE_WORD_MODEL=hey jarvis`
- `AI_COMPANION_WAKE_WORD_THRESHOLD=0.5`
- `AI_COMPANION_WAKE_LOOKBACK_SECONDS=0.8`

The interactive setup script will ask whether you want the default built-in `Hey Jarvis` pairing or a custom phrase plus matching OpenWakeWord model. For the default built-in option, setup downloads and verifies the model locally before writing `.env.local`. Custom phrases only work when you provide a model that was trained for that phrase, and setup now verifies that the file exists and can initialize.

For end-of-utterance detection, the runtime now uses the Silero VAD bundled with `openwakeword` instead of relying only on raw energy silence. `AI_COMPANION_SPEECH_SILENCE_SECONDS` is therefore the amount of VAD-confirmed trailing non-speech required before the utterance is finalized.

Example Linux/Raspberry Pi recording command template:

```python
config.runtime.audio_record_command = (
    "arecord",
    "-t",
    "raw",
    "-f",
    "S16_LE",
    "-r",
    "16000",
    "-c",
    "1",
    "{output_path}",
)
```

Example macOS recording command template using `rec`:

```python
config.runtime.audio_record_command = (
    "rec",
    "-q",
    "-c",
    "1",
    "-r",
    "16000",
    "-b",
    "16",
    "-e",
    "signed-integer",
    "-t",
    "raw",
    "{output_path}",
)
```

Equivalent `.env.local` example:

```env
AI_COMPANION_AUDIO_RECORD_COMMAND=rec -q -c 1 -r 16000 -b 16 -e signed-integer -t raw {output_path}
AI_COMPANION_SPEECH_SILENCE_SECONDS=0.75
AI_COMPANION_VAD_THRESHOLD=0.45
AI_COMPANION_VAD_FRAME_MS=30
AI_COMPANION_VAD_START_TRIGGER_FRAMES=2
AI_COMPANION_VAD_END_TRIGGER_FRAMES=5
AI_COMPANION_MAX_RECORDING_SECONDS=15
```

The `{output_path}` placeholder is expected and is filled in by the runtime when recording starts. For the built-in streaming STT flow, the runtime substitutes `-` and captures raw PCM from the recorder's `stdout`. That lets the app inspect live audio, keep a bounded wake-word ring buffer in memory, create WAV snapshots for `whisper.cpp`, and end the utterance after VAD-confirmed trailing non-speech. Custom recorder commands therefore need to support writing raw PCM to standard output.

`AI_COMPANION_MAX_RECORDING_SECONDS` adds a simple hard stop for each utterance so the recorder cannot run forever if the endpoint detector never settles.

`AI_COMPANION_FOLLOW_UP_MODE_ENABLED` controls whether the robot automatically opens a no-wake follow-up listen after a spoken reply. `AI_COMPANION_FOLLOW_UP_LISTEN_TIMEOUT_SECONDS` is the VAD-confirmed speech-start window for that follow-up turn. `AI_COMPANION_FOLLOW_UP_MAX_TURNS` limits how many wake-free follow-up turns can chain after the initial wake/manual turn before the robot requires the wake word again.

For a quick look at a small openWakeWord model outside the app, open the [wake-word model visualizer](https://basst314.github.io/ai-companion-robot/wakeword-model-visualizer.html) in a browser and choose an `.onnx` wake-word model file. The page parses the model locally and shows its layer layout, parameter counts, and per-node weight summaries.

When `interactive_console` is enabled in speech mode, the runtime supports all of these at once:

- type a phrase and press Enter
- press Enter on an empty line to start listening immediately
- say the configured wake word
- type `exit` to quit

The sticky terminal header also shows:
- microphone level plus explicit VAD idle/live/tail state
- the wake state (`listening` or `awake`)
- the live transcript preview
- the wake ring-buffer state for debugging handoff timing
- AI backend activity, including route/reply timing, the current turn-plan summary, and a clipped reply preview

If `.env.local` is not present, the app falls back to the default manual text-mode prompt and you can type messages at `You>`.

Examples:
- `look at me`
- `look at me and tell me a joke`
- `turn your head left`
- `can you see me`
- `who do you see`
- `what do you know about me`
- `tell me a joke`

To enable the real OpenAI reply path instead of the mock cloud services, set:

```env
AI_COMPANION_USE_MOCK_AI=false
AI_COMPANION_CLOUD_ENABLED=true
AI_COMPANION_CLOUD_PROVIDER_NAME=openai
AI_COMPANION_OPENAI_API_KEY=...
AI_COMPANION_OPENAI_RESPONSE_MODEL=...
AI_COMPANION_OPENAI_REPLY_MAX_OUTPUT_TOKENS=120
```

The cloud backend returns reply text and can request local tools such as a camera snapshot when needed. Audio output remains local and Piper is still a later milestone.
For short-term continuity, the OpenAI path also reuses the prior response thread for wake-free follow-ups and for fresh wake-word turns that happen again within a short in-memory resume window.
The setup script can now enable the OpenAI path interactively, asks for the API key when you opt in, and accepts a blank value so you can add the key later in `.env.local`.

Exit with:

```text
exit
```

### Updating / Re-running Setup

Re-run the bootstrap script whenever you want to refresh the environment:

```bash
./scripts/setup.sh
```

Helpful variants:
- `./scripts/setup.sh --yes` to accept defaults without prompts
- `./scripts/setup.sh --force` to rebuild `whisper.cpp` and rewrite `.env.local`
- `./scripts/setup.sh --skip-system-packages` if your machine already has the required OS-level tools

For more detailed setup notes and troubleshooting, see `docs/setup.md`.

---

## Notes

- The orchestrator runs locally and coordinates all components
- Cloud services are used selectively for higher-quality reasoning
- The system should degrade gracefully when offline
- Current implementation status and feature checklist live in `docs/progress.md`

---

## Status

Early working prototype with OpenWakeWord wake detection, local `whisper.cpp` STT, wake-free multi-turn follow-ups with VAD gating, short-term OpenAI thread continuity, and interactive terminal debugging available for experimentation
