# Raspberry Pi 5 Bring-Up

This guide turns a Raspberry Pi 5 into a runtime target for the AI Companion Robot.

It is optimized for:

- fresh SD card image
- headless SSH access
- repo synced from a development machine
- `.env.local.rpi` managed locally and deployed as `.env.local`
- audio and realtime runtime validation

The display and camera can be brought up after the core audio/realtime loop is stable.

## What You Need

- Raspberry Pi 5
- microSD card
- power supply
- Ethernet or Wi-Fi details
- development machine with this repo
- microphone that can provide raw PCM
- speaker or HDMI/ALSA audio output
- optional custom wake-word model file

## 1. Reimage The SD Card

Use Raspberry Pi Imager and select:

- Device: `Raspberry Pi 5`
- Operating System: Raspberry Pi OS Lite or Desktop, 64-bit
- Storage: your SD card

In advanced settings, configure:

- hostname
- username/password
- Wi-Fi if needed
- locale/timezone/keyboard
- SSH enabled

Write the image, eject the card cleanly, insert it into the Pi, and boot.

## 2. First Boot

Connect over SSH:

```bash
ssh <user>@<hostname>.local
```

If `.local` discovery does not work, check your router/DHCP lease list for the Pi IP.

Update the Pi:

```bash
sudo apt update
sudo apt full-upgrade -y
sudo reboot
```

Reconnect after reboot.

## 3. Sync The Repo

From the development machine:

```bash
./scripts/sync-to-pi.sh --host <hostname> --user <user>
```

If `.env.local.rpi` exists locally, the sync script copies it to the Pi as `~/ai-companion-robot/.env.local`.

To copy a wake model at the same time:

```bash
./scripts/sync-to-pi.sh \
  --host <hostname> \
  --user <user> \
  --copy-wake-model /absolute/path/to/custom_wake_model.onnx
```

The sync helper skips git metadata, virtualenvs, logs, artifacts, and local machine env files.

## 4. Install Runtime Dependencies

SSH into the Pi:

```bash
cd ~/ai-companion-robot
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
```

Install system packages needed for audio/browser operation as appropriate for the Pi image:

```bash
sudo apt install -y alsa-utils chromium-browser
```

Package names may differ slightly across Raspberry Pi OS releases.

## 5. Configure Pi Runtime

Keep the canonical Pi env file on the development machine at `.env.local.rpi`. It should include:

```bash
AI_COMPANION_INTERACTION_BACKEND=openai_realtime
AI_COMPANION_INPUT_MODE=speech
AI_COMPANION_CLOUD_ENABLED=true
AI_COMPANION_USE_MOCK_AI=false
AI_COMPANION_OPENAI_API_KEY=...
AI_COMPANION_OPENAI_REALTIME_MODEL=gpt-realtime-1.5
AI_COMPANION_OPENAI_REALTIME_VOICE=echo
AI_COMPANION_OPENAI_REALTIME_TURN_DETECTION=semantic_vad
AI_COMPANION_AUDIO_RECORD_COMMAND=arecord -D plughw:2,0 -f S16_LE -r 16000 -c 6 -t raw {output_path}
AI_COMPANION_AUDIO_INPUT_CHANNELS=6
AI_COMPANION_AUDIO_CHANNEL_INDEX=0
AI_COMPANION_AUDIO_OUTPUT_BACKEND=alsa_persistent
AI_COMPANION_AUDIO_ALSA_DEVICE=default:CARD=vc4hdmi1
AI_COMPANION_WAKE_WORD_ENABLED=true
AI_COMPANION_WAKE_WORD_PHRASE=Hey Oreo
AI_COMPANION_WAKE_WORD_MODEL=/home/<user>/models/hey_oreo.onnx
```

Adjust:

- ALSA capture device in `AI_COMPANION_AUDIO_RECORD_COMMAND`
- input channel count and selected channel
- ALSA output device
- wake phrase and wake model path
- browser launch mode and executable if needed

## 6. Validate Audio Devices

List capture/playback devices:

```bash
arecord -l
aplay -l
```

For a direct recorder smoke test:

```bash
arecord -D plughw:2,0 -f S16_LE -r 16000 -c 6 -t raw -d 2 /tmp/mic.raw
ls -lh /tmp/mic.raw
```

For wake-word scoring:

```bash
.venv/bin/python scripts/test-wakeword-live.py --max-seconds 15
```

## 7. Validate The App

Compile the runtime source:

```bash
python -m py_compile src/main.py src/ai/realtime.py src/audio/capture.py src/audio/wake.py
```

Run focused tests if the Pi has test dependencies installed:

```bash
pytest tests/test_realtime.py tests/test_config.py tests/test_respeaker_capture.py
```

Start the runtime:

```bash
.venv/bin/python -m main
```

For SSH sessions that need the active desktop/session environment:

```bash
./scripts/start-robot.sh
```

## 8. Browser Face

Recommended Pi settings:

```bash
AI_COMPANION_UI_BACKEND=browser
AI_COMPANION_UI_BROWSER_LAUNCH_MODE=kiosk
AI_COMPANION_UI_SHOW_TEXT_OVERLAY=true
```

If the display is blank:

- confirm Chromium is installed
- confirm the Pi has a graphical session if using kiosk mode
- check `logs/interactive-console.log`
- try `AI_COMPANION_UI_BROWSER_LAUNCH_MODE=windowed` during debugging

## 9. Next Hardware Pass

After the realtime loop is stable, bring up:

- camera module
- face detection / camera snapshots
- hardware actions
- wake threshold tuning
- display sleep/wake commands
