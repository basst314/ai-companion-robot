# Project Progress

This document tracks the current implementation status of the AI Companion Robot.

Update it with every iteration so it stays useful as:
- a feature checklist
- a status snapshot
- a quick reference for what is real vs mocked

---

## Current Stage

Status: early working local prototype

Current milestone:
- the project has a working mocked end-to-end orchestration loop
- the orchestrator can route manual text input to local actions, local queries, local AI, or cloud AI
- responses can be shown in the UI mock and acknowledged by the TTS mock
- interaction history is stored in memory
- a bootstrap setup script can prepare the local speech prototype on macOS and Raspberry Pi

What this means today:
- the architecture is no longer just scaffolded
- the main integration seams now exist
- the system is ready for replacing mocks one module at a time

---

## Functional Today

### Orchestrator

Status: functional

Available now:
- central `OrchestratorService`
- lifecycle management for `idle`, `listening`, `processing`, `responding`, `error`
- typed interaction state
- route execution for local actions, local queries, local AI, and cloud chat
- event history tracking
- fallback handling for vision, cloud AI, and TTS failures

Current limitations:
- manual-input-driven runtime
- no live microphone or camera loop yet
- event handling is recorded internally, not yet broadcast to external subscribers

### Routing

Status: functional

Available now:
- rule-based `IntentRouter`
- local action routing for:
  - `open your eyes`
  - `close your eyes`
  - `look at me`
  - `turn your head`
- local query routing for:
  - `who do you see`
  - `what do you know about me`
  - `what state are you in`
- local LLM route trigger
- cloud chat fallback for unmatched requests

Current limitations:
- keyword/rule-based only
- no confidence learning or ambiguity resolution yet

### Shared Contracts

Status: functional

Available now:
- typed models for:
  - `Transcript`
  - `RouteDecision`
  - `ActionRequest`
  - `ActionResult`
  - `QueryResult`
  - `AiResponse`
  - `SpeechOutput`
  - `InteractionContext`
  - `InteractionRecord`
  - `VisionDetection`
- typed event names for transcript, routing, query/action execution, TTS, and errors
- typed runtime and mock config

### Memory

Status: functional mock

Available now:
- `InMemoryMemoryService`
- active user lookup
- recent interaction history
- interaction persistence after each turn
- user summary lookup for local queries

Current limitations:
- no disk persistence
- no long-term summarization
- no multi-user management logic yet

### Vision

Status: functional mock

Available now:
- deterministic fake detections
- visible-person context available to orchestrator
- local query support for “who do you see”
- graceful failure path

Current limitations:
- no camera integration
- no real face detection
- no recognition pipeline
- no continuous background polling

### Hardware

Status: functional mock

Available now:
- mock eye state
- mock head direction
- local action execution through typed requests
- state changes reflected back into orchestrator state

Current limitations:
- no real GPIO, servo, or motor control
- no safety constraints or movement limits yet

### Local AI

Status: functional mock

Available now:
- stubbed lightweight local reply path
- explicit local-AI route supported by orchestrator

Current limitations:
- not yet a real local model
- not yet used for general command parsing

### Cloud AI

Status: functional mock

Available now:
- mock cloud conversational responder
- context-aware replies using transcript and detections
- fallback response when cloud generation fails

Current limitations:
- no real provider integration yet
- no prompt management
- no streaming responses

### TTS

Status: functional mock

Available now:
- `MockTtsService`
- printed `[TTS] ...` acknowledgement
- TTS started/finished events
- graceful failure handling

Current limitations:
- no audio synthesis
- no playback queue
- no voice selection

### UI

Status: functional mock

Available now:
- state rendering for lifecycle and emotion
- preview text updates
- response text display

Current limitations:
- console-backed only
- no animated face
- no actual display integration

### STT

Status: working local speech prototype

Available now:
- typed incremental transcript model with partial/final support
- `MockSttService` that can emit partial and final transcripts
- orchestrator method for partial transcript handling
- `WhisperCppSttService` for one-shot local transcription through `whisper.cpp`
- shell-based audio capture adapter for live microphone PCM streaming
- `OpenWakeWordWakeWordService` for transcript-independent wake-word detection
- wake-word-gated speech mode with a bounded rolling buffer while idle
- shared live-stream handoff from wake detection into the active utterance without restarting capture
- configurable built-in or custom wake-word model selection through runtime config
- bundled Silero VAD endpointing for noisy-room end-of-utterance detection
- tunable VAD endpoint controls through runtime config
- sticky terminal debug rows for mic state, VAD tail state, ring-buffer state, wake status, and transcript preview
- speech-mode runtime that supports typed phrases, Enter-to-talk, and wake-word activation in the same loop

