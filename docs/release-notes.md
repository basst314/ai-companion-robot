# Project Evolution & Release Notes

This file captures major project evolution over time based on commit history.

- **Ordering:** newest entries first.
- **Scope:** summarize meaningful product/architecture shifts, not every tiny edit.
- **Decision context:** when available, capture the "why" behind key changes (tradeoffs, constraints, alternatives) from local Codex conversation context.
- **Versioning:** this project is currently commit-driven (no formal release tags yet).

---

## 2026-03-24 — Local-First Reply Pipeline, Tool Calls, And Cleanup

### Highlights
- Removed the cloud planner from the hot path and cleaned out stale compatibility code and exports around the old two-call planner/reply design.
- Standardized deterministic local embodiment cues so listening, processing, speaking, and idle transitions stay on-robot instead of being planner decisions.
- Landed a single OpenAI response-model path with optional local tool continuation for `camera_snapshot`, plus a configurable spoken reply length cap.
- Added the speech latency profile and refreshed architecture/status docs to match the shipped local-first runtime.

### Why this matters
This is the wrap-up pass that makes the robot feel faster and the codebase easier to reason about. The normal turn path is now simpler, lower-latency, and better aligned with what the runtime actually does.

### Key decisions & rationale
- Decision: keep tool use inside the cloud reply loop rather than reintroducing a separate planning call.
  - Why: it preserves flexibility for image-needed turns without paying extra latency on normal conversation.
- Decision: keep embodiment and attention choreography deterministic and local.
  - Why: looking at the user, showing listening/speaking states, and returning to idle should not depend on cloud output quality or speed.

---

## 2026-03-24 — Planner Payload Trim And Prompt Cache Readiness

### Highlights
- Reduced the OpenAI planner structured output to the minimum fields needed for execution: `route_kind` plus ordered steps.
- Reordered the planner prompt so stable capability definitions come first, with dynamic context and the user transcript at the end for better prompt-cache routing.
- Hardened planner parsing so invalid extra arguments on no-arg capabilities such as `cloud_reply`, and mismatched route kinds, are normalized before validation/execution.

### Why this matters
This keeps planner latency lower without changing the overall two-call architecture, and makes the OpenAI planner path more resilient when a fast model returns slightly inconsistent structured output.

### Key decisions & rationale
- Decision: keep strict structured output while trimming planner metadata.
  - Why: reliability matters more than preserving optional planner fields like rationale or model-generated confidence.
- Decision: prefer `gpt-4o-mini` as the current planner default and keep a richer reply model separately.
  - Why: the planner task is short, schema-bound, and closer to classification/tool selection than open-ended response generation.

---

## 2026-03-23 — OpenAI Integration Polish And Debug Visibility

### Highlights
- Hardened the OpenAI planner schema for strict structured-output validation and improved runtime observability with raw AI request/response logging.
- Expanded the sticky terminal debug header with a dedicated AI backend row that shows planner/reply activity, durations, plan summaries, and reply previews.
- Polished setup and docs so the bootstrap flow can enable the OpenAI backend interactively, prompt for the API key, and still allow the key to be filled in later.

### Why this matters
This is the submission-polish pass for the hybrid orchestrator milestone: the real OpenAI integration is easier to configure, easier to debug, and better documented for both local development and Raspberry Pi bring-up.

### Key decisions & rationale
- Decision: keep the OpenAI API key prompt optional even when the real backend is enabled.
  - Why: it supports machine bootstrap and shared setup sessions where credentials are added later without blocking the rest of the environment.
- Decision: surface AI planner/reply timing directly in the sticky header.
  - Why: planner latency is now a first-order UX concern, and it needs to be visible without digging through logs.

## 2026-03-22 — Hybrid Turn Planner And Capability Execution

### Highlights
- Replaced the old single-route orchestrator flow with a multi-step `TurnPlan` pipeline built around typed capabilities, step validation, and step execution.
- Added a local capability registry for actions, queries, and cloud reply generation, plus a reactive policy layer for quick nonverbal behavior during active turns.
- Split cloud AI into planning and response services, with mock implementations for tests and an OpenAI Responses API-backed provider path for real deployments.
- Extended interaction persistence and telemetry so the runtime now records plan summaries, executed steps, and step-level events.

### Why this matters
This is the first architecture that matches the robot's intended behavior model: the system can now treat one utterance as a sequence of actions rather than a single route, while keeping validation, hardware authority, and TTS local on the robot.

### Key decisions & rationale
- Decision: keep a local shortcut layer and validator in front of cloud execution.
  - Why: the robot should stay responsive and safe even when cloud planning is slow or unavailable.
- Decision: keep cloud output text-only.
  - Why: speech output needs to remain local for Raspberry Pi deployment and later Piper integration.
- Decision: use an internal capability registry instead of MCP for v1.
  - Why: it delivers the tool-selection benefits needed for the robot without introducing another protocol before the local architecture is stable.

---

## 2026-03-22 — Dedicated VAD Endpointing For STT

### Highlights
- Replaced the brittle energy-based trailing-silence endpointing in the `whisper.cpp` speech path with bundled Silero VAD endpoint detection from `openwakeword`.
- Added VAD tuning controls to runtime config and generated `.env.local` setup: threshold, frame size, and start/end trigger smoothing.
- Updated the interactive terminal debug screen so the mic row now shows VAD tail state instead of the old silence/speech badges.
- Refreshed setup and speech docs to describe VAD-confirmed trailing non-speech semantics and the new default endpoint timing.

