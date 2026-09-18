"""AHP host：协议级端到端（真实 WebSocket JSON-RPC 客户端）。"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import uuid
from pathlib import Path

import aiohttp
import aiohttp.web
import pytest
import websockets

from harness_config_manager.ahp_host import (
    PROTOCOL_VERSION, AhpHost, build_web_app, write_auth_token,
)


async def _start_host(ahp: AhpHost) -> int:
    """在随机端口启动 aiohttp AHP host，返回端口。"""
    runner = aiohttp.web.AppRunner(build_web_app(ahp))
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner.addresses[0][1]


def _connect(host: AhpHost, port: int):
    """Authenticated test WebSocket connection."""
    return websockets.connect(
        f"ws://127.0.0.1:{port}",
        additional_headers={"Authorization": f"Bearer {host.auth_token}"},
    )


def _make_exec(dir_path: Path, name: str, body: str) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture
def fake_harnesses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # claude：回显收到的全部参数（验证 --model 注入）+ 固定输出行
    _make_exec(bin_dir, "claude", 'echo "ARGS:$*"; echo "claude-done"')
    _make_exec(bin_dir, "codex", 'echo "codex-done"')
    _make_exec(bin_dir, "opencode", 'echo "opencode-done"')
    zcode = _make_exec(bin_dir, "zcode-cli", 'echo "zcode-done"')
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("ZCODE_CLI", str(zcode))
    return bin_dir


class Client:
    """极简 AHP 测试客户端：JSON-RPC over WebSocket。"""

    def __init__(self, ws) -> None:
        self.ws = ws
        self._id = 0
        self.notifications: list[dict] = []

    async def request(self, method: str, params: dict) -> dict:
        self._id += 1
        await self.ws.send(json.dumps({
            "jsonrpc": "2.0", "id": self._id, "method": method, "params": params,
        }))
        while True:
            raw = await self.ws.recv()
            msg = json.loads(raw)
            if "id" in msg and msg["id"] == self._id:
                assert "error" not in msg, msg.get("error")
                return msg["result"]
            self.notifications.append(msg)

    async def notify(self, method: str, params: dict) -> None:
        await self.ws.send(json.dumps({
            "jsonrpc": "2.0", "method": method, "params": params,
        }))

    async def collect(self, method: str, timeout: float = 5.0) -> list[dict]:
        """等待并收集指定 method 的通知（含已缓冲的）。"""
        found = [n for n in self.notifications if n.get("method") == method]
        self.notifications = [n for n in self.notifications if n.get("method") != method]
        deadline = asyncio.get_event_loop().time() + timeout
        while not found:
            remaining = deadline - asyncio.get_event_loop().time()
            assert remaining > 0, f"timeout waiting for {method}"
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            msg = json.loads(raw)
            if msg.get("method") == method:
                found.append(msg)
            else:
                self.notifications.append(msg)
        return found

    def actions(self, channel: str | None = None) -> list[dict]:
        out = []
        for n in self.notifications:
            if n.get("method") != "action":
                continue
            p = n["params"]
            if channel is None or p.get("channel") == channel:
                out.append(p)
        return out


async def _scenario(fake_home: Path, tmp_path: Path) -> None:
    host = AhpHost()
    port = await _start_host(host)
    if True:
        async with _connect(host, port) as ws:
            client = Client(ws)

            # --- initialize：版本协商 + root 快照（含 4 个 agent） ---
            result = await client.request("initialize", {
                "channel": "ahp-root://",
                "protocolVersions": [PROTOCOL_VERSION],
                "clientId": "test-client",
                "clientInfo": {"name": "pytest", "version": "1"},
                "initialSubscriptions": ["ahp-root://"],
            })
            assert result["protocolVersion"] == PROTOCOL_VERSION
            assert result["serverInfo"]["name"] == "halter-agent-host"
            root = result["snapshots"][0]
            assert root["resource"] == "ahp-root://"
            providers = {a["provider"] for a in root["state"]["agents"]}
            assert providers == {"claude", "codex", "zcode", "opencode", "halter"}

            # --- createSession(provider=claude)：快照 + root/sessionAdded + ready ---
            session_uri = f"ahp-session:/{uuid.uuid4()}"
            result = await client.request("createSession", {
                "channel": session_uri,
                "provider": "claude",
                "workingDirectories": [tmp_path.as_uri()],
            })
            assert result["snapshot"]["state"]["provider"] == "claude"
            assert result["snapshot"]["state"]["lifecycle"] == "ready"
            added = await client.collect("root/sessionAdded")
            assert added[0]["params"]["summary"]["resource"] == session_uri

            # --- createChat：快照 + session/chatAdded ---
            chat_uri = f"ahp-chat:/{uuid.uuid4()}"
            result = await client.request("createChat", {
                "channel": session_uri, "chat": chat_uri,
            })
            assert result["snapshot"]["resource"] == chat_uri
            await client.collect("action")  # drain session/chatAdded

            # --- dispatchAction(chat/turnStarted)：echo + 流式 delta + complete ---
            turn_id = f"t-{uuid.uuid4().hex[:6]}"
            await client.notify("dispatchAction", {
                "channel": chat_uri,
                "clientSeq": 1,
                "action": {
                    "type": "chat/turnStarted",
                    "turnId": turn_id,
                    "startedAt": "2026-09-18T00:00:00.000Z",
                    "message": {
                        "text": "hello model-check",
                        "origin": {"kind": "user"},
                        "model": {"id": "opus"},
                    },
                },
            })
            complete = await client.collect("action", timeout=10)
            # drain：一直读到 turnComplete 出现
            while not any(p["params"]["action"]["type"] == "chat/turnComplete" for p in complete):
                complete += await client.collect("action", timeout=10)

            by_type = {}
            for p in complete:
                by_type.setdefault(p["params"]["action"]["type"], []).append(p["params"])

            # echo 的 turnStarted 带 origin
            started = by_type["chat/turnStarted"][0]
            assert started["action"]["turnId"] == turn_id
            assert started["origin"] == {"clientId": "test-client", "clientSeq": 1}
            assert started["serverSeq"] >= 1

            # markdown part 创建 + delta 流（含模型注入证据 + 完成行）
            assert by_type["chat/responsePart"][0]["action"]["part"]["kind"] == "markdown"
            deltas = "".join(d["action"]["content"] for d in by_type["chat/delta"])
            assert "ARGS:" in deltas
            assert "--model opus" in deltas          # 模型路由进 argv
            assert "hello model-check" in deltas      # 提示词进 argv
            assert "claude-done" in deltas            # harness 输出被流式回传

            done = by_type["chat/turnComplete"][0]
            assert done["action"]["turnId"] == turn_id
            assert done["action"]["duration"] >= 0

            # --- 订阅 chat 后 listSessions 可见 ---
            result = await client.request("listSessions", {"channel": "ahp-root://"})
            assert result["items"][0]["resource"] == session_uri
            assert result["items"][0]["provider"] == "claude"

            # --- 订阅快照包含已完成 turn ---
            result = await client.request("subscribe", {"channel": chat_uri})
            turns = result["snapshot"]["state"]["turns"]
            assert len(turns) == 1
            assert turns[0]["state"] == "complete"
            assert turns[0]["message"]["text"] == "hello model-check"
            assert turns[0]["responseParts"][0]["kind"] == "markdown"

    # 会话结束后进程干净退出（server 关闭时无残留子进程由 OS 保证，这里不强制检查）


def test_ahp_full_flow(fake_harnesses, fake_home: Path, tmp_path: Path) -> None:
    asyncio.run(_scenario(fake_home, tmp_path))


def test_initialize_version_mismatch(fake_harnesses, fake_home: Path) -> None:
    async def _neg() -> dict:
        host = AhpHost()
        port = await _start_host(host)
        async with _connect(host, port) as ws:
                await ws.send(json.dumps({
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {
                        "channel": "ahp-root://",
                        "protocolVersions": ["0.0.1"],
                        "clientId": "x",
                    },
                }))
                msg = json.loads(await ws.recv())
                return msg["error"]

    err = asyncio.run(_neg())
    assert err["code"] == -32005
    assert PROTOCOL_VERSION in err["data"]["supportedVersions"]


def test_create_session_unknown_provider(fake_harnesses, fake_home: Path) -> None:
    async def _bad() -> dict:
        host = AhpHost()
        port = await _start_host(host)
        async with _connect(host, port) as ws:
                await ws.send(json.dumps({
                    "jsonrpc": "2.0", "id": 2, "method": "initialize",
                    "params": {
                        "channel": "ahp-root://",
                        "protocolVersions": [PROTOCOL_VERSION],
                        "clientId": "x",
                    },
                }))
                await ws.recv()
                await ws.send(json.dumps({
                    "jsonrpc": "2.0", "id": 3, "method": "createSession",
                    "params": {"channel": "ahp-session:/x", "provider": "nope"},
                }))
                msg = json.loads(await ws.recv())
                return msg["error"]

    err = asyncio.run(_bad())
    assert err["code"] == -32004
    assert "nope" in err["message"]


# ---------------------------------------------------------------------------
# HTTP + SSE 传输（WebView 等禁止明文 WS 的环境）


async def _http_scenario(fake_home: Path, tmp_path: Path) -> None:
    import uuid as _uuid

    host = AhpHost()
    port = await _start_host(host)
    base = f"http://127.0.0.1:{port}"
    client_id = "http-test"
    headers = {"Authorization": f"Bearer {host.auth_token}"}

    async with aiohttp.ClientSession() as http:
        async def rpc(method: str, params: dict) -> dict:
            async with http.post(f"{base}/rpc", params={"client": client_id}, headers=headers,
                                 json={"jsonrpc": "2.0", "id": _uuid.uuid4().hex[:6],
                                       "method": method, "params": params}) as r:
                assert r.status == 200, await r.text()
                return await r.json()

        # SSE 流（后台收集）
        events: list[str] = []
        async def sse() -> None:
            async with http.get(f"{base}/rpc/stream", params={"client": client_id}, headers=headers) as r:
                while True:
                    line = await r.content.readline()
                    if not line:
                        break
                    if line.startswith(b"data: "):
                        events.append(line[6:].decode().strip())
        sse_task = asyncio.create_task(sse())
        await asyncio.sleep(0.1)

        r = await rpc("initialize", {
            "channel": "ahp-root://", "protocolVersions": [PROTOCOL_VERSION],
            "clientId": client_id,
        })
        assert r["result"]["protocolVersion"] == PROTOCOL_VERSION
        providers = {a["provider"] for a in r["result"]["snapshots"][0]["state"]["agents"]}
        assert "zcode" in providers

        su = f"ahp-session:/{_uuid.uuid4()}"
        await rpc("createSession", {"channel": su, "provider": "claude",
                                    "workingDirectories": [tmp_path.as_uri()]})
        cu = f"ahp-chat:/{_uuid.uuid4()}"
        await rpc("createChat", {"channel": su, "chat": cu})
        await rpc("subscribe", {"channel": cu})
        # dispatch 是通知：POST 返回 204
        async with http.post(f"{base}/rpc", params={"client": client_id}, headers=headers, json={
            "jsonrpc": "2.0", "method": "dispatchAction",
            "params": {"channel": cu, "clientSeq": 1, "action": {
                "type": "chat/turnStarted", "turnId": "t-http",
                "startedAt": "2026-09-18T00:00:00Z",
                "message": {"text": "http hello", "origin": {"kind": "user"}}}},
        }) as r:
            assert r.status == 204

        # 等 SSE 收到 turnComplete
        for _ in range(100):
            if any('"chat/turnComplete"' in e for e in events):
                break
            await asyncio.sleep(0.05)
        deltas = [e for e in events if '"chat/delta"' in e]
        assert deltas, f"no deltas in SSE: {events[:3]}"
        assert any("claude-done" in e for e in deltas)
        sse_task.cancel()


def test_ahp_http_sse_transport(fake_harnesses, fake_home: Path, tmp_path: Path) -> None:
    asyncio.run(_http_scenario(fake_home, tmp_path))


def test_ahp_native_halter_runtime_audits_read_only_tools(
    fake_harnesses, fake_home: Path, tmp_path: Path
) -> None:
    """provider=halter runs halter's own model/tool loop instead of an external CLI."""
    from harness_config_manager.agent import FunctionModelClient, ModelResponse, ToolCall

    (tmp_path / "answer.txt").write_text("native-agent-ready\n", encoding="utf-8")

    seen_messages: list[list[dict]] = []

    responses = [
        ModelResponse(tool_calls=(ToolCall("call-native", "read_file", {"path": "answer.txt"}),)),
        ModelResponse(content="native final answer"),
        ModelResponse(content="second turn saw prior history"),
    ]

    def factory() -> FunctionModelClient:
        async def complete(**kwargs):
            seen_messages.append(list(kwargs["messages"]))
            return responses.pop(0)

        return FunctionModelClient(complete)

    async def _native() -> None:
        host = AhpHost(model_client_factory=factory)
        port = await _start_host(host)
        async with _connect(host, port) as ws:
            client = Client(ws)
            init = await client.request("initialize", {
                "channel": "ahp-root://", "protocolVersions": [PROTOCOL_VERSION],
                "clientId": "native-test", "initialSubscriptions": ["ahp-root://"],
            })
            providers = {
                a["provider"] for a in init["snapshots"][0]["state"]["agents"]
            }
            assert "halter" in providers
            native = next(
                a for a in init["snapshots"][0]["state"]["agents"]
                if a["provider"] == "halter"
            )
            assert native["_meta"]["halter:permissions"] == ["read-only", "workspace-write (approval required)"]

            su = f"ahp-session:/{uuid.uuid4()}"
            await client.request("createSession", {
                "channel": su, "provider": "halter",
                "workingDirectories": [tmp_path.as_uri()],
            })
            cu = f"ahp-chat:/{uuid.uuid4()}"
            await client.request("createChat", {"channel": su, "chat": cu})
            await client.request("subscribe", {"channel": cu})
            await client.notify("dispatchAction", {
                "channel": cu, "clientSeq": 7,
                "action": {
                    "type": "chat/turnStarted", "turnId": "native-turn",
                    "message": {
                        "text": "read answer.txt",
                        "origin": {"kind": "user"},
                        "model": {"id": "openai/test-model"},
                        "halter": {"mode": "yolo"},
                    },
                },
            })
            actions = await client.collect("action", timeout=10)
            while not any(
                p["params"]["action"]["type"] == "chat/turnComplete" for p in actions
            ):
                actions += await client.collect("action", timeout=10)
            by_type: dict[str, list[dict]] = {}
            for p in actions:
                by_type.setdefault(p["params"]["action"]["type"], []).append(p["params"])
            assert by_type["halter/toolCall"][0]["action"]["part"]["name"] == "read_file"
            assert by_type["halter/toolResult"][0]["action"]["part"]["ok"] is True
            deltas = "".join(p["action"]["content"] for p in by_type["chat/delta"])
            assert "native-agent-ready" not in deltas
            assert "native final answer" in deltas
            complete = by_type["chat/turnComplete"][0]["action"]
            assert complete["turnId"] == "native-turn"

            # A second turn on the same native chat receives the prior model/tool
            # history; this is the backend behavior used by follow-up UI messages.
            await client.notify("dispatchAction", {
                "channel": cu, "clientSeq": 8,
                "action": {
                    "type": "chat/turnStarted", "turnId": "native-turn-2",
                    "message": {
                        "text": "continue", "origin": {"kind": "user"},
                        "halter": {"mode": "safe"},
                    },
                },
            })
            second = await client.collect("action", timeout=10)
            while not any(
                p["params"]["action"]["type"] == "chat/turnComplete" for p in second
            ):
                second += await client.collect("action", timeout=10)
            assert any(
                p["params"]["action"]["type"] == "chat/delta"
                and "second turn saw prior history" in p["params"]["action"].get("content", "")
                for p in second
            )
            flattened = [json.dumps(m, ensure_ascii=False) for m in seen_messages[-1]]
            assert any("native final answer" in item for item in flattened)
            assert any("native-agent-ready" in item for item in flattened)

    asyncio.run(_native())
    audit_files = list((fake_home / ".config/halter/agent-audit").rglob("audit.jsonl"))
    assert audit_files and audit_files[0].stat().st_mode & 0o777 == 0o600


