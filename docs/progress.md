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
- the project has a working hybrid turn-planning loop
- the orchestrator can validate and execute multi-step turns that mix local actions, local queries, reactive cues, and cloud reply generation
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
- multi-step turn execution with plan validation
- internal event bus plus event history tracking
- fast local reactive policies during active turns
- fallback handling for vision, cloud AI, and TTS failures

Current limitations:
- manual-input-driven runtime
- no live microphone or camera loop yet
- no background/idle autonomy yet

### Turn Planning

Status: functional

Available now:
- typed `TurnPlan`, `PlanStep`, and capability registry
- local shortcut planner for obvious safe single-purpose turns
- cloud planner for multi-action or conversational turns
- validation for unknown capabilities, unavailable subsystems, and bad arguments
- hybrid execution for turns such as `look at me and tell me a joke`

Current limitations:
- no streaming cloud planning/reply yet
- no idle/background behavior planner yet

### Shared Contracts

Status: functional

Available now:
- typed models for:
  - `Transcript`
  - `TurnPlan`
  - `CapabilityDefinition`
  - `PlanStep`
  - `PlanStepResult`
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

### Cloud AI

Status: mock plus first real provider interface

Available now:
- mock cloud planner
- mock cloud conversational responder
- first OpenAI-specific planner/reply service interfaces
- context-aware replies using transcript, detections, and local step results
- fallback response when cloud generation fails

Current limitations:
- real OpenAI path requires credentials and is not exercised by CI
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
- Piper integration is still pending

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
- sticky terminal AI row for backend state, planning/reply durations, compact plan preview, and compact reply preview
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
- optional interactive OpenAI enablement plus API-key prompt with blank-later support

Current limitations:
- only macOS and Raspberry Pi are first-class setup targets
- no Piper/TTS automation yet
- no hardware, vision, or cloud credential setup yet

---

## Mocked vs Real

### Real implementation pieces

- shared typed contracts
- orchestrator control flow
- turn planning and validation logic
- state transitions
- event tracking and in-process pub/sub
- failure handling patterns
- integration tests for the main interaction loop

### Mocked subsystems

- TTS
- UI rendering
- memory persistence
- vision
- hardware control
- cloud AI

### Not built yet

- real TTS provider integration
- real camera/vision pipeline
- real hardware drivers
- real cloud LLM provider integration
- production logging/config loading

---

## End-to-End Flows Available

### Working flow now

Manual text input
-> orchestrator
-> local reactive cue
-> turn plan selection
-> local action/query and optional cloud reply
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
-> orchestrator planning and execution flow

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
- planner behavior
- plan validation behavior
- hybrid local/cloud turn execution
- partial transcript handling
- memory and vision-backed local query flow
- cloud fallback behavior
- reactive behavior timing
- TTS failure recovery
- vision failure recovery

Current test status:
- `76 passed`

---

## Next Recommended Work

### High priority

- add persistent memory storage on disk
- add real hardware/vision availability reporting into the capability registry
- add streaming cloud reply support once the local execution path is stable

### After that

- harden the real OpenAI provider path for deployment usage
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
- [x] Hybrid turn planning exists
- [x] Capability registry and validation exist
- [x] Memory mock is integrated
- [x] Vision mock is integrated
- [x] Hardware mock is integrated
- [x] Cloud AI mock is integrated
- [x] TTS mock is integrated
- [x] Partial transcript handling exists
- [x] STT mock is wired into runtime loop
- [x] Real cloud AI integration exists
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
