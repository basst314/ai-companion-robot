# Agent Notes

The active runtime is realtime-first.

## Package Map

- `ai/`: OpenAI cloud and realtime clients
- `audio/`: microphone capture, channel extraction, shared live buffer, wake detection
- `orchestrator/`: runtime coordination, lifecycle state, event handling, realtime tool execution
- `ui/`: mock UI, browser face service, browser protocol, face animation controller
- `hardware/`: local hardware action surface
- `memory/`: active-user and recent-interaction surface
- `vision/`: local camera/vision surface
- `shared/`: config, events, models, console helpers, process helpers
- `scripts/`: Pi sync, setup, wake-word live testing, start helper
- `docs/`: architecture, setup, Pi bring-up, visual playgrounds, release notes

## Runtime Flow

```text
wake detection
-> shared live audio
-> realtime session
-> validated local tools
-> streamed playback
-> UI/event updates
```

## Configuration

- `.env.example` is tracked and generic.
- `.env.local`, `.env.local.rpi`, and `.env.local.mac` are ignored.
- `.env.local.rpi` is the development-machine source for Pi secrets/settings.
- `scripts/sync-to-pi.sh` copies `.env.local.rpi` to the Pi as `.env.local` when present.

## Development Checks

```bash
pytest
pytest tests/test_realtime.py tests/test_config.py tests/test_respeaker_capture.py
python -m py_compile src/main.py src/ai/realtime.py src/audio/capture.py src/audio/wake.py
```

## Documentation Rules

- Keep README and setup docs focused on current behavior.
- Put historical/evolutionary notes in `docs/release-notes.md`.
- Keep hostnames, usernames, and secrets out of tracked docs and examples.
