"""Native MCP tool bridge: declared MCP servers as gated agent tools.

Brings the MCP servers halter already manages (``~/.config/halter/mcp.toml``)
into the native agent's tool loop:

- stdio servers are launched as short-lived subprocesses per call, so a
  crashing server can never take down the AHP host or the agent turn
- ``http`` servers use the streamable-HTTP transport (JSON-RPC POST, with
  both plain-JSON and SSE responses supported); the legacy SSE-only
  transport is still skipped
- tool names are prefixed ``mcp_<server>_<tool>`` to stay addressable in the
  OpenAI tools schema
- only tools whose annotations declare ``readOnlyHint`` are exposed without
  an explicit per-tool allowlist; state-changing tools must be listed in
  ``[native_agent.mcp] write`` and then go through the normal approval flow
- secrets from ``${VAR}`` placeholders are expanded into subprocess env or
  HTTP headers and never appear in events, results, or audit output
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import Any

import aiohttp

from .mcp_manifest import _load_secrets, expand_placeholders, load_manifest

MCP_PROTOCOL_VERSION = "2025-06-18"
MCP_LIST_TIMEOUT = 15.0
MCP_CALL_TIMEOUT = 60.0
MAX_MCP_OUTPUT_CHARS = 24_000
CLIENT_INFO = {"name": "halter", "version": "0.1.0"}


def sanitize_identifier(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value)
    return cleaned.strip("_") or "x"


def tool_name_for(server: str, tool: str) -> str:
    return f"mcp_{sanitize_identifier(server)}_{sanitize_identifier(tool)}"


@dataclass(frozen=True)
class McpToolDescriptor:
    server: str
    tool: str
    schema: dict[str, Any]
    read_only: bool

    @property
    def name(self) -> str:
        return tool_name_for(self.server, self.tool)


def _stdio_specs() -> list[Any]:
    return [
        spec for spec in load_manifest()
        if spec.transport == "stdio" and spec.command
    ]


def _http_specs() -> list[Any]:
    # Streamable HTTP only; the legacy SSE-only transport stays out of scope.
    return [
        spec for spec in load_manifest()
        if spec.transport == "http" and spec.url
    ]


def _find_spec(server: str) -> Any:
    for spec in [*_stdio_specs(), *_http_specs()]:
        if spec.name == server:
            return spec
    return None


def _expanded_env(spec: Any) -> dict[str, str]:
    expanded, _missing = expand_placeholders(spec, _load_secrets())
    merged = dict(os.environ)
    merged.update(expanded.env)
    return merged


class _StdioSession:
    """One newline-delimited JSON-RPC conversation with an MCP server."""

    def __init__(self, spec: Any, timeout: float) -> None:
        self.spec = spec
        self.timeout = timeout
        self.proc: asyncio.subprocess.Process | None = None
        self._next_id = 0

    async def __aenter__(self) -> "_StdioSession":
        self.proc = await asyncio.create_subprocess_exec(
            self.spec.command, *list(self.spec.args or []),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=_expanded_env(self.spec),
            start_new_session=True,
        )
        return self

    async def __aexit__(self, *_exc: object) -> None:
        proc = self.proc
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=3)
        except (OSError, asyncio.TimeoutError, ProcessLookupError):
            try:
                proc.kill()
            except (OSError, ProcessLookupError):
                pass

    async def _send(self, payload: dict[str, Any]) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write(
            (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        )
        await self.proc.stdin.drain()

    async def _read_response(self, want_id: int) -> dict[str, Any]:
        assert self.proc is not None and self.proc.stdout is not None
        while True:
            raw = await asyncio.wait_for(self.proc.stdout.readline(), self.timeout)
            if not raw:
                raise RuntimeError(
                    f"MCP server {self.spec.name!r} closed its output"
                )
            try:
                message = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(message, dict) or message.get("id") != want_id:
                continue  # notification or server-initiated request
            if "error" in message:
                error = message["error"] or {}
                raise RuntimeError(
                    f"MCP error from {self.spec.name!r}: "
                    f"{error.get('code')}: {error.get('message')}"
                )
            return message.get("result") or {}

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        await self._send({
            "jsonrpc": "2.0", "id": request_id,
            "method": method, "params": params,
        })
        return await self._read_response(request_id)

    async def notify(self, method: str) -> None:
        await self._send({"jsonrpc": "2.0", "method": method})

    async def handshake(self) -> None:
        await self.request("initialize", {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        })
        await self.notify("notifications/initialized")


class _HttpSession:
    """Streamable-HTTP MCP conversation: JSON-RPC POST per message.

    Handles both ``application/json`` and ``text/event-stream`` replies and
    carries the server-assigned ``Mcp-Session-Id`` on follow-up requests.
    """

    def __init__(self, spec: Any, timeout: float) -> None:
        self.spec = spec
        self.timeout = timeout
        self._next_id = 0
        self._session_id: str | None = None
        expanded, _missing = expand_placeholders(spec, _load_secrets())
        self._url = expanded.url or ""
        self._extra_headers = dict(expanded.headers)

    async def __aenter__(self) -> "_HttpSession":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    def _result_of(self, message: Any, want_id: int) -> dict[str, Any]:
        if not isinstance(message, dict) or message.get("id") != want_id:
            raise RuntimeError(f"MCP HTTP response id mismatch from {self.spec.name!r}")
        if "error" in message:
            error = message["error"] or {}
            raise RuntimeError(
                f"MCP error from {self.spec.name!r}: "
                f"{error.get('code')}: {error.get('message')}"
            )
        return message.get("result") or {}

    async def _read_sse(self, response: Any, want_id: int) -> dict[str, Any]:
        data_lines: list[str] = []
        async for raw in response.content:
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                if data_lines:
                    message = json.loads("\n".join(data_lines))
                    if isinstance(message, dict) and message.get("id") == want_id:
                        return self._result_of(message, want_id)
                    data_lines = []
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        raise RuntimeError(f"MCP HTTP stream ended without response from {self.spec.name!r}")

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self._extra_headers,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.post(self._url, json=payload, headers=headers) as response:
                assigned = response.headers.get("Mcp-Session-Id")
                if assigned:
                    self._session_id = assigned
                if payload.get("id") is None:
                    return None  # notification: server replies 202/empty
                if response.status >= 400:
                    body = await response.text()
                    raise RuntimeError(
                        f"MCP HTTP {response.status} from {self.spec.name!r}: "
                        f"{body[:200]}"
                    )
                content_type = response.headers.get("Content-Type", "")
                if "text/event-stream" in content_type:
                    return await self._read_sse(response, payload["id"])
                return self._result_of(json.loads(await response.text()), payload["id"])

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        result = await self._post({
            "jsonrpc": "2.0", "id": self._next_id,
            "method": method, "params": params,
        })
        return result or {}

    async def notify(self, method: str) -> None:
        await self._post({"jsonrpc": "2.0", "method": method})

    async def handshake(self) -> None:
        await self.request("initialize", {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        })
        await self.notify("notifications/initialized")


def _open_session(spec: Any, timeout: float):
    """Context manager for whichever transport the spec declares."""
    if spec.transport == "http":
        return _HttpSession(spec, timeout)
    return _StdioSession(spec, timeout)


def _tool_schema(server: str, tool: dict[str, Any]) -> dict[str, Any]:
    name = str(tool.get("name") or "")
    parameters = tool.get("inputSchema")
    if not isinstance(parameters, dict):
        parameters = {"type": "object", "properties": {}}
    description = str(
        tool.get("description") or f"MCP tool {server}/{name}"
    )[:1000]
    return {
        "type": "function",
        "function": {
            "name": tool_name_for(server, name),
            "description": description,
            "parameters": parameters,
        },
    }


async def list_mcp_tools() -> list[McpToolDescriptor]:
    """Discover tools from all declared stdio/http MCP servers.

    Servers that fail to start, handshake, or answer are skipped silently at
    the tool-surface level: a broken server must not break the agent turn.
    """
    descriptors: list[McpToolDescriptor] = []
    for spec in [*_stdio_specs(), *_http_specs()]:
        try:
            async with _open_session(spec, MCP_LIST_TIMEOUT) as session:
                await session.handshake()
                result = await session.request("tools/list", {})
        except (OSError, RuntimeError, ValueError, asyncio.TimeoutError):
            continue
        for tool in result.get("tools") or []:
            if not isinstance(tool, dict) or not tool.get("name"):
                continue
            annotations = tool.get("annotations") or {}
            descriptors.append(McpToolDescriptor(
                server=spec.name,
                tool=str(tool["name"]),
                schema=_tool_schema(spec.name, tool),
                read_only=bool(annotations.get("readOnlyHint")),
            ))
    return descriptors


async def call_mcp_tool(
    server: str, tool: str, arguments: dict[str, Any],
    timeout: float = MCP_CALL_TIMEOUT,
) -> dict[str, Any]:
    """Invoke one MCP tool (stdio: per-call process; http: fresh session)."""
    spec = _find_spec(server)
    if spec is None:
        raise RuntimeError(f"MCP server {server!r} is not declared as stdio/http")
    async with _open_session(spec, timeout) as session:
        await session.handshake()
        result = await session.request("tools/call", {
            "name": tool, "arguments": arguments,
        })
    if result.get("isError"):
        raise RuntimeError(_content_text(result) or "MCP tool reported an error")
    text = _content_text(result)
    structured = result.get("structuredContent")
    output: dict[str, Any] = {
        "server": server,
        "tool": tool,
        "output": text[:MAX_MCP_OUTPUT_CHARS] + (
            f"\n... [truncated {len(text) - MAX_MCP_OUTPUT_CHARS} chars]"
            if len(text) > MAX_MCP_OUTPUT_CHARS else ""
        ),
    }
    if isinstance(structured, dict):
        output["structured"] = structured
    return output


def _content_text(result: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "\n".join(parts)


def mcp_allowlists(config: Any) -> tuple[set[str] | None, set[str]]:
    """(read allowlist, write allowlist) from ``[native_agent.mcp]``.

    A missing ``read`` key means every declared read-only tool is allowed;
    ``write`` is always an explicit per-tool opt-in.
    """
    raw = getattr(config, "native_mcp", None) or {}
    read_list = raw.get("read")
    read_allow = set(read_list) if read_list is not None else None
    write_allow = set(raw.get("write") or [])
    return read_allow, write_allow
