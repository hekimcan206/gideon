#!/usr/bin/env bash
# GIDEON installer for Linux and macOS: backend virtualenv + speech engines + models, then the Electron UI.
#   scripts/install.sh            # backend (engines + live audio) and UI
#   scripts/install.sh --dev      # also test tooling (pytest, ruff, mypy)
#   scripts/install.sh --no-ui    # backend only
#   scripts/install.sh --no-models
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEV=0; UI=1; MODELS=1
for arg in "$@"; do
  case "$arg" in
    --dev) DEV=1 ;;
    --no-ui) UI=0 ;;
    --no-models) MODELS=0 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$*"; }

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3.12 python3.13 python3; do
    if command -v "$candidate" >/dev/null 2>&1 \
      && "$candidate" -c 'import sys; sys.exit(0 if (3, 12) <= sys.version_info[:2] < (3, 14) else 1)'; then
      PYTHON="$candidate"; break
    fi
  done
fi
[ -n "$PYTHON" ] || { echo "Python 3.12 or 3.13 is required (set PYTHON=/path/to/python)." >&2; exit 1; }
step "Using $("$PYTHON" --version) at $(command -v "$PYTHON")"

VENV="$ROOT/backend/.venv"
step "Creating virtual environment in backend/.venv"
"$PYTHON" -m venv "$VENV"
PY="$VENV/bin/python"
"$PY" -m pip install --quiet --upgrade pip

EXTRAS="engines,live"
if [ "$DEV" = 1 ]; then EXTRAS="$EXTRAS,dev"; fi
step "Installing the backend with extras [$EXTRAS]"
"$PY" -m pip install --quiet -e "$ROOT/backend[$EXTRAS]"
# openwakeword's metadata pins tflite-runtime on Linux, which has no wheels for Python 3.12+. GIDEON uses the
# ONNX backend; the dependencies openwakeword really imports are in the [engines] extra.
"$PY" -m pip install --quiet --no-deps openwakeword==0.6.0

if [ "$(uname -s)" = "Linux" ] && ! ldconfig -p 2>/dev/null | grep -q libportaudio; then
  echo "note: live microphone mode needs PortAudio (Debian/Ubuntu: sudo apt install libportaudio2)."
fi

if [ "$MODELS" = 1 ]; then
  step "Downloading models (openWakeWord hey_jarvis, faster-whisper ${GIDEON_STT_MODEL:-base})"
  "$PY" -c "import openwakeword.utils as u; u.download_models(['hey_jarvis'])"
  "$PY" -c "import os; from faster_whisper import WhisperModel; WhisperModel(os.environ.get('GIDEON_STT_MODEL', 'base'), device='cpu', compute_type='int8')"
fi

if [ "$UI" = 1 ]; then
  command -v npm >/dev/null 2>&1 || { echo "Node.js 22.12+ is required for the UI (or use --no-ui)." >&2; exit 1; }
  step "Installing the UI (Electron, Vite) and building it"
  (cd "$ROOT/ui" && npm ci --no-audit --no-fund && npm run build)
fi

step "Done"
cat <<MSG
Run the desktop app (Electron starts the backend with a random per-launch token):
  scripts/start.sh
Backend only, with a microphone (from backend/):
  GIDEON_AUDIO=mic .venv/bin/python -m gideon serve
No microphone? Generate synthetic utterances and replay them (from backend/):
  .venv/bin/python -m gideon fixtures && GIDEON_AUDIO=wav .venv/bin/python -m gideon serve
MSG
