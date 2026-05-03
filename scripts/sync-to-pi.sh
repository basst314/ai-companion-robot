#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/sync-to-pi.sh --host <hostname> --user <username> [options]

Sync the current repo checkout to a Raspberry Pi without copying local machine state.

Options:
  --host <hostname>              Pi hostname or IP address
  --user <username>              SSH username
  --target-dir <path>            Target directory on the Pi (default: ~/ai-companion-robot)
  --port <port>                  SSH port (default: 22)
  --env-file <path>              Copy this local env file to <target-dir>/.env.local
                                 (default: .env.local.rpi when present)
  --no-env-file                  Do not copy any env file
  --copy-wake-model <path>       Copy a custom wake-word model into artifacts/openwakeword/models/
  --help                         Show this help
EOF
}

fail() {
  printf '[sync-to-pi] ERROR: %s\n' "$1" >&2
  exit 1
}

log() {
  printf '[sync-to-pi] %s\n' "$1"
}

HOST=""
USER_NAME=""
TARGET_DIR="~/ai-companion-robot"
PORT="22"
ENV_FILE_PATH=""
ENV_FILE_EXPLICIT=0
SKIP_ENV_FILE=0
WAKE_MODEL_PATH=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      HOST="${2:-}"
      shift 2
      ;;
    --user)
      USER_NAME="${2:-}"
      shift 2
      ;;
    --target-dir)
      TARGET_DIR="${2:-}"
      shift 2
      ;;
    --port)
      PORT="${2:-}"
      shift 2
      ;;
    --env-file)
      ENV_FILE_PATH="${2:-}"
      ENV_FILE_EXPLICIT=1
      shift 2
      ;;
    --no-env-file)
      SKIP_ENV_FILE=1
      shift
      ;;
    --copy-wake-model)
      WAKE_MODEL_PATH="${2:-}"
      shift 2
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

[[ -n "${HOST}" ]] || fail "--host is required"
[[ -n "${USER_NAME}" ]] || fail "--user is required"

command -v rsync >/dev/null 2>&1 || fail "rsync is required"
command -v ssh >/dev/null 2>&1 || fail "ssh is required"

if [[ -n "${WAKE_MODEL_PATH}" ]] && [[ ! -f "${WAKE_MODEL_PATH}" ]]; then
  fail "wake-word model file not found: ${WAKE_MODEL_PATH}"
fi
if [[ "${SKIP_ENV_FILE}" -eq 0 && "${ENV_FILE_EXPLICIT}" -eq 0 && -f ".env.local.rpi" ]]; then
  ENV_FILE_PATH=".env.local.rpi"
fi
if [[ -n "${ENV_FILE_PATH}" ]] && [[ ! -f "${ENV_FILE_PATH}" ]]; then
  fail "env file not found: ${ENV_FILE_PATH}"
fi

REMOTE="${USER_NAME}@${HOST}"
SSH_CMD=(ssh -p "${PORT}")

log "ensuring target directory exists on ${REMOTE}:${TARGET_DIR}"
"${SSH_CMD[@]}" "${REMOTE}" "mkdir -p ${TARGET_DIR}"

log "syncing repository to ${REMOTE}:${TARGET_DIR}"
rsync -az --delete \
  -e "ssh -p ${PORT}" \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '.pytest_cache/' \
  --exclude '__pycache__/' \
  --exclude '.mypy_cache/' \
  --exclude '.DS_Store' \
  --exclude '.env' \
  --exclude '.env.local' \
  --exclude 'artifacts/' \
  --exclude 'external/' \
  --exclude 'logs/' \
  ./ "${REMOTE}:${TARGET_DIR}/"

if [[ -n "${ENV_FILE_PATH}" ]]; then
  log "copying env file to ${REMOTE}:${TARGET_DIR}/.env.local"
  rsync -az -e "ssh -p ${PORT}" "${ENV_FILE_PATH}" "${REMOTE}:${TARGET_DIR}/.env.local"
fi

if [[ -n "${WAKE_MODEL_PATH}" ]]; then
  REMOTE_MODEL_DIR="${TARGET_DIR}/artifacts/openwakeword/models"
  REMOTE_MODEL_PATH="${REMOTE_MODEL_DIR}/$(basename "${WAKE_MODEL_PATH}")"
  log "copying custom wake-word model to ${REMOTE}:${REMOTE_MODEL_PATH}"
  "${SSH_CMD[@]}" "${REMOTE}" "mkdir -p ${REMOTE_MODEL_DIR}"
  rsync -az -e "ssh -p ${PORT}" "${WAKE_MODEL_PATH}" "${REMOTE}:${REMOTE_MODEL_PATH}"
fi

log "sync complete"
