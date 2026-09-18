"""Durable, private storage for AHP sessions and native conversation state."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from .agent import redact_value

if TYPE_CHECKING:
    from .ahp_host import AhpChat, AhpSession


class AgentSessionStore:
    """Persist AHP session/chat state as one private JSON file per session.

    The store is deliberately boring and local-first: no database, no network,
    and atomic file replacement.  It is not a vendor session parser; it stores
    only halter-owned AHP state and redacted model/tool history.
    """

    def __init__(self, home: Path | None = None) -> None:
        self.root = (home or Path.home()) / ".config/halter/agent-sessions"
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass

    def _dir(self, channel: str) -> Path:
        digest = hashlib.sha256(channel.encode("utf-8")).hexdigest()[:20]
        path = self.root / f"ahp-{digest}"
        path.mkdir(parents=True, exist_ok=True)
        try:
            path.chmod(0o700)
        except OSError:
            pass
        return path

    def path(self, channel: str) -> Path:
        return self._dir(channel) / "session.json"

    def save(self, session: "AhpSession") -> Path:
        target = self.path(session.uri)
        payload = {
            "version": 1,
            "session": session.summary(),
            "chats": [self._chat_state(chat) for chat in session.chats.values()],
        }
        raw = json.dumps(redact_value(payload), ensure_ascii=False, indent=2) + "\n"
        fd, tmp_name = tempfile.mkstemp(prefix=".session-", suffix=".json", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(raw)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, target)
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
        return target

    def delete(self, channel: str) -> None:
        try:
            import shutil
            shutil.rmtree(self._dir(channel))
        except FileNotFoundError:
            return

    def load_all(self) -> list["AhpSession"]:
        from .ahp_host import AhpChat, AhpSession, STATUS_ERROR

        sessions: list[AhpSession] = []
        if not self.root.exists():
            return sessions
        for path in sorted(self.root.glob("ahp-*/session.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                state = doc.get("session") or {}
                uri = str(state.get("resource") or "")
                if not uri.startswith("ahp-session:/"):
                    continue
                session = AhpSession(
                    uri=uri,
                    provider=str(state.get("provider") or ""),
                    title=str(state.get("title") or "Restored session"),
                    lifecycle=str(state.get("lifecycle") or "ready"),
                    status=int(state.get("status") or 1),
                    activity=state.get("activity"),
                    working_directories=[str(x) for x in state.get("workingDirectories") or []],
                    created_at=str(state.get("createdAt") or ""),
                    modified_at=str(state.get("modifiedAt") or ""),
                )
                for raw_chat in doc.get("chats") or []:
                    chat_uri = str(raw_chat.get("resource") or "")
                    if not chat_uri.startswith("ahp-chat:/"):
                        continue
                    active = raw_chat.get("activeTurn") or None
                    turns = list(raw_chat.get("turns") or [])
                    if active:
                        # A persisted active turn means the host stopped before
                        # completion. Do not present it as still runnable.
                        active["state"] = "error"
                        active.setdefault("responseParts", []).append({
                            "kind": "error",
                            "id": "p-host-interrupted",
                            "error": {
                                "errorType": "HostInterrupted",
                                "message": "AHP host stopped before this turn completed",
                            },
                        })
                        turns.append(active)
                        active = None
                    chat = AhpChat(
                        uri=chat_uri,
                        session=session,
                        title=str(raw_chat.get("title") or "Restored chat"),
                        status=int(raw_chat.get("status") or 1),
                        activity=raw_chat.get("activity"),
                        turns=turns,
                        active_turn=active,
                        agent_messages=list(raw_chat.get("agentMessages") or []),
                        modified_at=str(raw_chat.get("modifiedAt") or ""),
                    )
                    if chat.status == 8:
                        chat.status = STATUS_ERROR
                        chat.activity = None
                    session.chats[chat.uri] = chat
                sessions.append(session)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return sessions

    def _chat_state(self, chat: "AhpChat") -> dict:
        return {
            "resource": chat.uri,
            "title": chat.title,
            "status": chat.status,
            "activity": chat.activity,
            "modifiedAt": chat.modified_at,
            "turns": chat.turns,
            "activeTurn": chat.active_turn,
            "agentMessages": chat.agent_messages,
        }
