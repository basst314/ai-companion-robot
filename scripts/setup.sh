#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

PLATFORM=""
ASSUME_YES=0
FORCE=0
SKIP_SYSTEM_PACKAGES=0
ENV_FILE="${REPO_DIR}/.env.local"

usage() {
  cat <<'EOF'
Usage: scripts/setup.sh [options]

Bootstrap the AI Companion Robot development/runtime environment.

Options:
  --platform <macos|rpi>       Override detected platform
  --yes                        Run non-interactively with defaults
  --force                      Recreate .venv and rewrite generated config
  --skip-system-packages       Skip apt/brew dependency installation
  --help                       Show this help
EOF
}

log() {
  printf '[setup] %s\n' "$1"
}

fail() {
  printf '[setup] ERROR: %s\n' "$1" >&2
  exit 1
}

confirm() {
  local prompt="$1"
  if [[ "${ASSUME_YES}" -eq 1 ]]; then
    return 0
  fi

  local answer
  read -r -p "${prompt} [Y/n] " answer
  case "${answer}" in
    ""|y|Y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

prompt_with_default() {
  local prompt="$1"
  local default_value="$2"
  if [[ "${ASSUME_YES}" -eq 1 ]]; then
    printf '%s' "${default_value}"
    return 0
  fi

  local answer
  read -r -p "${prompt} [${default_value}] " answer
  printf '%s' "${answer:-${default_value}}"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command '$1' was not found"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --platform)
        PLATFORM="${2:-}"
        shift 2
        ;;
      --yes)
        ASSUME_YES=1
        shift
        ;;
      --force)
        FORCE=1
        shift
        ;;
      --skip-system-packages)
        SKIP_SYSTEM_PACKAGES=1
        shift
        ;;
      --help)
        usage
        exit 0
        ;;
      *)
        fail "unknown argument: $1"
        ;;
    esac
  done
}

detect_platform() {
  if [[ -n "${PLATFORM}" ]]; then
    case "${PLATFORM}" in
      macos|rpi) return 0 ;;
      *) fail "unsupported --platform value '${PLATFORM}'" ;;
    esac
  fi

  case "$(uname -s)" in
    Darwin)
      PLATFORM="macos"
      ;;
    Linux)
      if [[ -r /proc/device-tree/model ]] && grep -qi "Raspberry Pi" /proc/device-tree/model 2>/dev/null; then
        PLATFORM="rpi"
      elif [[ -r /etc/os-release ]] && grep -qi "debian\|raspbian\|ubuntu" /etc/os-release; then
        fail "detected Debian-family Linux, but not Raspberry Pi. Re-run with --platform rpi if you want to use the Raspberry Pi setup path."
      else
        fail "unsupported Linux platform; supported platforms are Raspberry Pi and macOS"
      fi
      ;;
    *)
      fail "unsupported platform '$(uname -s)'"
      ;;
  esac
}

install_system_packages() {
  if [[ "${SKIP_SYSTEM_PACKAGES}" -eq 1 ]]; then
    log "skipping system package installation"
    return 0
  fi

  if [[ "${PLATFORM}" == "macos" ]]; then
    require_command brew
    log "installing macOS system dependencies with Homebrew"
    brew install python@3.11 sox git
    return 0
  fi

  require_command sudo
  log "installing Raspberry Pi system dependencies with apt"
  sudo apt-get update
  local chromium_package="chromium-browser"
  local chromium_browser_candidate
  local chromium_candidate
  chromium_browser_candidate="$(apt-cache policy chromium-browser 2>/dev/null | awk '/Candidate:/ {print $2; exit}')"
  chromium_candidate="$(apt-cache policy chromium 2>/dev/null | awk '/Candidate:/ {print $2; exit}')"
  if [[ -n "${chromium_browser_candidate}" ]] && [[ "${chromium_browser_candidate}" != "(none)" ]]; then
    chromium_package="chromium-browser"
  elif [[ -n "${chromium_candidate}" ]] && [[ "${chromium_candidate}" != "(none)" ]]; then
    chromium_package="chromium"
  else
    fail "could not find a supported Chromium package on this Raspberry Pi image"
  fi

  sudo apt-get install -y \
    build-essential \
    git \
    python3 \
    python3-venv \
    python3-pip \
    alsa-utils \
    libasound2-dev \
    curl \
    "${chromium_package}" \
    wtype

  if apt-cache show rpd-wayland-core >/dev/null 2>&1; then
    log "installing Raspberry Pi desktop packages for Wayland kiosk sessions"
    sudo apt-get install -y --no-install-recommends rpd-wayland-core rpd-theme rpd-preferences
    return 0
  fi

  if apt-cache show raspberrypi-ui-mods >/dev/null 2>&1; then
    log "installing Raspberry Pi desktop packages for X11 kiosk sessions"
    sudo apt-get install -y --no-install-recommends xserver-xorg lightdm raspberrypi-ui-mods
    return 0
  fi

  fail "could not find a supported Raspberry Pi desktop package set for browser kiosk mode"
}

