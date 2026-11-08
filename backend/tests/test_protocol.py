from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from gideon import protocol as p
from gideon.protocol import AssistantState, Outcome

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "protocol" / "schema.json"


def frame(**fields: object) -> str:
    return json.dumps({"v": 1, **fields})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (frame(type="hello", client="ui"), p.HelloClient),
        (frame(type="ptt"), p.PushToTalk),
        (frame(type="cancel"), p.Cancel),
        (frame(type="say", text="saat kaç", language="tr"), p.Say),
        (frame(type="ping", nonce=7), p.Ping),
    ],
)
def test_parses_every_client_message(raw: str, expected: type) -> None:
    assert isinstance(p.parse_client(raw), expected)


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("{not json", "bad_json"),
        ("[1, 2]", "bad_frame"),
        (json.dumps({"type": "ptt"}), "version_mismatch"),
        (json.dumps({"v": 2, "type": "ptt"}), "version_mismatch"),
        (frame(type="launch_missiles"), "invalid_frame"),
        (frame(type="ptt", extra="field"), "invalid_frame"),  # extra="forbid": typos fail loudly
        (frame(type="say", text=""), "invalid_frame"),
        (frame(type="say", text="x" * 501), "invalid_frame"),
        (frame(type="say", text="hi", language="de"), "invalid_frame"),
        (frame(type="hello", client=""), "invalid_frame"),
    ],
)
def test_rejects_bad_frames_with_specific_codes(raw: str, code: str) -> None:
    with pytest.raises(p.ProtocolError) as err:
        p.parse_client(raw)
    assert err.value.code == code


def test_server_messages_round_trip_through_the_discriminated_union() -> None:
    messages = [
        p.HelloServer(version="1.0.0", state=AssistantState.IDLE, skills=["clock"],
                      engines=p.EngineInfo(wake="w", vad="v", stt="s", tts="t", audio="a")),
        p.StateChanged(state=AssistantState.LISTENING, previous=AssistantState.IDLE, interaction_id="abc"),
        p.WakeDetected(interaction_id="abc", model="hey_jarvis", score=0.93),
        p.TranscriptMessage(interaction_id="abc", text="what time is it", language="en"),
        p.ResponseMessage(interaction_id="abc", skill="clock", intent="time.now", text="It's 14:05.", language="en"),
        p.TraceMessage(interaction_id="abc", outcome=Outcome.OK, marks_ms={"wake": 0.0},
                       stages=[p.StageSpan(stage="stt", start_ms=10.0, duration_ms=250.5)],
                       wake_to_first_audio_ms=1900.0, speech_end_to_first_audio_ms=1200.0),
        p.NotificationMessage(text="Your timer is done.", language="en"),
        p.ErrorMessage(code="pipeline_error", message="RuntimeError"),
        p.Pong(nonce=3),
    ]  # fmt: skip
    for message in messages:
        decoded = p.SERVER_ADAPTER.validate_json(p.dump_server(message))
        assert decoded == message


def test_dump_omits_unset_optional_fields() -> None:
    data = json.loads(p.dump_server(p.ErrorMessage(code="x", message="y")))
    assert data == {"v": 1, "type": "error", "code": "x", "message": "y"}


def test_wake_score_is_bounded() -> None:
    with pytest.raises(ValidationError):
        p.WakeDetected(interaction_id="a", model="m", score=1.5)


def test_schema_covers_both_directions() -> None:
    schema = p.json_schema()
    defs = schema["$defs"]
    server_types = {ref["$ref"].rsplit("/", 1)[1] for ref in defs["ServerMessage"]["oneOf"]}
    client_types = {ref["$ref"].rsplit("/", 1)[1] for ref in defs["ClientMessage"]["oneOf"]}
    assert {"HelloServer", "StateChanged", "TraceMessage", "Pong"} <= server_types
    assert client_types == {"HelloClient", "PushToTalk", "Cancel", "Say", "Ping"}
    assert defs["ServerMessage"]["discriminator"]["propertyName"] == "type"


def test_committed_schema_matches_the_models() -> None:
    """The UI's TypeScript types are generated from protocol/schema.json; it must never lag the models."""
    committed = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    assert committed == p.json_schema(), "run `python -m gideon export-schema` and `npm run gen:protocol`"