def test_ahp_external_runner_respects_dispatch_mode(
    fake_harnesses, fake_home: Path, tmp_path: Path
) -> None:
    """The desktop's selected mode reaches external harness argv, not only task cards."""
    _make_exec(fake_harnesses, "claude", 'echo "ARGS:$*"; echo done')

    async def _mode() -> None:
        host = AhpHost()
        port = await _start_host(host)
        async with _connect(host, port) as ws:
            client = Client(ws)
            await client.request("initialize", {
                "channel": "ahp-root://", "protocolVersions": [PROTOCOL_VERSION],
                "clientId": "mode-test",
            })
            su = f"ahp-session:/{uuid.uuid4()}"
            await client.request("createSession", {
                "channel": su, "provider": "claude",
                "workingDirectories": [tmp_path.as_uri()],
            })
            cu = f"ahp-chat:/{uuid.uuid4()}"
            await client.request("createChat", {"channel": su, "chat": cu})
            await client.request("subscribe", {"channel": cu})
            await client.notify("dispatchAction", {
                "channel": cu, "clientSeq": 1,
                "action": {
                    "type": "chat/turnStarted", "turnId": "turn-yolo",
                    "message": {
                        "text": "mode check", "origin": {"kind": "user"},
                        "halter": {"mode": "yolo"},
                    },
                },
            })
            actions = await client.collect("action", timeout=10)
            while not any(
                p["params"]["action"]["type"] == "chat/turnComplete" for p in actions
            ):
                actions += await client.collect("action", timeout=10)
            deltas = "".join(
                p["params"]["action"].get("content", "")
                for p in actions
                if p["params"]["action"]["type"] == "chat/delta"
            )
            assert "--dangerously-skip-permissions" in deltas

    asyncio.run(_mode())
    audit_files = list((fake_home / ".config/halter/agent-audit").rglob("audit.jsonl"))
    assert audit_files
    records = [
        json.loads(line)
        for line in audit_files[0].read_text(encoding="utf-8").splitlines()
    ]
    by_kind = {record["kind"]: record for record in records}
    assert by_kind["runner.started"]["mode"] == "yolo"
    assert by_kind["runner.started"]["provider"] == "claude"
    assert "ARGS:" in by_kind["runner.finished"]["output"]


