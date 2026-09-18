"""Private, read-only inspection helpers for native-agent audit trails."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AuditSummary:
    session_id: str
    audit_path: Path
    session_path: Path | None
    provider: str | None
    title: str | None
    workspace: str | None
    event_count: int
    first_event_at: str | None
    last_event_at: str | None
    size_bytes: int
    kinds: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "audit_path": str(self.audit_path),
            "session_path": str(self.session_path) if self.session_path else None,
            "provider": self.provider,
            "title": self.title,
            "workspace": self.workspace,
            "event_count": self.event_count,
            "first_event_at": self.first_event_at,
            "last_event_at": self.last_event_at,
            "size_bytes": self.size_bytes,
            "kinds": dict(sorted(self.kinds.items())),
        }


def audit_root(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".config" / "halter" / "agent-audit"


def session_store_path(session_id: str, home: Path | None = None) -> Path | None:
    path = (
        (home or Path.home()) / ".config" / "halter" / "agent-sessions"
        / session_id / "session.json"
    )
    return path if path.is_file() else None


def validate_session_id(session_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", session_id or ""):
        raise ValueError(f"invalid audit session id: {session_id!r}")
    return session_id


def read_audit_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid audit event at {path}:{line_number}") from exc
            if not isinstance(event, dict):
                raise ValueError(f"invalid audit event at {path}:{line_number}")
            events.append(event)
    return events


def summarize_audit(session_id: str, home: Path | None = None) -> AuditSummary:
    validate_session_id(session_id)
    path = audit_root(home) / session_id / "audit.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"audit not found: {session_id}")
    events = read_audit_events(path)
    timestamps = [str(event.get("ts")) for event in events if event.get("ts")]
    kinds: dict[str, int] = {}
    for event in events:
        kind = str(event.get("kind") or "unknown")
        kinds[kind] = kinds.get(kind, 0) + 1

    provider: str | None = None
    title: str | None = None
    workspace: str | None = None
    session_path = session_store_path(session_id, home)
    if session_path is not None:
        try:
            doc = json.loads(session_path.read_text(encoding="utf-8"))
            state = doc.get("session") or {}
            provider = str(state.get("provider")) if state.get("provider") is not None else None
            title = str(state.get("title")) if state.get("title") is not None else None
            directories = state.get("workingDirectories") or []
            workspace = str(directories[0]) if directories else None
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # Audit listing remains useful even if durable state is corrupted.
            pass

    return AuditSummary(
        session_id=session_id,
        audit_path=path,
        session_path=session_path,
        provider=provider,
        title=title,
        workspace=workspace,
        event_count=len(events),
        first_event_at=min(timestamps) if timestamps else None,
        last_event_at=max(timestamps) if timestamps else None,
        size_bytes=path.stat().st_size,
        kinds=kinds,
    )


def list_audits(limit: int = 100, home: Path | None = None) -> list[AuditSummary]:
    root = audit_root(home)
    if not root.is_dir():
        return []
    summaries: list[AuditSummary] = []
    for directory in root.iterdir():
        if not directory.is_dir() or not (directory / "audit.jsonl").is_file():
            continue
        try:
            summaries.append(summarize_audit(directory.name, home))
        except (OSError, ValueError):
            continue
    summaries.sort(key=lambda item: item.last_event_at or "", reverse=True)
    return summaries[: max(limit, 0)]


def show_audit(
    session_id: str,
    *,
    kind: str | None = None,
    tail: int = 200,
    home: Path | None = None,
) -> dict[str, Any]:
    session_id = validate_session_id(session_id)
    path = audit_root(home) / session_id / "audit.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"audit not found: {session_id}")
    events = read_audit_events(path)
    if kind:
        events = [event for event in events if event.get("kind") == kind]
    selected = events[-max(tail, 0):] if tail else events
    summary = summarize_audit(session_id, home)
    return {
        "summary": summary.to_dict(),
        "total_events": len(events),
        "returned_events": len(selected),
        "events": selected,
    }


# Per-file scan bound: one audit file never blocks the search indefinitely.
MAX_SEARCH_LINES = 50_000


def _normalise_date_bound(value: str, *, end_of_day: bool) -> str:
    """Accept date-only bounds and compare against ISO timestamps."""
    value = value.strip()
    if "T" not in value:
        value += "T23:59:59Z" if end_of_day else "T00:00:00Z"
    return value


def _event_matches(
    event: dict[str, Any],
    *,
    text: str | None,
    kind: str | None,
    transaction_id: str | None,
    tool_name: str | None,
    date_from: str | None,
    date_to: str | None,
) -> bool:
    if kind and str(event.get("kind") or "") != kind:
        return False
    if transaction_id:
        result = event.get("result")
        candidates = {
            str(event.get("id") or ""),
            str(result.get("transactionId") or "") if isinstance(result, dict) else "",
        }
        if transaction_id not in candidates:
            return False
    if tool_name:
        name = str(event.get("name") or event.get("tool") or "")
        if name != tool_name:
            return False
    ts = str(event.get("ts") or "")
    if date_from and ts < date_from:
        return False
    if date_to and ts > date_to:
        return False
    if text:
        haystack = json.dumps(event, ensure_ascii=False).lower()
        if not all(term in haystack for term in text.lower().split() if term):
            return False
    return True


def search_audits(
    *,
    text: str | None = None,
    kind: str | None = None,
    provider: str | None = None,
    workspace: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    transaction_id: str | None = None,
    tool_name: str | None = None,
    session_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
    home: Path | None = None,
) -> dict[str, Any]:
    """Search events across audit files with bounded reads and pagination.

    provider / workspace filter whole sessions via the durable session store;
    the remaining criteria filter individual events.  Corrupted files are
    skipped and reported instead of failing the search.
    """
    if session_id is not None:
        session_id = validate_session_id(session_id)
    norm_from = _normalise_date_bound(date_from, end_of_day=False) if date_from else None
    norm_to = _normalise_date_bound(date_to, end_of_day=True) if date_to else None

    root = audit_root(home)
    directories: list[Path] = []
    if session_id is not None:
        candidate = root / session_id / "audit.jsonl"
        if candidate.is_file():
            directories.append(candidate.parent)
    elif root.is_dir():
        directories = [
            item for item in sorted(root.iterdir())
            if item.is_dir() and (item / "audit.jsonl").is_file()
        ]

    matches: list[dict[str, Any]] = []
    skipped_files: list[str] = []
    truncated_files: list[str] = []
    sessions_scanned = 0
    total = 0
    for directory in directories:
        name = directory.name
        try:
            validate_session_id(name)
        except ValueError:
            continue
        if provider or workspace:
            meta = _session_metadata(name, home)
            if provider and meta.get("provider") != provider:
                continue
            if workspace and workspace not in (meta.get("workspace") or ""):
                continue
        sessions_scanned += 1
        path = directory / "audit.jsonl"
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, 1):
                    if line_number > MAX_SEARCH_LINES:
                        truncated_files.append(name)
                        break
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        raise ValueError(f"invalid audit event at {path}:{line_number}")
                    if not isinstance(event, dict):
                        raise ValueError(f"invalid audit event at {path}:{line_number}")
                    if _event_matches(
                        event, text=text, kind=kind, transaction_id=transaction_id,
                        tool_name=tool_name, date_from=norm_from, date_to=norm_to,
                    ):
                        if offset <= total < offset + max(limit, 0):
                            matches.append({
                                "session_id": name,
                                "line": line_number,
                                "event": event,
                            })
                        total += 1
        except (OSError, ValueError):
            skipped_files.append(name)
            continue
    return {
        "query": {
            "text": text, "kind": kind, "provider": provider, "workspace": workspace,
            "date_from": date_from, "date_to": date_to,
            "transaction_id": transaction_id, "tool_name": tool_name,
            "session_id": session_id, "limit": limit, "offset": offset,
        },
        "matches": matches,
        "total": total,
        "returned": len(matches),
        "sessions_scanned": sessions_scanned,
        "skipped_files": skipped_files,
        "truncated_files": truncated_files,
    }


def _session_metadata(session_id: str, home: Path | None) -> dict[str, Any]:
    path = session_store_path(session_id, home)
    if path is None:
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        state = doc.get("session") or {}
        directories = state.get("workingDirectories") or []
        return {
            "provider": state.get("provider"),
            "workspace": str(directories[0]) if directories else None,
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
