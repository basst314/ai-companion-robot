# Architecture

## 1. Purpose

This document defines the system architecture for the AI Companion Robot.

It serves as the source of truth for:
- system structure
- component responsibilities
- data flow
- local vs cloud boundaries

All implementations should follow this architecture.

---

## 2. High-Level System

The system consists of a local runtime (Raspberry Pi) and optional cloud services.

Core idea:
- low-latency interaction runs locally
- high-quality reasoning runs in the cloud

---

## 3. Core Components

### 3.1 Orchestrator (Local)

The orchestrator is the central control loop running on the Raspberry Pi.

Responsibilities:
- coordinate all components
- manage interaction flow
- route data between modules
- call cloud services when needed
- maintain conversation state
- manage personality layer

This is the most important component.

---

### 3.2 Audio Input (STT)

Input:
- microphone audio stream

Processing:
- continuous wake-word detection using OpenWakeWord (local)
- speech-to-text using whisper.cpp (local)
- end-of-utterance detection using the Silero VAD bundled with OpenWakeWord
- shared live audio buffering so wake detection and STT consume the same microphone stream without restarting capture

Output:
- transcript
- detected language
- confidence score
- wake-word activation events with buffered pre-roll handoff into the STT loop

---

### 3.3 AI Layer

Runs primarily in the cloud.

Input:
- transcript
- context (memory, user identity, state)

Processing:
- generate response
- apply personality tone

Output:
- response text
- optional metadata (emotion, intent)

---

### 3.4 Text-to-Speech (TTS)

Runs locally using Piper.

Input:
- response text
- language

Processing:
- generate audio waveform

Output:
- audio playback

---

### 3.5 Vision

Input:
- camera frames

Processing:
- face detection
- optional face recognition

Output:
- detected faces
- identity (if known)
- position (for future tracking)

---

### 3.6 UI (Face Display)

Responsibilities:
- render animated eyes
- reflect emotional state
- react to events

Input:
- emotion/state from orchestrator

---

### 3.7 Memory

Local storage system.

Stores:
- known users
- preferences
- past interactions (optional summaries)

Used by:
- orchestrator
- AI layer

---

## 4. Data Flow

### Voice Interaction
```
User speech
→ Microphone
→ Wake-word detection (OpenWakeWord, local)
→ STT (local)
→ Orchestrator
→ AI (cloud)
→ Orchestrator
→ TTS (local)
→ Speaker
```

---

### Vision Interaction
```
Camera
→ Vision module
→ Orchestrator
→ AI (optional)
→ UI / behavior updates
```

---

## 5. Local vs Cloud Responsibilities

### Local (Raspberry Pi)

- Wake-word detection (OpenWakeWord)
- STT (whisper.cpp)
- TTS (Piper)
- Vision processing
- UI rendering
- Orchestrator
- Memory
- Hardware control

### Cloud

- LLM (primary intelligence)
- optional enhanced STT
- optional enhanced TTS

---

## 6. Interaction Model

The system is event-driven.

Examples:
- "speech_detected"
- "face_detected"
- "response_ready"
- "audio_finished"

The orchestrator reacts to events and triggers actions.

---

## 7. Multilingual Support

- STT detects language automatically
- responses default to same language
- TTS selects voice based on language
- memory stores language metadata

Supported languages:
- English
- German
- Indonesian

---

## 8. Failure Handling

System should degrade gracefully:

- if cloud unavailable → fallback responses
- if STT fails → ignore input
- if TTS fails → skip audio but keep state

---

## 9. Future Extensions

- servo-based head movement
- directional awareness (mic array)
- multi-user tracking
- improved memory system
- local AI fallback

---

## 10. Design Constraints

- prioritize responsiveness over completeness
- keep modules loosely coupled
- avoid blocking operations
- prefer asynchronous design
- optimize for real-time interaction

---
