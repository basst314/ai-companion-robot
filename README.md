# AI Companion Robot

[![CI](https://github.com/basst314/ai-companion-robot/actions/workflows/ci.yml/badge.svg)](https://github.com/basst314/ai-companion-robot/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/basst314/ai-companion-robot/branch/main/graph/badge.svg)](https://codecov.io/gh/basst314/ai-companion-robot)


A desktop AI companion robot built on a Raspberry Pi, designed to be conversational, expressive, and personality-driven.

The system combines voice interaction, computer vision, a screen-based face, and a lightweight local control layer with cloud-based AI to create a responsive and engaging companion.

---

## Overview

The robot listens, understands, and responds using a pipeline:
```
Microphone → Speech-to-Text → AI → Text-to-Speech → Speaker
```
It also uses a camera for basic awareness and a display to show animated facial expressions.

---

## Architecture

The system is split between local execution on the Raspberry Pi and cloud services.

### Runs on Raspberry Pi (Local)

- Audio input (microphone)
- Speech-to-text (Whisper / whisper.cpp)
- Text-to-speech (Piper)
- Camera processing (face detection)
- Display rendering (eyes and expressions)
- AI orchestrator (main control loop)
- Memory (local storage)
- Hardware control (future)

### Runs in the Cloud

- Large Language Model (primary reasoning and response generation)
- Optional fallback speech-to-text
- Optional higher-quality text-to-speech

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
- TTS: Piper (local)
- AI: cloud LLM (e.g. OpenAI, Gemini)
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
- clones and builds `whisper.cpp`
- downloads a default Whisper model
- generates `.env.local` with the local STT runtime settings
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
- `--yes`
- `--force`
- `--skip-system-packages`

The generated `.env.local` file is user-editable and contains:
- `AI_COMPANION_INPUT_MODE`
- `AI_COMPANION_INTERACTIVE_CONSOLE`
- `AI_COMPANION_STT_BACKEND`
- `AI_COMPANION_WHISPER_BINARY_PATH`
- `AI_COMPANION_WHISPER_MODEL_PATH`
- `AI_COMPANION_AUDIO_RECORD_COMMAND`
- `AI_COMPANION_SPEECH_SILENCE_SECONDS`
- `AI_COMPANION_WAKE_WORD_ENABLED`
- `AI_COMPANION_WAKE_WORD_PHRASE`
- `AI_COMPANION_WAKE_WINDOW_SECONDS`
- `AI_COMPANION_WAKE_STRIDE_SECONDS`
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

The bootstrap script now configures the current local speech path automatically: `whisper.cpp` with wake-word gated listening, shared live-stream handoff, and typed/Enter/manual fallback in the interactive console.

You need:
- a built `whisper.cpp` binary such as `whisper-cli`
- a Whisper model file in ggml format
- a local recording command that writes 16 kHz mono raw PCM to `stdout`

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
- `AI_COMPANION_WAKE_WORD_PHRASE=Hello`

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
AI_COMPANION_MAX_RECORDING_SECONDS=15
```

The `{output_path}` placeholder is expected and is filled in by the runtime when recording starts. For the built-in streaming STT flow, the runtime substitutes `-` and captures raw PCM from the recorder's `stdout`. That lets the app inspect live audio, keep a bounded wake-word ring buffer in memory, create WAV snapshots for `whisper.cpp`, and end the utterance after confirmed silence. Custom recorder commands therefore need to support writing raw PCM to standard output.

`AI_COMPANION_MAX_RECORDING_SECONDS` adds a simple hard stop for each utterance so the recorder cannot run forever when background noise prevents the current silence-based end-of-utterance logic from settling.

When `interactive_console` is enabled in speech mode, the runtime supports all of these at once:

- type a phrase and press Enter
- press Enter on an empty line to start listening immediately
- say the configured wake word
- type `exit` to quit

The sticky terminal header also shows:
- microphone level and silence progress
- the wake state (`listening` or `awake`)
- the live transcript preview
- the wake ring-buffer state for debugging handoff timing

If `.env.local` is not present, the app falls back to the default manual text-mode prompt and you can type messages at `You>`.

Examples:
- `open your eyes`
- `look at me`
- `turn your head left`
- `who do you see`
- `what do you know about me`
- `tell me a joke`
- `please use your local brain`

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

Early working prototype with wake-word gated local STT, shared live-stream handoff, and interactive terminal debugging available for experimentation