def test_ahp_can_cancel_native_turn(fake_harnesses, fake_home: Path, tmp_path: Path) -> None:
    from harness_config_manager.agent import FunctionModelClient

    def factory() -> FunctionModelClient:
        async def complete(**_kwargs):
            await asyncio.Future()
            raise AssertionError("unreachable")

        return FunctionModelClient(complete)

    async def _cancel() -> None:
        host = AhpHost(model_client_factory=factory)
        port = await _start_host(host)
        async with _connect(host, port) as ws:
            client = Client(ws)
            await client.request("initialize", {
                "channel": "ahp-root://", "protocolVersions": [PROTOCOL_VERSION],
                "clientId": "cancel-test",
            })
            su = f"ahp-session:/{uuid.uuid4()}"
            await client.request("createSession", {
                "channel": su, "provider": "halter",
                "workingDirectories": [tmp_path.as_uri()],
            })
            cu = f"ahp-chat:/{uuid.uuid4()}"
            await client.request("createChat", {"channel": su, "chat": cu})
            await client.request("subscribe", {"channel": cu})
            await client.notify("dispatchAction", {
                "channel": cu, "clientSeq": 1,
                "action": {
                    "type": "chat/turnStarted", "turnId": "cancel-turn",
                    "message": {"text": "wait", "origin": {"kind": "user"}},
                },
            })
            for _ in range(100):
                actions = await client.collect("action", timeout=0.2)
                if any(p["params"]["action"]["type"] == "chat/responsePart" for p in actions):
                    break
            await client.notify("dispatchAction", {
                "channel": cu, "clientSeq": 2,
                "action": {"type": "chat/turnCancelled"},
            })
            for _ in range(100):
                actions += await client.collect("action", timeout=0.2)
                if any(p["params"]["action"]["type"] == "chat/error" for p in actions):
                    break
            errors = [p for p in actions if p["params"]["action"]["type"] == "chat/error"]
            assert errors
            assert errors[0]["params"]["action"]["part"]["error"]["errorType"] == "Cancelled"

    asyncio.run(_cancel())


