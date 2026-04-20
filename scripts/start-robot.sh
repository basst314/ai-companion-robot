#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: scripts/start-robot.sh [options]

Start the robot app with the browser-backed face renderer.

Options:
  --help    Show this help
EOF
}

case "${1:-}" in
  --help|-h)
    usage
    exit 0
    ;;
esac

if [[ ! -f "${REPO_DIR}/.venv/bin/python" ]]; then
  printf '[start-robot] ERROR: missing .venv/bin/python. Run ./scripts/setup.sh first.\n' >&2
  exit 1
fi

cleanup_previous_run() {
  local user_name
  user_name="$(id -un)"
  if command -v pkill >/dev/null 2>&1; then
    pkill -u "${user_name}" -f "src/main.py" >/dev/null 2>&1 || true
    pkill -u "${user_name}" -f "chromium-browser" >/dev/null 2>&1 || true
    pkill -u "${user_name}" -f "\bchromium\b" >/dev/null 2>&1 || true
    pkill -u "${user_name}" -f "google-chrome" >/dev/null 2>&1 || true
  fi
}

cleanup_previous_run

has_capture_device() {
  if command -v arecord >/dev/null 2>&1 && arecord -l 2>/dev/null | grep -q '^card '; then
    return 0
  fi
  if command -v pw-dump >/dev/null 2>&1 && pw-dump 2>/dev/null | grep -q '"media.class": "Audio/Source"'; then
    return 0
  else
    return 1
  fi
}

launch_robot() {
  local -a runtime_env
  local browser_profile_dir="/tmp/ai-companion-robot/chromium-profile"
  if command -v wtype >/dev/null 2>&1; then
    (
      sleep 2
      wtype -k F12 >/dev/null 2>&1 || true
    ) &
  fi
  runtime_env=(
    "AI_COMPANION_INTERACTIVE_CONSOLE=true"
    "AI_COMPANION_UI_BROWSER_PROFILE_DIR=${AI_COMPANION_UI_BROWSER_PROFILE_DIR:-${browser_profile_dir}}"
    "AI_COMPANION_UI_BROWSER_EXTRA_ARGS=${AI_COMPANION_UI_BROWSER_EXTRA_ARGS:---ozone-platform=wayland --password-store=basic --use-mock-keychain}"
  )
  if ! has_capture_device; then
    printf '[start-robot] WARNING: no audio capture source found; falling back to manual mode and disabling wake-word.\n' >&2
    runtime_env+=(
      "AI_COMPANION_INPUT_MODE=manual"
      "AI_COMPANION_WAKE_WORD_ENABLED=false"
    )
  fi
  exec env "$@" "${runtime_env[@]}" "${REPO_DIR}/.venv/bin/python" "${REPO_DIR}/src/main.py"
}

if [[ -n "${XDG_RUNTIME_DIR:-}" && -n "${WAYLAND_DISPLAY:-}" ]]; then
  launch_robot \
    XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR}" \
    WAYLAND_DISPLAY="${WAYLAND_DISPLAY}"
fi

if [[ -f "/run/user/1000/wayland-0" ]] || [[ -S "/run/user/1000/wayland-0" ]]; then
  launch_robot \
    XDG_RUNTIME_DIR="/run/user/1000" \
    WAYLAND_DISPLAY="wayland-0"
fi

launch_robot
