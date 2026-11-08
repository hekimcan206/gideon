"""Versioned, typed WebSocket protocol shared by the backend and the Electron UI.

Every frame is one JSON object with a protocol version ``v`` and a ``type`` discriminator. The pydantic models
below are the single source of truth: ``python -m gideon export-schema`` writes ``protocol/schema.json`` and the UI
generates its TypeScript types from that file, so a field rename breaks the UI build instead of failing silently
at runtime. CI regenerates both and fails on drift.

Compatibility rule: adding an optional field is a minor change and keeps ``PROTOCOL_VERSION``; removing or
renaming a field, or changing its meaning, bumps it. Peers reject frames from another major version.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

PROTOCOL_VERSION: Literal[1] = 1


class AssistantState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


class Outcome(StrEnum):
    OK = "ok"
    NO_SPEECH = "no_speech"
    EMPTY_TRANSCRIPT = "empty_transcript"
    BARGE_IN = "barge_in"
    CANCELLED = "cancelled"
    ERROR = "error"


class _Frame(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    v: Literal[1] = PROTOCOL_VERSION


# ---------------------------------------------------------------------------------------------------------------
# Server -> client
# ---------------------------------------------------------------------------------------------------------------


class EngineInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    wake: str
    vad: str
    stt: str
    tts: str
    audio: str


class HelloServer(_Frame):
    type: Literal["hello"] = "hello"
    server: str = "gideon"
    version: str
    state: AssistantState
    engines: EngineInfo
    skills: list[str]


class StateChanged(_Frame):
    type: Literal["state"] = "state"
    state: AssistantState
    previous: AssistantState
    interaction_id: str | None = None


class WakeDetected(_Frame):
    type: Literal["wake"] = "wake"
    interaction_id: str
    model: str
    score: float = Field(ge=0.0, le=1.0)
    barge_in: bool = False


class TranscriptMessage(_Frame):
    type: Literal["transcript"] = "transcript"
    interaction_id: str
    text: str
    language: str
    source: Literal["speech", "typed"] = "speech"


class ResponseMessage(_Frame):
    type: Literal["response"] = "response"
    interaction_id: str
    skill: str
    intent: str
    text: str
    language: str


class StageSpan(BaseModel):
    """One bar of the latency waterfall, relative to the first mark of the interaction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: str
    start_ms: float = Field(ge=0.0)
    duration_ms: float = Field(ge=0.0)


class TraceMessage(_Frame):
    type: Literal["trace"] = "trace"
    interaction_id: str
    outcome: Outcome
    stages: list[StageSpan]
    marks_ms: dict[str, float]
    wake_to_first_audio_ms: float | None = None
    speech_end_to_first_audio_ms: float | None = None


class NotificationMessage(_Frame):
    type: Literal["notification"] = "notification"
    text: str
    language: str


class ErrorMessage(_Frame):
    type: Literal["error"] = "error"
    code: str
    message: str
    interaction_id: str | None = None


class Pong(_Frame):
    type: Literal["pong"] = "pong"
    nonce: int


ServerMessage = Annotated[
    HelloServer
    | StateChanged
    | WakeDetected
    | TranscriptMessage
    | ResponseMessage
    | TraceMessage
    | NotificationMessage
    | ErrorMessage
    | Pong,
    Field(discriminator="type"),
]

# ---------------------------------------------------------------------------------------------------------------
# Client -> server
# ---------------------------------------------------------------------------------------------------------------


class HelloClient(_Frame):
    type: Literal["hello"] = "hello"
    client: str = Field(min_length=1, max_length=64)
    token: str | None = Field(default=None, max_length=256)


class PushToTalk(_Frame):
    """Start listening without the wake word (UI button or hotkey)."""

    type: Literal["ptt"] = "ptt"


class Cancel(_Frame):
    """Stop speaking/thinking now (manual barge-in)."""

    type: Literal["cancel"] = "cancel"


class Say(_Frame):
    """A typed command: skips wake word, VAD and STT, runs the same skills and TTS."""

    type: Literal["say"] = "say"
    text: str = Field(min_length=1, max_length=500)
    language: Literal["en", "tr"] | None = None


class Ping(_Frame):
    type: Literal["ping"] = "ping"
    nonce: int


ClientMessage = Annotated[
    HelloClient | PushToTalk | Cancel | Say | Ping,
    Field(discriminator="type"),
]

SERVER_ADAPTER: TypeAdapter[ServerMessage] = TypeAdapter(ServerMessage)
CLIENT_ADAPTER: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


class ProtocolError(ValueError):
    """A frame that is not valid JSON, has the wrong version, or does not match the schema."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def parse_client(raw: str | bytes) -> ClientMessage:
    """Parse one client frame; version mismatches get their own error code so the UI can say "update me"."""
    import json

    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProtocolError("bad_json", "frame is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ProtocolError("bad_frame", "frame must be a JSON object")
    if data.get("v") != PROTOCOL_VERSION:
        raise ProtocolError(
            "version_mismatch", f"expected protocol v{PROTOCOL_VERSION}, got {data.get('v')!r}"
        )
    try:
        return CLIENT_ADAPTER.validate_python(data)
    except ValueError as exc:
        raise ProtocolError("invalid_frame", _short_error(exc)) from exc


def dump_server(message: BaseModel) -> str:
    return message.model_dump_json(exclude_none=True)


def json_schema() -> dict[str, Any]:
    """One schema document with both directions, consumed by json-schema-to-typescript."""
    server = SERVER_ADAPTER.json_schema(ref_template="#/$defs/{model}")
    client = CLIENT_ADAPTER.json_schema(ref_template="#/$defs/{model}")
    defs: dict[str, Any] = {**server.pop("$defs", {}), **client.pop("$defs", {})}
    _tidy(defs)
    defs["ServerMessage"] = server
    defs["ClientMessage"] = client
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://github.com/hekimcan206/gideon/protocol/v{PROTOCOL_VERSION}",
        "title": "GideonProtocol",
        "description": f"GIDEON WebSocket protocol v{PROTOCOL_VERSION}. Generated from gideon/protocol.py.",
        "type": "object",
        "properties": {
            "server": {"$ref": "#/$defs/ServerMessage"},
            "client": {"$ref": "#/$defs/ClientMessage"},
        },
        "$defs": dict(sorted(defs.items())),
    }


def _tidy(defs: dict[str, Any]) -> None:
    """Make the schema describe the wire format precisely, for clean generated TypeScript.

    * a field with a non-null default (``v``, ``type``, ``barge_in`` ...) is always serialised, so it is *required*
      on the wire; without this the generated union could not narrow on ``type``;
    * per-property ``title``s are dropped so the generator does not emit an alias type for every field.
    """
    for definition in defs.values():
        props = definition.get("properties")
        if not props:
            continue
        required = set(definition.get("required", []))
        for name, prop in props.items():
            prop.pop("title", None)
            if prop.get("default") is not None:
                required.add(name)
        definition["required"] = [name for name in props if name in required]


def _short_error(exc: ValueError) -> str:
    text = str(exc).splitlines()
    return " ".join(line.strip() for line in text[:3])[:300]