def test_ahp_native_sessions_restore_after_host_restart(
    fake_harnesses, fake_home: Path, tmp_path: Path
) -> None:
    from harness_config_manager.agent import FunctionModelClient, ModelResponse

    def factory_once() -> FunctionModelClient:
        async def complete(**_kwargs):
            return ModelResponse(content="durable first answer")
        return FunctionModelClient(complete)

    seen_restored: list[list[dict]] = []

    def factory_restored() -> FunctionModelClient:
        async def complete(**kwargs):
            seen_restored.append(list(kwargs["messages"]))
            return ModelResponse(content="restored follow-up answer")
        return FunctionModelClient(complete)

    async def _run(host: AhpHost, prompt: str, turn_id: str) -> None:
        port = await _start_host(host)
        async with _connect(host, port) as ws:
            client = Client(ws)
            await client.request("initialize", {
                "channel": "ahp-root://", "protocolVersions": [PROTOCOL_VERSION],
                "clientId": "durable-test",
            })
            result = await client.request("listSessions", {"channel": "ahp-root://"})
            if turn_id == "turn-first":
                assert result["items"] == []
            else:
                assert result["items"] and result["items"][0]["resource"] == session_uri

            if turn_id == "turn-first":
                await client.request("createSession", {
                    "channel": session_uri, "provider": "halter",
                    "workingDirectories": [tmp_path.as_uri()],
                })
                await client.request("createChat", {
                    "channel": session_uri, "chat": chat_uri,
                })
            await client.request("subscribe", {"channel": chat_uri})
            await client.notify("dispatchAction", {
                "channel": chat_uri, "clientSeq": 1,
                "action": {
                    "type": "chat/turnStarted", "turnId": turn_id,
                    "message": {"text": prompt, "origin": {"kind": "user"}},
                },
            })
            actions = await client.collect("action", timeout=10)
            while not any(
                p["params"]["action"]["type"] == "chat/turnComplete" for p in actions
            ):
                actions += await client.collect("action", timeout=10)
            if turn_id != "turn-first":
                fetched = await client.request("fetchTurns", {"channel": chat_uri})
                assert len(fetched["turns"]) == 2
                assert fetched["turns"][0]["state"] == "complete"

    session_uri = f"ahp-session:/{uuid.uuid4()}"
    chat_uri = f"ahp-chat:/{uuid.uuid4()}"
    asyncio.run(_run(
        AhpHost(model_client_factory=factory_once),
        "remember api_key=super-secret-value-123",
        "turn-first",
    ))

    session_files = list((fake_home / ".config/halter/agent-sessions").rglob("session.json"))
    assert len(session_files) == 1
    assert session_files[0].stat().st_mode & 0o777 == 0o600
    assert "super-secret-value-123" not in session_files[0].read_text(encoding="utf-8")

    asyncio.run(_run(
        AhpHost(model_client_factory=factory_restored),
        "continue after restart",
        "turn-second",
    ))
    flattened = [json.dumps(m, ensure_ascii=False) for m in seen_restored[0]]
    assert any("durable first answer" in item for item in flattened)
    assert any("api_key=<REDACTED>" in item for item in flattened)