### Why this matters
This improves end-of-utterance detection in noisy home environments without introducing another speech dependency or disturbing the existing OpenWakeWord wake-word flow. The result is faster and more reliable turn finalization when appliances, TVs, or other steady background noise are present.

### Key decisions & rationale
- Decision: reuse the Silero VAD already bundled with `openwakeword`.
  - Why: it keeps the speech stack smaller, matches the existing runtime setup path, and avoids adding another real-time audio dependency.
- Decision: limit this change to endpointing and keep wake detection unchanged.
  - Why: OpenWakeWord wake detection was already working well on the shared live stream, so the safest improvement was to swap only the end-of-utterance logic.
- Decision: limit this change to end-of-utterance detection and leave speech-start/no-speech gating unchanged.
  - Why: that minimizes regression risk while still fixing the brittle part of the current speech UX.

---

## 2026-03-22 — OpenWakeWord Wake Detection Migration

### Highlights
- Replaced transcript-based wake-word detection with an `OpenWakeWord`-backed detector on the shared live microphone stream.
- Preserved the existing ring-buffer handoff into `whisper.cpp` STT so the active turn still includes buffered lookback audio plus the wake phrase and following speech.
- Added model-backed wake-word configuration, with a built-in starter pairing of `Hey Jarvis` and the matching OpenWakeWord model.
- Hardened `scripts/setup.sh` so a fresh machine rebuilds incompatible virtual environments, provisions the OpenWakeWord runtime assets, validates the selected wake model, and writes a working `.env.local`.

### Why this matters
This is the architectural shift from a brittle transcript-gated wake flow to a dedicated wake-word detector. It improves resilience, reduces dependence on transcription timing for activation, and makes fresh-machine setup much more predictable for both macOS development and Raspberry Pi deployment.

### Key decisions & rationale
- Decision: keep `whisper.cpp` for STT and replace only wake detection.
  - Why: this preserves the existing speech-turn behavior and minimizes regression risk while improving the weakest part of the pipeline.
- Decision: keep the shared mic stream and ring buffer.
  - Why: this preserves low-latency handoff and avoids losing pre-roll audio around the wake event.
- Decision: ship a built-in `Hey Jarvis` starter path first.
  - Why: OpenWakeWord needs a matching model; a known built-in pairing is the safest way to guarantee first-run success before adding a custom `Oreo` model later.

---

## 2026-03-22 — STT Reliability Hardening

### Highlights
- Improved deterministic cleanup of STT recording artifacts to reduce noisy leftover files and make repeated runs more predictable.
- Landed a follow-up merge from the bug-review branch, continuing stability improvements around recently introduced speech features.

### Notable commits
- `97fe470` — Fix deterministic STT recording artifact pruning
- `4a7f693` — Merge pull request #3 from `codex/review-codebase-and-report-bugs`

---

## 2026-03-21 — Speech UX Expansion (STT, Debug UI, Wake Word)

### Highlights
- **STT capabilities expanded** from basic support to richer local speech workflows:
  - exported and integrated new STT services
  - handled silent turns more gracefully
  - improved typed speech fallback paths
  - introduced streaming STT behavior and polished terminal output
- **Interactive terminal debug screen introduced** for easier runtime visibility and troubleshooting.
- **Wake-word-gated shared-stream speech input introduced** (including Whisper-based flow), enabling a more natural hands-free interaction pattern while preserving buffered audio context.
- Setup and README docs were refreshed to support the new speech/wake workflow.

### Why this matters
This date marks the biggest shift from a scaffolded conversational loop toward a practical voice-first prototype: better observability (debug UI), better usability (wake word + shared stream), and better robustness (silence handling + deterministic STT behavior).

### Notable commits
- `a17cee5` — feat: export new STT services and improve setup docs
- `da23c37` — fix: handle silent stt turns and typed speech input
- `e09792d` — Implement streaming STT and console polish
- `f555598` — Add interactive terminal debug screen
- `02b31fb` — Add wake-word gated shared-stream speech input
- `b1a73ba` — Refresh README for wake-word speech flow

---

## 2026-03-20 — Project Foundation & Core Scaffolding

### Highlights
- Initial repository bootstrapping and architecture documentation established.
- Core project scaffold and orchestrator structure introduced.
- Mock TTS and mock UI services added with pluggable interfaces, enabling end-to-end flow without hardware dependencies.
- Early setup/readability improvements and interactive input handling refinements landed.

### Why this matters
This is the foundational phase where the architecture and integration seams were created, making it possible to iterate quickly on STT, UI, and orchestration in later commits.

### Notable commits
- `9f37a64` — Initial commit
- `8910e60` — adding architecture and agent docs
- `2bf05be` — add readme
- `6659d6a` — feat: initialize project scaffold with packaging and orchestrator
- `db6b101` — feat: add mock TTS and UI services with pluggable interfaces
- `8aaf2e5` — docs: improve README setup readability
- `526a026` — Handle interactive input EOF gracefully
- `aa45776` — Merge pull request #2 from `codex/locate-potential-bugs-and-recommend-fixes`
- `2fb7a1e` — Merge pull request #1 from `codex/update-readme-for-readability`

---

## Update Template (for future entries)

When adding a new entry, use this structure and keep it at the top:

```md
## YYYY-MM-DD — Short Milestone Name

### Highlights
- ...

### Why this matters
- ...

### Key decisions & rationale (optional but encouraged)
- Decision: ...
  - Why: ...
  - Alternatives considered: ...
  - Source/context: local Codex thread, issue notes, or PR discussion

### Notable commits
- `<hash>` — <subject>
- `<hash>` — <subject>
```