python_at_least_311() {
  local python_cmd="$1"
  "${python_cmd}" - <<'EOF'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
EOF
}

select_python() {
  if [[ "${PLATFORM}" == "macos" ]]; then
    if command -v python3.11 >/dev/null 2>&1 && python_at_least_311 python3.11; then
      printf '%s' "python3.11"
      return 0
    fi

    local brew_python
    brew_python="$(brew --prefix python@3.11)/bin/python3.11"
    if [[ -x "${brew_python}" ]] && python_at_least_311 "${brew_python}"; then
      printf '%s' "${brew_python}"
      return 0
    fi
  fi

  if command -v python3 >/dev/null 2>&1 && python_at_least_311 python3; then
    printf '%s' "python3"
    return 0
  fi

  fail "Python 3.11+ is required. Install it and re-run scripts/setup.sh."
}

choose_wake_setup() {
  if [[ "${ASSUME_YES}" -eq 1 ]]; then
    printf '%s' "default"
    return 0
  fi

  if ! confirm "Enable wake-word detection with OpenWakeWord?"; then
    printf '%s' "off"
    return 0
  fi

  cat >&2 <<'EOF'

Wake-word setup options:
  default - use the built-in Hey Jarvis + hey jarvis model pairing
  custom  - provide your own spoken phrase and matching model name/path
EOF
  printf '%s' "$(prompt_with_default "Wake-word setup (default, custom)" "default")"
}

choose_custom_wake_phrase() {
  printf '%s' "$(prompt_with_default "Custom wake phrase" "Robot")"
}

choose_custom_wake_model() {
  printf '%s' "$(prompt_with_default "OpenWakeWord model name or path" "/absolute/path/to/custom_model.tflite")"
}

choose_cloud_ai_mode() {
  if [[ "${ASSUME_YES}" -eq 1 ]]; then
    printf '%s' "mock"
    return 0
  fi

  if confirm "Enable the real OpenAI realtime backend?"; then
    printf '%s' "openai"
    return 0
  fi

  printf '%s' "mock"
}

choose_openai_api_key() {
  if [[ "${ASSUME_YES}" -eq 1 ]]; then
    printf '%s' ""
    return 0
  fi

  local answer
  read -r -s -p "OpenAI API key (leave blank to add it later) [] " answer
  printf '\n' >&2
  printf '%s' "${answer}"
}

create_virtualenv() {
  local python_cmd="$1"
  local recreate_venv=0
  if [[ -x "${REPO_DIR}/.venv/bin/python" ]] && [[ "${FORCE}" -eq 0 ]]; then
    local existing_version
    local target_version
    existing_version="$("${REPO_DIR}/.venv/bin/python" - <<'EOF'
import sys
print(f"{sys.version_info[0]}.{sys.version_info[1]}")
EOF
)"
    target_version="$("${python_cmd}" - <<'EOF'
