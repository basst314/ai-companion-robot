# AI Companion Robot

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
- `AI_COMPANION_STT_BACKEND`
- `AI_COMPANION_WHISPER_BINARY_PATH`
- `AI_COMPANION_WHISPER_MODEL_PATH`
- `AI_COMPANION_AUDIO_RECORD_COMMAND`
- `AI_COMPANION_RECORD_SECONDS`
- `AI_COMPANION_LANGUAGE_MODE`

### What The Script Installs

On Raspberry Pi:
- `python3`, `python3-venv`, `python3-pip`
- build tools for `whisper.cpp`
- `alsa-utils` for `arecord`

On macOS:
- `python@3.11`
- `cmake`
- `ffmpeg`
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
- audio capture uses `ffmpeg` with the `avfoundation` input device
- the setup script tries to detect and prefer `MacBook Pro Microphone` or `Built-in Microphone` over linked iPhone microphones
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
19 passed
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

### Speech Input Prototype

The bootstrap script configures the first real STT milestone automatically: local `whisper.cpp` in push-to-talk mode.

You need:
- a built `whisper.cpp` binary such as `whisper-cli`
- a Whisper model file in ggml format
- a local recording command that writes a 16 kHz mono WAV file

Example `whisper.cpp` setup:

```bash
git clone https://github.com/ggml-org/whisper.cpp.git
cd whisper.cpp
cmake -B build
cmake --build build -j --config Release
./models/download-ggml-model.sh base
```

Example one-shot transcription experiment:

```bash
arecord -d 5 -f S16_LE -r 16000 -c 1 /tmp/robot-test.wav
./build/bin/whisper-cli -m models/ggml-base.bin -f /tmp/robot-test.wav --output-json
```

To wire this into the app, configure:
- `runtime.input_mode = "speech"`
- `runtime.stt_backend = "whisper_cpp"`
- `runtime.whisper_binary_path`
- `runtime.whisper_model_path`
- `runtime.audio_record_command`

Example Linux/Raspberry Pi recording command template:

```python
config.runtime.audio_record_command = (
    "arecord",
    "-d",
    "{duration_seconds}",
    "-f",
    "S16_LE",
    "-r",
    "16000",
    "-c",
    "1",
    "{output_path}",
)
```

Example macOS recording command template using `ffmpeg`:

```python
config.runtime.audio_record_command = (
    "ffmpeg",
    "-y",
    "-f",
    "avfoundation",
    "-i",
    ":<audio_index>",
    "-t",
    "{duration_seconds}",
    "-ar",
    "16000",
    "-ac",
    "1",
    "{output_path}",
)
```

The `{duration_seconds}` and `{output_path}` placeholders are expected and are filled in by the runtime when recording starts.

When `interactive_console` is enabled in speech mode, the runtime shows:

```text
Press Enter to record, or type 'exit' to quit>
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

Early working prototype with real one-shot STT integration available for experimentation
