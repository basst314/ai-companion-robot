# Project Evolution & Release Notes

This file captures major project evolution over time based on commit history.

- **Ordering:** newest entries first.
- **Scope:** summarize meaningful product/architecture shifts, not every tiny edit.
- **Decision context:** when available, capture the "why" behind key changes (tradeoffs, constraints, alternatives) from local Codex conversation context.
- **Versioning:** this project is currently commit-driven (no formal release tags yet).

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