import sys
print(f"{sys.version_info[0]}.{sys.version_info[1]}")
EOF
)"
    if [[ "${existing_version}" == "${target_version}" ]]; then
      log "reusing existing .venv"
    else
      log "recreating .venv because it uses Python ${existing_version}, expected ${target_version}"
      recreate_venv=1
    fi
  else
    recreate_venv=1
  fi

  if [[ "${recreate_venv}" -eq 1 ]]; then
    rm -rf "${REPO_DIR}/.venv"
    log "creating virtual environment with ${python_cmd}"
    "${python_cmd}" -m venv "${REPO_DIR}/.venv"
  fi

  log "installing Python package dependencies"
  "${REPO_DIR}/.venv/bin/python" -m pip install --upgrade pip
  local python_minor_version
  python_minor_version="$("${REPO_DIR}/.venv/bin/python" - <<'EOF'
import sys
print(f"{sys.version_info[0]}.{sys.version_info[1]}")
EOF
)"
  if [[ "${PLATFORM}" == "rpi" ]] && [[ "${python_minor_version}" == "3.13" ]]; then
    log "using Raspberry Pi Python 3.13 compatibility path for openWakeWord"
    "${REPO_DIR}/.venv/bin/python" -m pip install \
      "pytest>=8.0" \
      "pytest-cov>=5.0" \
      "onnxruntime<2,>=1.10.0" \
      "pyalsaaudio>=0.10,<1" \
      "requests<3,>=2.0" \
      "tqdm<5.0,>=4.0" \
      "scipy<2,>=1.3" \
      "scikit-learn<2,>=1"
    "${REPO_DIR}/.venv/bin/python" -m pip install --no-deps "openwakeword>=0.6,<0.7"
    "${REPO_DIR}/.venv/bin/python" -m pip install --no-deps -e "${REPO_DIR}"
    return 0
  fi
  "${REPO_DIR}/.venv/bin/python" -m pip install -e "${REPO_DIR}[dev]"
}

resolve_wake_model() {
  local wake_setup="$1"
  local wake_model="$2"

  if [[ "${wake_setup}" == "off" ]]; then
    printf '%s|%s' "" ""
    return 0
  fi

  local resolved
  local resolved_raw
  resolved="$("${REPO_DIR}/.venv/bin/python" - "$wake_setup" "$wake_model" 2>&1 <<'EOF'
import importlib.util
import pathlib
import platform
import sys

wake_setup, wake_model = sys.argv[1:]

try:
    import openwakeword
    import openwakeword.utils
    from openwakeword.model import Model
except ImportError as exc:
    raise SystemExit(f"OpenWakeWord is not installed correctly: {exc}")


def select_framework(model_ref: str) -> str:
    normalized = model_ref.strip().lower()
    if normalized.endswith(".onnx"):
        return "onnx"
    if normalized.endswith(".tflite"):
        return "tflite"
    if platform.system() == "Darwin":
        return "onnx"
    if importlib.util.find_spec("ai_edge_litert") or importlib.util.find_spec("tflite_runtime"):
        return "tflite"
    return "onnx"


def ensure_runtime_support_files() -> None:
    resources_dir = pathlib.Path(openwakeword.__file__).resolve().parent / "resources" / "models"
    resources_dir.mkdir(parents=True, exist_ok=True)
    for feature_model in openwakeword.FEATURE_MODELS.values():
        tflite_path = resources_dir / feature_model["download_url"].split("/")[-1]
        onnx_path = resources_dir / tflite_path.name.replace(".tflite", ".onnx")
        if not tflite_path.exists():
            openwakeword.utils.download_file(feature_model["download_url"], str(resources_dir))
        if not onnx_path.exists():
            openwakeword.utils.download_file(feature_model["download_url"].replace(".tflite", ".onnx"), str(resources_dir))
    for vad_model in openwakeword.VAD_MODELS.values():
        vad_path = resources_dir / vad_model["download_url"].split("/")[-1]
        if not vad_path.exists():
            openwakeword.utils.download_file(vad_model["download_url"], str(resources_dir))


def ensure_builtin_model(model_ref: str) -> str:
    normalized = model_ref.strip().replace(" ", "_").lower()
    matched_name = None
    for metadata in openwakeword.MODELS.values():
        model_path = metadata["model_path"]
        stem = pathlib.Path(model_path).stem
        if normalized == stem or normalized in stem:
            matched_name = stem
            break
    if matched_name is None:
        raise SystemExit(f"Unable to resolve built-in OpenWakeWord model '{model_ref}'")
    openwakeword.utils.download_models(model_names=[matched_name])
    return model_ref


ensure_runtime_support_files()

if wake_setup == "default":
    framework = select_framework(wake_model)
    resolved_model = ensure_builtin_model(wake_model)
elif wake_setup == "custom":
    candidate = pathlib.Path(wake_model).expanduser()
    if not candidate.is_absolute():
        candidate = pathlib.Path.cwd() / candidate
    if not candidate.exists():
        raise SystemExit(
            "Custom wake-word models must already exist on disk. "
            f"Model path not found: {candidate}"
        )
    resolved_model = candidate
    framework = select_framework(str(candidate))
else:
    raise SystemExit(f"Unsupported wake setup '{wake_setup}'")

try:
    Model(wakeword_models=[str(resolved_model)], inference_framework=framework)
except Exception as exc:
    raise SystemExit(
        "Failed to initialize the selected OpenWakeWord model. "
        f"Model={resolved_model} framework={framework} error={exc}"
    )

print(f"{resolved_model}|{framework}")
EOF
)" || fail "${resolved}"
  resolved_raw="${resolved}"
  resolved="${resolved_raw##*$'\n'}"
  [[ "${resolved}" == *"|"* ]] || fail "unexpected OpenWakeWord resolver output: ${resolved_raw}"
  printf '%s' "${resolved}"
}

