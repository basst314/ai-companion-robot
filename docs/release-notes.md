# Project Evolution & Release Notes

This file captures major project evolution over time based on commit history.

- **Ordering:** newest entries first.
- **Scope:** summarize meaningful product/architecture shifts, not every tiny edit.
- **Decision context:** when available, capture the "why" behind key changes (tradeoffs, constraints, alternatives) from local Codex conversation context.
- **Versioning:** this project is currently commit-driven (no formal release tags yet).

---

## 2026-04-26 — OpenAI Realtime Speech Backend

### Highlights
- Added an OpenAI Realtime speech-to-speech interaction backend alongside the original turn-based orchestrator path.
- Kept wake-word detection, tool validation, hardware execution, UI events, and audio playback local on the Pi while streaming active speech and assistant audio through a Realtime WebSocket session.
- Added Realtime session configuration for semantic VAD, turn eagerness, voice selection, streamed PCM sample rate, and optional local energy barge-in.
- Implemented multi-turn Realtime conversation continuity so wake-free follow-up turns stay awake on the same session instead of falling back to wake-word listening between normal turns.
- Implemented barge-in handling that stops local playback, cancels only active server responses, and truncates unheard assistant audio with `conversation.item.truncate` so server-side context matches what the user actually heard.
- Added the `[RT]` interactive console row and structured Realtime logs for phase, streamed byte/chunk counters, response counts, interrupt counts, voice, and last event.
- Hardened ALSA Realtime playback so interrupt/drop write races recover without killing the playback worker.

### Why this matters
This gives the robot a much more natural voice loop: faster first audio, solid wake-free multi-turn conversation, and speech-over-speech interruption without discarding the Realtime session's short-term context. The Pi remains the authority for local capabilities and safety-sensitive hardware actions.

## 2026-04-25 — Pi Turn-Latency Optimization

### Highlights
- Added a hot `whisper-server` transcription path on the Pi so the model stays loaded across turns instead of spawning `whisper-cli` for every utterance.
- Moved ReSpeaker channel selection into the app-side capture pipeline and kept the Pi recorder command as a direct six-channel stream.
- Replaced the cloud request path with a persistent HTTP client and switched the Pi default response model to `gpt-5.4-mini` for lower latency.
- Added per-turn latency spans for wake, STT, cloud, and TTS stages, and quieted Chromium browser process logs so production output stays focused on the robot.

### Why this matters
The current Pi runtime now spends less time on avoidable process startup and transport overhead, while keeping the quality-sensitive VAD and speech behavior intact. The result should be a noticeably faster turn loop without sacrificing the existing speech-detection thresholds or the local-first architecture.

## 2026-04-19 — Pi Voice Pipeline Cleanup

### Highlights
- Tuned the Raspberry Pi speech path for faster final STT, with `base.en` as the practical default and reduced partial-transcript overhead.
- Added explicit ReSpeaker channel selection so the robot captures the board's processed channel 0 instead of relying on ALSA's mono conversion.
- Restored the Pi playback path to the persistent HDMI ALSA backend on `vc4hdmi1` and updated the startup/docs to match the current hardware defaults.
- Improved terminal trace logging for wake word, VAD, Whisper, cloud reply, and TTS timing so future audio debugging is much easier.

### Why this matters
The robot is now much closer to a usable voice-first setup on the Pi: speech turns finalize faster, the mic input path is cleaner, and the shipped defaults line up with the hardware.

## 2026-04-13 — Browser-Backed Face Renderer

### Highlights
- Introduced the browser-backed face renderer in Chromium kiosk mode on Raspberry Pi, with a matching windowed Chromium mode for desktop development.
- Replaced the older `pygame` and framebuffer display path with the new browser face bridge, so the robot face now renders through a single browser runtime.
- Updated setup, env defaults, docs, and tests so new checkouts point at the browser UI flow immediately.

### Why this matters
This gives the project its first browser-backed face experience while keeping the runtime simpler to reason about. It reduces setup confusion, makes the docs match the shipped path, and leaves future face work focused on one browser renderer instead of multiple parallel implementations.

## 2026-04-05 — Minimal Neon Face Baseline

### Highlights
- Simplified the face system down to a single experimental `neon_bot` theme so visual iteration could focus on one strong direction instead of preserving multiple older variants.
- Shifted the face style toward an ultra-minimal digital robot look: pitch-black background, solid neon-cyan circular eyes, simple geometric mouths, and a soft glow treatment.
- Removed the remaining product/docs language that implied several actively supported face themes when the current intent is to iterate on one baseline.

### Why this matters
This locks in a clean visual starting point for the robot face. Instead of spreading effort across multiple personalities and older eye styles, the project now has one deliberate baseline that is easy to test on the Pi and easy to refine in later passes.