def test_native_apply_patch_requires_and_records_user_approval(
    fake_harnesses, fake_home: Path, tmp_path: Path
) -> None:
    import subprocess

    (tmp_path / "source.txt").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    patch = """diff --git a/source.txt b/source.txt
--- a/source.txt
+++ b/source.txt
@@ -1 +1 @@
-before
+after
"""
    from harness_config_manager.agent import FunctionModelClient, ModelResponse, ToolCall

    def factory() -> FunctionModelClient:
        responses = iter([
            ModelResponse(tool_calls=(ToolCall(
                "write-1", "apply_patch",
                {"patch": patch, "summary": "change source marker"},
            ),)),
            ModelResponse(content="approved patch applied"),
        ])

        async def complete(**_kwargs):
            return next(responses)

        return FunctionModelClient(complete)

    async def _approved() -> None:
        host = AhpHost(model_client_factory=factory)
        port = await _start_host(host)
        async with _connect(host, port) as ws:
            client = Client(ws)
            await client.request("initialize", {
                "channel": "ahp-root://", "protocolVersions": [PROTOCOL_VERSION],
                "clientId": "approval-test",
            })
            su = f"ahp-session:/{uuid.uuid4()}"
            await client.request("createSession", {
                "channel": su, "provider": "halter",
                "workingDirectories": [tmp_path.as_uri()],
            })
            cu = f"ahp-chat:/{uuid.uuid4()}"
            await client.request("createChat", {"channel": su, "chat": cu})
            await client.request("subscribe", {"channel": cu})
            await client.notify("dispatchAction", {
                "channel": cu, "clientSeq": 1,
                "action": {
                    "type": "chat/turnStarted", "turnId": "write-turn",
                    "message": {
                        "text": "edit source.txt", "origin": {"kind": "user"},
                        "halter": {"mode": "workspace-write"},
                    },
                },
            })
            actions = await client.collect("action", timeout=10)
            def finished(items):
                return any(p["params"]["action"]["type"] == "chat/turnComplete" for p in items)

            while not any(
                p["params"]["action"]["type"] in {"halter/approvalRequest", "chat/turnComplete"}
                for p in actions
            ):
                actions += await client.collect("action", timeout=10)
            completed = [p for p in actions if p["params"]["action"]["type"] == "chat/turnComplete"]
            assert not completed, "turn completed without approval"
            request = next(
                p["params"]["action"]["approval"] for p in actions
                if p["params"]["action"]["type"] == "halter/approvalRequest"
            )
            assert request["tool"] == "apply_patch"
            assert "before" in request["input"]["patch"]
            await client.notify("dispatchAction", {
                "channel": cu, "clientSeq": 2,
                "action": {
                    "type": "halter/approvalResponse",
                    "approvalId": request["id"], "approved": True,
                },
            })
            while not finished(actions):
                actions += await client.collect("action", timeout=10)
            by_type: dict[str, list[dict]] = {}
            for p in actions:
                by_type.setdefault(p["params"]["action"]["type"], []).append(p["params"])
            assert by_type["halter/approvalResult"][0]["action"]["approved"] is True
            assert by_type["halter/toolResult"][0]["action"]["part"]["ok"] is True
            result = by_type["halter/toolResult"][0]["action"]["part"]["result"]
            assert result["rollback"]["method"] == "reverse-exact-patch"
            assert (tmp_path / "source.txt").read_text(encoding="utf-8") == "after\n"

            await client.notify("dispatchAction", {
                "channel": cu, "clientSeq": 3,
                "action": {
                    "type": "halter/rollbackRequest",
                    "transactionId": result["transactionId"],
                },
            })
            rollback_actions = await client.collect("action", timeout=10)
            while not any(
                p["params"]["action"]["type"] == "halter/rollbackResult"
                for p in rollback_actions
            ):
                rollback_actions += await client.collect("action", timeout=10)
            rollback = next(
                p["params"]["action"] for p in rollback_actions
                if p["params"]["action"]["type"] == "halter/rollbackResult"
            )
            assert rollback["ok"] is True
            assert (tmp_path / "source.txt").read_text(encoding="utf-8") == "before\n"

            chat_snapshot = await client.request("subscribe", {"channel": cu})
            history = chat_snapshot["snapshot"]["state"]["approvalHistory"]
            assert history[0]["tool"] == "apply_patch"
            assert history[0]["status"] == "approved"
            restored = AhpHost(model_client_factory=factory)
            restored_snapshot = restored.snapshot(cu)
            assert restored_snapshot is not None
            assert restored_snapshot["state"]["approvalHistory"][0]["status"] == "approved"

    asyncio.run(_approved())
    transaction_files = list((fake_home / ".config/halter/agent-transactions").rglob("*.json"))
    assert len(transaction_files) == 1
    transaction_path = transaction_files[0]
    assert transaction_path.stat().st_mode & 0o777 == 0o600
    transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
    assert transaction["state"] == "rolled_back"
    assert transaction["rollback"]["fixedArgv"] == ["git", "apply", "-R"]
    assert "-before" in transaction["patch"]
    assert (tmp_path / "source.txt").read_text(encoding="utf-8") == "before\n"
    audit_files = list((fake_home / ".config/halter/agent-audit").rglob("audit.jsonl"))
    records = [
        json.loads(line)
        for line in audit_files[0].read_text(encoding="utf-8").splitlines()
    ]
    by_kind = {record["kind"]: record for record in records}
    assert by_kind["turn.started"]["permissionMode"] == "workspace-write"
    assert by_kind["approval.requested"]["tool"] == "apply_patch"
    assert by_kind["approval.response"]["approved"] is True
    assert by_kind["transaction.rolledBack"]["id"] == transaction["id"]


