#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

PLATFORM=""
MODEL=""
LANGUAGE_MODE=""
TTS_BACKEND=""
TTS_LANGUAGES=""
TTS_SERVICE_MODE=""
TTS_EXPRESSIVE_DE=0
ASSUME_YES=0
FORCE=0
SKIP_SYSTEM_PACKAGES=0

WHISPER_REPO_DIR="${REPO_DIR}/artifacts/whisper.cpp"
WHISPER_REPO_URL="https://github.com/ggml-org/whisper.cpp.git"
PIPER_VOICE_DIR="${REPO_DIR}/artifacts/piper-voices"
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
  --tts-backend <mock|piper>   Select local TTS backend
  --tts-languages <en,de,id>   Comma-separated Piper voice languages to provision
  --tts-service-mode <managed|external>
                               Piper runtime mode for generated config
  --tts-expressive-de          Also download the expressive German Piper pack
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
      --tts-backend)
        TTS_BACKEND="${2:-}"
        shift 2
        ;;
      --tts-languages)
        TTS_LANGUAGES="${2:-}"
        shift 2
        ;;
      --tts-service-mode)
        TTS_SERVICE_MODE="${2:-}"
        shift 2
        ;;
      --tts-expressive-de)
        TTS_EXPRESSIVE_DE=1
        shift
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
  printf '%s' "$(prompt_with_default "Custom wake phrase" "Robot")"
}

choose_custom_wake_model() {
  printf '%s' "$(prompt_with_default "OpenWakeWord model name or path" "/absolute/path/to/custom_model.tflite")"
}

choose_tts_backend() {
  if [[ -n "${TTS_BACKEND}" ]]; then
    printf '%s' "${TTS_BACKEND}"
    return 0
  fi

  if [[ "${ASSUME_YES}" -eq 1 ]]; then
    printf '%s' "mock"
    return 0
  fi

  if confirm "Enable local Piper TTS?"; then
    printf '%s' "piper"
    return 0
  fi

  printf '%s' "mock"
}

choose_tts_languages() {
  local default_languages="en,de,id"
  if [[ -n "${TTS_LANGUAGES}" ]]; then
    printf '%s' "${TTS_LANGUAGES}"
    return 0
  fi

  printf '%s' "$(prompt_with_default "Piper voice languages (comma-separated: en,de,id)" "${default_languages}")"
}

choose_tts_service_mode() {
  local default_mode="managed"
  if [[ -n "${TTS_SERVICE_MODE}" ]]; then
    printf '%s' "${TTS_SERVICE_MODE}"
    return 0
  fi

  printf '%s' "$(prompt_with_default "Piper service mode (managed, external)" "${default_mode}")"
}

choose_tts_expressive_de() {
  if [[ "${TTS_EXPRESSIVE_DE}" -eq 1 ]]; then
    printf '%s' "true"
    return 0
  fi

  if [[ "${ASSUME_YES}" -eq 1 ]]; then
    printf '%s' "false"
    return 0
  fi

  if confirm "Also provision the expressive German Piper voice pack?"; then
    printf '%s' "true"
    return 0
  fi

  printf '%s' "false"
}

choose_cloud_ai_mode() {
  if [[ "${ASSUME_YES}" -eq 1 ]]; then
    printf '%s' "mock"
    return 0
  fi

  if confirm "Enable the real OpenAI backend for cloud replies and tool use?"; then
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
  local tts_backend="$2"
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
      "requests<3,>=2.0" \
      "tqdm<5.0,>=4.0" \
      "scipy<2,>=1.3" \
      "scikit-learn<2,>=1"
    if [[ "${tts_backend}" == "piper" ]]; then
      "${REPO_DIR}/.venv/bin/python" -m pip install "piper-tts[http]>=1.4,<2"
    fi
    "${REPO_DIR}/.venv/bin/python" -m pip install --no-deps "openwakeword>=0.6,<0.7"
    "${REPO_DIR}/.venv/bin/python" -m pip install --no-deps -e "${REPO_DIR}"
    return 0
  fi
  if [[ "${tts_backend}" == "piper" ]]; then
    "${REPO_DIR}/.venv/bin/python" -m pip install -e "${REPO_DIR}[dev,tts]"
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

download_piper_voice() {
  local voice_name="$1"
  mkdir -p "${PIPER_VOICE_DIR}"
  log "downloading Piper voice ${voice_name}"
  if ! "${REPO_DIR}/.venv/bin/python" -m piper.download_voices "${voice_name}" --data-dir "${PIPER_VOICE_DIR}"; then
    log "retrying Piper voice download for ${voice_name} from ${PIPER_VOICE_DIR}"
    (
      cd "${PIPER_VOICE_DIR}"
      "${REPO_DIR}/.venv/bin/python" -m piper.download_voices "${voice_name}"
    )
  fi
}

provision_piper_voices() {
  local selected_languages="$1"
  local expressive_de_enabled="$2"
  IFS=',' read -r -a languages <<< "${selected_languages}"
  for language_code in "${languages[@]}"; do
    case "$(printf '%s' "${language_code}" | tr '[:upper:]' '[:lower:]' | xargs)" in
      en)
        download_piper_voice "en_US-hfc_female-medium"
        ;;
      de)
        download_piper_voice "de_DE-thorsten-medium"
        ;;
      id)
        download_piper_voice "id_ID-news_tts-medium"
        ;;
      "")
        ;;
      *)
        fail "unsupported Piper voice language '${language_code}'"
        ;;
    esac
  done

  if [[ "${expressive_de_enabled}" == "true" ]]; then
    download_piper_voice "de_DE-thorsten_emotional-medium"
  fi
}

