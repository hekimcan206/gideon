#!/usr/bin/env bash
# Run a command in the backend dev image (docker build --target dev -t gideon-backend:dev backend) with the
# repository mounted, e.g. scripts/dev-docker.sh pytest -q. For machines without a local Python.
# Models live in the gideon-models volume (seeded from the image); HF_HUB_OFFLINE=0 allows downloading more.
set -euo pipefail
cd "$(dirname "$0")/.."
MSYS_NO_PATHCONV=1 docker run --rm -i -v "$(pwd -W 2>/dev/null || pwd):/repo" -v gideon-models:/models/hf \
  -e PYTHONPATH=/repo/backend/src -e HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}" -w /repo/backend ${DOCKER_ARGS:-} gideon-backend:dev "$@"