def test_native_apply_patch_is_denied_without_workspace_write_mode(
    fake_harnesses, fake_home: Path, tmp_path: Path
) -> None:
    (tmp_path / "readonly.txt").write_text("unchanged\n", encoding="utf-8")
    patch = """diff --git a/readonly.txt b/readonly.txt
--- a/readonly.txt
+++ b/readonly.txt
@@ -1 +1 @@
-unchanged
+changed
"""
    from harness_config_manager.agent import FunctionModelClient, ModelResponse, ToolCall

    def factory() -> FunctionModelClient:
        responses = iter([
            ModelResponse(tool_calls=(ToolCall(
                "write-denied", "apply_patch", {"patch": patch},
            ),)),
            ModelResponse(content="write was denied"),
        ])

        async def complete(**_kwargs):
            return next(responses)

        return FunctionModelClient(complete)

    async def _denied() -> None:
        host = AhpHost(model_client_factory=factory)
        port = await _start_host(host)
        async with _connect(host, port) as ws:
            client = Client(ws)
            await client.request("initialize", {
                "channel": "ahp-root://", "protocolVersions": [PROTOCOL_VERSION],
                "clientId": "deny-test",
            })
            su = f"ahp-session:/{uuid.uuid4()}"
            await client.request("createSession", {
                "channel": su, "provider": "halter",
                "workingDirectories": [tmp_path.as_uri()],
            })
            cu = f"ahp-chat:/{uuid.uuid4()}"
            await client.request("createChat", {"channel": su, "chat": cu})
            await client.request("subscribe", {"channel": cu})
            await client.notify("dispatchAction", {
                "channel": cu, "clientSeq": 1,
                "action": {
                    "type": "chat/turnStarted", "turnId": "deny-turn",
                    "message": {
                        "text": "try to edit", "origin": {"kind": "user"},
                        "halter": {"mode": "safe"},
                    },
                },
            })
            actions = await client.collect("action", timeout=10)
            while not any(
                p["params"]["action"]["type"] == "chat/turnComplete" for p in actions
            ):
                actions += await client.collect("action", timeout=10)
            requests = [p for p in actions if p["params"]["action"]["type"] == "halter/approvalRequest"]
            assert requests == []
            results = [p for p in actions if p["params"]["action"]["type"] == "halter/toolResult"]
            assert results
            assert results[0]["params"]["action"]["part"]["ok"] is False
            assert "workspace-write" in results[0]["params"]["action"]["part"]["result"]["message"]

    asyncio.run(_denied())
    assert (tmp_path / "readonly.txt").read_text(encoding="utf-8") == "unchanged\n"


