"""WebSocket server: pushes pipeline events to UI clients and accepts their commands.

Security posture (see docs/threat-model.md): binds to loopback by default; rejects browser connections whose
``Origin`` is not on the allow-list (any web page the user visits could otherwise open ``ws://127.0.0.1``);
optional shared token in the client ``hello``; 64 KiB frame limit; every inbound frame is schema-validated and a
bad frame produces an ``error`` reply, never an exception in the pipeline.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
from collections.abc import Sequence
from http import HTTPStatus

from pydantic import BaseModel
from websockets.asyncio.server import Server, ServerConnection, broadcast, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from gideon import __version__
from gideon import protocol as p
from gideon.pipeline import Assistant

log = logging.getLogger(__name__)

HELLO_TIMEOUT_S = 5.0
MAX_FRAME_BYTES = 64 * 1024


class Hub:
    """Fan-out of server messages to every authenticated client."""

    def __init__(self) -> None:
        self.clients: set[ServerConnection] = set()
        self.sent: list[BaseModel] = []  # bounded history, handy for diagnostics and tests
        self._history = 200

    def emit(self, message: BaseModel) -> None:
        self.sent.append(message)
        if len(self.sent) > self._history:
            del self.sent[: len(self.sent) - self._history]
        if self.clients:
            broadcast(self.clients, p.dump_server(message))


class GideonServer:
    def __init__(
        self,
        assistant: Assistant,
        hub: Hub,
        *,
        host: str = "127.0.0.1",
        port: int = 18765,
        allowed_origins: Sequence[str] = (),
        token: str | None = None,
    ) -> None:
        self.assistant = assistant
        self.hub = hub
        self.host = host
        self.port = port
        self.allowed_origins = frozenset(allowed_origins)
        self.token = token or None
        self._server: Server | None = None

    async def start(self) -> Server:
        self._server = await serve(
            self._handle,
            self.host,
            self.port,
            process_request=self._check_origin,
            max_size=MAX_FRAME_BYTES,
            ping_interval=20,
            ping_timeout=20,
        )
        log.info("listening", extra={"fields": {"host": self.host, "port": self.bound_port}})
        return self._server

    @property
    def bound_port(self) -> int:
        if self._server is None:
            return self.port
        return int(next(iter(self._server.sockets)).getsockname()[1])

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    def _check_origin(self, connection: ServerConnection, request: Request) -> Response | None:
        origin = request.headers.get("Origin")
        # Browsers always send Origin; a missing header means a non-browser local client (CLI, tests), which
        # already runs with the user's privileges and gains nothing from this socket.
        if origin is not None and origin not in self.allowed_origins:
            log.warning("origin rejected", extra={"fields": {"origin": origin[:100]}})
            return connection.respond(HTTPStatus.FORBIDDEN, "origin not allowed\n")
        return None

    async def _handle(self, ws: ServerConnection) -> None:
        if not await self._handshake(ws):
            return
        self.hub.clients.add(ws)
        try:
            async for raw in ws:
                await self._on_frame(ws, raw)
        except ConnectionClosed:
            pass
        finally:
            self.hub.clients.discard(ws)

    async def _handshake(self, ws: ServerConnection) -> bool:
        try:
            raw = await asyncio.wait_for(ws.recv(), HELLO_TIMEOUT_S)
            message = p.parse_client(raw)
        except TimeoutError:
            await ws.close(4408, "hello timeout")
            return False
        except p.ProtocolError as exc:
            await self._send(ws, p.ErrorMessage(code=exc.code, message=str(exc)))
            await ws.close(4400 if exc.code != "version_mismatch" else 4426, exc.code)
            return False
        except ConnectionClosed:
            return False
        if not isinstance(message, p.HelloClient):
            await self._send(ws, p.ErrorMessage(code="hello_required", message="first frame must be hello"))
            await ws.close(4400, "hello required")
            return False
        if self.token is not None and not hmac.compare_digest(
            (message.token or "").encode(), self.token.encode()
        ):
            await self._send(ws, p.ErrorMessage(code="unauthorized", message="bad token"))
            await ws.close(4401, "unauthorized")
            return False
        await self._send(
            ws,
            p.HelloServer(
                version=__version__,
                state=self.assistant.state,
                engines=self.assistant.engines(),
                skills=self.assistant.router.skill_names,
            ),
        )
        log.info("client connected", extra={"fields": {"client": message.client}})
        return True

    async def _on_frame(self, ws: ServerConnection, raw: str | bytes) -> None:
        try:
            message = p.parse_client(raw)
        except p.ProtocolError as exc:
            await self._send(ws, p.ErrorMessage(code=exc.code, message=str(exc)))
            return
        match message:
            case p.Ping(nonce=nonce):
                await self._send(ws, p.Pong(nonce=nonce))
            case p.PushToTalk():
                await self.assistant.push_to_talk()
            case p.Cancel():
                await self.assistant.cancel()
            case p.Say(text=text, language=language):
                await self.assistant.say(text, language)
            case p.HelloClient():
                await self._send(ws, p.ErrorMessage(code="already_hello", message="hello already received"))

    @staticmethod
    async def _send(ws: ServerConnection, message: BaseModel) -> None:
        with contextlib.suppress(ConnectionClosed):
            await ws.send(p.dump_server(message))
