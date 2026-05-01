# Architecture

## 1. Purpose

This document describes the current system architecture for the AI Companion Robot:

- system structure
- component responsibilities
- data flow
- local vs cloud boundaries
- failure handling and extension points

## 2. High-Level System

The system consists of a local runtime on Raspberry Pi and an OpenAI Realtime session in the cloud.

Core idea:

- latency-sensitive capture, wake detection, tool validation, UI, and playback stay local
- realtime language understanding and response audio stream through OpenAI Realtime
- local tools are exposed to the model only through the orchestrator's validation layer

## 3. Core Components

### 3.1 Orchestrator

The orchestrator is the central control loop.

Responsibilities:

- coordinate local components
- manage lifecycle and emotion state
- start wake-triggered realtime sessions
- validate realtime model tool calls
- execute local actions and queries
- publish events for UI and debugging
- maintain short-term interaction state

### 3.2 Audio Capture

Input:

- raw microphone PCM from a shell command

Processing:

- optional one-time audio init command
- ReSpeaker/interleaved-channel extraction
- shared live audio buffering
- wake-window lookback
- chunk fanout to wake detection and realtime streaming

Output:

- mono PCM chunks
- wake-window audio context
- realtime input queue chunks

### 3.3 Wake Detection

Wake detection runs locally with OpenWakeWord.

Responsibilities:

- score fixed-size PCM frames
- apply wake threshold, patience, and debounce behavior
- mark the wake stream offset
- start the realtime utterance with configured lookback audio
- update terminal/debug wake and ring-buffer status

### 3.4 OpenAI Realtime

The realtime client owns the active WebSocket session.

Responsibilities:

- send session configuration
- stream microphone PCM to the model
- receive streamed assistant PCM
- play audio locally through command or ALSA output
- emit playback lifecycle events
- handle server VAD / semantic VAD events
- handle barge-in by interrupting local playback and truncating unheard assistant audio
- dispatch tool calls to the orchestrator

### 3.5 Local Tools

The capability registry defines which tools are available. The orchestrator validates every requested tool before execution.

Current local capability surfaces:

- UI actions and state
- hardware actions
- memory queries
- vision/camera snapshots
- robot status queries

### 3.6 UI

The UI layer renders the robot face and reacts to lifecycle events.

Responsibilities:

- browser bridge and Chromium launch
- animated face state
- text overlay
- listening/speaking/idle transitions
- reactive expression changes

### 3.7 Memory

Memory stores active-user context and interaction history. The current implementation is lightweight and local, with room for persistence or summarization.

### 3.8 Vision

Vision owns local camera snapshot and detection surfaces. Realtime model tool calls can request a camera snapshot through the orchestrator.

## 4. Data Flow

### Realtime Voice Session

```text
User speech
-> microphone command
-> channel extraction
-> shared live audio buffer
-> OpenWakeWord wake detection
-> OpenAI Realtime audio input
-> model response / tool calls
-> orchestrator tool validation
-> local tool execution
-> streamed assistant audio
-> local playback
-> UI/event updates
```

### Tool Call Flow

```text
Realtime model tool call
-> orchestrator
-> capability registry validation
-> local service execution
-> tool result returned to realtime session
```

### Face/UI Flow

```text
Orchestrator lifecycle event
-> event bus
-> UI service / browser bridge
-> robot face state update
```

## 5. Local vs Cloud Responsibilities

### Local

- microphone capture
- channel extraction
- wake detection
- wake lookback buffering
- realtime audio playback
- tool validation
- local action/query execution
- UI rendering
- memory
- vision/camera access
- hardware control

### Cloud

- realtime speech understanding
- response generation
- response audio streaming
- tool-call planning within the constrained tool definitions

## 6. Event Model

The system is event-driven. Important events include:

- `listening_started`
- `face_detected`
- `plan_created`
- `step_started`
- `step_finished`
- `response_ready`
- `audio_playback_started`
- `audio_playback_finished`
- `audio_interrupted`
- `audio_finished`
- `error_occurred`

## 7. Configuration

Runtime configuration is loaded from `.env` and `.env.local`, with process environment taking precedence.

Important groups:

- `AI_COMPANION_OPENAI_REALTIME_*`
- `AI_COMPANION_AUDIO_*`
- `AI_COMPANION_WAKE_WORD_*`
- `AI_COMPANION_UI_*`
- `AI_COMPANION_MOCK_*`

Pi deployments commonly use a local `.env.local.rpi` on the development machine and sync it to the Pi as `.env.local`.

## 8. Failure Handling

The system should degrade gracefully:

- if wake detection fails to initialize, fail early during runtime startup
- if realtime session errors, publish an error event and return to idle
- if local playback is interrupted, keep the realtime session state consistent
- if a tool fails, return a tool failure result instead of exposing uncontrolled exceptions to the model
- if UI/browser launch fails, keep the orchestrator error visible through logs

## 9. Design Constraints

- prioritize responsiveness over completeness
- keep local authority over hardware and camera tools
- keep modules loosely coupled
- prefer asynchronous I/O
- avoid blocking the event loop
- keep Pi-specific secrets out of tracked files

## 10. Extension Points

- richer hardware actions
- persistent memory
- multi-user tracking
- richer camera/vision summaries
- additional local tool definitions
- refined face expression policy