def test_native_availability_accepts_configured_local_provider(fake_home: Path) -> None:
    cfg_path = fake_home / ".config/halter/config.toml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        '[model_providers.local]\nbase_url = "http://127.0.0.1:11434/v1"\napi_key_env = ""\n',
        encoding="utf-8",
    )
    host = AhpHost()
    native = next(
        a for a in host.root_state()["agents"] if a["provider"] == "halter"
    )
    assert native["_meta"]["halter:available"] is True


async def _auth_failure_scenario() -> None:
    host = AhpHost(auth_token="test-token")
    port = await _start_host(host)
    base = f"http://127.0.0.1:{port}"
    async with aiohttp.ClientSession() as http:
        async with http.post(
            f"{base}/rpc", json={"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}
        ) as response:
            assert response.status == 401
        async with http.post(
            f"{base}/rpc",
            headers={"Authorization": "Bearer wrong", "Origin": "https://evil.example"},
            json={"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}},
        ) as response:
            assert response.status == 403


def test_ahp_rejects_missing_token_and_browser_origin(fake_home: Path) -> None:
    asyncio.run(_auth_failure_scenario())


def test_ahp_rejects_browser_origin_even_with_token(fake_home: Path) -> None:
    async def _evil_origin() -> None:
        host = AhpHost(auth_token="test-token")
        port = await _start_host(host)
        try:
            async with websockets.connect(
                f"ws://127.0.0.1:{port}",
                origin="https://evil.example",
                additional_headers={"Authorization": f"Bearer {host.auth_token}"},
            ):
                raise AssertionError("disallowed browser origin connected")
        except Exception as exc:
            assert "403" in str(exc)

    asyncio.run(_evil_origin())


def test_ahp_token_file_is_private_and_atomic(fake_home: Path, tmp_path: Path) -> None:
    path = tmp_path / "nested" / "ahp-token"
    write_auth_token(path, "secret-token")
    assert path.read_text(encoding="utf-8") == "secret-token"
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(path.parent.glob(".ahp-token.*.tmp"))


def test_native_plan_is_broadcast_and_restored(fake_harnesses, fake_home: Path, tmp_path: Path) -> None:
    from harness_config_manager.agent import FunctionModelClient, ModelResponse, ToolCall

    responses = iter([
        ModelResponse(tool_calls=(ToolCall(
            "plan", "update_plan",
            {"steps": [{"title": "Inspect", "status": "in_progress"}], "note": "active"},
        ),)),
        ModelResponse(content="plan ready"),
    ])

    def factory() -> FunctionModelClient:
        async def complete(**_kwargs):
            return next(responses)
        return FunctionModelClient(complete)

    async def _run() -> None:
        host = AhpHost(model_client_factory=factory)
        port = await _start_host(host)
        async with _connect(host, port) as ws:
            client = Client(ws)
            await client.request("initialize", {
                "channel": "ahp-root://", "protocolVersions": [PROTOCOL_VERSION],
                "clientId": "plan-test",
            })
            su = f"ahp-session:/{uuid.uuid4()}"
            await client.request("createSession", {
                "channel": su, "provider": "halter",
                "workingDirectories": [tmp_path.as_uri()],
            })
            cu = f"ahp-chat:/{uuid.uuid4()}"
            await client.request("createChat", {"channel": su, "chat": cu})
            await client.request("subscribe", {"channel": cu})
            await client.notify("dispatchAction", {
                "channel": cu, "clientSeq": 1,
                "action": {
                    "type": "chat/turnStarted", "turnId": "plan-turn",
                    "message": {"text": "plan", "origin": {"kind": "user"}},
                },
            })
            actions = await client.collect("action", timeout=10)
            while not any(
                p["params"]["action"]["type"] == "halter/planChanged" for p in actions
            ):
                actions += await client.collect("action", timeout=10)
            plan = next(
                p["params"]["action"]["plan"] for p in actions
                if p["params"]["action"]["type"] == "halter/planChanged"
            )
            assert plan["steps"][0]["title"] == "Inspect"
            while not any(
                p["params"]["action"]["type"] == "chat/turnComplete" for p in actions
            ):
                actions += await client.collect("action", timeout=10)

            restored = AhpHost(model_client_factory=factory)
            snapshot = restored.snapshot(cu)
            assert snapshot is not None
            assert snapshot["state"]["currentPlan"]["steps"][0]["status"] == "in_progress"

    asyncio.run(_run())
