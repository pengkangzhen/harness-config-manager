"""hcm 自建 AHP（Agent Host Protocol）服务器。

把 claude / codex / zcode / opencode 无头 runner 挂载为 AHP agent backend，
任何 AHP 客户端（VS Code Agents 窗口、AHPX、官方 client 库）都可以：

  initialize -> listSessions / createSession(provider) -> createChat
  -> dispatchAction(chat/turnStarted) -> 流式收到 chat/responsePart / chat/delta
  -> chat/turnComplete

协议实现遵循 Agent Host Protocol 0.9.0（microsoft/agent-host-protocol）：
- 传输：WebSocket 文本帧，每帧一条 JSON-RPC 2.0 消息
- 通道：ahp-root:// / ahp-session:/<uuid> / ahp-chat:/<uuid>
- 同步：服务端单调 serverSeq 广播 action 信封；客户端 dispatch 带 origin 回显
- 状态：root（agents 目录）/ session（生命周期+chat 目录）/ chat（turns）
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets as secret_token
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import aiohttp
import aiohttp.web
import websockets

from .config import load_config
from .agent import (
    AgentRuntime, AuditLog, ModelClient, OpenAICompatibleModelClient,
    TransactionStore, _safe_session_id, _truncate_text,
    transaction_root_for_session,
)
from .model_health import check_model_health, is_local_base_url, resolve_route
from .agent_store import AgentSessionStore
from .runner import RUNNERS, RunnerSpec

PROTOCOL_VERSION = "0.9.0"
SERVER_INFO = {"name": "halter-agent-host", "version": "0.1.0", "title": "halter Agent Host"}

# 会话状态位（规范 SessionStatus）
STATUS_IDLE = 1
STATUS_ERROR = 2
STATUS_IN_PROGRESS = 8

ROOT_URI = "ahp-root://"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _short(text: str, limit: int = 60) -> str:
    return (text[: limit - 1] + "…") if len(text) > limit else text


@dataclass
class AhpChat:
    uri: str
    session: "AhpSession"
    title: str = "New chat"
    status: int = STATUS_IDLE
    activity: str | None = None
    turns: list[dict[str, Any]] = field(default_factory=list)
    active_turn: dict[str, Any] | None = None
    turn_claimed: bool = False
    proc: asyncio.subprocess.Process | None = None
    agent_task: asyncio.Task[None] | None = None
    agent_messages: list[dict[str, Any]] = field(default_factory=list)
    current_plan: dict[str, Any] | None = None
    approvals: dict[str, asyncio.Future[bool]] = field(default_factory=dict)
    approval_history: list[dict[str, Any]] = field(default_factory=list)
    # plan step id -> {toolCallIds, approvalIds, transactionIds,
    # auditTimestamps, updatedAt}; durable evidence for the current plan.
    plan_evidence: dict[str, Any] = field(default_factory=dict)
    modified_at: str = field(default_factory=_now_iso)

    def state(self) -> dict[str, Any]:
        return {
            "resource": self.uri,
            "title": self.title,
            "status": self.status,
            "activity": self.activity,
            "modifiedAt": self.modified_at,
            "turns": self.turns,
            "activeTurn": self.active_turn,
            "currentPlan": self.current_plan,
            "approvalHistory": self.approval_history,
            "planEvidence": self.plan_evidence,
            "auditSessionId": _safe_session_id(self.session.uri),
        }

    def summary(self) -> dict[str, Any]:
        return {
            "resource": self.uri,
            "title": self.title,
            "status": self.status,
            "activity": self.activity,
            "modifiedAt": self.modified_at,
        }


@dataclass
class AhpSession:
    uri: str
    provider: str
    title: str
    lifecycle: str = "creating"
    status: int = STATUS_IDLE
    activity: str | None = None
    working_directories: list[str] = field(default_factory=list)
    chats: dict[str, AhpChat] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)
    modified_at: str = field(default_factory=_now_iso)

    def summary(self) -> dict[str, Any]:
        return {
            "resource": self.uri,
            "provider": self.provider,
            "title": self.title,
            "status": self.status,
            "activity": self.activity,
            "createdAt": self.created_at,
            "modifiedAt": self.modified_at,
            "workingDirectories": self.working_directories,
        }

    def state(self) -> dict[str, Any]:
        d = self.summary()
        d.update({
            "lifecycle": self.lifecycle,
            "chats": [c.summary() for c in self.chats.values()],
            "defaultChat": next(iter(self.chats)) if len(self.chats) == 1 else None,
        })
        return d


@dataclass
class AhpConn:
    """一条逻辑客户端连接（WS，或 HTTP+SSE 组合）。"""
    client_id: str
    outbox: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=1000))
    alive: bool = True
    streaming: bool = False
    last_seen: float = field(default_factory=time.monotonic)


class AhpHost:
    """AHP 服务器实例：连接、状态、广播与 harness 执行。"""

    def __init__(self, home: Path | None = None, model_client_factory: Callable[[], ModelClient] | None = None,
                 auth_token: str | None = None) -> None:
        self._home = home
        # Every transport can execute a local runner, so every transport must
        # present this process-local bearer token. Non-browser clients may omit
        # Origin; browser clients must match the explicitly allow-listed origin.
        self.auth_token = auth_token or secret_token.token_urlsafe(32)
        self.sessions: dict[str, AhpSession] = {}
        self.connections: dict[str, AhpConn] = {}
        self.subscriptions: dict[str, set[str]] = {}   # client_id -> channel uris
        self.server_seq: int = 0
        self._seq_lock = asyncio.Lock()
        self._turn_tasks: set[asyncio.Task[None]] = set()
        self._cfg = load_config()
        self._store = AgentSessionStore(home)
        self.sessions = {session.uri: session for session in self._store.load_all()}
        self._model_client_factory = model_client_factory or (
            lambda: OpenAICompatibleModelClient(self._cfg)
        )
        self._native_injected = model_client_factory is not None

    # ------------------------------------------------------------------
    # 状态快照

    def _native_model_available(self) -> bool:
        prefixes = list(self._cfg.model_providers) + [None]
        seen: set[str | None] = set()
        for prefix in prefixes:
            if prefix in seen:
                continue
            seen.add(prefix)
            model = f"{prefix}/availability-check" if prefix is not None else None
            route = resolve_route(model, self._cfg)
            if route.error or not route.base_url:
                continue
            key = os.environ.get(route.api_key_env, "") if route.api_key_env else ""
            if key or is_local_base_url(route.base_url):
                return True
        return False

    def _native_health_summary(self) -> dict[str, Any]:
        report = check_model_health(self._cfg)
        return {
            "available": report["available"],
            "model": report["model"],
            "issues": [
                f"{check['label']}: {check['detail']}"
                for check in report["checks"] if check["status"] == "error"
            ],
        }

    def root_state(self) -> dict[str, Any]:
        agents = []
        for key, spec in RUNNERS.items():
            available = spec.resolve() is not None
            model_id = self._cfg.models.get(key)
            models = [{
                "id": model_id or "default",
                "provider": key,
                "name": model_id or f"{spec.display} default",
            }]
            agents.append({
                "provider": key,
                "displayName": spec.display + ("" if available else " (unavailable)"),
                "description": f"halter headless runner for {spec.display}",
                "models": models,
                "_meta": {"halter:available": available, "halter:key": key},
            })

        # The native provider does not wrap an external CLI. It runs halter's
        # auditable read-only model/tool loop inside the selected workspace.
        native_model = self._cfg.models.get("halter")
        agents.append({
            "provider": "halter",
            "displayName": "halter Native",
            "description": "Auditable native halter agent runtime with approval-gated tools",
            "models": [{
                "id": native_model or "default",
                "provider": "halter",
                "name": native_model or "Configured native model",
            }],
            "_meta": {
                "halter:available": self._native_injected or self._native_model_available(),
                "halter:key": "halter",
                "halter:health": self._native_health_summary(),
                "halter:tools": [
                    "update_plan", "read_file", "list_dir", "search_files", "git_status", "git_diff", "list_project_sessions", "read_project_session", "delegate_harness", "apply_patch", "run_tests", "run_tests_workspace",
                ],
                "halter:permissions": ["read-only", "workspace-write (approval required)"],
            },
        })
        return {"agents": agents, "activeSessions": len(self.sessions)}

    def snapshot(self, channel: str) -> dict[str, Any] | None:
        if channel == ROOT_URI:
            state: dict[str, Any] = self.root_state()
        elif channel.startswith("ahp-session:/"):
            sess = self.sessions.get(channel)
            if sess is None:
                return None
            state = sess.state()
        elif channel.startswith("ahp-chat:/"):
            chat = self._chat(channel)
            if chat is None:
                return None
            state = chat.state()
        else:
            return None
        return {"resource": channel, "state": state, "fromSeq": self.server_seq}

    def _chat(self, uri: str) -> AhpChat | None:
        for sess in self.sessions.values():
            if uri in sess.chats:
                return sess.chats[uri]
        return None

    # ------------------------------------------------------------------
    # 广播

    async def _next_seq(self) -> int:
        async with self._seq_lock:
            self.server_seq += 1
            return self.server_seq

    async def broadcast_action(self, channel: str, action: dict[str, Any],
                               origin: dict[str, Any] | None = None) -> int:
        seq = await self._next_seq()
        params = {"channel": channel, "action": action, "serverSeq": seq}
        if origin:
            params["origin"] = origin
        await self._broadcast("action", params)
        return seq

    async def broadcast_notification(self, method: str, params: dict[str, Any]) -> None:
        await self._broadcast(method, params)

    async def _broadcast(self, method: str, params: dict[str, Any]) -> None:
        channel = params.get("channel", "")
        message = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        for client_id, conn in list(self.connections.items()):
            if channel not in self.subscriptions.get(client_id, set()):
                continue
            if not conn.alive or (
                not conn.streaming and time.monotonic() - conn.last_seen > 300
            ):
                self.connections.pop(client_id, None)
                self.subscriptions.pop(client_id, None)
                conn.alive = False
                continue
            try:
                conn.outbox.put_nowait(message)
            except asyncio.QueueFull:
                # A client that cannot drain its stream must reconnect; keeping it
                # alive would let one slow browser tab consume unbounded memory.
                conn.alive = False
                self.connections.pop(client_id, None)
                self.subscriptions.pop(client_id, None)

    # ------------------------------------------------------------------
    # 连接生命周期

    async def route_message(self, conn: AhpConn, msg: dict[str, Any]) -> dict[str, Any] | None:
        """处理一条客户端消息；请求返回响应 dict，通知返回 None。"""
        client_id = conn.client_id
        if not isinstance(msg, dict):
            return {"jsonrpc": "2.0", "id": None, "error": {
                "code": -32600, "message": "Invalid Request: message must be an object"
            }}
        if "id" in msg:
            result, error = await self._handle_request(client_id, msg)
            reply: dict[str, Any] = {"jsonrpc": "2.0", "id": msg["id"]}
            if error is not None:
                reply["error"] = error
            else:
                reply["result"] = result
            return reply
        await self._handle_notification(client_id, msg)
        return None

    def new_conn(self, client_id: str | None = None) -> AhpConn:
        conn = AhpConn(client_id or f"client-{uuid.uuid4().hex[:8]}")
        self.connections[conn.client_id] = conn
        self.subscriptions[conn.client_id] = {ROOT_URI}
        return conn

    def drop_conn(self, conn: AhpConn) -> None:
        conn.alive = False
        self.connections.pop(conn.client_id, None)
        self.subscriptions.pop(conn.client_id, None)

    async def _handle_request(self, client_id: str,
                              msg: dict[str, Any]) -> tuple[Any, dict[str, Any] | None]:
        method = msg.get("method", "")
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            return None, {"code": -32600, "message": "Invalid params: expected an object"}
        try:
            handler = getattr(self, f"_cmd_{method.replace('/', '_')}", None)
            if handler is None:
                return None, {"code": -32601, "message": f"Method not found: {method}"}
            return await handler(client_id, params), None
        except AhpError as e:
            return None, {"code": e.code, "message": e.message, "data": e.data}
        except Exception as e:  # pragma: no cover - 防御
            return None, {"code": -32603, "message": f"Internal error: {e}"}

    async def _handle_notification(self, client_id: str, msg: dict[str, Any]) -> None:
        method = msg.get("method", "")
        params = msg.get("params") or {}
        if not isinstance(method, str) or not isinstance(params, dict):
            return
        if method == "dispatchAction":
            await self._on_dispatch_action(client_id, params)
        elif method == "unsubscribe":
            channel = params.get("channel", "")
            self.subscriptions.get(client_id, set()).discard(channel)

    # ------------------------------------------------------------------
    # 命令

    async def _cmd_initialize(self, client_id: str, params: dict[str, Any]) -> Any:
        versions = params.get("protocolVersions") or []
        if PROTOCOL_VERSION not in versions:
            raise AhpError(-32005, "Unsupported protocol version",
                           {"supportedVersions": [PROTOCOL_VERSION]})
        # 规范：clientId 由客户端在 initialize 提供（重连标识）；服务端采用之
        supplied = params.get("clientId")
        if isinstance(supplied, str) and supplied and supplied != client_id:
            if len(supplied) > 256:
                raise AhpError(-32002, "clientId is too long")
            conn = self.connections.pop(client_id, None)
            existing = self.connections.pop(supplied, None)
            if existing is not None and existing is not conn:
                existing.alive = False
            subs_existing = self.subscriptions.pop(client_id, set())
            if conn is not None:
                conn.client_id = supplied
                self.connections[supplied] = conn
                self.subscriptions[supplied] = subs_existing
                client_id = supplied
        subs = params.get("initialSubscriptions") or [ROOT_URI]
        if not isinstance(subs, list) or not all(isinstance(item, str) for item in subs):
            raise AhpError(-32002, "initialSubscriptions must be a string array")
        self.subscriptions[client_id] = set(subs)
        snapshots = [s for s in (self.snapshot(ch) for ch in subs) if s is not None]
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "serverSeq": self.server_seq,
            "serverInfo": SERVER_INFO,
            "defaultDirectory": Path.cwd().as_uri(),
            "snapshots": snapshots,
        }

    async def _cmd_ping(self, client_id: str, params: dict[str, Any]) -> Any:
        return {}

    async def _cmd_listSessions(self, client_id: str, params: dict[str, Any]) -> Any:
        items = sorted(self.sessions.values(), key=lambda s: s.modified_at, reverse=True)
        return {"items": [s.summary() for s in items], "total": len(items)}

    async def _cmd_subscribe(self, client_id: str, params: dict[str, Any]) -> Any:
        channel = params.get("channel", "")
        snap = self.snapshot(channel)
        if snap is None:
            raise AhpError(-32001, f"Unknown channel: {channel}")
        self.subscriptions.setdefault(client_id, set()).add(channel)
        return {"snapshot": snap}

    async def _cmd_createSession(self, client_id: str, params: dict[str, Any]) -> Any:
        uri = params.get("channel", "")
        if not uri.startswith("ahp-session:/"):
            raise AhpError(-32002, f"Invalid session URI: {uri}")
        if uri in self.sessions:
            raise AhpError(-32003, f"Session already exists: {uri}")
        provider = params.get("provider", "")
        if provider != "halter" and provider not in RUNNERS:
            raise AhpError(-32004, f"No agent for provider: {provider}")
        dirs = params.get("workingDirectories") or []
        workdir = dirs[0] if dirs else Path.cwd().as_uri()
        session = AhpSession(
            uri=uri, provider=provider,
            title=f"hcm @{provider}",
            working_directories=[workdir],
        )
        self.sessions[uri] = session
        self.subscriptions.setdefault(client_id, set()).add(uri)
        # root 目录通知 + 生命周期就绪
        await self.broadcast_notification("root/sessionAdded", {
            "channel": ROOT_URI, "summary": session.summary(),
        })
        session.lifecycle = "ready"
        await self.broadcast_action(uri, {"type": "session/lifecycleChanged", "lifecycle": "ready"})
        self._store.save(session)
        return {"snapshot": self.snapshot(uri)}

    async def _cmd_createChat(self, client_id: str, params: dict[str, Any]) -> Any:
        session_uri = params.get("channel", "")
        session = self.sessions.get(session_uri)
        if session is None:
            raise AhpError(-32001, f"Unknown session: {session_uri}")
        chat_uri = params.get("chat", "")
        if not chat_uri.startswith("ahp-chat:/"):
            raise AhpError(-32002, f"Invalid chat URI: {chat_uri}")
        if chat_uri in session.chats:
            raise AhpError(-32003, f"Chat already exists: {chat_uri}")
        chat = AhpChat(uri=chat_uri, session=session)
        initial = params.get("initialMessage") or {}
        if initial.get("text"):
            chat.title = _short(initial["text"])
        session.chats[chat_uri] = chat
        self.subscriptions.setdefault(client_id, set()).add(chat_uri)
        await self.broadcast_action(session_uri, {
            "type": "session/chatAdded", "summary": chat.summary(),
        })
        self._store.save(session)
        return {"snapshot": self.snapshot(chat_uri)}

    async def _cmd_fetchTurns(self, client_id: str, params: dict[str, Any]) -> Any:
        chat = self._chat(params.get("channel", ""))
        if chat is None:
            raise AhpError(-32001, "Unknown chat")
        return {"turns": chat.turns}

    async def _cmd_disposeSession(self, client_id: str, params: dict[str, Any]) -> Any:
        uri = params.get("channel", "")
        session = self.sessions.pop(uri, None)
        if session is None:
            raise AhpError(-32001, f"Unknown session: {uri}")
        self._store.delete(uri)
        await self.broadcast_notification("root/sessionRemoved", {"channel": ROOT_URI, "session": uri})
        return {}

    # ------------------------------------------------------------------
    # dispatchAction：chat/turnStarted -> 执行 harness -> 流式回传

    async def _on_dispatch_action(self, client_id: str, params: dict[str, Any]) -> None:
        channel = params.get("channel", "")
        action = params.get("action") or {}
        origin = {"clientId": client_id, "clientSeq": params.get("clientSeq", 0)}
        atype = action.get("type", "")

        if atype == "chat/turnStarted":
            chat = self._chat(channel)
            if chat is None or chat.active_turn is not None or chat.turn_claimed:
                return
            # Claim synchronously: create_task may not start before another
            # dispatch notification is handled on the same event loop.
            chat.turn_claimed = True
            # A turn must not consume the inbound message loop: approvals and
            # cancellation arrive as later notifications while it is running.
            turn_task = asyncio.create_task(self._run_turn(chat, action, origin))
            self._turn_tasks.add(turn_task)
            turn_task.add_done_callback(self._turn_tasks.discard)
        elif atype == "halter/rollbackRequest":
            chat = self._chat(channel)
            if chat is not None:
                rollback_task = asyncio.create_task(
                    self._rollback_native_transaction(chat, action, origin)
                )
                self._turn_tasks.add(rollback_task)
                rollback_task.add_done_callback(self._turn_tasks.discard)
        elif atype == "halter/approvalResponse":
            chat = self._chat(channel)
            approval_id = str(action.get("approvalId") or "")
            future = chat.approvals.get(approval_id) if chat else None
            if future is not None and not future.done():
                future.set_result(bool(action.get("approved")))
        elif atype == "chat/turnCancelled":
            chat = self._chat(channel)
            if chat:
                for future in chat.approvals.values():
                    if not future.done():
                        future.cancel()
                chat.approvals.clear()
            if chat and chat.proc is not None:
                try:
                    os.killpg(os.getpgid(chat.proc.pid), 15)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
            if chat and chat.agent_task is not None:
                chat.agent_task.cancel()

    async def _rollback_native_transaction(
        self, chat: AhpChat, action: dict[str, Any], origin: dict[str, Any]
    ) -> None:
        import re as _re

        transaction_id = str(action.get("transactionId") or "")
        audit = AuditLog(chat.session.uri)
        try:
            if not _re.fullmatch(r"[A-Za-z0-9._-]+", transaction_id):
                raise ValueError("invalid transaction id")
            store = TransactionStore(
                transaction_root_for_session(chat.session.uri, self._home)
            )
            transaction_path = store.path(transaction_id)
            transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
            workdir = chat.session.working_directories[0] if chat.session.working_directories else "."
            workspace = Path(_file_uri_to_path(workdir)).expanduser().resolve(strict=True)
            if transaction.get("workspace") != str(workspace):
                raise PermissionError("transaction belongs to a different workspace")
            if transaction.get("state") != "applied":
                raise RuntimeError(f"transaction is not reversible in state {transaction.get('state')!r}")
            patch = str(((transaction.get("rollback") or {}).get("exactPatch")))

            def _run() -> subprocess.CompletedProcess[str]:
                import subprocess as _subprocess

                checked = _subprocess.run(
                    ["git", "apply", "-R", "--check", "--whitespace=nowarn"],
                    input=patch, cwd=workspace, text=True, capture_output=True,
                    timeout=15, check=False,
                )
                if checked.returncode != 0:
                    return checked
                return _subprocess.run(
                    ["git", "apply", "-R", "--whitespace=nowarn"],
                    input=patch, cwd=workspace, text=True, capture_output=True,
                    timeout=15, check=False,
                )

            proc = await asyncio.to_thread(_run)
            if proc.returncode != 0:
                raise RuntimeError(
                    _truncate_text(proc.stderr.strip() or proc.stdout.strip() or "reverse patch failed")
                )
            transaction["state"] = "rolled_back"
            transaction["rolledBackAt"] = _now_iso()
            store.save(transaction)
            plan_step_id = transaction.get("planStepId")
            await audit.append(
                "transaction.rolledBack", id=transaction_id,
                workspace=str(workspace), origin=origin,
                **({"planStepId": plan_step_id} if plan_step_id else {}),
            )
            await self.broadcast_action(chat.uri, {
                "type": "halter/rollbackResult", "transactionId": transaction_id,
                "ok": True,
                **({"planStepId": plan_step_id} if plan_step_id else {}),
            })
        except Exception as exc:
            await audit.append(
                "transaction.rollbackFailed", id=transaction_id,
                errorType=type(exc).__name__, message=str(exc), origin=origin,
            )
            await self.broadcast_action(chat.uri, {
                "type": "halter/rollbackResult", "transactionId": transaction_id,
                "ok": False, "error": {
                    "errorType": type(exc).__name__, "message": str(exc),
                },
            })

    async def _run_turn(self, chat: AhpChat, action: dict[str, Any],
                        origin: dict[str, Any]) -> None:
        session = chat.session
        turn_id = action.get("turnId") or f"t-{uuid.uuid4().hex[:8]}"
        message = action.get("message") or {}
        prompt = message.get("text", "")
        model = (message.get("model") or {}).get("id")
        if model in ("default", ""):
            model = None
        raw_mode = ((message.get("halter") or {}).get("mode") or "safe")
        mode = "yolo" if raw_mode in {"yolo", "workspace-write", "full"} else "safe"

        started = time.monotonic()
        chat.status = STATUS_IN_PROGRESS
        chat.activity = f"running @{session.provider}"
        session.status = STATUS_IN_PROGRESS
        await self.broadcast_action(chat.uri, {
            "type": "chat/turnStarted", "turnId": turn_id,
            "startedAt": action.get("startedAt") or _now_iso(),
            "message": message,
        }, origin)
        await self.broadcast_action(chat.uri, {
            "type": "chat/activityChanged", "activity": chat.activity,
        })

        turn: dict[str, Any] = {
            "id": turn_id,
            "startedAt": action.get("startedAt") or _now_iso(),
            "message": message,
            "responseParts": [],
            "usage": None,
            "state": "active",
        }
        chat.active_turn = turn
        if not chat.title or chat.title == "New chat":
            chat.title = _short(prompt)

        if session.provider == "halter":
            try:
                workdir = session.working_directories[0] if session.working_directories else "."
                native_workspace = Path(_file_uri_to_path(workdir)).expanduser().resolve(strict=True)
            except OSError as e:
                part = {
                    "kind": "error", "id": f"p-{uuid.uuid4().hex[:8]}",
                    "error": {"errorType": "WorkspaceError", "message": str(e)},
                }
                await self._finish_turn(chat, turn_id, started, "error", part)
                return
            chat.agent_task = asyncio.create_task(
                self._run_native_turn(
                    chat, turn, started, prompt, model, native_workspace,
                    "workspace-write" if raw_mode == "workspace-write" else "read-only",
                )
            )
            return

        spec: RunnerSpec = RUNNERS[session.provider]
        prefix = spec.resolve()
        argv: list[str] | None = None
        audit = AuditLog(session.uri)
        external_output: list[str] = []
        try:
            if prefix is None:
                raise RuntimeError(f"@{session.provider} runner unavailable")
            workdir = session.working_directories[0] if session.working_directories else "."
            local_dir = _file_uri_to_path(workdir)
            argv = spec.build_argv(prefix, prompt, Path(local_dir), mode, model)
            await audit.append(
                "runner.started", provider=session.provider, mode=mode,
                model=model, argv=argv,
            )
            chat.proc = await asyncio.create_subprocess_exec(
                *argv, cwd=local_dir,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        except Exception as e:
            await audit.append(
                "runner.failed", provider=session.provider,
                errorType=type(e).__name__, message=str(e),
            )
            part = {"kind": "error", "id": f"p-{uuid.uuid4().hex[:8]}",
                    "error": {"errorType": "SpawnError", "message": str(e)}}
            await self._finish_turn(chat, turn_id, started, "error", part)
            return

        part_id = f"p-{uuid.uuid4().hex[:8]}"
        markdown_part = {"kind": "markdown", "id": part_id, "content": ""}
        turn["responseParts"].append(markdown_part)
        await self.broadcast_action(chat.uri, {
            "type": "chat/responsePart", "turnId": turn_id,
            "part": {"kind": "markdown", "id": part_id, "content": ""},
        })
        proc = chat.proc
        assert proc is not None and proc.stdout is not None
        # 逐行流式回传：每读到一行就广播 delta，并累积进状态（供后续订阅快照）
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace")
            markdown_part["content"] += line
            external_output.append(line)
            await self.broadcast_action(chat.uri, {
                "type": "chat/delta", "turnId": turn_id, "partId": part_id,
                "content": line,
            })
        code = await proc.wait()
        chat.proc = None
        await audit.append(
            "runner.finished", provider=session.provider, exitCode=code,
            output=_truncate_text("".join(external_output)),
        )
        if code == 0:
            await self._finish_turn(chat, turn_id, started, "complete")
        else:
            part = {"kind": "error", "id": f"p-{uuid.uuid4().hex[:8]}",
                    "error": {"errorType": "HarnessExit",
                              "message": f"exit code {code}"}}
            await self._finish_turn(chat, turn_id, started, "error", part)

    def _record_plan_evidence(
        self, chat: AhpChat, step_id: Any, bucket_key: str, entry: dict[str, Any]
    ) -> bool:
        """Append one evidence entry to chat.plan_evidence; True when changed."""
        step = str(step_id or "").strip()
        if not step:
            return False
        bucket = chat.plan_evidence.get(step)
        if bucket is None:
            bucket = {
                "toolCallIds": [],
                "approvalIds": [],
                "transactionIds": [],
                "auditTimestamps": [],
                "updatedAt": _now_iso(),
            }
            chat.plan_evidence[step] = bucket
        bucket.setdefault(bucket_key, []).append(entry)
        bucket["auditTimestamps"].append(_now_iso())
        bucket["updatedAt"] = _now_iso()
        return True

    async def _broadcast_plan_evidence(self, chat: AhpChat, turn_id: str) -> None:
        await self.broadcast_action(chat.uri, {
            "type": "halter/planEvidence",
            "turnId": turn_id,
            "evidence": chat.plan_evidence,
        })

    async def _run_native_turn(self, chat: AhpChat, turn: dict[str, Any],
                               started: float, prompt: str,
                               model: str | None, workspace: Path,
                               permission_mode: str = "read-only") -> None:
        """Run halter's own model/tool loop with AHP-compatible events."""
        part_id = f"p-{uuid.uuid4().hex[:8]}"
        markdown_part = {"kind": "markdown", "id": part_id, "content": ""}
        turn["responseParts"].append(markdown_part)
        await self.broadcast_action(chat.uri, {
            "type": "chat/responsePart", "turnId": turn["id"],
            "part": {"kind": "markdown", "id": part_id, "content": ""},
        })

        async def emit(event: dict[str, Any]) -> None:
            event_type = event.get("type")
            if event_type == "assistant_delta":
                content = str(event.get("content") or "")
                markdown_part["content"] += content
                await self.broadcast_action(chat.uri, {
                    "type": "chat/delta", "turnId": turn["id"], "partId": part_id,
                    "content": content,
                })
            elif event_type == "plan_updated":
                plan = {
                    "steps": event.get("steps") or [],
                    "note": str(event.get("note") or ""),
                    "updatedAt": _now_iso(),
                }
                chat.current_plan = plan
                turn["responseParts"].append({
                    "kind": "halter/plan", "plan": plan,
                })
                await self.broadcast_action(chat.uri, {
                    "type": "halter/planChanged", "turnId": turn["id"], "plan": plan,
                })
            elif event_type == "approval_request":
                request = dict(event)
                request.pop("type", None)
                await self.broadcast_action(chat.uri, {
                    "type": "halter/approvalRequest",
                    "turnId": turn["id"], "approval": request,
                })
            elif event_type == "approval_result":
                await self.broadcast_action(chat.uri, {
                    "type": "halter/approvalResult",
                    "turnId": turn["id"],
                    "approvalId": str(event.get("id")),
                    "toolCallId": str(event.get("toolCallId")),
                    "approved": bool(event.get("approved")),
                })
                step_id = str(event.get("planStepId") or "")
                bucket = chat.plan_evidence.get(step_id) if step_id else None
                if bucket is not None:
                    for item in bucket.get("approvalIds", []):
                        if item.get("id") == event.get("id"):
                            item["approved"] = bool(event.get("approved"))
                    bucket["updatedAt"] = _now_iso()
                    await self._broadcast_plan_evidence(chat, turn["id"])
            elif event_type == "tool_call":
                part = {
                    "kind": "halter/toolCall", "id": str(event.get("id")),
                    "name": str(event.get("name")), "arguments": event.get("arguments") or {},
                    "planStepId": event.get("planStepId"),
                }
                turn["responseParts"].append(part)
                await self.broadcast_action(chat.uri, {
                    "type": "halter/toolCall", "turnId": turn["id"], "part": part,
                })
                if self._record_plan_evidence(
                    chat, event.get("planStepId"), "toolCallIds",
                    {"id": str(event.get("id")), "name": str(event.get("name")),
                     "ts": _now_iso()},
                ):
                    await self._broadcast_plan_evidence(chat, turn["id"])
            elif event_type == "tool_result":
                part = {
                    "kind": "halter/toolResult", "id": str(event.get("id")),
                    "name": str(event.get("name")), "ok": bool(event.get("ok")),
                    "result": event.get("result") or {},
                    "planStepId": event.get("planStepId"),
                }
                turn["responseParts"].append(part)
                await self.broadcast_action(chat.uri, {
                    "type": "halter/toolResult", "turnId": turn["id"], "part": part,
                })
                transaction_id = ""
                if event.get("ok"):
                    transaction_id = str(
                        ((event.get("result") or {}).get("transactionId") or "")
                    )
                if transaction_id and self._record_plan_evidence(
                    chat, event.get("planStepId"), "transactionIds",
                    {"id": transaction_id, "ts": _now_iso()},
                ):
                    await self._broadcast_plan_evidence(chat, turn["id"])

        async def request_approval(request: dict[str, Any]) -> bool:
            approval_id = str(request.get("id") or "")
            future = asyncio.get_running_loop().create_future()
            chat.approvals[approval_id] = future
            record = {
                "id": approval_id,
                "tool": str(request.get("tool") or ""),
                "summary": str(request.get("summary") or ""),
                "status": "pending",
                "requestedAt": _now_iso(),
                "decidedAt": None,
            }
            chat.approval_history.append(record)
            await self.broadcast_action(chat.uri, {
                "type": "halter/approvalRequest",
                "turnId": turn["id"],
                "approval": request,
            })
            if self._record_plan_evidence(
                chat, request.get("planStepId"), "approvalIds",
                {"id": approval_id, "tool": str(request.get("tool") or ""),
                 "approved": None, "ts": _now_iso()},
            ):
                await self._broadcast_plan_evidence(chat, turn["id"])
            try:
                approved = await future
                record.update({
                    "status": "approved" if approved else "denied",
                    "decidedAt": _now_iso(),
                })
                return approved
            finally:
                chat.approvals.pop(approval_id, None)

        try:
            runtime = AgentRuntime(
                workspace=workspace,
                model_client=self._model_client_factory(),
                session_channel=chat.session.uri,
                request_approval=request_approval,
                config=self._cfg,
            )
            result = await runtime.run(
                prompt,
                model=model,
                history=list(chat.agent_messages),
                emit=emit,
                permission_mode=permission_mode,
            )
            chat.agent_messages = result.messages[1:]
            turn["usage"] = {
                "iterations": result.iterations,
                "toolCalls": result.tool_calls,
                "hcmKind": "halter-native/read-only",
            }
            await self._finish_turn(chat, turn["id"], started, "complete")
        except asyncio.CancelledError:
            part = {
                "kind": "error", "id": f"p-{uuid.uuid4().hex[:8]}",
                "error": {"errorType": "Cancelled", "message": "turn cancelled by client"},
            }
            await self._finish_turn(chat, turn["id"], started, "error", part)
            raise
        except Exception as e:
            part = {
                "kind": "error", "id": f"p-{uuid.uuid4().hex[:8]}",
                "error": {"errorType": type(e).__name__, "message": str(e)},
            }
            await self._finish_turn(chat, turn["id"], started, "error", part)
        finally:
            chat.agent_task = None

    async def _finish_turn(self, chat: AhpChat, turn_id: str, started: float,
                           state: str, error_part: dict[str, Any] | None = None) -> None:
        duration = int((time.monotonic() - started) * 1000)
        if chat.active_turn and chat.active_turn.get("id") == turn_id:
            turn = chat.active_turn
            turn["duration"] = duration
            turn["state"] = state
            if error_part:
                turn["responseParts"].append(error_part)
            chat.turns.append(turn)
            chat.active_turn = None
        chat.turn_claimed = False
        chat.status = STATUS_ERROR if state == "error" else STATUS_IDLE
        chat.activity = None
        chat.session.status = chat.status
        chat.modified_at = _now_iso()
        chat.session.modified_at = chat.modified_at
        if error_part:
            await self.broadcast_action(chat.uri, {
                "type": "chat/error", "turnId": turn_id,
                "duration": duration, "part": error_part,
            })
        else:
            await self.broadcast_action(chat.uri, {
                "type": "chat/turnComplete", "turnId": turn_id, "duration": duration,
            })
        await self.broadcast_action(chat.uri, {
            "type": "chat/activityChanged", "activity": None,
        })
        await self.broadcast_notification("root/sessionSummaryChanged", {
            "channel": ROOT_URI, "session": chat.session.uri,
            "changes": {"status": chat.status, "modifiedAt": chat.modified_at},
        })
        self._store.save(chat.session)


