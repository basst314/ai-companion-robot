#!/usr/bin/env bash

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_SMOKE_MAIN=0
MAIN_TIMEOUT_SECONDS=15

usage() {
  cat <<'EOF'
Usage: scripts/validate-rpi-runtime.sh [options]

Run staged Raspberry Pi validation checks for ai-companion-robot.

Options:
  --smoke-main                  Start src/main.py briefly to catch startup/config errors
  --main-timeout <seconds>      Timeout for --smoke-main (default: 15)
  --help                        Show this help
EOF
}

log() {
  printf '[validate-rpi] %s\n' "$1"
}

fail() {
  printf '[validate-rpi] ERROR: %s\n' "$1" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --smoke-main)
      RUN_SMOKE_MAIN=1
      shift
      ;;
    --main-timeout)
      MAIN_TIMEOUT_SECONDS="${2:-}"
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

cd "${REPO_DIR}"

[[ -x ".venv/bin/python" ]] || fail "missing .venv/bin/python. Run ./scripts/setup.sh --platform rpi first."
[[ -f ".env.local" ]] || fail "missing .env.local. Run ./scripts/setup.sh --platform rpi first."

command -v arecord >/dev/null 2>&1 || fail "arecord is not installed"
command -v aplay >/dev/null 2>&1 || fail "aplay is not installed"

log "listing microphone devices"
arecord -l || true

log "listing playback devices"
aplay -l || true

log "running test suite"
.venv/bin/pytest -q

if [[ "${RUN_SMOKE_MAIN}" -eq 1 ]]; then
  if command -v timeout >/dev/null 2>&1; then
    log "running short startup smoke test for src/main.py without requiring a live microphone"
    AI_COMPANION_WAKE_WORD_ENABLED=false \
      AI_COMPANION_INPUT_MODE=text \
      timeout "${MAIN_TIMEOUT_SECONDS}" .venv/bin/python src/main.py || status=$?
    if [[ "${status:-0}" -ne 0 ]] && [[ "${status:-0}" -ne 124 ]]; then
      fail "src/main.py exited with status ${status}"
    fi
  else
    log "skipping --smoke-main because timeout is unavailable on this system"
  fi
fi

log "validation completed"
