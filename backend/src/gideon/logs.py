"""Structured JSON logging on the standard library (one JSON object per line, no extra dependency).

Call sites pass structured data as ``extra={"fields": {...}}``; every line carries a monotonic timestamp so log
lines can be joined with the JSONL latency trace, which uses the same clock.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "mono_ns": time.monotonic_ns(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure(level: str = "INFO", *, json_output: bool = True) -> None:
    handler = logging.StreamHandler()
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("websockets", "faster_whisper", "httpx", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