prepare_openwakeword_runtime_support() {
  log "ensuring OpenWakeWord runtime support files are available"
  local output
  output="$("${REPO_DIR}/.venv/bin/python" - 2>&1 <<'EOF'
import pathlib

try:
    import openwakeword
    import openwakeword.utils
except ImportError as exc:
    raise SystemExit(f"OpenWakeWord is not installed correctly: {exc}")

resources_dir = pathlib.Path(openwakeword.__file__).resolve().parent / "resources" / "models"
resources_dir.mkdir(parents=True, exist_ok=True)

for feature_model in openwakeword.FEATURE_MODELS.values():
    tflite_path = resources_dir / feature_model["download_url"].split("/")[-1]
    onnx_path = resources_dir / tflite_path.name.replace(".tflite", ".onnx")
    if not tflite_path.exists():
        openwakeword.utils.download_file(feature_model["download_url"], str(resources_dir))
    if not onnx_path.exists():
        openwakeword.utils.download_file(feature_model["download_url"].replace(".tflite", ".onnx"), str(resources_dir))

for vad_model in openwakeword.VAD_MODELS.values():
    vad_path = resources_dir / vad_model["download_url"].split("/")[-1]
    if not vad_path.exists():
        openwakeword.utils.download_file(vad_model["download_url"], str(resources_dir))

print(resources_dir)
EOF
)" || fail "${output}"
  log "OpenWakeWord resources ready at ${output##*$'\n'}"
}