verify_piper_setup() {
  local service_mode="$1"
  if [[ "${service_mode}" != "managed" ]]; then
    log "skipping Piper startup verification for external service mode"
    return 0
  fi

  local log_file="${REPO_DIR}/logs/piper-setup.log"
  mkdir -p "$(dirname "${log_file}")"
  log "verifying Piper startup and installed voices"
  "${REPO_DIR}/.venv/bin/python" -m piper.http_server \
    -m "en_US-hfc_female-medium" \
    --host "127.0.0.1" \
    --port "5001" \
    --data-dir "${PIPER_VOICE_DIR}" \
    >"${log_file}" 2>&1 &
  local server_pid=$!

  local healthy=0
  for _attempt in $(seq 1 40); do
    if curl -fsS "http://127.0.0.1:5001/voices" >/dev/null 2>&1; then
      healthy=1
      break
    fi
    sleep 0.25
  done

  kill "${server_pid}" >/dev/null 2>&1 || true
  wait "${server_pid}" >/dev/null 2>&1 || true

  if [[ "${healthy}" -ne 1 ]]; then
    fail "Piper verification failed; see ${log_file}"
  fi
}

write_env_file() {
  local selected_model="$1"
  local selected_language_mode="$2"
  local wake_setup="$3"
  local wake_phrase="$4"
  local wake_model="$5"
  local cloud_ai_mode="$6"
  local openai_api_key="$7"
  local tts_backend="$8"
  local tts_languages="$9"
  local tts_service_mode="${10}"
  local tts_expressive_de="${11}"
  local whisper_binary="${WHISPER_REPO_DIR}/build/bin/whisper-cli"
  local whisper_model="${WHISPER_REPO_DIR}/models/ggml-${selected_model}.bin"
  local audio_command
  local playback_command

  if [[ "${PLATFORM}" == "macos" ]]; then
    audio_command="rec -q -c 1 -r 16000 -b 16 -e signed-integer -t raw {output_path}"
    playback_command="afplay {input_path}"
  else
    audio_command='arecord -t raw -f S16_LE -r 16000 -c 1 {output_path}'
    playback_command='aplay {input_path}'
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
AI_COMPANION_OPENAI_RESPONSE_MODEL=gpt-5.2
AI_COMPANION_OPENAI_TIMEOUT_SECONDS=20
AI_COMPANION_OPENAI_REPLY_MAX_OUTPUT_TOKENS=120
AI_COMPANION_USE_MOCK_TTS=$([[ "${tts_backend}" == "mock" ]] && printf '%s' "true" || printf '%s' "false")
AI_COMPANION_TTS_BACKEND=${tts_backend}
AI_COMPANION_TTS_PIPER_BASE_URL=http://127.0.0.1:5001
AI_COMPANION_TTS_PIPER_SERVICE_MODE=${tts_service_mode}
AI_COMPANION_TTS_PIPER_DATA_DIR=${PIPER_VOICE_DIR}
AI_COMPANION_TTS_PIPER_COMMAND=
AI_COMPANION_TTS_DEFAULT_VOICE_EN=en_US-hfc_female-medium
AI_COMPANION_TTS_DEFAULT_VOICE_DE=de_DE-thorsten-medium
AI_COMPANION_TTS_DEFAULT_VOICE_ID=id_ID-news_tts-medium
AI_COMPANION_TTS_EXPRESSIVE_DE_VOICE=de_DE-thorsten_emotional-medium
AI_COMPANION_TTS_EXPRESSIVE_DE_ENABLED=${tts_expressive_de}
AI_COMPANION_TTS_AUDIO_PLAY_COMMAND=${playback_command}
AI_COMPANION_TTS_QUEUE_MAX=4
AI_COMPANION_TTS_SAVE_ARTIFACTS=false
AI_COMPANION_TTS_SYNTHESIS_TIMEOUT_SECONDS=20
AI_COMPANION_TTS_PLAYBACK_TIMEOUT_SECONDS=60
AI_COMPANION_WHISPER_BINARY_PATH=${whisper_binary}
AI_COMPANION_WHISPER_MODEL_PATH=${whisper_model}
AI_COMPANION_AUDIO_RECORD_COMMAND=${audio_command}
AI_COMPANION_SPEECH_LATENCY_PROFILE=fast
AI_COMPANION_SPEECH_SILENCE_SECONDS=0.55
AI_COMPANION_VAD_THRESHOLD=0.45
AI_COMPANION_VAD_FRAME_MS=30
AI_COMPANION_VAD_START_TRIGGER_FRAMES=2
AI_COMPANION_VAD_END_TRIGGER_FRAMES=4
AI_COMPANION_MAX_RECORDING_SECONDS=15
AI_COMPANION_WAKE_WORD_ENABLED=$([[ "${wake_setup}" == "off" ]] && printf '%s' "false" || printf '%s' "true")
AI_COMPANION_FOLLOW_UP_MODE_ENABLED=true
AI_COMPANION_FOLLOW_UP_LISTEN_TIMEOUT_SECONDS=3.0
AI_COMPANION_FOLLOW_UP_MAX_TURNS=10
AI_COMPANION_WAKE_WORD_PHRASE=${wake_phrase}
AI_COMPANION_WAKE_WORD_MODEL=${wake_model}
AI_COMPANION_WAKE_WORD_THRESHOLD=0.5
AI_COMPANION_WAKE_LOOKBACK_SECONDS=0.5
AI_COMPANION_UTTERANCE_FINALIZE_TIMEOUT_SECONDS=0.25
AI_COMPANION_UTTERANCE_TAIL_STABLE_POLLS=1
AI_COMPANION_LANGUAGE_MODE=${selected_language_mode}
# Provisioned Piper voice languages: ${tts_languages}
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

  local selected_tts_backend
  selected_tts_backend="$(choose_tts_backend)"
  case "${selected_tts_backend}" in
    mock|piper) ;;
    *) fail "unsupported TTS backend '${selected_tts_backend}'" ;;
  esac

  local selected_tts_languages="en,de,id"
  local selected_tts_service_mode="managed"
  local selected_tts_expressive_de="false"
  if [[ "${selected_tts_backend}" == "piper" ]]; then
    selected_tts_languages="$(choose_tts_languages)"
    selected_tts_service_mode="$(choose_tts_service_mode)"
    selected_tts_expressive_de="$(choose_tts_expressive_de)"
    case "${selected_tts_service_mode}" in
      managed|external) ;;
      *) fail "unsupported Piper service mode '${selected_tts_service_mode}'" ;;
    esac
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

  create_virtualenv "${python_cmd}" "${selected_tts_backend}"
  prepare_openwakeword_runtime_support
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
  if [[ "${selected_tts_backend}" == "piper" ]]; then
    provision_piper_voices "${selected_tts_languages}" "${selected_tts_expressive_de}"
    verify_piper_setup "${selected_tts_service_mode}"
  fi
  write_env_file \
    "${selected_model}" \
    "${selected_language_mode}" \
    "${wake_setup}" \
    "${wake_phrase}" \
    "${wake_model}" \
    "${cloud_ai_mode}" \
    "${openai_api_key}" \
    "${selected_tts_backend}" \
    "${selected_tts_languages}" \
    "${selected_tts_service_mode}" \
    "${selected_tts_expressive_de}"
  run_verification

  cat <<EOF

Setup complete.

Generated config: ${ENV_FILE}
Whisper binary: ${WHISPER_REPO_DIR}/build/bin/whisper-cli
Whisper model: ${WHISPER_REPO_DIR}/models/ggml-${selected_model}.bin
TTS backend: ${selected_tts_backend}

Next steps:
  1. Run the app with: .venv/bin/python src/main.py
  2. In interactive speech mode, press Enter to start speaking, type a phrase directly, or use the configured wake word
  3. Edit ${ENV_FILE} if you want to adjust model, language mode, VAD endpoint timing, wake-word settings, TTS voices/settings, recorder settings, or add an OpenAI API key later
EOF
}

main "$@"
