#!/usr/bin/env bash

# https://wiki.seeedstudio.com/respeaker_mic_array_v3.0/#faq

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TUNING_DIR="${REPO_DIR}/external/usb_4_mic_array"
TUNING_PY="${TUNING_DIR}/tuning.py"
PYTHON_BIN="${TUNING_DIR}/venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" || ! -f "${TUNING_PY}" ]]; then
  printf 'ReSpeaker tuning repo is not ready. Run ./scripts/setup.sh --platform rpi first.\n' >&2
  exit 1
fi

set_param() {
  sudo "${PYTHON_BIN}" "${TUNING_PY}" "$1" "$2"
}

set_param GAMMA_E 3
set_param GAMMA_ETAIL 3
set_param GAMMA_ENL 5
set_param AGCMAXGAIN 3
set_param GAMMA_NN 3
set_param GAMMA_NN_SR 3
set_param GAMMA_NS 3
set_param GAMMA_NS_SR 3