class AhpError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code, self.message, self.data = code, message, data


def _file_uri_to_path(uri: str) -> str:
    if uri.startswith("file://"):
        return uri[7:]
    return uri


def _chunks(text: str, size: int = 2000) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


def _default_allowed_origins() -> set[str]:
    origins = {
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
    }
    configured = os.environ.get("HALTER_AHP_ALLOWED_ORIGINS", "")
    origins.update(item.strip() for item in configured.split(",") if item.strip())
    return origins


def _request_token(request: aiohttp.web.Request) -> str:
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        return authorization[len("Bearer "):].strip()
    return request.headers.get("X-Halter-AHP-Token", request.query.get("token", "")).strip()


def build_web_app(ahp: AhpHost) -> aiohttp.web.Application:
    """aiohttp 应用：WS（/ 或 /ws）+ HTTP JSON-RPC（POST /rpc）+ SSE（GET /rpc/stream）。

    AHP 传输无关（规范：任何有序可靠双向流皆可）；HTTP+SSE 组合用于
    WebView 等禁止明文 WebSocket 的客户端环境。除健康检查外，所有传输都
    要求 bearer token；带 Origin 的浏览器请求还必须命中显式 allow-list。
    """
    app = aiohttp.web.Application()
    allowed_origins = _default_allowed_origins()

    @aiohttp.web.middleware
    async def authenticate(request: aiohttp.web.Request, handler):
        origin = request.headers.get("Origin")
        if origin and origin not in allowed_origins:
            raise aiohttp.web.HTTPForbidden(text="origin not allowed")
        if request.path == "/healthz":
            return await handler(request)
        supplied = _request_token(request)
        if not supplied or not hmac.compare_digest(supplied, ahp.auth_token):
            raise aiohttp.web.HTTPUnauthorized(
                text="AHP authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await handler(request)

    app.middlewares.append(authenticate)

    async def ws_handler(request: aiohttp.web.Request) -> aiohttp.web.WebSocketResponse:
        ws = aiohttp.web.WebSocketResponse()
        await ws.prepare(request)
        conn = ahp.new_conn()

        async def writer() -> None:
            try:
                while conn.alive:
                    msg = await conn.outbox.get()
                    await ws.send_str(msg)
            except (ConnectionError, RuntimeError, asyncio.CancelledError):
                pass

        wtask = asyncio.create_task(writer())
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw.data)
                except json.JSONDecodeError:
                    continue
                reply = await ahp.route_message(conn, msg)
                if reply is not None:
                    await ws.send_str(json.dumps(reply))
        finally:
            ahp.drop_conn(conn)
            wtask.cancel()
        return ws

    def _conn_for(request: aiohttp.web.Request) -> AhpConn:
        supplied = request.query.get("client")
        if supplied and supplied in ahp.connections:
            conn = ahp.connections[supplied]
            conn.last_seen = time.monotonic()
            return conn
        return ahp.new_conn(supplied)

    async def rpc_handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
        conn = _conn_for(request)
        try:
            msg = json.loads(await request.text())
        except json.JSONDecodeError:
            return aiohttp.web.json_response(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32700, "message": "Parse error"}}, status=400)
        reply = await ahp.route_message(conn, msg)
        if reply is None:
            return aiohttp.web.Response(status=204)
        return aiohttp.web.json_response(reply)

    async def stream_handler(request: aiohttp.web.Request) -> aiohttp.web.StreamResponse:
        conn = _conn_for(request)
        conn.streaming = True
        resp = aiohttp.web.StreamResponse(headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
        })
        await resp.prepare(request)
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(conn.outbox.get(), timeout=15.0)
                    await resp.write(f"data: {msg}\n\n".encode())
                except asyncio.TimeoutError:
                    await resp.write(b": keepalive\n\n")
        except (ConnectionError, RuntimeError, asyncio.CancelledError):
            pass
        finally:
            # Keep the logical HTTP-RPC/SSE client alive so reconnecting the
            # stream retains its subscriptions; idle clients are reclaimed in
            # _broadcast instead.
            conn.streaming = False
            conn.last_seen = time.monotonic()
        return resp

    async def health(request: aiohttp.web.Request) -> aiohttp.web.Response:
        return aiohttp.web.json_response({
            "ok": True,
            "protocol": PROTOCOL_VERSION,
            "serverInfo": SERVER_INFO,
        })

    app.router.add_get("/", ws_handler)
    app.router.add_get("/ws", ws_handler)
    app.router.add_post("/rpc", rpc_handler)
    app.router.add_get("/rpc/stream", stream_handler)
    app.router.add_get("/healthz", health)
    return app


def _ensure_gui_path() -> None:
    """GUI/launchd 进程 PATH 常缺 homebrew 等目录；启动时补齐，保证 runner 可发现。"""
    extra = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin",
             str(Path.home() / ".local/bin"), str(Path.home() / ".cargo/bin")]
    parts = [*extra, *os.environ.get("PATH", "").split(":")]
    os.environ["PATH"] = ":".join(dict.fromkeys(p for p in parts if p))


def write_auth_token(path: Path, token: str) -> None:
    """Atomically persist the AHP token with owner-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(token)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


async def serve(host: str = "127.0.0.1", port: int = 7433,
                auth_token: str | None = None,
                token_file: Path | None = None) -> None:
    _ensure_gui_path()
    ahp = AhpHost(auth_token=auth_token)
    token_path = token_file or Path.home() / ".config/halter/ahp-token"
    write_auth_token(token_path, ahp.auth_token)
    runner = aiohttp.web.AppRunner(build_web_app(ahp))
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, host, port)
    await site.start()
    print(f"halter AHP host listening on http://{host}:{port} "
          f"(ws + http-rpc + sse, protocol {PROTOCOL_VERSION})")
    print(f"AHP auth token written to {token_path}")
    await asyncio.Future()
