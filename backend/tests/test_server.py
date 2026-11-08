from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from gideon.pipeline import Assistant
from gideon.server import GideonServer, Hub
from tests.conftest import AssistantFactory

ALLOWED = "app://gideon"


@pytest.fixture
async def server(make_assistant: AssistantFactory) -> AsyncIterator[GideonServer]:
    hub = Hub()
    assistant: Assistant = make_assistant("")
    assistant.emit = hub.emit
    srv = GideonServer(assistant, hub, port=0, allowed_origins=[ALLOWED], token="s3cret")
    await srv.start()
    yield srv
    await srv.close()


def url(srv: GideonServer) -> str:
    return f"ws://127.0.0.1:{srv.bound_port}"


async def hello(ws: ClientConnection, token: str | None = "s3cret") -> dict[str, Any]:
    await ws.send(json.dumps({"v": 1, "type": "hello", "client": "pytest", "token": token}))
    return json.loads(await ws.recv())


async def recv_until(ws: ClientConnection, kind: str, limit: int = 20) -> dict[str, Any]:
    for _ in range(limit):
        message = json.loads(await asyncio.wait_for(ws.recv(), 5))
        if message["type"] == kind:
            return message
    raise AssertionError(f"no {kind} message")


async def test_handshake_returns_engines_skills_and_state(server: GideonServer) -> None:
    async with connect(url(server), origin=ALLOWED) as ws:  # type: ignore[arg-type]
        reply = await hello(ws)
    assert reply["type"] == "hello" and reply["v"] == 1
    assert reply["state"] == "idle"
    assert reply["engines"]["stt"] == "fake-stt"
    assert reply["skills"] == ["clock", "timer", "notes", "apps"]


async def test_foreign_browser_origin_is_rejected_before_the_upgrade(server: GideonServer) -> None:
    with pytest.raises(InvalidStatus) as err:
        async with connect(url(server), origin="https://evil.example"):  # type: ignore[arg-type]
            pass
    assert err.value.response.status_code == 403


async def test_bad_token_closes_with_4401(server: GideonServer) -> None:
    async with connect(url(server), origin=ALLOWED) as ws:  # type: ignore[arg-type]
        reply = await hello(ws, token="guess")
        assert reply == {"v": 1, "type": "error", "code": "unauthorized", "message": "bad token"}
        with pytest.raises(ConnectionClosed) as err:
            await ws.recv()
    assert err.value.rcvd is not None and err.value.rcvd.code == 4401


async def test_first_frame_must_be_hello_and_version_is_checked(server: GideonServer) -> None:
    async with connect(url(server)) as ws:
        await ws.send(json.dumps({"v": 1, "type": "ptt"}))
        assert json.loads(await ws.recv())["code"] == "hello_required"
    async with connect(url(server)) as ws:
        await ws.send(json.dumps({"v": 99, "type": "hello", "client": "old"}))
        assert json.loads(await ws.recv())["code"] == "version_mismatch"
        with pytest.raises(ConnectionClosed) as err:
            await ws.recv()
        assert err.value.rcvd is not None and err.value.rcvd.code == 4426


async def test_invalid_frames_get_an_error_and_the_connection_survives(server: GideonServer) -> None:
    async with connect(url(server)) as ws:
        await hello(ws)
        await ws.send("{oops")
        assert json.loads(await ws.recv())["code"] == "bad_json"
        await ws.send(json.dumps({"v": 1, "type": "say", "text": ""}))
        assert json.loads(await ws.recv())["code"] == "invalid_frame"
        await ws.send(json.dumps({"v": 1, "type": "ping", "nonce": 42}))
        assert json.loads(await ws.recv()) == {"v": 1, "type": "pong", "nonce": 42}


async def test_oversized_frames_close_the_connection(server: GideonServer) -> None:
    async with connect(url(server)) as ws:
        await hello(ws)
        await ws.send("x" * (70 * 1024))
        with pytest.raises(ConnectionClosed) as err:
            await ws.recv()
    assert err.value.rcvd is not None and err.value.rcvd.code == 1009


async def test_typed_command_is_broadcast_to_every_client(server: GideonServer) -> None:
    async with connect(url(server)) as a, connect(url(server)) as b:
        await hello(a)
        await hello(b)
        await a.send(json.dumps({"v": 1, "type": "say", "text": "saat kaç", "language": "tr"}))
        for ws in (a, b):
            response = await recv_until(ws, "response")
            assert response["text"] == "Saat 14:05." and response["language"] == "tr"
            trace = await recv_until(ws, "trace")
            assert trace["outcome"] == "ok"
            assert next(s["stage"] for s in trace["stages"]) == "route"


async def test_push_to_talk_and_cancel_drive_the_state(server: GideonServer) -> None:
    async with connect(url(server)) as ws:
        await hello(ws)
        await ws.send(json.dumps({"v": 1, "type": "ptt"}))
        assert (await recv_until(ws, "state"))["state"] == "listening"
        await ws.send(json.dumps({"v": 1, "type": "cancel"}))
        assert (await recv_until(ws, "state"))["state"] == "idle"
        assert (await recv_until(ws, "trace"))["outcome"] == "cancelled"