write_env_file() {
  local wake_setup="$1"
  local wake_phrase="$2"
  local wake_model="$3"
  local cloud_ai_mode="$4"
  local openai_api_key="$5"
  local audio_record_command
  local audio_play_command
  local audio_output_backend
  local audio_alsa_device
  local ui_backend
  local ui_browser_launch_mode
  local ui_browser_executable
  local ui_browser_extra_args
  local interactive_console
  local audio_input_channels
  local audio_channel_index

  if [[ "${PLATFORM}" == "macos" ]]; then
    audio_record_command="rec -q -c 1 -r 16000 -b 16 -e signed-integer -t raw {output_path}"
    audio_play_command="afplay {input_path}"
    audio_output_backend="command"
    audio_alsa_device="default"
    ui_backend="browser"
    ui_browser_launch_mode="windowed"
    ui_browser_executable=""
    ui_browser_extra_args=""
    interactive_console="true"
    audio_input_channels="1"
    audio_channel_index="0"
  else
    audio_record_command='arecord -D hw:CARD=ArrayUAC10,DEV=0 -t raw -f S16_LE -r 16000 -c 6 -q {output_path}'
    audio_play_command=""
    audio_output_backend="alsa_persistent"
    audio_alsa_device="default:CARD=vc4hdmi1"
    ui_backend="browser"
    ui_browser_launch_mode="kiosk"
    if command -v chromium-browser >/dev/null 2>&1; then
      ui_browser_executable="chromium-browser"
    elif command -v chromium >/dev/null 2>&1; then
      ui_browser_executable="chromium"
    else
      ui_browser_executable="chromium-browser"
    fi
    ui_browser_extra_args="--ozone-platform=wayland"
    interactive_console="false"
    audio_input_channels="6"
    audio_channel_index="0"
  fi

  if [[ -f "${ENV_FILE}" ]] && [[ "${FORCE}" -eq 0 ]] && ! confirm "Overwrite existing ${ENV_FILE}?"; then
    log "keeping existing ${ENV_FILE}"
    return 0
  fi

  log "writing ${ENV_FILE}"
  cat > "${ENV_FILE}" <<EOF
# Generated by scripts/setup.sh
# Recorder commands must write raw PCM; the runtime replaces {output_path} with "-".
AI_COMPANION_AUTO_RUN=true
AI_COMPANION_INPUT_MODE=speech
AI_COMPANION_INTERACTION_BACKEND=openai_realtime
AI_COMPANION_INTERACTIVE_CONSOLE=${interactive_console}
AI_COMPANION_DEFAULT_LANGUAGE=en
AI_COMPANION_LANGUAGE_MODE=en
AI_COMPANION_DATA_DIR=data
AI_COMPANION_MODELS_DIR=models
AI_COMPANION_LOGS_DIR=logs

AI_COMPANION_USE_MOCK_AI=$([[ "${cloud_ai_mode}" == "openai" ]] && printf '%s' "false" || printf '%s' "true")
AI_COMPANION_CLOUD_ENABLED=$([[ "${cloud_ai_mode}" == "openai" ]] && printf '%s' "true" || printf '%s' "false")
AI_COMPANION_CLOUD_PROVIDER_NAME=openai
AI_COMPANION_OPENAI_API_KEY=${openai_api_key}
AI_COMPANION_OPENAI_BASE_URL=https://api.openai.com/v1/responses
AI_COMPANION_OPENAI_RESPONSE_MODEL=
AI_COMPANION_OPENAI_TIMEOUT_SECONDS=20
AI_COMPANION_OPENAI_REPLY_MAX_OUTPUT_TOKENS=120
AI_COMPANION_OPENAI_REALTIME_MODEL=gpt-realtime-1.5
AI_COMPANION_OPENAI_REALTIME_VOICE=echo
AI_COMPANION_OPENAI_REALTIME_TURN_DETECTION=semantic_vad
AI_COMPANION_OPENAI_REALTIME_TURN_EAGERNESS=auto
AI_COMPANION_OPENAI_REALTIME_LOCAL_BARGE_IN_ENABLED=false
AI_COMPANION_OPENAI_REALTIME_BASE_URL=wss://api.openai.com/v1/realtime
AI_COMPANION_OPENAI_REALTIME_AUDIO_SAMPLE_RATE=24000

AI_COMPANION_AUDIO_INIT_COMMAND=
AI_COMPANION_AUDIO_RECORD_COMMAND=${audio_record_command}
AI_COMPANION_AUDIO_INPUT_CHANNELS=${audio_input_channels}
AI_COMPANION_AUDIO_CHANNEL_INDEX=${audio_channel_index}
AI_COMPANION_AUDIO_OUTPUT_BACKEND=${audio_output_backend}
AI_COMPANION_AUDIO_PLAY_COMMAND=${audio_play_command}
AI_COMPANION_AUDIO_ALSA_DEVICE=${audio_alsa_device}
AI_COMPANION_AUDIO_ALSA_SAMPLE_RATE=24000
AI_COMPANION_AUDIO_ALSA_PERIOD_FRAMES=512
AI_COMPANION_AUDIO_ALSA_BUFFER_FRAMES=2048
AI_COMPANION_AUDIO_ALSA_KEEPALIVE_INTERVAL_MS=20

AI_COMPANION_UI_BACKEND=${ui_backend}
AI_COMPANION_UI_IDLE_SLEEP_SECONDS=300
AI_COMPANION_UI_SLEEPING_EYES_GRACE_SECONDS=12
AI_COMPANION_UI_SHOW_TEXT_OVERLAY=true
AI_COMPANION_UI_SLEEP_COMMAND=
AI_COMPANION_UI_WAKE_COMMAND=
AI_COMPANION_UI_BROWSER_HOST=127.0.0.1
AI_COMPANION_UI_BROWSER_HTTP_PORT=8765
AI_COMPANION_UI_BROWSER_WS_PORT=8766
AI_COMPANION_UI_BROWSER_LAUNCH_MODE=${ui_browser_launch_mode}
AI_COMPANION_UI_BROWSER_EXECUTABLE=${ui_browser_executable}
AI_COMPANION_UI_BROWSER_PROFILE_DIR=
AI_COMPANION_UI_BROWSER_EXTRA_ARGS=${ui_browser_extra_args}
AI_COMPANION_UI_BROWSER_STATE_PATH=
AI_COMPANION_UI_FACE_IDLE_ENABLED=true
AI_COMPANION_UI_FACE_IDLE_FREQUENCY=0.26
AI_COMPANION_UI_FACE_IDLE_INTENSITY=0.63
AI_COMPANION_UI_FACE_IDLE_PAUSE_RANDOMNESS=0.54
AI_COMPANION_UI_FACE_SECONDARY_MICRO_MOTION=true
AI_COMPANION_UI_FACE_IDLE_BEHAVIORS=blink||look_side||quick_glance||bored||curious||scoot||boundary_press

AI_COMPANION_WAKE_WORD_ENABLED=$([[ "${wake_setup}" == "off" ]] && printf '%s' "false" || printf '%s' "true")
AI_COMPANION_WAKE_WORD_PHRASE=${wake_phrase}
AI_COMPANION_WAKE_WORD_MODEL=${wake_model}
AI_COMPANION_WAKE_WORD_THRESHOLD=0.5
AI_COMPANION_WAKE_LOOKBACK_SECONDS=0.5
AI_COMPANION_FOLLOW_UP_MODE_ENABLED=true
AI_COMPANION_FOLLOW_UP_LISTEN_TIMEOUT_SECONDS=5.0
AI_COMPANION_FOLLOW_UP_MAX_TURNS=10

AI_COMPANION_USE_MOCK_VISION=true
AI_COMPANION_USE_MOCK_HARDWARE=true
EOF
}

