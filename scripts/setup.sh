#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

PLATFORM=""
MODEL=""
LANGUAGE_MODE=""
ASSUME_YES=0
FORCE=0
SKIP_SYSTEM_PACKAGES=0

WHISPER_REPO_DIR="${REPO_DIR}/artifacts/whisper.cpp"
WHISPER_REPO_URL="https://github.com/ggml-org/whisper.cpp.git"
ENV_FILE="${REPO_DIR}/.env.local"
DEFAULT_RECORD_SECONDS=5

usage() {
  cat <<'EOF'
Usage: scripts/setup.sh [options]

Bootstrap the AI Companion Robot development/runtime environment.

Options:
  --platform <macos|rpi>       Override detected platform
  --model <tiny|base|small>
                               Whisper model to download
  --language-mode <auto|en|de|id>
                               Default runtime language mode
  --yes                        Run non-interactively with defaults
  --force                      Rebuild/rewrite generated artifacts when possible
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
      --model)
        MODEL="${2:-}"
        shift 2
        ;;
      --language-mode)
        LANGUAGE_MODE="${2:-}"
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
    brew install python@3.11 cmake sox git
    return 0
  fi

  require_command sudo
  log "installing Raspberry Pi system dependencies with apt"
  sudo apt-get update
  sudo apt-get install -y build-essential cmake git python3 python3-venv python3-pip alsa-utils curl
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

choose_model() {
  local default_model="base"
  if [[ -n "${MODEL}" ]]; then
    printf '%s' "${MODEL}"
    return 0
  fi

  cat >&2 <<'EOF'

Whisper model options:
  tiny  - smallest and fastest, lowest accuracy
  base  - recommended for Raspberry Pi, better multilingual accuracy with manageable speed
  small - slower and larger, but more accurate than base

For multilingual use, the non-.en models are preferred because they support language detection
and transcription beyond English.
EOF
  printf '%s' "$(prompt_with_default "Whisper model (tiny, base, small)" "${default_model}")"
}

choose_language_mode() {
  local default_language="auto"
  if [[ -n "${LANGUAGE_MODE}" ]]; then
    printf '%s' "${LANGUAGE_MODE}"
    return 0
  fi

  printf '%s' "$(prompt_with_default "Runtime language mode (auto, en, de, id)" "${default_language}")"
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
  printf '%s' "$(prompt_with_default "Custom wake phrase" "Oreo")"
}

choose_custom_wake_model() {
  printf '%s' "$(prompt_with_default "OpenWakeWord model name or path" "/absolute/path/to/custom_model.tflite")"
}

