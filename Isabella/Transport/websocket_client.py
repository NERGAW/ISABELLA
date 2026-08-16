"""Reusable synchronous wrapper around an asyncio ISABELLA WebSocket client."""

from __future__ import annotations

import asyncio
import threading
from time import sleep
from typing import Any

from websockets.asyncio.client import connect

from Isabella.Protocol import MAX_MESSAGE_BYTES, MessageType, NodeIdentity, ProtocolMessage, decode, encode


class WebSocketNodeClient:
    def __init__(self, uri: str, identity: NodeIdentity, *, token: str | None = None, device_identity=None, pairing: bool = False, requested_permissions=(), timeout: float = 15, max_message_size: int = MAX_MESSAGE_BYTES, available_capabilities: set[str] | None = None, authorized_events: set[str] | None = None, max_reconnect_attempts: int = 4, max_backoff_seconds: float = 8) -> None:
        self.uri = uri
        self.identity = identity
        self.token = token
        self.device_identity = device_identity
        self.pairing = pairing
        self.requested_permissions = tuple(requested_permissions)
        self.timeout = timeout
        self.max_message_size = min(max_message_size, MAX_MESSAGE_BYTES)
        self.available_capabilities = available_capabilities or set(identity.capabilities)
        self.authorized_events = authorized_events or set()
        self.max_reconnect_attempts = max_reconnect_attempts
        self.max_backoff_seconds = max_backoff_seconds
        self.reconnects = 0
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name=f"IsabellaNodeClient-{identity.node_id}", daemon=True)
        self._thread.start()
        self._socket = None
        self.welcome: ProtocolMessage | None = None

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run(self, coroutine, timeout: float | None = None):
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop).result(timeout or self.timeout)

    def connect(self) -> ProtocolMessage:
        return self._run(self._connect(), self.timeout + 1)

    async def _connect(self) -> ProtocolMessage:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
        self._socket = await asyncio.wait_for(connect(self.uri, additional_headers=headers, max_size=self.max_message_size, ping_interval=None), self.timeout)
        hello = ProtocolMessage(MessageType.HELLO, self.identity.node_id, "primary.local", {"identity": self.identity.to_dict()})
        payload = {"identity": self.identity.to_dict()}
        if self.device_identity:
            if self.pairing:
                payload["pairing"] = {"public_identity": self.device_identity.public_identity, "permissions": list(self.requested_permissions)}
            else:
                proof = f"{self.identity.node_id}|{hello.timestamp}|{hello.id}".encode()
                payload["device_auth"] = {"signature": self.device_identity.sign(proof)}
            hello = ProtocolMessage(hello.type, hello.source, hello.destination, payload, hello.id, hello.protocol_version, hello.timestamp, hello.correlation_id)
        await self._socket.send(encode(hello, max_bytes=self.max_message_size, available_capabilities=self.available_capabilities).decode())
        raw = await asyncio.wait_for(self._socket.recv(), self.timeout)
        response = decode(raw, max_bytes=self.max_message_size, available_capabilities=self.available_capabilities, authorized_events=self.authorized_events)
        if response.type is not MessageType.WELCOME:
            await self._socket.close()
            raise ConnectionError(response.payload.get("code", "WELCOME_REQUIRED"))
        self.welcome = response
        return response

    def send(self, message: ProtocolMessage) -> None:
        self._run(self._send(message))

    async def _send(self, message: ProtocolMessage) -> None:
        if self._socket is None:
            raise ConnectionError("Node is not connected")
        await self._socket.send(encode(message, max_bytes=self.max_message_size, available_capabilities=self.available_capabilities).decode())

    def receive(self, timeout: float | None = None) -> ProtocolMessage:
        return self._run(self._receive(timeout), (timeout or self.timeout) + 1)

    async def _receive(self, timeout: float | None) -> ProtocolMessage:
        if self._socket is None:
            raise ConnectionError("Node is not connected")
        raw = await asyncio.wait_for(self._socket.recv(), timeout or self.timeout)
        return decode(raw, max_bytes=self.max_message_size, available_capabilities=self.available_capabilities, authorized_events=self.authorized_events)

    def heartbeat(self, sequence: int = 0) -> ProtocolMessage:
        message = ProtocolMessage(MessageType.HEARTBEAT, self.identity.node_id, "primary.local", {"status": "ONLINE", "sequence": sequence})
        self.send(message)
        return self.receive()

    def reconnect(self) -> ProtocolMessage:
        self.disconnect(stop_loop=False)
        last_error = None
        for attempt in range(self.max_reconnect_attempts):
            if attempt:
                sleep(min(2 ** (attempt - 1), self.max_backoff_seconds))
            try:
                response = self.connect()
                self.reconnects += 1
                return response
            except Exception as exc:
                last_error = exc
        raise ConnectionError("Reconnect attempts exhausted") from last_error

    def disconnect(self, *, stop_loop: bool = True) -> bool:
        if self._socket is not None:
            try:
                goodbye = ProtocolMessage(MessageType.GOODBYE, self.identity.node_id, "primary.local", {"reason": "client disconnect"})
                self.send(goodbye)
                self._run(self._socket.close())
            except Exception:
                pass
            self._socket = None
        if stop_loop and self._thread.is_alive():
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(2)
        return not self._thread.is_alive() if stop_loop else True