### Key decisions & rationale
- Decision: keep only one experimental face theme for now.
  - Why: it keeps visual iteration focused and makes it much easier to judge whether a change actually improves the robot's look on the real hardware.
- Decision: favor simple glowing geometry over anatomical eye details.
  - Why: the robot reads better as a stylized digital character, and the minimal shapes are cheaper to tune and animate consistently on the small display.

## 2026-04-04 — ALSA-Native Raspberry Pi HDMI Audio Output

### Highlights
- Replaced the Pi-specific persistent `aplay` timing loop with a dedicated ALSA-backed playback worker thread that owns the HDMI device continuously.
- Added an explicit TTS audio backend selector plus Pi ALSA settings for device, sample rate, period frames, buffer frames, and keepalive interval.
- Tightened playback lifecycle semantics so `TTS_PLAYBACK_STARTED` is emitted only after playback actually starts, which keeps robot face timing aligned with audible speech.
- Added focused tests for the ALSA worker, backend selection, config parsing, and delayed-start playback-event timing.
- Removed the ALSA playback path's dependency on `audioop`, making the new backend compatible with Raspberry Pi OS Trixie / Python 3.13.

### Why this matters
The earlier Pi HDMI fixes solved clipping but forced a tradeoff between startup delay and runtime stability. The ALSA-native backend removes that tradeoff on the tested Pi path: speech now starts promptly without clipped opening words, and the periodic pops/dropouts are gone.

### Key decisions & rationale
- Decision: use one dedicated audio-owner thread for the Pi HDMI output instead of pacing PCM writes from the app's main asyncio loop.
  - Why: audio timing on the Pi must stay isolated from STT, UI, logging, and orchestration jitter.
- Decision: keep the command playback backend for macOS/dev and make the ALSA worker the Pi-specific production path.
  - Why: the Pi needed a lower-level backend for robust HDMI behavior, while the simpler command path remains a good fit elsewhere.
- Decision: keep the worker as the single owner of the audio device and separate idle keepalive from speech playback inside that owner.
  - Why: it avoids multiple writers fighting over the same HDMI/ALSA sink and eliminates the silence backlog that caused startup delay.

## 2026-04-04 — Robot Face UI And Pi Framebuffer Backend

### Highlights
- Added real face-rendering backends for both windowed/fullscreen `pygame-ce` and Raspberry Pi `fb0`, with procedural robot eyes, smooth interpolation between states, playful idle micro-animations, sleeping-eyes behavior, and placeholder scene plumbing for future camera/image takeovers.
- Added a face/theme layer so palette, eye geometry, blink timing, idle motion, transition durations, and named expression presets can be tuned without rewriting the renderer.
- Added the `neon_bot` face theme as the then-current experimental minimal cyan-blue robot look.
- Extended `UiService` with `start()`, `shutdown()`, `show_content(...)`, and `clear_content()`, and added new UI runtime config for backend selection, frame rates, sleep timing, display sleep/wake hooks, and theme selection.
- Updated the orchestrator so visual `speaking` begins on playback start events instead of when speech is merely queued or synthesized.
- Hardened OpenAI structured-reply parsing so truncated structured outputs surface a clear runtime error instead of a raw JSON decode failure.

### Why this matters
This is the first robot-face milestone that can actually live reliably on the Raspberry Pi display hardware used for the robot. The robot now has a real face surface, an experimental visual baseline, and playback timing that is good enough for visible speech animation while still respecting the messy reality of Pi HDMI audio and framebuffer output.

### Key decisions & rationale
- Decision: keep the face procedural instead of sprite-based.
  - Why: this keeps the look easy to customize, helps transitions stay fluid, and makes it cheap to add new expressions and personalities later.
- Decision: drive visible speaking only from playback lifecycle events.
  - Why: reply generation and synthesis completion are not the same as audible speech, and the face needs the latter to feel convincing.
- Decision: use sleeping eyes first, then optional display blank/off hooks for real power saving.
  - Why: it preserves character and readability while still allowing actual screen-power reduction after a grace window.

## 2026-04-04 — Raspberry Pi 5 Bring-Up And Setup Compatibility

### Highlights
- Added a focused Raspberry Pi 5 bring-up guide covering SD card imaging, headless SSH, repo sync, Pi bootstrap, and staged validation.
- Added `scripts/sync-to-pi.sh` to copy a clean repo checkout to a Pi without dragging over local machine state.
- Added `scripts/validate-rpi-runtime.sh` to verify audio devices, run tests, and smoke-test app startup on the Pi.
- Hardened `scripts/setup.sh` for current Raspberry Pi OS Trixie images by adding a Python 3.13-compatible `openwakeword` install path and by always provisioning shared OpenWakeWord runtime assets needed by endpoint VAD.

