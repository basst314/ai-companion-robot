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
- the project has a working local-first turn-routing loop
- the orchestrator can validate and execute multi-step turns that mix local actions, local queries, reactive cues, and a single tool-aware cloud reply path
- responses can be shown through a mock UI or the browser-backed Chromium kiosk face renderer and spoken through queued mock or Piper-backed TTS
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
- lifecycle management for `idle`, `listening`, `processing`, `responding`, `speaking`, and `error`
- typed interaction state
- multi-step turn execution with plan validation
- short-term OpenAI response-thread continuity across immediate follow-ups and brief wake-word resumptions
- internal event bus plus event history tracking
- fast local reactive policies during active turns
- fallback handling for vision, cloud AI, and TTS failures

Current limitations:
- real speech input exists, but UI/vision/hardware are still partly or fully mocked
- no real camera loop yet
- no background/idle autonomy yet

### Turn Routing

Status: functional

Available now:
- typed `TurnPlan`, `PlanStep`, and capability registry
- local-first turn director with high-precision local shortcuts for obvious safe turns
- single cloud reply fallback for normal conversation
- tool-aware cloud replies for requests that need extra evidence such as a camera snapshot
- validation for unknown capabilities, unavailable subsystems, and bad arguments
- hybrid execution for turns such as `look at me and tell me a joke`

Current limitations:
- no streaming cloud replies yet
- local shortcut coverage is intentionally narrow
- no idle/background autonomy planner yet

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

Status: mock plus real OpenAI reply provider

Available now:
- mock cloud conversational responder
- OpenAI Responses API-backed reply service
- single response-model call for normal chat turns
- optional local tool continuation for `camera_snapshot`
- context-aware replies using transcript, detections, and local step results
- structured reply metadata that carries spoken reply language for TTS voice selection
- short-term `previous_response_id` reuse across immediate follow-ups and a brief wake-word resume window
- configurable spoken reply cap through `AI_COMPANION_OPENAI_REPLY_MAX_OUTPUT_TOKENS`
- fallback response when cloud generation fails

Current limitations:
- real OpenAI path requires credentials and is not exercised by CI
- the default camera tool still uses a mock snapshot payload until real vision is wired in
- no streaming responses

### TTS

Status: working local + mock prototype

Available now:
- `MockTtsService`
- queued TTS requests with append/replace policies
- mock and Piper-backed synthesis/playback adapters
- precise queue/synthesis/playback events
- playback-start synchronization through `AudioPlaybackSession.wait_started()`
- standardized playback event payloads with timing metadata for UI sync
- persistent `aplay` output reuse and service prewarm for Raspberry Pi HDMI deployments
- printed `[TTS] ...` acknowledgement
- graceful failure handling
- terminal debug row for TTS backend/voice/phase/queue timing

Current limitations:
- cloud TTS provider is not implemented yet
- direct user barge-in during TTS is still intentionally out of scope

### UI

Status: working mock plus real browser-backed face prototype

Available now:
- state rendering for lifecycle and emotion
- preview text updates
- response text display
- `UiService` lifecycle hooks for startup and shutdown
- content-mode API surface for future `camera` and `image` scenes
- browser-backed robot face renderer launched in Chromium kiosk mode
- smooth state transitions between idle, listening, thinking, responding, speaking, and sleep
- idle micro-animation including blinks, glances, breathing drift, and playful expression variants
- sleeping-eyes grace window before optional display blank/off hooks
- browser-bridge tuning for idle behavior, state snapshots, and face override payloads

Current limitations:
- camera and image scenes are placeholders only
- there is still no real vision-driven content takeover yet

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
- wake-free follow-up turns after spoken replies
- VAD-confirmed speech gating for follow-up turns so TV/music noise does not become a real submitted turn
- configurable max follow-up-turn cap to prevent endless wake-free loops
- sticky terminal debug rows for mic state, VAD tail state, ring-buffer state, wake status, and transcript preview
- sticky terminal AI row for backend state, route/reply durations, compact plan preview, and compact reply preview
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
- optional Piper dependency install plus English/German/Indonesian voice provisioning
- generated `.env.local` runtime configuration
- generated wake-free follow-up speech settings in `.env.local`
- optional interactive OpenAI enablement plus API-key prompt with blank-later support

Current limitations:
- only macOS and Raspberry Pi are first-class setup targets
- no hardware or vision setup yet

---

## Mocked vs Real

### Real implementation pieces

- shared typed contracts
- orchestrator control flow
- turn routing and validation logic
- state transitions
- event tracking and in-process pub/sub
- failure handling patterns
- local speech synthesis/playback with Piper
- playback-accurate TTS lifecycle for UI sync
- browser-backed face rendering in Chromium kiosk mode
- integration tests for the main interaction loop

### Mocked subsystems

- memory persistence
- vision
- hardware control

### Not built yet

- cloud TTS provider integration
- real camera/vision pipeline
- real hardware drivers
- production logging/config loading

---

## End-to-End Flows Available

### Working flow now

Manual text input
-> orchestrator
-> local reactive cue
-> local turn routing
-> local action/query and optional cloud reply
-> mock UI update
-> queued TTS synthesis/playback
-> memory persistence

See `README.md` for current setup and run commands.

### Partially supported flow

Mock STT partial transcript
-> orchestrator listening state update
-> no execution until final transcript

### New experimental flow

Push-to-talk or wake-word speech input
-> external recorder streams PCM
-> `whisper.cpp` produces the final transcript
-> orchestrator routing and execution flow
-> local TTS reply
-> optional no-wake follow-up turn if VAD confirms new speech

### Not yet available

Real camera frame capture
-> vision snapshot/detection pipeline
-> cloud/tool-aware responses from real images

---

## Test Coverage

Currently covered:
- app bootstrapping
- full manual turn execution
- local turn-director behavior
- plan validation behavior
- hybrid local/cloud turn execution
- tool-aware cloud reply round-trip
- partial transcript handling
- memory and vision-backed local query flow
- cloud fallback behavior
- reactive behavior timing
- TTS failure recovery
- vision failure recovery

Current test status:
- `83 passed`

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

## Recent Notes

- The normal turn path is now local-first: deterministic embodiment plus narrow local shortcuts, then one cloud reply call when needed.
- The cloud reply service can request `camera_snapshot` and continue the same response turn with local tool output.
- Speech endpoint tuning is now grouped under `AI_COMPANION_SPEECH_LATENCY_PROFILE`, and spoken cloud replies have a configurable hard length cap.
- Wake-free follow-up speech now stays guarded by VAD-confirmed speech start, and short-term OpenAI thread continuity survives brief pauses between wake-word turns.

---

## Iteration Checklist

Use this checklist during each implementation pass.

- [x] Shared typed contracts exist
- [x] Orchestrator can execute a full mocked turn
- [x] Local-first turn routing exists
- [x] Capability registry and validation exist
- [x] Memory mock is integrated
- [x] Vision mock is integrated
- [x] Hardware mock is integrated
- [x] Cloud AI mock is integrated
- [x] TTS mock is integrated
- [x] Partial transcript handling exists
- [x] STT mock is wired into runtime loop
- [x] Real cloud AI integration exists
- [x] Real TTS integration exists
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
