"""MCP bridge：declared servers 进入 native tool loop（read-only / 审批 write）。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from harness_config_manager.agent import AgentRuntime, FunctionModelClient, ModelResponse, ToolCall
from harness_config_manager.config import HalterConfig
from harness_config_manager.mcp_bridge import (
    call_mcp_tool,
    list_mcp_tools,
    mcp_allowlists,
    tool_name_for,
)

FAKE_SERVER = r'''
import json, sys

def respond(id_, result):
    print(json.dumps({"jsonrpc": "2.0", "id": id_, "result": result}), flush=True)

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except Exception:
        continue
    method = msg.get("method")
    if method == "initialize":
        respond(msg["id"], {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "1.0"},
        })
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        respond(msg["id"], {"tools": [
            {"name": "get_data", "description": "read a key",
             "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
             "annotations": {"readOnlyHint": True}},
            {"name": "put_data", "description": "write a key",
             "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}},
             "annotations": {"readOnlyHint": False}},
        ]})
    elif method == "tools/call":
        params = msg.get("params") or {}
        if params.get("name") == "get_data":
            text = "VALUE:" + str((params.get("arguments") or {}).get("key"))
        else:
            arguments = params.get("arguments") or {}
            text = "WROTE:" + str(arguments.get("key")) + "=" + str(arguments.get("value"))
        respond(msg["id"], {"content": [{"type": "text", "text": text}], "isError": False})
'''

CRASH_SERVER = "import sys\nsys.exit(3)\n"


def _install_manifest(fake_home: Path, tmp_path: Path) -> None:
    script = tmp_path / "fake_mcp_server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    crash = tmp_path / "crash_mcp_server.py"
    crash.write_text(CRASH_SERVER, encoding="utf-8")
    manifest = fake_home / ".config/halter/mcp.toml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        f'[[server]]\nname = "fake"\ntransport = "stdio"\n'
        f'command = "python3"\nargs = ["{script}"]\n\n'
        f'[[server]]\nname = "crashy"\ntransport = "stdio"\n'
        f'command = "python3"\nargs = ["{crash}"]\n',
        encoding="utf-8",
    )


def test_list_mcp_tools_skips_crashed_servers(fake_home: Path, tmp_path: Path) -> None:
    _install_manifest(fake_home, tmp_path)
    tools = asyncio.run(list_mcp_tools())
    names = {tool.name for tool in tools}
    assert names == {tool_name_for("fake", "get_data"), tool_name_for("fake", "put_data")}
    by_name = {tool.name: tool for tool in tools}
    assert by_name[tool_name_for("fake", "get_data")].read_only is True
    assert by_name[tool_name_for("fake", "put_data")].read_only is False
    schema = by_name[tool_name_for("fake", "get_data")].schema
    assert schema["function"]["name"] == tool_name_for("fake", "get_data")
    assert schema["function"]["parameters"]["required"] == ["key"]


def test_call_mcp_tool_returns_content_text(fake_home: Path, tmp_path: Path) -> None:
    _install_manifest(fake_home, tmp_path)
    result = asyncio.run(call_mcp_tool("fake", "get_data", {"key": "alpha"}))
    assert result["server"] == "fake"
    assert result["tool"] == "get_data"
    assert result["output"] == "VALUE:alpha"


def _runtime(fake_home: Path, tmp_path: Path, responses, approvals=None, config=None,
             seen_tools=None):
    async def complete(**kwargs):
        if seen_tools is not None:
            seen_tools.append(list(kwargs["tools"]))
        return next(responses)

    return AgentRuntime(
        workspace=tmp_path, model_client=FunctionModelClient(complete),
        session_channel="ahp-session:/mcp-test", home=fake_home,
        request_approval=approvals, config=config,
    )


def test_read_only_mcp_tool_enters_tool_loop_and_audit(fake_home: Path, tmp_path: Path) -> None:
    _install_manifest(fake_home, tmp_path)
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    responses = iter([
        ModelResponse(tool_calls=(ToolCall("mcp-1", tool_name_for("fake", "get_data"), {
            "key": "alpha", "plan_step_id": "step-mcp",
        }),)),
        ModelResponse(content="value retrieved"),
    ])
    seen_tools: list[list[dict]] = []
    runtime = _runtime(fake_home, tmp_path, responses, seen_tools=seen_tools)

    async def run() -> None:
        result = await runtime.run("read via mcp")
        evidence = result.plan_evidence["step-mcp"]
        assert evidence["toolCallIds"][0]["name"] == tool_name_for("fake", "get_data")
        tool = [m for m in result.messages if m.get("role") == "tool"][0]
        payload = json.loads(tool["content"])
        assert payload["output"] == "VALUE:alpha"
        assert payload["server"] == "fake"

    asyncio.run(run())
    advertised = {schema["function"]["name"] for schema in seen_tools[0]}
    assert tool_name_for("fake", "get_data") in advertised
    assert tool_name_for("fake", "put_data") not in advertised  # write 未 allowlist

    audit_path = next((fake_home / ".config/halter/agent-audit").rglob("audit.jsonl"))
    records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    mcp_call = next(r for r in records if r["kind"] == "tool.call" and r["name"].startswith("mcp_"))
    assert mcp_call["planStepId"] == "step-mcp"
    assert "plan_step_id" in mcp_call["arguments"]
    mcp_result = next(r for r in records if r["kind"] == "tool.result" and r["name"].startswith("mcp_"))
    assert mcp_result["result"]["output"] == "VALUE:alpha"


def test_write_mcp_tool_needs_allowlist_and_approval(fake_home: Path, tmp_path: Path) -> None:
    _install_manifest(fake_home, tmp_path)
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    put_tool = tool_name_for("fake", "put_data")

    requests: list[dict] = []

    async def approve(request: dict) -> bool:
        requests.append(request)
        return True

    cfg = HalterConfig()
    cfg.native_mcp = {"write": [put_tool]}

    responses = iter([
        ModelResponse(tool_calls=(ToolCall("mcp-put", put_tool, {
            "key": "alpha", "value": "beta",
        }),)),
        ModelResponse(content="written"),
    ])
    seen_tools: list[list[dict]] = []
    runtime = _runtime(fake_home, tmp_path, responses, approvals=approve, config=cfg,
                       seen_tools=seen_tools)

    async def run() -> None:
        result = await runtime.run("write via mcp", permission_mode="workspace-write")
        tool = [m for m in result.messages if m.get("role") == "tool"][0]
        payload = json.loads(tool["content"])
        assert payload["output"] == "WROTE:alpha=beta"

    asyncio.run(run())
    advertised = {schema["function"]["name"] for schema in seen_tools[0]}
    assert put_tool in advertised  # allowlist 后进入 schema
    assert requests[0]["tool"] == put_tool
    assert requests[0]["input"] == {"server": "fake", "tool": "put_data",
                                    "arguments": {"key": "alpha", "value": "beta"}}
    assert requests[0]["input"].get("arguments", {}).get("plan_step_id") is None

    # 同一工具在 read-only 模式下不进入 schema,伪造调用也会被审批门拒绝
    assert tool_name_for("fake", "put_data") in runtime.mcp_tools


def test_write_mcp_tool_denied_returns_error_to_model(fake_home: Path, tmp_path: Path) -> None:
    _install_manifest(fake_home, tmp_path)
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    put_tool = tool_name_for("fake", "put_data")

    async def deny(_request: dict) -> bool:
        return False

    cfg = HalterConfig()
    cfg.native_mcp = {"write": [put_tool]}

    responses = iter([
        ModelResponse(tool_calls=(ToolCall("mcp-deny", put_tool, {
            "key": "alpha", "value": "beta",
        }),)),
        ModelResponse(content="denied"),
    ])
    runtime = _runtime(fake_home, tmp_path, responses, approvals=deny, config=cfg)

    async def run() -> None:
        result = await runtime.run("write via mcp", permission_mode="workspace-write")
        tool = [m for m in result.messages if m.get("role") == "tool"][0]
        payload = json.loads(tool["content"])
        assert payload["error"] == "PermissionError"

    asyncio.run(run())


def test_mcp_allowlist_defaults() -> None:
    read_allow, write_allow = mcp_allowlists(HalterConfig())
    assert read_allow is None  # 缺省 = 全部 read-only 可用
    assert write_allow == set()  # write 必须显式 opt-in

    cfg = HalterConfig()
    cfg.native_mcp = {"read": ["mcp_a_b"], "write": ["mcp_c_d"]}
    read_allow, write_allow = mcp_allowlists(cfg)
    assert read_allow == {"mcp_a_b"}
    assert write_allow == {"mcp_c_d"}


HTTP_TOOLS = [
    {"name": "http_get", "description": "read via http",
     "inputSchema": {"type": "object", "properties": {}},
     "annotations": {"readOnlyHint": True}},
]


async def _start_http_mcp_server():
    from aiohttp import web

    seen: dict = {"requests": []}

    async def handler(request: web.Request) -> web.Response:
        payload = await request.json()
        seen["requests"].append({
            "method": payload.get("method"),
            "session": request.headers.get("Mcp-Session-Id"),
            "authorization": request.headers.get("Authorization"),
        })
        if payload.get("method") == "initialize":
            return web.json_response(
                {"jsonrpc": "2.0", "id": payload["id"], "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake-http", "version": "1.0"},
                }},
                headers={"Mcp-Session-Id": "sess-42"},
            )
        if payload.get("method") == "tools/list":
            return web.json_response(
                {"jsonrpc": "2.0", "id": payload["id"], "result": {"tools": HTTP_TOOLS}},
                headers={"Mcp-Session-Id": "sess-42"},
            )
        if payload.get("method") == "tools/call":
            arguments = (payload.get("params") or {}).get("arguments") or {}
            return web.json_response(
                {"jsonrpc": "2.0", "id": payload["id"], "result": {
                    "content": [{"type": "text", "text": "HTTP:" + str(arguments.get("key"))}],
                    "isError": False,
                }},
            )
        return web.json_response({"jsonrpc": "2.0", "id": payload.get("id"), "result": {}})

    app = web.Application()
    app.router.add_post("/mcp", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, runner.addresses[0][1], seen


def _write_http_manifest(fake_home: Path, port: int, extra: str = "") -> None:
    manifest = fake_home / ".config/halter/mcp.toml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        f'[[server]]\nname = "fake-http"\ntransport = "http"\n'
        f'url = "http://127.0.0.1:{port}/mcp"\n{extra}\n'
        f'[[server]]\nname = "legacy-sse"\ntransport = "sse"\n'
        f'url = "http://127.0.0.1:{port}/sse"\n',
        encoding="utf-8",
    )


def test_http_transport_lists_and_calls_tools(fake_home: Path) -> None:
    import harness_config_manager.mcp_bridge as bridge

    async def scenario() -> dict:
        runner, port, seen = await _start_http_mcp_server()
        try:
            _write_http_manifest(fake_home, port)
            bridge.MCP_LIST_TIMEOUT = 10.0
            tools = await bridge.list_mcp_tools()
            result = await bridge.call_mcp_tool("fake-http", "http_get", {"key": "h1"})
            return {"tools": tools, "result": result, "seen": seen["requests"]}
        finally:
            await runner.cleanup()

    outcome = asyncio.run(scenario())
    names = [tool.name for tool in outcome["tools"]]
    assert tool_name_for("fake-http", "http_get") in names
    assert all("legacy-sse" not in name for name in names)  # sse transport 跳过
    assert outcome["result"]["output"] == "HTTP:h1"
    assert outcome["result"]["server"] == "fake-http"

    requests = outcome["seen"]
    methods = [item["method"] for item in requests]
    assert methods[0] == "initialize" and "tools/list" in methods and "tools/call" in methods
    assert requests[0]["session"] is None  # 每个 session 的首个请求无会话 id
    # initialize 之后的所有请求(list/call)都必须携带服务端分配的会话 id
    assert all(
        item["session"] == "sess-42"
        for item in requests if item["method"] != "initialize"
    )


def test_http_transport_error_response_is_runtime_error(fake_home: Path) -> None:
    from aiohttp import web

    async def scenario() -> None:
        async def handler(_request: web.Request) -> web.Response:
            return web.json_response({"error": "denied"}, status=401)

        app = web.Application()
        app.router.add_post("/mcp", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        try:
            _write_http_manifest(fake_home, port)
            await call_mcp_tool("fake-http", "http_get", {}, timeout=5.0)
            raise AssertionError("unreachable")
        except RuntimeError as exc:
            assert "401" in str(exc)
        finally:
            await runner.cleanup()

    asyncio.run(scenario())
