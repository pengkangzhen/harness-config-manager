"""Native MCP tool bridge: declared MCP servers as gated agent tools.

Brings the MCP servers halter already manages (``~/.config/halter/mcp.toml``)
into the native agent's tool loop:

- stdio servers are launched as short-lived subprocesses per call, so a
  crashing server can never take down the AHP host or the agent turn
- tool names are prefixed ``mcp_<server>_<tool>`` to stay addressable in the
  OpenAI tools schema
- only tools whose annotations declare ``readOnlyHint`` are exposed without
  an explicit per-tool allowlist; state-changing tools must be listed in
  ``[native_agent.mcp] write`` and then go through the normal approval flow
- secrets from ``${VAR}`` placeholders are expanded into the subprocess env
  and never appear in events, results, or audit output

HTTP/SSE transports are out of scope for this phase and are skipped.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import Any

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
    """Discover tools from all declared stdio MCP servers.

    Servers that fail to start, handshake, or answer are skipped silently at
    the tool-surface level: a broken server must not break the agent turn.
    """
    descriptors: list[McpToolDescriptor] = []
    for spec in _stdio_specs():
        try:
            async with _StdioSession(spec, MCP_LIST_TIMEOUT) as session:
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
    """Invoke one MCP tool; the server process exists only for this call."""
    spec = next(
        (item for item in _stdio_specs() if item.name == server), None,
    )
    if spec is None:
        raise RuntimeError(f"MCP server {server!r} is not declared as stdio")
    async with _StdioSession(spec, timeout) as session:
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
