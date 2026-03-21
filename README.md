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

## Notes

- The orchestrator runs locally and coordinates all components
- Cloud services are used selectively for higher-quality reasoning
- The system should degrade gracefully when offline

---

## Status

Early development