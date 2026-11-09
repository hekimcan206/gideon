#!/usr/bin/env bash
# Start the Electron UI; it spawns the Python backend from backend/.venv with a random per-launch token.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export GIDEON_SPAWN_BACKEND=1
export GIDEON_PYTHON="${GIDEON_PYTHON:-$ROOT/backend/.venv/bin/python}"
export GIDEON_BACKEND_DIR="$ROOT/backend"
export GIDEON_AUDIO="${GIDEON_AUDIO:-mic}"
cd "$ROOT/ui"
exec npx electron .
