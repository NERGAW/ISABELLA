"""Synchronous lifecycle wrapper over the official asynchronous MCP SDK."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
from contextlib import AsyncExitStack
import os
import threading
from typing import Any

from Isabella.Skills.base import RiskLevel
from .models import MCPServer, MCPTool, MCPToolResult, MCPTransport


class MCPClientError(RuntimeError):
    pass


class MCPClient:
    def __init__(self, server: MCPServer) -> None:
        self.server = server
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name=f"IsabellaMCP-{server.id}", daemon=True)
        self._thread.start()
        self._client = None
        self._commands = None
        self._worker = None
        self._tools: tuple[MCPTool, ...] = ()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def connect(self, timeout: float) -> tuple[MCPTool, ...]:
        ready: Future = Future()
        self._worker = asyncio.run_coroutine_threadsafe(self._session_worker(ready), self._loop)
        try:
            self._tools = ready.result(timeout=timeout)
            return self._tools
        except TimeoutError as exc:
            self._worker.cancel()
            raise MCPClientError("MCP connection timed out") from exc
        except Exception as exc:
            raise MCPClientError(str(exc)) from exc

    async def _session_worker(self, ready: Future) -> None:
        from mcp import Client

        try:
            async with AsyncExitStack() as stack:
                target: Any = self.server.command_or_url
                metadata = self.server.metadata
                if self.server.transport is MCPTransport.STDIO:
                    from mcp import StdioServerParameters
                    from mcp.client.stdio import stdio_client

                    environment = {
                        child_name: os.environ[source_name]
                        for child_name, source_name in metadata.get("environment_variables", {}).items()
                        if source_name in os.environ
                    }
                    parameters = StdioServerParameters(
                        command=self.server.command_or_url,
                        args=list(metadata.get("args", [])),
                        env=environment or None,
                        cwd=metadata.get("cwd"),
                    )
                    target = stdio_client(parameters)
                elif metadata.get("headers_from_environment"):
                    import httpx2
                    from mcp.client.streamable_http import streamable_http_client

                    headers = {
                        header: os.environ[source_name]
                        for header, source_name in metadata["headers_from_environment"].items()
                        if source_name in os.environ
                    }
                    http_client = await stack.enter_async_context(
                        httpx2.AsyncClient(headers=headers, timeout=self.server.timeout),
                    )
                    target = streamable_http_client(self.server.command_or_url, http_client=http_client)
                client = await stack.enter_async_context(
                    Client(target, read_timeout_seconds=self.server.timeout),
                )
                self._client = client
                self._commands = asyncio.Queue()
                response = await client.list_tools()
                ready.set_result(tuple(self._convert_tool(tool) for tool in response.tools))
                while True:
                    operation, payload, result_future = await self._commands.get()
                    if operation == "disconnect":
                        result_future.set_result(True)
                        break
                    name, arguments, timeout = payload
                    try:
                        result = await self._call_tool(name, arguments, timeout)
                        result_future.set_result(result)
                    except Exception as exc:
                        result_future.set_exception(exc)
        except Exception as exc:
            if not ready.done():
                ready.set_exception(exc)
        finally:
            self._client = None
            self._commands = None

    def _convert_tool(self, tool) -> MCPTool:
        metadata = dict(getattr(tool, "meta", None) or {})
        annotations = getattr(tool, "annotations", None)
        destructive = bool(getattr(annotations, "destructive_hint", False))
        risk_name = str(metadata.get("risk_level", "CRITICAL" if destructive else "CAUTION")).upper()
        risk = RiskLevel(risk_name) if risk_name in RiskLevel._value2member_map_ else RiskLevel.CAUTION
        return MCPTool(
            self.server.id, tool.name, getattr(tool, "description", "") or "Ferramenta MCP externa.",
            dict(getattr(tool, "input_schema", None) or {}), risk, metadata,
        )

    def list_tools(self) -> tuple[MCPTool, ...]:
        return self._tools

    def call_tool(self, name: str, arguments: dict[str, Any], timeout: float) -> MCPToolResult:
        if self._commands is None:
            raise MCPClientError("MCP server is not connected")
        result: Future = Future()
        self._loop.call_soon_threadsafe(
            self._commands.put_nowait, ("call", (name, arguments, timeout), result),
        )
        try:
            return result.result(timeout=timeout + 0.5)
        except TimeoutError as exc:
            raise MCPClientError("MCP operation timed out") from exc
        except Exception as exc:
            raise MCPClientError(str(exc)) from exc

    async def _call_tool(self, name: str, arguments: dict[str, Any], timeout: float) -> MCPToolResult:
        if self._client is None:
            raise MCPClientError("MCP server is not connected")
        response = await asyncio.wait_for(self._client.call_tool(name, arguments), timeout=timeout)
        content = getattr(response, "structured_content", None)
        if content is None:
            content = [item.model_dump(mode="json") if hasattr(item, "model_dump") else str(item) for item in getattr(response, "content", ())]
        failed = bool(getattr(response, "is_error", False))
        return MCPToolResult(not failed, content, "MCP tool returned an error" if failed else None)

    def disconnect(self, timeout: float = 5.0) -> bool:
        try:
            if self._commands:
                result: Future = Future()
                self._loop.call_soon_threadsafe(
                    self._commands.put_nowait, ("disconnect", None, result),
                )
                result.result(timeout=timeout)
            if self._worker:
                self._worker.result(timeout=timeout)
            return True
        finally:
            self._client = None
            self._tools = ()
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout)

    def health_check(self) -> bool:
        return self._client is not None and self._thread.is_alive()