configure_labwc_hide_cursor() {
  if [[ "${PLATFORM}" != "rpi" ]]; then
    return 0
  fi

  local labwc_dir="${HOME}/.config/labwc"
  local rc_xml="${labwc_dir}/rc.xml"
  mkdir -p "${labwc_dir}"
  if [[ ! -f "${rc_xml}" ]]; then
    cat > "${rc_xml}" <<'EOF'
<?xml version="1.0"?>
<openbox_config xmlns="http://openbox.org/3.4/rc">
</openbox_config>
EOF
  fi

  python3 - "${rc_xml}" <<'EOF'
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

path = Path(sys.argv[1])
ns = {"rc": "http://openbox.org/3.4/rc"}
ET.register_namespace("", ns["rc"])

tree = ET.parse(path)
root = tree.getroot()

keyboard = root.find("rc:keyboard", ns)
if keyboard is None:
    keyboard = ET.Element(f"{{{ns['rc']}}}keyboard")
    touch = root.find("rc:touch", ns)
    if touch is not None:
        index = list(root).index(touch)
        root.insert(index, keyboard)
    else:
        root.append(keyboard)

for keybind in list(keyboard.findall("rc:keybind", ns)):
    if keybind.get("key") == "F12":
        keyboard.remove(keybind)

keybind = ET.Element(f"{{{ns['rc']}}}keybind", {"key": "F12"})
ET.SubElement(keybind, f"{{{ns['rc']}}}action", {"name": "HideCursor"})
keyboard.append(keybind)

path.write_text(
    "<?xml version=\"1.0\"?>\n" + ET.tostring(root, encoding="unicode") + "\n"
)
EOF

  log "configured LabWC HideCursor keybind in ${rc_xml}"
}

