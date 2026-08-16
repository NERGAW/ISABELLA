"""Local WebSocket server carrying only ISABELLA Protocol envelopes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import threading
from typing import Any

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from Isabella.Events import EventType
from Isabella.Nodes import Node, NodeStatus, NodeType, TrustState
from Isabella.Protocol import (
    MAX_MESSAGE_BYTES, MessageType, NodeIdentity, ProtocolError, ProtocolMessage,
    ProtocolValidationError, decode, dispatch_command, encode, error_message,
    negotiate_hello,
)
from .models import ConnectionStatus, NodeConnection, now_iso


PROTOCOL_TO_NODE = {
    "COMPUTER": NodeType.SECONDARY_PC, "SMARTPHONE": NodeType.MOBILE,
    "HOME": NodeType.HOME, "WEARABLE": NodeType.HELMET,
    "EMBEDDED": NodeType.EMBEDDED, "PANEL": NodeType.DISPLAY,
}


class WebSocketNodeServer:
    def __init__(self, config: dict[str, Any], *, node_manager, registry, authentication, rate_limiter, event_bus=None, device_security=None) -> None:
        self.config = config
        self.node_manager = node_manager
        self.registry = registry
        self.authentication = authentication
        self.rate_limiter = rate_limiter
        self.event_bus = event_bus
        self.device_security = device_security
        self.host = config["host"]
        self.port = int(config["port"])
        self.max_message_size = min(int(config["max_message_size"]), MAX_MESSAGE_BYTES)
        self.heartbeat_seconds = float(config["heartbeat_seconds"])
        self.heartbeat_timeout_seconds = self.heartbeat_seconds * 2
        self.connection_timeout = float(config["connection_timeout_seconds"])
        self.authorized_events = set(config.get("event_allowlist", []))
        self.connections: dict[str, NodeConnection] = {}
        self._sockets: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._loop = asyncio.new_event_loop()
        self._thread: threading.Thread | None = None
        self._server = None
        self._started = threading.Event()
        self.messages_received = 0
        self.messages_sent = 0
        self.errors = 0
        self.reconnects = 0

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        if self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        self._started.clear()
        self.authentication.initialize()
        self._thread = threading.Thread(target=self._run_loop, name="IsabellaWebSocket", daemon=True)
        self._thread.start()
        if not self._started.wait(self.connection_timeout):
            return False
        return self._server is not None

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._start_async())
            self._started.set()
            self._loop.run_forever()
        except Exception:
            self.errors += 1
            self._started.set()
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()

    async def _start_async(self) -> None:
        self._server = await serve(self._handler, self.host, self.port, max_size=int(self.config["max_message_size"]), ping_interval=None)
        self.port = int(self._server.sockets[0].getsockname()[1])
        self._emit(EventType.TRANSPORT_STARTED, {"host": self.host, "port": self.port})

    async def _handler(self, websocket) -> None:
        remote = websocket.remote_address[0] if websocket.remote_address else "unknown"
        authorization = websocket.request.headers.get("Authorization") if websocket.request else None
        connection = NodeConnection(remote_address=remote, authenticated=self.authentication.validate_header(authorization))
        with self._lock:
            self.connections[connection.connection_id] = connection
            self._sockets[connection.connection_id] = websocket
        self._emit(EventType.TRANSPORT_CONNECTION_OPENED, connection.to_dict())
        try:
            raw = await asyncio.wait_for(websocket.recv(), self.connection_timeout)
            hello = self._decode(raw, connection)
            if hello.type is not MessageType.HELLO:
                raise ProtocolValidationError("EXPECTED_HELLO", "First message must be HELLO")
            identity = NodeIdentity.from_dict(hello.payload["identity"])
            connection.node_id = identity.node_id
            connection.protocol_version = identity.protocol_version
            if self.device_security:
                auth = hello.payload.get("device_auth", {})
                record = self.device_security.store.get(identity.node_id)
                if record:
                    proof = f"{identity.node_id}|{hello.timestamp}|{hello.id}".encode()
                    try:
                        stamp = datetime.fromisoformat(hello.timestamp).timestamp()
                    except ValueError:
                        stamp = 0
                    connection.authenticated = self.device_security.authenticate(identity.node_id, proof, str(auth.get("signature", "")), hello.id, stamp)
                    if not connection.authenticated:
                        raise PermissionError("Device credential mismatch")
                else:
                    pairing = hello.payload.get("pairing")
                    if not pairing or not self.device_security.pairing_open:
                        raise PermissionError("Unknown Node may only request pairing while pairing mode is open")
                    request = self.device_security.request_pairing(identity.node_id, str(pairing.get("public_identity", "")), tuple(pairing.get("permissions", ())))
                    connection.authenticated = False
                    connection.pairing_id = request.pairing_id
            with self._lock:
                if any(item.connection_id != connection.connection_id and item.node_id == identity.node_id and item.status in {ConnectionStatus.ESTABLISHED, ConnectionStatus.DEGRADED} for item in self.connections.values()):
                    raise ProtocolValidationError("DUPLICATE_CONNECTION", "Node already has an active connection")
            self._register_or_reconnect(identity)
            primary = self.node_manager.primary().protocol_identity()
            welcome = negotiate_hello(hello, primary, primary_capabilities=set(primary.capabilities), peer_capabilities=self.node_manager.known_capabilities, heartbeat_seconds=int(self.heartbeat_seconds))
            if welcome.type is MessageType.ERROR:
                await self._send(websocket, connection, welcome)
                return
            if connection.pairing_id:
                payload = dict(welcome.payload)
                request = self.device_security.get_pairing_request(connection.pairing_id)
                payload["pairing"] = {"pairing_id": connection.pairing_id, "status": "PAIRING",
                                      "display_code": request.display_code if request else ""}
                welcome = ProtocolMessage(welcome.type, welcome.source, welcome.destination, payload,
                                          welcome.id, welcome.protocol_version, welcome.timestamp, welcome.correlation_id)
                await self._send(websocket, connection, welcome)
                await websocket.close(code=1000, reason="pairing request accepted")
                return
            connection.status = ConnectionStatus.ESTABLISHED
            await self._send(websocket, connection, welcome)
            missed = 0
            while True:
                try:
                    raw = await asyncio.wait_for(websocket.recv(), self.heartbeat_timeout_seconds)
                    missed = 0
                except asyncio.TimeoutError:
                    missed += 1
                    connection.status = ConnectionStatus.DEGRADED if missed == 1 else ConnectionStatus.OFFLINE
                    if connection.node_id:
                        self.node_manager.update_status(connection.node_id, NodeStatus.DEGRADED if missed == 1 else NodeStatus.OFFLINE)
                    if missed >= 2:
                        await websocket.close(code=1001, reason="heartbeat timeout")
                        break
                    continue
                message = self._decode(raw, connection)
                if not self.rate_limiter.allow(connection.connection_id):
                    raise ProtocolValidationError("RATE_LIMITED", "Connection message rate exceeded")
                connection.last_seen = now_iso()
                if connection.node_id:
                    self.node_manager.heartbeat(connection.node_id)
                if message.type is MessageType.HEARTBEAT:
                    reply = ProtocolMessage(MessageType.HEARTBEAT, primary.node_id, connection.node_id, {"status": "ONLINE", "sequence": message.payload.get("sequence")}, correlation_id=message.correlation_id)
                    await self._send(websocket, connection, reply)
                elif message.type is MessageType.COMMAND_REQUEST:
                    try:
                        if self.device_security and not self.device_security.authorize(connection.node_id, "send_commands"):
                            raise ProtocolValidationError("PERMISSION_DENIED", "Node lacks send_commands permission")
                        result = dispatch_command(message, authenticated=connection.authenticated, registry=self.registry, source_node=connection.node_id)
                        payload = {"request_id": message.id, "success": result.success, "status": result.status, "message": result.message, "data": result.data, "error": result.error_code}
                    except ProtocolValidationError as exc:
                        payload = {"request_id": message.id, "success": False, "status": "denied", "message": str(exc), "data": {}, "error": exc.code}
                    reply = ProtocolMessage(MessageType.COMMAND_RESULT, primary.node_id, connection.node_id, payload, correlation_id=message.correlation_id)
                    await self._send(websocket, connection, reply)
                elif message.type is MessageType.GOODBYE:
                    await websocket.close(code=1000, reason="goodbye")
                    break
                elif message.type in {MessageType.STATUS, MessageType.TELEMETRY}:
                    continue
                else:
                    raise ProtocolValidationError("MESSAGE_NOT_ALLOWED", "Message type is not accepted from a Node")
        except (ProtocolValidationError, ValueError, KeyError, PermissionError) as exc:
            self.errors += 1
            connection.errors += 1
            error = exc if isinstance(exc, ProtocolValidationError) else ProtocolValidationError("INVALID_NODE", "Node handshake is invalid")
            request = locals().get("hello") or ProtocolMessage(MessageType.ERROR, connection.node_id or "unknown", "primary", {})
            try:
                await self._send(websocket, connection, error_message(request, error.to_error(getattr(request, "id", None))))
            except Exception:
                pass
            self._emit(EventType.TRANSPORT_PROTOCOL_ERROR, {"connection_id": connection.connection_id, "code": error.code})
            await websocket.close(code=1008, reason=error.code)
        except ConnectionClosed:
            pass
        finally:
            connection.status = ConnectionStatus.CLOSED
            if connection.node_id and self.node_manager.get(connection.node_id):
                self.node_manager.update_status(connection.node_id, NodeStatus.DISCONNECTED)
            with self._lock:
                self._sockets.pop(connection.connection_id, None)
            self._emit(EventType.TRANSPORT_CONNECTION_CLOSED, connection.to_dict())

    def _register_or_reconnect(self, identity: NodeIdentity) -> None:
        existing = self.node_manager.get(identity.node_id)
        node_type = PROTOCOL_TO_NODE.get(identity.node_type.value)
        if node_type is None:
            raise ValueError("Unsupported remote Node type")
        if existing:
            if existing.trust is TrustState.REVOKED or existing.node_type is not node_type:
                raise PermissionError("Known Node identity is not accepted")
            self.node_manager.update_capabilities(existing.node_id, identity.capabilities)
            self.node_manager.heartbeat(existing.node_id)
            self.reconnects += 1
            return
        trust = TrustState.PAIRING if self.device_security else TrustState.UNTRUSTED
        node = Node(identity.node_id, identity.name, node_type, NodeStatus.CONNECTING, identity.protocol_version, identity.capabilities, trust=trust)
        self.node_manager.register(node)

    def _decode(self, raw, connection: NodeConnection) -> ProtocolMessage:
        if not isinstance(raw, (str, bytes)):
            raise ProtocolValidationError("BINARY_NOT_SUPPORTED", "Only JSON text/bytes are supported")
        size = len(raw.encode("utf-8") if isinstance(raw, str) else raw)
        if size > self.max_message_size:
            raise ProtocolValidationError("MESSAGE_TOO_LARGE", "Message exceeds transport limit")
        message = decode(raw, max_bytes=self.max_message_size, available_capabilities=self.node_manager.known_capabilities, authorized_events=self.authorized_events)
        connection.messages_received += 1
        self.messages_received += 1
        self._emit(EventType.TRANSPORT_MESSAGE_RECEIVED, {"connection_id": connection.connection_id, "type": message.type.value})
        return message

    async def _send(self, websocket, connection: NodeConnection, message: ProtocolMessage) -> None:
        data = encode(message, max_bytes=self.max_message_size, available_capabilities=self.node_manager.known_capabilities, authorized_events=self.authorized_events)
        await websocket.send(data.decode("utf-8"))
        connection.messages_sent += 1
        self.messages_sent += 1
        self._emit(EventType.TRANSPORT_MESSAGE_SENT, {"connection_id": connection.connection_id, "type": message.type.value})

    def broadcast_event(self, event) -> None:
        if event.type not in self.authorized_events or not self._thread or not self._thread.is_alive():
            return
        asyncio.run_coroutine_threadsafe(self._broadcast_event(event), self._loop)

    async def _broadcast_event(self, event) -> None:
        primary = self.node_manager.primary()
        if not primary:
            return
        with self._lock:
            targets = [(key, socket) for key, socket in self._sockets.items() if self.connections[key].status is ConnectionStatus.ESTABLISHED]
        for connection_id, socket in targets:
            connection = self.connections[connection_id]
            message = ProtocolMessage(MessageType.EVENT, primary.node_id, connection.node_id, {"event": event.type, "data": event.payload}, correlation_id=event.correlation_id or event.id)
            try:
                await self._send(socket, connection, message)
            except Exception:
                self.errors += 1

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            active = sum(item.status in {ConnectionStatus.ESTABLISHED, ConnectionStatus.DEGRADED} for item in self.connections.values())
        return {"status": "ONLINE" if self._thread and self._thread.is_alive() and self._server else "OFFLINE", "connections": active, "known_connections": len(self.connections), "messages_received": self.messages_received, "messages_sent": self.messages_sent, "errors": self.errors, "reconnects": self.reconnects}

    def shutdown(self) -> bool:
        if not self._thread:
            return True
        future = asyncio.run_coroutine_threadsafe(self._shutdown_async(), self._loop)
        try:
            future.result(timeout=3)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(3)
        return not self._thread.is_alive()

    async def _shutdown_async(self) -> None:
        with self._lock:
            sockets = list(self._sockets.values())
        await asyncio.gather(*(socket.close(code=1001, reason="server shutdown") for socket in sockets), return_exceptions=True)
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    def _emit(self, event_type, payload: dict[str, Any]) -> None:
        if self.event_bus:
            self.event_bus.emit(event_type, "transport", payload)
