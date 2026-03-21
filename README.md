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

Use these steps on a fresh machine (for example, a Raspberry Pi) or in another development environment.

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
8 passed
```

---

## Run

Start the current interactive mock app with:

```bash
.venv/bin/python src/main.py
```

If the virtual environment is already activated, you can also run:

```bash
python src/main.py
```

Then type messages at the `You>` prompt.

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

---

## Notes

- The orchestrator runs locally and coordinates all components
- Cloud services are used selectively for higher-quality reasoning
- The system should degrade gracefully when offline
- Current implementation status and feature checklist live in `docs/progress.md`

---

## Status

Early working prototype with mocked end-to-end orchestration