### Why this matters
This makes fresh Pi bring-up much more predictable. A brand-new Raspberry Pi 5 can now be imaged, bootstrapped, validated, and used for typed or voice-adjacent testing with far less manual setup friction, even on newer Raspberry Pi OS images where the Python/runtime stack differs from older assumptions.

## 2026-03-28 — Multi-Turn Voice Conversations

### Highlights
- Added wake-free multi-turn voice conversations, so after the initial wake word the robot can keep listening and responding for several turns without requiring the wake phrase again each time.
- Tightened wake-free follow-up listening so the second turn only proceeds when VAD confirms real speech, which prevents common false positives such as TV audio, music, or placeholder Whisper text like `[BLANK AUDIO]`.
- Added `AI_COMPANION_FOLLOW_UP_LISTEN_TIMEOUT_SECONDS` to control how long the robot waits for confirmed speech when it opens a wake-free follow-up listen.
- Added `AI_COMPANION_FOLLOW_UP_MAX_TURNS` as a safety cap so wake-free conversations eventually return to ordinary wake-word listening instead of looping indefinitely. The default is `10`.
- Added short-term OpenAI conversation continuity so immediate follow-ups, and fresh wake-word turns shortly afterwards, can continue the same thread.
- Cloud replies now carry explicit reply-language metadata for TTS, so the robot can speak in the requested language and then naturally return to the current turn language afterwards.
- Updated the terminal debug view to show clearer VAD state.

### Why this matters
This makes the robot feel like a real conversational partner instead of a single-turn voice command system. After the initial wake word, you can continue naturally for several turns, pause briefly and resume the same thread, and rely on the robot to fall back to wake-word mode instead of getting stuck in an endless loop or reacting to background audio.

### Key decisions & rationale
- Decision: require VAD-confirmed speech for wake-free follow-up turns instead of trusting non-empty Whisper output alone.
  - Why: speech-like background audio can still produce text such as `[BLANK AUDIO]` or `(MUSIC)`, so the safer gate is real speech detection before the turn is allowed to continue.
- Decision: cap wake-free follow-up chains with `AI_COMPANION_FOLLOW_UP_MAX_TURNS`.
  - Why: it prevents runaway back-and-forth if a TV, music, or another false positive keeps slipping through.
- Decision: use `previous_response_id` plus a short resume window instead of the Conversations API or a new memory layer.
  - Why: it preserves context across nearby turns with minimal code and without committing yet to a durable long-term memory architecture.
- Decision: have the model return structured reply language metadata rather than relying on local language-guess heuristics.
  - Why: TTS voice selection becomes cleaner, more multilingual, and less brittle than trying to infer the intended reply language from transcript wording.

## 2026-03-28 — Local Piper TTS, Queueing, And Setup Provisioning

### Highlights
- Added a real local TTS path based on Piper HTTP, while keeping the app-side TTS interface provider-pluggable for future cloud or alternative backends.
- Introduced queued speech requests with append, replace-pending, and interrupt-and-replace behavior, plus explicit synthesis/playback lifecycle events for robot timing.
- Extended the terminal debug view with dedicated TTS state, voice, style, queue, and timing visibility.
- Expanded `scripts/setup.sh` and generated config so local TTS can be provisioned end to end: optional dependency install, voice downloads, playback command setup, and managed/external Piper modes.
- Added explicit reply language propagation through the AI response contract so TTS can reliably select English, German, or Indonesian voices.

### Why this matters
This is the first real end-to-end spoken reply milestone. The robot can now hear a question, generate a response, and speak it aloud locally with observable queue and playback state, which closes the loop needed for later animation, embodiment timing, and Raspberry Pi deployment.

### Key decisions & rationale
- Decision: keep Piper behind an HTTP provider boundary instead of coupling the app directly to an in-process synthesis library.
  - Why: it keeps the runtime pluggable, makes local dev and Raspberry Pi deployment use the same app contract, and leaves room for cloud TTS later without rewriting orchestrator logic.
- Decision: start Piper lazily on first speech in managed mode.
  - Why: it keeps startup lighter and avoids paying the process cost when a run never reaches a spoken reply.
- Decision: keep user barge-in and AEC out of the first delivery, while still supporting app-driven interrupt-and-replace speech.
  - Why: reliable local playback and event timing were the critical path for this milestone; full duplex audio handling is a separate, riskier layer.


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
  - Why: OpenWakeWord needs a matching model; a known built-in pairing is the safest way to guarantee first-run success before adding a custom wake-word model later.

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
