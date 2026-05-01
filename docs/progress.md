# Progress

OpenAI Realtime is the maintained speech path.

## Active Runtime

- Realtime audio input/output through `ai.realtime`
- Wake-word gating through `audio.wake`
- Shared microphone capture through `audio.capture`
- ReSpeaker/interleaved-channel extraction
- Local tool validation for UI, hardware, memory, and vision
- Browser-backed robot face UI
- Pi env sync through `.env.local.rpi`

## Working Areas

- Realtime conversation continuity and interruption behavior
- ALSA playback robustness on Raspberry Pi
- Browser face launch and kiosk behavior
- Wake model and threshold tuning
- Camera snapshot tool integration
- Local memory surface

## Useful Validation

```bash
pytest tests/test_realtime.py tests/test_config.py tests/test_respeaker_capture.py
python -m py_compile src/main.py src/ai/realtime.py src/audio/capture.py src/audio/wake.py
```

On the Pi:

```bash
.venv/bin/python scripts/test-wakeword-live.py --max-seconds 15
./scripts/start-robot.sh
```

## Notes

- Keep deployable Pi secrets in `.env.local.rpi`.
- Keep tracked docs and examples generic.
- Use `docs/robot-face-playground.html` for face tuning.
- Use `docs/wakeword-model-visualizer.html` for wake model inspection.
