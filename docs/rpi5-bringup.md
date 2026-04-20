# Raspberry Pi 5 Bring-Up Guide

This guide turns a fresh Raspberry Pi 5 into a runtime target for `ai-companion-robot`.

It is optimized for the first successful robot bring-up:
- fresh SD card image
- headless SSH access
- repo cloned on the Pi
- `scripts/setup.sh --platform rpi` completed
- audio and runtime validation passing

The small display and camera are intentionally deferred until the core voice runtime is stable.

## What You Need

- Raspberry Pi 5
- microSD card you are okay erasing
- power supply for the Pi 5
- Ethernet or Wi-Fi details
- your laptop with Raspberry Pi Imager installed
- this repo available locally

Optional for the first pass:
- microphone
- speaker or HDMI audio output
- your custom wake-word model file, such as `custom_wake_model.onnx`

## 1. Reimage The SD Card

Use Raspberry Pi Imager and select:
- Device: `Raspberry Pi 5`
- Operating System: `Raspberry Pi OS Lite (64-bit)` on Bookworm or newer
- Storage: your existing SD card

Before writing, open the Imager advanced settings and set:
- hostname: choose a stable, memorable name
- username and password: create the account you want to keep
- Wi-Fi: your SSID and password if not using Ethernet
- locale/timezone/keyboard: your normal settings
- SSH: enabled

Write the image, eject the card cleanly, then insert it into the Pi.

## 2. First Boot

Connect power and give the Pi a minute or two for first boot.

From your laptop, connect over SSH:

```bash
ssh <user>@<hostname>.local
```

If `.local` discovery does not work:
- check your router or DHCP lease list for the Pi IP
- or try `ping <hostname>.local`

## 3. Update The Pi

Run:

```bash
sudo apt update
sudo apt full-upgrade -y
sudo reboot
```

Reconnect over SSH after the reboot.

## 4. Copy Or Clone The Repo

You can either clone from GitHub on the Pi or sync your current local checkout.

### Option A: Clone on the Pi

```bash
git clone <your-repo-url> ~/ai-companion-robot
cd ~/ai-companion-robot
```

### Option B: Sync your local checkout

From your laptop, run:

```bash
./scripts/sync-to-pi.sh --host <hostname>.local --user <user>
```

If you already have a custom wake-word model locally and want it copied into the standard Pi path too:

```bash
./scripts/sync-to-pi.sh \
  --host <hostname>.local \
  --user <user> \
  --copy-wake-model /absolute/path/to/custom_wake_model.onnx
```

The sync helper deliberately skips `.env.local`, `.venv`, `.git`, and build artifacts so the Pi gets its own clean runtime setup.

If Raspberry Pi Imager only offers a newer Trixie-based Lite image, that is fine for this repo.

## 5. Run The Pi Bootstrap

SSH into the Pi, then run:

```bash
cd ~/ai-companion-robot
./scripts/setup.sh --platform rpi
```

Recommended interactive choices:
- Whisper model: `base.en` for the current Pi baseline, or `tiny.en` if you want the fastest first pass
- language mode: `auto`
- TTS backend: `piper` if you want the real voice stack now
- cloud AI mode: `openai` if you want live cloud replies immediately
- wake word: use `custom` only if your model file is already on the Pi

On current Raspberry Pi OS Trixie images, Python 3.13 is the default. This repo's setup script includes a compatibility path for `openwakeword` on that platform, but the safest first pass is still to leave wake-word disabled until the rest of the runtime is stable.

If you copied the custom wake-word model with `sync-to-pi.sh`, the expected Pi path is:

```text
~/ai-companion-robot/artifacts/openwakeword/models/<your-model-filename>
```

## 6. Update Pi-Specific Runtime Config

After setup finishes, open `.env.local` on the Pi and verify:

- `AI_COMPANION_OPENAI_API_KEY`
- `AI_COMPANION_AUDIO_RECORD_COMMAND`
- `AI_COMPANION_TTS_AUDIO_BACKEND`
- `AI_COMPANION_TTS_AUDIO_PLAY_COMMAND`
- `AI_COMPANION_TTS_ALSA_DEVICE`
- `AI_COMPANION_WHISPER_COMMAND_EXTRA_ARGS`
- `AI_COMPANION_WAKE_WORD_ENABLED`
- `AI_COMPANION_WAKE_WORD_PHRASE`
- `AI_COMPANION_WAKE_WORD_MODEL`
- `AI_COMPANION_WHISPER_BINARY_PATH`
- `AI_COMPANION_WHISPER_MODEL_PATH`
- `AI_COMPANION_PARTIAL_TRANSCRIPTS_ENABLED`

Important:
- do not copy your Mac `.env.local` directly to the Pi because it contains machine-specific absolute paths
- if your mic needs a different ALSA device, update `AI_COMPANION_AUDIO_RECORD_COMMAND`
- if your speaker path differs, update `AI_COMPANION_TTS_ALSA_DEVICE`
- the Pi setup now defaults to `AI_COMPANION_TTS_AUDIO_BACKEND=alsa_persistent` because the ALSA-native playback path is much more robust on HDMI displays/speakers than command-driven `aplay` alone
- for the ReSpeaker 4 Mic Array v3.0, the current Pi baseline uses `scripts/respeaker_capture.py` to capture the board's processed channel 0 from the six-channel USB stream before handing audio to the robot runtime

## 7. Validate The Pi Runtime

Run the staged validator on the Pi:

```bash
cd ~/ai-companion-robot
./scripts/validate-rpi-runtime.sh
```

This checks:
- `arecord -l`
- `aplay -l`
- Python virtualenv presence
- generated `.env.local`
- `pytest`

You can also ask it to do a short app startup smoke test:

```bash
./scripts/validate-rpi-runtime.sh --smoke-main --main-timeout 20
```

That smoke test temporarily forces text input and disables wake-word listening so you can verify the app boots even before a microphone is connected.

If wake word is configured and your microphone is ready:

```bash
.venv/bin/python scripts/test-wakeword-live.py --max-seconds 15
```

Then launch the full runtime:

```bash
.venv/bin/python src/main.py
```

## 8. Second Pass Hardware Bring-Up

After the core runtime is working, move on to:
- the small display for robot eyes and face rendering in Chromium kiosk mode
- the camera module
- audio device tuning
- wake-word threshold tuning on the Pi

That keeps the first session focused on getting one stable working robot runtime before layering on extra hardware variables.

If the display is blank, verify that `scripts/start-robot.sh` launched Chromium successfully and that the browser bridge is running. The legacy SDL/framebuffer diagnostics have been retired with the older UI renderers, so Chromium kiosk startup is now the supported path to inspect first.
