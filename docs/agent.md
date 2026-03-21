# Agent Guidelines

## 1. Purpose

This document defines implementation rules and coding guidelines for AI coding agents working on this repository.

It exists to keep the codebase:
- modular
- maintainable
- responsive
- aligned with the robot architecture

All generated code should follow these guidelines unless explicitly overridden.

---

## 2. General Principles

- Prefer clarity over cleverness
- Keep modules small and focused
- Avoid tightly coupled components
- Write code that is easy to debug on Raspberry Pi
- Favor incremental implementation over premature abstraction
- Optimize for real-time interaction, not theoretical completeness

---

## 3. Architecture Rules

- The Raspberry Pi is the local runtime and system orchestrator
- The orchestrator runs locally and is the source of truth for interaction flow
- Cloud services are used only when needed
- Local modules should continue to function when cloud services are unavailable
- Hardware control must remain local
- Real-time loops must not depend on cloud availability

---

## 4. Module Boundaries

Code should be organized into clearly separated modules.

Expected areas include:

- `audio/`
- `vision/`
- `tts/`
- `stt/`
- `orchestrator/`
- `ui/`
- `memory/`
- `hardware/`
- `shared/`

Guidelines:
- each module should have a narrow responsibility
- avoid circular dependencies
- shared types and utilities belong in `shared/`

---

## 5. Orchestrator Rules

The orchestrator is the central coordinator.

It should:
- receive events from modules
- maintain system state
- decide what actions to trigger
- call local or cloud AI as needed

It should not:
- contain low-level hardware logic
- contain UI rendering details
- contain direct camera or microphone implementation details

---

## 6. Event-Driven Design

Prefer event-driven coordination.

Examples of events:
- `speech_detected`
- `transcript_ready`
- `face_detected`
- `response_ready`
- `tts_started`
- `tts_finished`

Guidelines:
- events should be explicit and well named
- avoid hidden side effects
- event payloads should use typed models where practical

---

## 7. Async and Performance Rules

- Prefer asynchronous code for I/O-bound operations
- Avoid blocking the main interaction loop
- Keep latency low for audio and UI updates
- Long-running work should be delegated to background tasks
- Do not block on cloud calls if local progress can continue

Examples of work that should not block unnecessarily:
- cloud LLM calls
- network requests
- file I/O
- model loading

---

## 8. Hardware Rules

- Hardware-facing code must be isolated from business logic
- Prepare for future servo and ESP32 integration
- Do not hardcode hardware assumptions in unrelated modules
- Use configuration for pins, ports, device names, and model paths
- Fail gracefully if hardware is missing during development

---

## 9. Local vs Cloud Rules

Default assumption:
- local first for low-latency and hardware-adjacent behavior
- cloud for heavy reasoning and higher-quality responses

Use local for:
- orchestration
- UI state
- memory access
- hardware control
- basic STT/TTS
- vision preprocessing

Use cloud for:
- main conversational reasoning
- richer personality generation
- optional advanced STT/TTS

Cloud calls must be wrapped in interfaces so providers can be changed later.

---

## 10. Error Handling

- Fail gracefully
- Prefer partial functionality over total failure
- Log meaningful errors
- Do not swallow exceptions silently
- Return structured error states where useful

Examples:
- if cloud AI fails, provide a fallback response
- if camera fails, keep voice interaction running
- if TTS fails, keep state consistent and continue

---

## 11. Logging

All major modules should log important events.

Include:
- startup and shutdown
- hardware detection
- cloud call failures
- state transitions
- interaction lifecycle milestones

Do not:
- spam logs with noisy frame-level detail by default
- log sensitive user content unnecessarily

Prefer structured, readable logs.

---

## 12. Configuration

Do not hardcode environment-specific values.

Use configuration for:
- API keys
- model names
- language defaults
- file paths
- hardware device names
- feature flags

Use environment variables or config files where appropriate.

---

## 13. Code Style

- Use Python type hints where practical
- Prefer dataclasses or typed models for structured data
- Keep functions short and focused
- Prefer explicit naming over abbreviations
- Add comments only where they improve understanding
- Avoid unnecessary frameworks

---

## 14. Testing Strategy

Prefer small, testable units.

Focus testing on:
- event routing
- state transitions
- module interfaces
- error handling
- local/cloud fallback behavior

Where hardware is involved:
- separate pure logic from hardware adapters
- make hardware-dependent pieces mockable

---

## 15. Implementation Priority

When building features, prioritize in this order:

1. working local prototype
2. stable interfaces
3. graceful fallback behavior
4. improved intelligence
5. polish and optimization

Do not over-engineer early versions.

---

## 16. Behavior and Personality Rules

The robot should feel:
- conversational
- expressive
- slightly funny
- capable of playful and cheesy responses

However:
- personality must not interfere with core functionality
- humor should be additive, not blocking
- reactions should be context-aware
- sound effects and expressions should be controlled by structured state, not random scattered logic

---

## 17. What to Avoid

- giant monolithic files
- hardcoded provider-specific logic everywhere
- blocking calls inside the main loop
- direct coupling between UI and hardware layers
- premature microservices
- unnecessary complexity
- hidden global state

---

## 18. Preferred Development Approach

Build incrementally.

Good sequence:
- get one end-to-end interaction working
- stabilize module interfaces
- then expand behavior and polish

Prefer working vertical slices over unfinished infrastructure.

---