configure_labwc_cursor_autostart() {
  if [[ "${PLATFORM}" != "rpi" ]]; then
    return 0
  fi

  local labwc_dir="${HOME}/.config/labwc"
  local autostart_script="${labwc_dir}/autostart"
  mkdir -p "${labwc_dir}"
  cat > "${autostart_script}" <<'EOF'
#!/usr/bin/env sh

if command -v wtype >/dev/null 2>&1; then
  (
    sleep 4
    wtype -k F12 >/dev/null 2>&1 || true
  ) &
fi
EOF
  chmod +x "${autostart_script}"
  log "configured LabWC cursor autostart helper in ${autostart_script}"
}

run_verification() {
  log "running test suite"
  "${REPO_DIR}/.venv/bin/pytest" -q
}

main() {
  parse_args "$@"
  detect_platform

  log "detected platform: ${PLATFORM}"
  if [[ "${ASSUME_YES}" -eq 0 ]] && ! confirm "Continue with ${PLATFORM} setup?"; then
    fail "setup cancelled"
  fi

  local wake_setup
  wake_setup="$(choose_wake_setup)"
  case "${wake_setup}" in
    off|default|custom) ;;
    *) fail "unsupported wake-word setup '${wake_setup}'" ;;
  esac

  local wake_phrase=""
  local wake_model=""
  case "${wake_setup}" in
    default)
      wake_phrase="Hey Jarvis"
      wake_model="hey jarvis"
      ;;
    custom)
      wake_phrase="$(choose_custom_wake_phrase)"
      wake_model="$(choose_custom_wake_model)"
      [[ -n "${wake_phrase}" ]] || fail "custom wake phrase cannot be empty"
      [[ -n "${wake_model}" ]] || fail "custom wake model cannot be empty"
      ;;
  esac

  local cloud_ai_mode
  cloud_ai_mode="$(choose_cloud_ai_mode)"
  case "${cloud_ai_mode}" in
    mock|openai) ;;
    *) fail "unsupported cloud AI mode '${cloud_ai_mode}'" ;;
  esac

  local openai_api_key=""
  if [[ "${cloud_ai_mode}" == "openai" ]]; then
    openai_api_key="$(choose_openai_api_key)"
  fi

  install_system_packages

  local python_cmd
  python_cmd="$(select_python)"
  log "using Python interpreter: ${python_cmd}"

  create_virtualenv "${python_cmd}"
  prepare_openwakeword_runtime_support
  if [[ "${wake_setup}" != "off" ]]; then
    log "resolving OpenWakeWord model '${wake_model}'"
    local resolved_wake
    resolved_wake="$(resolve_wake_model "${wake_setup}" "${wake_model}")"
    wake_model="${resolved_wake%%|*}"
    log "using OpenWakeWord model reference: ${wake_model}"
  fi
  write_env_file \
    "${wake_setup}" \
    "${wake_phrase}" \
    "${wake_model}" \
    "${cloud_ai_mode}" \
    "${openai_api_key}"
  configure_labwc_hide_cursor
  configure_labwc_cursor_autostart
  run_verification

  cat <<EOF

Setup complete.

Generated config: ${ENV_FILE}

Next steps:
  1. Add AI_COMPANION_OPENAI_API_KEY to ${ENV_FILE} if you did not enter it during setup
  2. Run the app with: .venv/bin/python src/main.py
  3. Adjust recorder, wake-word, realtime, browser face, or playback settings in ${ENV_FILE} for your machine
EOF
}

main "$@"