Current limitations:
- no partial transcript support from the real STT path yet
- recording still depends on a configured external command such as `rec` or `arecord`
- microphone setup is scripted for supported platforms, but device-specific debugging may still be manual
- custom wake phrases still require a matching custom OpenWakeWord model

### Setup

Status: first automated bootstrap available

Available now:
- `scripts/setup.sh` for macOS and Raspberry Pi bootstrap
- automatic `.venv` creation and Python dependency install
- Python-version-aware `.venv` recreation when the existing environment is incompatible
- `whisper.cpp` clone/build plus default model download
- OpenWakeWord runtime resolution plus built-in wake-model verification during setup
- generated `.env.local` runtime configuration

Current limitations:
- only macOS and Raspberry Pi are first-class setup targets
- no Piper/TTS automation yet
- no hardware, vision, or cloud credential setup yet

---

## Mocked vs Real

### Real implementation pieces

- shared typed contracts
- orchestrator control flow
- routing logic
- state transitions
- event tracking
- failure handling patterns
- integration tests for the main interaction loop

### Mocked subsystems

- TTS
- UI rendering
- memory persistence
- vision
- hardware control
- local AI
- cloud AI

### Not built yet

- real TTS provider integration
- real camera/vision pipeline
- real hardware drivers
- real local LLM
- real cloud LLM provider integration
- production logging/config loading

---

## End-to-End Flows Available

### Working flow now

Manual text input
-> orchestrator
-> route selection
-> local action or local query or mock AI
-> mock UI update
-> mock TTS acknowledgement
-> memory persistence

See `README.md` for current setup and run commands.

### Partially supported flow

Mock STT partial transcript
-> orchestrator listening state update
-> no execution until final transcript

### New experimental flow

Push-to-talk speech input
-> external recorder command creates WAV
-> `whisper.cpp` produces final transcript
-> orchestrator route selection and response flow

### Not yet available

Live microphone
-> real streaming STT
-> orchestrator
-> real spoken output

---

## Test Coverage

Currently covered:
- app bootstrapping
- full manual turn execution
- routing behavior
- partial transcript handling
- memory and vision-backed local query flow
- cloud fallback behavior
- TTS failure recovery
- vision failure recovery

Current test status:
- `8 passed`

---

## Next Recommended Work

### High priority

- wire `MockSttService` into `OrchestratorService.run()` so runtime can simulate partial plus final transcript streams
- add a dedicated `ai/` package to architecture docs if not already documented elsewhere
- add a real event subscription mechanism if UI or other modules should react independently
- add persistent memory storage on disk

### After that

- integrate a real cloud LLM provider behind the existing interface
- integrate real TTS
- integrate real STT
- add real vision polling/detection
- add real hardware adapters

### Later

- local lightweight LLM for constrained reasoning
- streaming cloud responses
- richer multi-user memory
- animated face UI
- head tracking and servo coordination

---

## Iteration Checklist

Use this checklist during each implementation pass.

- [x] Shared typed contracts exist
- [x] Orchestrator can execute a full mocked turn
- [x] Rule-based local routing exists
- [x] Memory mock is integrated
- [x] Vision mock is integrated
- [x] Hardware mock is integrated
- [x] Cloud AI mock is integrated
- [x] Local AI mock is integrated
- [x] TTS mock is integrated
- [x] Partial transcript handling exists
- [ ] STT mock is wired into runtime loop
- [ ] Real cloud AI integration exists
- [ ] Real TTS integration exists
- [ ] Real STT integration exists
- [ ] Real vision integration exists
- [ ] Real hardware integration exists
- [ ] Persistent memory exists
- [ ] Display UI exists
- [ ] Full Raspberry Pi hardware smoke test exists

---

## Update Notes

Suggested update rule for future iterations:
- update `Current Stage`
- update each subsystem section that changed
- mark checklist items done when complete
- add any newly discovered gaps or blockers
