# syntax=docker/dockerfile:1.7
# GIDEON backend. Targets: runtime (engines, non-root, models baked in) and dev (adds test tooling).
FROM python:3.12-slim AS deps
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore
WORKDIR /app
COPY pyproject.toml ./
# Dependencies first (cached layer), read straight from pyproject so versions live in one place.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install $(python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; print(' '.join(p['dependencies'] + p['optional-dependencies']['engines']))") \
 # openwakeword's metadata requires tflite-runtime on Linux (no CPython 3.12 wheels); GIDEON uses its ONNX backend.
 && pip install --no-deps openwakeword==0.6.0

FROM deps AS models
ARG WHISPER_MODEL=base
ENV HF_HOME=/models/hf
# Bake the models into the image: the container starts offline-capable and the first request is not a download.
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('${WHISPER_MODEL}', device='cpu', compute_type='int8')" \
 && python -c "import openwakeword.utils as u; u.download_models(['hey_jarvis'])"

FROM models AS runtime
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/pip pip install --no-deps . \
 && useradd --uid 10001 --create-home --shell /usr/sbin/nologin gideon \
 && mkdir -p /data /fixtures && chown gideon:gideon /data /fixtures
# HF_HUB_OFFLINE: use the baked model only (the root filesystem is read-only in compose; no hub calls at start).
ENV GIDEON_HOST=0.0.0.0 GIDEON_DATA_DIR=/data GIDEON_WAV_DIR=/fixtures GIDEON_AUDIO=wav GIDEON_APPS_DRY_RUN=1 \
    HF_HUB_OFFLINE=1
USER 10001
EXPOSE 18765
HEALTHCHECK --interval=10s --timeout=3s --start-period=60s --retries=6 \
  CMD python -c "import socket; socket.create_connection(('127.0.0.1', 18765), 2).close()"
ENTRYPOINT ["python", "-m", "gideon"]
CMD ["serve"]

FROM models AS dev
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install $(python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; print(' '.join(p['optional-dependencies']['dev']))")
COPY . .
RUN pip install --no-deps -e .
ENTRYPOINT []
CMD ["pytest"]