choose_cloud_ai_mode() {
  if [[ "${ASSUME_YES}" -eq 1 ]]; then
    printf '%s' "mock"
    return 0
  fi

  if confirm "Enable the real OpenAI backend for planning and replies?"; then
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
import os
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

prepare_whisper_repo() {
  mkdir -p "${REPO_DIR}/artifacts"
  if [[ -d "${WHISPER_REPO_DIR}/.git" ]]; then
    log "reusing existing whisper.cpp checkout"
    return 0
  fi

  if [[ -d "${WHISPER_REPO_DIR}" ]] && [[ "${FORCE}" -eq 0 ]]; then
    fail "directory ${WHISPER_REPO_DIR} exists but is not a git checkout; use --force after removing it manually"
  fi

  if [[ -d "${WHISPER_REPO_DIR}" ]]; then
    rm -rf "${WHISPER_REPO_DIR}"
  fi

  log "cloning whisper.cpp"
  git clone --depth 1 "${WHISPER_REPO_URL}" "${WHISPER_REPO_DIR}"
}

build_whisper() {
  local whisper_binary="${WHISPER_REPO_DIR}/build/bin/whisper-cli"
  if [[ -x "${whisper_binary}" ]] && [[ "${FORCE}" -eq 0 ]]; then
    log "reusing existing whisper.cpp build"
    return 0
  fi

  log "building whisper.cpp"
  cmake -S "${WHISPER_REPO_DIR}" -B "${WHISPER_REPO_DIR}/build"
  cmake --build "${WHISPER_REPO_DIR}/build" -j --config Release
}

download_model() {
  local selected_model="$1"
  local model_path="${WHISPER_REPO_DIR}/models/ggml-${selected_model}.bin"
  if [[ -f "${model_path}" ]] && [[ "${FORCE}" -eq 0 ]]; then
    log "reusing existing model ${selected_model}"
    return 0
  fi

  log "downloading Whisper model ${selected_model}"
  (
    cd "${WHISPER_REPO_DIR}"
    ./models/download-ggml-model.sh "${selected_model}"
  )
}

write_env_file() {
  local selected_model="$1"
  local selected_language_mode="$2"
  local wake_setup="$3"
  local wake_phrase="$4"
  local wake_model="$5"
  local cloud_ai_mode="$6"
  local openai_api_key="$7"
  local whisper_binary="${WHISPER_REPO_DIR}/build/bin/whisper-cli"
  local whisper_model="${WHISPER_REPO_DIR}/models/ggml-${selected_model}.bin"
  local audio_command

  if [[ "${PLATFORM}" == "macos" ]]; then
    audio_command="rec -q -c 1 -r 16000 -b 16 -e signed-integer -t raw {output_path}"
  else
    audio_command='arecord -t raw -f S16_LE -r 16000 -c 1 {output_path}'
  fi

  if [[ -f "${ENV_FILE}" ]] && [[ "${FORCE}" -eq 0 ]] && ! confirm "Overwrite existing ${ENV_FILE}?"; then
    log "keeping existing ${ENV_FILE}"
    return 0
  fi

  log "writing ${ENV_FILE}"
  cat > "${ENV_FILE}" <<EOF
# Generated by scripts/setup.sh
# Recorder commands must write raw PCM to stdout; the runtime replaces {output_path} with "-".
AI_COMPANION_INPUT_MODE=speech
AI_COMPANION_INTERACTIVE_CONSOLE=true
AI_COMPANION_STT_BACKEND=whisper_cpp
AI_COMPANION_USE_MOCK_AI=$([[ "${cloud_ai_mode}" == "openai" ]] && printf '%s' "false" || printf '%s' "true")
AI_COMPANION_CLOUD_ENABLED=$([[ "${cloud_ai_mode}" == "openai" ]] && printf '%s' "true" || printf '%s' "false")
AI_COMPANION_CLOUD_PROVIDER_NAME=openai
AI_COMPANION_OPENAI_API_KEY=${openai_api_key}
AI_COMPANION_OPENAI_BASE_URL=https://api.openai.com/v1/responses
AI_COMPANION_OPENAI_PLANNER_MODEL=gpt-4o-mini
AI_COMPANION_OPENAI_RESPONSE_MODEL=gpt-5.2
AI_COMPANION_OPENAI_TIMEOUT_SECONDS=20
AI_COMPANION_WHISPER_BINARY_PATH=${whisper_binary}
AI_COMPANION_WHISPER_MODEL_PATH=${whisper_model}
AI_COMPANION_AUDIO_RECORD_COMMAND=${audio_command}
AI_COMPANION_SPEECH_SILENCE_SECONDS=0.75
AI_COMPANION_VAD_THRESHOLD=0.45
AI_COMPANION_VAD_FRAME_MS=30
AI_COMPANION_VAD_START_TRIGGER_FRAMES=2
AI_COMPANION_VAD_END_TRIGGER_FRAMES=5
AI_COMPANION_MAX_RECORDING_SECONDS=15
AI_COMPANION_WAKE_WORD_ENABLED=$([[ "${wake_setup}" == "off" ]] && printf '%s' "false" || printf '%s' "true")
AI_COMPANION_WAKE_WORD_PHRASE=${wake_phrase}
AI_COMPANION_WAKE_WORD_MODEL=${wake_model}
AI_COMPANION_WAKE_WORD_THRESHOLD=0.5
AI_COMPANION_WAKE_LOOKBACK_SECONDS=0.8
AI_COMPANION_UTTERANCE_FINALIZE_TIMEOUT_SECONDS=0.6
AI_COMPANION_UTTERANCE_TAIL_STABLE_POLLS=2
AI_COMPANION_LANGUAGE_MODE=${selected_language_mode}
EOF
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

  local selected_model
  selected_model="$(choose_model)"
  case "${selected_model}" in
    tiny|base|small) ;;
    *) fail "unsupported Whisper model '${selected_model}'" ;;
  esac

  local selected_language_mode
  selected_language_mode="$(choose_language_mode)"
  case "${selected_language_mode}" in
    auto|en|de|id) ;;
    *) fail "unsupported language mode '${selected_language_mode}'" ;;
  esac

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
  if [[ "${wake_setup}" != "off" ]]; then
    log "resolving OpenWakeWord model '${wake_model}'"
    local resolved_wake
    resolved_wake="$(resolve_wake_model "${wake_setup}" "${wake_model}")"
    wake_model="${resolved_wake%%|*}"
    log "using OpenWakeWord model reference: ${wake_model}"
  fi
  prepare_whisper_repo
  build_whisper
  download_model "${selected_model}"
  write_env_file \
    "${selected_model}" \
    "${selected_language_mode}" \
    "${wake_setup}" \
    "${wake_phrase}" \
    "${wake_model}" \
    "${cloud_ai_mode}" \
    "${openai_api_key}"
  run_verification

  cat <<EOF

Setup complete.

Generated config: ${ENV_FILE}
Whisper binary: ${WHISPER_REPO_DIR}/build/bin/whisper-cli
Whisper model: ${WHISPER_REPO_DIR}/models/ggml-${selected_model}.bin

Next steps:
  1. Run the app with: .venv/bin/python src/main.py
  2. In interactive speech mode, press Enter to start speaking, type a phrase directly, or use the configured wake word
  3. Edit ${ENV_FILE} if you want to adjust model, language mode, VAD endpoint timing, wake-word settings, recorder settings, or add an OpenAI API key later
EOF
}

main "$@"
