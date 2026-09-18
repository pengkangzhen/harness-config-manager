"""Halter-native, read-only agent runtime.

This module is the first step from "dispatch external harnesses" to a real
workspace harness: it owns the model/tool loop, confines every tool to the
selected workspace, and appends a private JSONL audit trail.  The initial tool
surface is deliberately read-only; write tools will be added only after the UI
has an explicit diff/approval flow.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import stat
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

import aiohttp

from .config import HalterConfig
from .mcp_bridge import (
    McpToolDescriptor,
    call_mcp_tool,
    list_mcp_tools,
    mcp_allowlists,
)
from .model_health import is_local_base_url, resolve_route
from .patch_compare import compare_provider_diffs
from .runner import RUNNERS
from .sessions import build_context, redact_text, scan_sessions

SYSTEM_PROMPT = (
    "You are halter, a careful local coding agent. Work only with the supplied "
    "workspace. Start multi-step work by calling update_plan, update the plan as "
    "state changes, and inspect files before making claims. Give every plan step "
    "a stable id and reuse it when the step survives a later update; when a tool "
    "call belongs to one plan step, pass plan_step_id in the tool arguments so "
    "the resulting evidence (tool call, approval, transaction) links back to it. "
    "Prefer small verifiable "
    "steps, and state uncertainty. Use project session tools to recall prior "
    "workspace history, and delegate_harness for an independent "
    "external-agent review in a disposable clone, then apply only approved "
    "unified diffs to the real workspace. Declared MCP tools appear as "
    "mcp_<server>_<tool>; read-only ones are directly callable while "
    "state-changing ones require explicit user approval. "
    "Workspace writes are available only as "
    "unified diffs and require explicit user approval. Run tests in the "
    "disposable sandbox with run_tests first; only after sandbox tests pass and "
    "the user explicitly needs a final confirmation may you request "
    "run_tests_workspace, which re-runs one fixed command in the real workspace "
    "and always requires a second explicit approval. If write mode is "
    "unavailable, propose precise changes without pretending that you edited files."
)

MAX_TOOL_RESULT_CHARS = 24_000
MAX_ITERATIONS = 12
MAX_PATCH_CHARS = 100_000
MAX_PATCH_LINES = 5_000
MAX_HISTORY_TURNS = 12
MAX_HISTORY_CHARS = 100_000
WRITE_TOOL_NAMES = {"apply_patch", "run_tests", "run_tests_workspace"}
PLAN_STATUSES = {"pending", "in_progress", "done", "blocked"}
PLAN_STEP_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,64}")
TEST_COMMANDS = {
    "pytest": ["pytest"],
    "npm-test": ["npm", "test", "--silent"],
    "cargo-test": ["cargo", "test"],
    "go-test": ["go", "test", "./..."],
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "update_plan",
            "description": "Replace the visible execution plan with a concise, ordered set of task steps.",
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 20,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "pattern": "^[A-Za-z0-9._-]{1,64}$",
                                    "description": "Stable step identifier; reuse it when the step survives a later plan update.",
                                },
                                "title": {"type": "string", "minLength": 1},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "done", "blocked"]},
                                "detail": {"type": "string"},
                            },
                            "required": ["title", "status"],
                        },
                    },
                    "note": {"type": "string"},
                },
                "required": ["steps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file inside the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Workspace-relative path (absolute paths must remain inside the workspace)."},
                    "offset": {"type": "integer", "minimum": 1, "description": "1-based starting line."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "description": "Maximum lines to return."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List one workspace directory, including file sizes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search text files under the workspace for a literal or regular-expression query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string", "description": "Optional workspace-relative search root."},
                    "regex": {"type": "boolean", "description": "Interpret query as a Python regular expression."},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Return git branch and working-tree status for the workspace.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Return the current git diff without modifying the working tree.",
            "parameters": {
                "type": "object",
                "properties": {
                    "staged": {"type": "boolean"},
                    "path": {"type": "string", "description": "Optional workspace-relative path."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_project_sessions",
            "description": "List recent AI-assistant sessions already associated with this workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_project_session",
            "description": "Read a redacted handoff/context for one session that belongs to this workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string", "description": "Session ref from list_project_sessions, e.g. claude:<id>."},
                    "tail": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["ref"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run a fixed, recognized project test command after explicit user approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "enum": ["pytest", "npm-test", "cargo-test", "go-test"],
                    },
                    "timeout_seconds": {"type": "integer", "minimum": 5, "maximum": 900},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests_workspace",
            "description": (
                "Final verification: re-run one fixed test command directly in the "
                "REAL workspace after sandbox tests passed. Requires a second explicit "
                "user approval. Use only for the minimal final confirmation run."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "enum": ["pytest", "npm-test", "cargo-test", "go-test"],
                    },
                    "timeout_seconds": {"type": "integer", "minimum": 5, "maximum": 900},
                    "reason": {
                        "type": "string",
                        "description": "Why a real-workspace re-run is needed after the sandbox run.",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_harness",
            "description": "Run a supported external coding harness in a disposable git clone and return its output plus any diff it produced. The real workspace is not modified.",
            "parameters": {
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "enum": ["claude", "codex", "zcode", "opencode"],
                        "description": "External harness to consult.",
                    },
                    "prompt": {"type": "string", "description": "Specific task or review question for the external harness."},
                    "model": {"type": "string", "description": "Optional external-harness model."},
                    "timeout_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
                },
                "required": ["provider", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_patch",
            "description": "Apply one unified diff to files inside the workspace. Requires explicit user approval when workspace-write mode is enabled.",
            "parameters": {
                "type": "object",
                "properties": {
                    "patch": {"type": "string", "description": "A standard unified diff produced by git diff."},
                    "summary": {"type": "string", "description": "Short human-readable reason for the change."},
                },
                "required": ["patch"],
            },
        },
    },
]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelResponse:
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    streamed: bool = False


class ModelClient(Protocol):
    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str | None,
        stream_delta: StreamCallback | None = None,
    ) -> ModelResponse:
        """Return the next assistant message."""


@dataclass(frozen=True)
class AgentResult:
    content: str
    messages: list[dict[str, Any]]
    iterations: int
    tool_calls: int
    plan_evidence: dict[str, dict[str, Any]] = field(default_factory=dict)


AgentEvent = dict[str, Any]
EventCallback = Callable[[AgentEvent], Awaitable[None]]
StreamCallback = Callable[[str], Awaitable[None]]
ApprovalCallback = Callable[[dict[str, Any]], Awaitable[bool]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _safe_session_id(channel: str) -> str:
    digest = hashlib.sha256(channel.encode("utf-8")).hexdigest()[:20]
    return f"ahp-{digest}"


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {str(k): redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    return value


def _truncate_text(value: str, limit: int = MAX_TOOL_RESULT_CHARS) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n... [truncated {len(value) - limit} chars]"


class AuditLog:
    """Private, append-only JSONL event log for one native agent session."""

    def __init__(self, session_channel: str, home: Path | None = None) -> None:
        self.path = (home or Path.home()) / ".config/halter/agent-audit" / _safe_session_id(session_channel) / "audit.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
            if self.path.exists():
                self.path.chmod(0o600)
            else:
                self.path.touch(mode=0o600)
                self.path.chmod(0o600)
        except OSError:
            # Audit must remain usable in restricted test/desktop environments.
            pass

    async def append(self, kind: str, **data: Any) -> None:
        event = {"ts": _now_iso(), "kind": kind, **redact_value(data)}
        def _write() -> None:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        await asyncio.to_thread(_write)


def transaction_root_for_session(session_channel: str, home: Path | None = None) -> Path:
    return (
        (home or Path.home()) / ".config/halter/agent-transactions"
        / _safe_session_id(session_channel)
    )


class TransactionStore:
    """Private, exact rollback metadata for approved workspace writes."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass

    def path(self, transaction_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", transaction_id):
            raise ValueError("invalid transaction id")
        return self.root / f"{transaction_id}.json"

    def save(self, transaction: dict[str, Any]) -> Path:
        target = self.path(str(transaction["id"]))
        raw = json.dumps(transaction, ensure_ascii=False, indent=2) + "\n"
        fd, tmp_name = tempfile.mkstemp(prefix=".transaction-", suffix=".json", dir=self.root)
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


class WorkspaceTools:
    """Workspace-confined tools; only apply_patch mutates the working tree."""

    EXCLUDED_DIRS = {
        ".git", ".hg", ".svn", ".venv", "venv", ".halter", "node_modules", "target",
        "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist",
        "build", ".next", ".cache",
    }

    def __init__(self, workspace: Path, transaction_root: Path | None = None) -> None:
        self.workspace = workspace.expanduser().resolve(strict=True)
        self.names = {schema["function"]["name"] for schema in TOOL_SCHEMAS}
        self.transaction_root = transaction_root

    def resolve_path(self, raw: str) -> Path:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        try:
            resolved = candidate.resolve(strict=False)
            resolved.relative_to(self.workspace)
        except (OSError, ValueError) as exc:
            raise PermissionError(f"path escapes workspace: {raw}") from exc
        return resolved

    async def update_plan(
        self, args: dict[str, Any], known_steps: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """Validate a full plan replacement and keep step ids stable.

        Step id precedence: the model-provided id, then the id of a known step
        with the same title, then a fresh generated id.  This keeps ids stable
        across updates even when the model omits them, while still allowing
        reordering and retitling.
        """
        raw_steps = args.get("steps")
        if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= 20:
            raise ValueError("plan steps must contain between 1 and 20 items")
        known_by_title: dict[str, str] = {}
        for step_id, title in (known_steps or {}).items():
            known_by_title.setdefault(title, step_id)
        steps: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        for index, raw in enumerate(raw_steps):
            if not isinstance(raw, dict):
                raise ValueError(f"plan step {index + 1} must be an object")
            title = str(raw.get("title") or "").strip()
            status = str(raw.get("status") or "pending")
            if not title or len(title) > 200:
                raise ValueError(f"plan step {index + 1} has an invalid title")
            if status not in PLAN_STATUSES:
                raise ValueError(f"plan step {index + 1} has invalid status: {status}")
            raw_id = str(raw.get("id") or "").strip()
            if raw_id and not PLAN_STEP_ID_PATTERN.fullmatch(raw_id):
                raise ValueError(f"plan step {index + 1} has an invalid id: {raw_id}")
            step_id = raw_id or known_by_title.get(title) or f"step-{uuid.uuid4().hex[:8]}"
            if step_id in seen_ids:
                raise ValueError(f"plan step {index + 1} reuses id {step_id!r}")
            seen_ids.add(step_id)
            steps.append({
                "id": step_id,
                "title": title[:200],
                "status": status,
                "detail": _truncate_text(str(raw.get("detail") or ""), 1000),
            })
        return {
            "updated": True,
            "steps": steps,
            "note": _truncate_text(str(args.get("note") or ""), 2000),
        }

    async def read_file(self, args: dict[str, Any]) -> dict[str, Any]:
        path = self.resolve_path(str(args.get("path", "")))
        if not path.is_file():
            raise FileNotFoundError(f"not a file: {path.relative_to(self.workspace)}")
        offset = max(int(args.get("offset") or 1), 1)
        limit = min(max(int(args.get("limit") or 400), 1), 2000)

        def _read() -> dict[str, Any]:
            stat_result = path.stat()
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
            selected = lines[offset - 1 : offset - 1 + limit]
            return {
                "path": str(path.relative_to(self.workspace)),
                "totalLines": len(lines),
                "offset": offset,
                "lines": len(selected),
                "content": _truncate_text("".join(selected)),
                "bytes": stat_result.st_size,
            }
        return await asyncio.to_thread(_read)

    async def list_dir(self, args: dict[str, Any]) -> dict[str, Any]:
        path = self.resolve_path(str(args.get("path") or "."))
        if not path.is_dir():
            raise FileNotFoundError(f"not a directory: {path}")
        limit = min(max(int(args.get("limit") or 500), 1), 2000)

        def _list() -> dict[str, Any]:
            entries: list[dict[str, Any]] = []
            for child in sorted(path.iterdir(), key=lambda p: p.name):
                try:
                    st = child.lstat()
                except OSError:
                    continue
                kind = "dir" if child.is_dir() else "file" if child.is_file() else "other"
                entries.append({
                    "name": child.name,
                    "kind": kind,
                    "bytes": st.st_size,
                    "mode": stat.filemode(st.st_mode),
                })
                if len(entries) >= limit:
                    break
            return {
                "path": str(path.relative_to(self.workspace)),
                "entries": entries,
                "truncated": len(entries) >= limit,
            }
        return await asyncio.to_thread(_list)

    async def search_files(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "")
        if not query:
            raise ValueError("query must not be empty")
        root = self.resolve_path(str(args.get("path") or "."))
        if not root.is_dir():
            raise FileNotFoundError(f"not a directory: {root}")
        use_regex = bool(args.get("regex"))
        max_results = min(max(int(args.get("max_results") or 50), 1), 200)
        pattern = re.compile(query) if use_regex else None

        def _search() -> dict[str, Any]:
            matches: list[dict[str, Any]] = []
            files_seen = 0
            for current, dirs, files in os.walk(root):
                dirs[:] = sorted(d for d in dirs if d not in self.EXCLUDED_DIRS)
                for filename in sorted(files):
                    path = Path(current) / filename
                    files_seen += 1
                    try:
                        if path.stat().st_size > 1_000_000 or path.suffix.lower() in {
                            ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip",
                            ".gz", ".tar", ".lock", ".bin", ".exe", ".dylib", ".so",
                        }:
                            continue
                        with path.open("r", encoding="utf-8", errors="ignore") as fh:
                            for line_no, line in enumerate(fh, 1):
                                hit = bool(pattern.search(line)) if pattern else query in line
                                if not hit:
                                    continue
                                matches.append({
                                    "path": str(path.relative_to(self.workspace)),
                                    "line": line_no,
                                    "text": line.rstrip("\n")[:500],
                                })
                                if len(matches) >= max_results:
                                    return {"query": query, "matches": matches, "truncated": True, "filesSeen": files_seen}
                    except OSError:
                        continue
            return {"query": query, "matches": matches, "truncated": False, "filesSeen": files_seen}
        return await asyncio.to_thread(_search)

    def _validate_patch_paths(self, patch: str) -> None:
        """Reject absolute/parent paths before handing a patch to git apply."""
        saw_header = False
        for index, line in enumerate(patch.splitlines(), 1):
            if not (line.startswith("--- ") or line.startswith("+++ ")):
                continue
            saw_header = True
            raw = line[4:].split("\t", 1)[0].strip()
            if raw == "/dev/null":
                continue
            if raw.startswith('"') and raw.endswith('"'):
                raw = raw[1:-1]
            if raw.startswith("b/"):
                raw = raw[2:]
            elif raw.startswith("a/"):
                raw = raw[2:]
            if not raw or raw.startswith("/") or raw.startswith("\\") or ".." in Path(raw).parts:
                raise PermissionError(f"patch path escapes workspace at diff line {index}: {raw}")
            resolved = self.resolve_path(raw)
            relative = resolved.relative_to(self.workspace)
            if relative.parts and relative.parts[0] in self.EXCLUDED_DIRS:
                raise PermissionError(f"patch targets an excluded internal directory: {raw}")
        if not saw_header:
            raise ValueError("patch has no ---/+++ file headers")

    async def workspace_context(self) -> dict[str, Any]:
        """Build a compact, read-only bootstrap context for the selected project."""
        manifests: dict[str, str] = {}
        for name, limit in (
            ("pyproject.toml", 6000),
            ("package.json", 6000),
            ("Cargo.toml", 4000),
            ("go.mod", 3000),
        ):
            path = self.workspace / name
            if path.is_file():
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")[:limit]
                    manifests[name] = redact_text(text)
                except OSError:
                    continue
        readme = self.workspace / "README.md"
        readme_text = ""
        if readme.is_file():
            try:
                readme_text = redact_text(readme.read_text(encoding="utf-8", errors="replace")[:6000])
            except OSError:
                readme_text = ""

        try:
            top_entries = [
                {"name": item.name, "kind": "dir" if item.is_dir() else "file"}
                for item in sorted(self.workspace.iterdir(), key=lambda item: item.name)[:80]
            ]
        except OSError:
            top_entries = []

        git = await self._git("status", "--porcelain=v1", "--branch")
        sessions = scan_sessions(project=self.workspace)
        sessions.sort(
            key=lambda item: item.updated_at or datetime.fromtimestamp(0, timezone.utc),
            reverse=True,
        )
        return {
            "manifests": manifests,
            "readme": readme_text,
            "topEntries": top_entries,
            "git": git,
            "recentSessions": [
                {
                    "ref": item.ref,
                    "tool": item.tool,
                    "title": item.title,
                    "updatedAt": item.updated_at.isoformat() if item.updated_at else None,
                }
                for item in sessions[:5]
            ],
        }

    async def list_project_sessions(self, args: dict[str, Any]) -> dict[str, Any]:
        limit = min(max(int(args.get("limit") or 20), 1), 50)
        items = scan_sessions(project=self.workspace)
        items.sort(key=lambda item: item.updated_at or datetime.fromtimestamp(0, timezone.utc), reverse=True)
        selected = items[:limit]
        return {
            "items": [
                {
                    "ref": item.ref,
                    "tool": item.tool,
                    "title": item.title,
                    "updatedAt": item.updated_at.isoformat() if item.updated_at else None,
                    "messageCount": item.message_count,
                    "model": item.model,
                }
                for item in selected
            ],
            "total": len(items),
        }

    async def read_project_session(self, args: dict[str, Any]) -> dict[str, Any]:
        ref = str(args.get("ref") or "")
        tail = min(max(int(args.get("tail") or 40), 1), 200)
        matching = [item for item in scan_sessions(project=self.workspace) if item.ref == ref]
        if not matching:
            raise PermissionError(f"session is not associated with this workspace: {ref}")
        text = build_context(ref, project=self.workspace, tail=tail)
        return {
            "ref": ref,
            "tail": tail,
            "content": _truncate_text(text, 60_000),
        }

    def _prepare_sandbox(self, root: Path) -> tuple[Path, dict[str, Any]]:
        """Clone the workspace plus bounded untracked state into *root*."""
        workspace_diff = subprocess.run(
            ["git", "diff", "--binary", "HEAD"], cwd=self.workspace,
            text=False, capture_output=True, timeout=15, check=False,
        )
        if workspace_diff.returncode != 0:
            raise RuntimeError(
                _truncate_text(
                    workspace_diff.stderr.decode("utf-8", errors="replace")
                    or "workspace is not a git repository"
                )
            )
        clone = root / "workspace"
        cloned = subprocess.run(
            ["git", "clone", "--quiet", str(self.workspace), str(clone)],
            text=True, capture_output=True, timeout=60, check=False,
        )
        if cloned.returncode != 0:
            raise RuntimeError(_truncate_text(cloned.stderr or "git clone failed"))
        if workspace_diff.stdout:
            applied = subprocess.run(
                ["git", "apply", "--binary"], cwd=clone, input=workspace_diff.stdout,
                text=False, capture_output=True, timeout=15, check=False,
            )
            if applied.returncode != 0:
                raise RuntimeError(
                    _truncate_text(
                        applied.stderr.decode("utf-8", errors="replace")
                        or "failed to snapshot workspace changes"
                    )
                )

        copied: list[str] = []
        skipped: list[str] = []
        total_bytes = 0
        listed = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=self.workspace, text=False, capture_output=True, timeout=15, check=False,
        )
        if listed.returncode == 0:
            for raw_path in listed.stdout.split(b"\0"):
                if not raw_path:
                    continue
                rel = raw_path.decode("utf-8", errors="replace")
                source = self.resolve_path(rel)
                relative = source.relative_to(self.workspace)
                if (
                    relative.parts
                    and (
                        relative.parts[0] in self.EXCLUDED_DIRS
                        or any(
                            relative.parts[i:i + 2] == (".config", "halter")
                            for i in range(len(relative.parts) - 1)
                        )
                    )
                ):
                    skipped.append(rel)
                    continue
                try:
                    mode = source.lstat().st_mode
                except OSError:
                    skipped.append(rel)
                    continue
                if not stat.S_ISREG(mode):
                    skipped.append(rel)
                    continue
                try:
                    size = source.stat().st_size
                    if len(copied) >= 5000 or total_bytes + size > 200 * 1024 * 1024:
                        skipped.append(rel)
                        continue
                    target = clone / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    copied.append(rel)
                    total_bytes += size
                except OSError:
                    skipped.append(rel)
        return clone, {
            "untrackedCopied": copied,
            "untrackedSkipped": skipped,
            "untrackedBytes": total_bytes,
        }

    async def run_tests(self, args: dict[str, Any]) -> dict[str, Any]:
        key = str(args.get("command") or "")
        if key not in TEST_COMMANDS:
            raise ValueError(f"unsupported test command: {key}")
        argv = list(TEST_COMMANDS[key])
        timeout = min(max(int(args.get("timeout_seconds") or 600), 5), 900)

        with tempfile.TemporaryDirectory(prefix="halter-tests-") as raw_root:
            clone, snapshot = await asyncio.to_thread(
                self._prepare_sandbox, Path(raw_root)
            )
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=clone,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                output, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                try:
                    os.killpg(os.getpgid(proc.pid), 15)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
                await proc.wait()
                raise RuntimeError(f"test command timed out after {timeout}s")

            def _sandbox_changes() -> dict[str, Any]:
                diff = subprocess.run(
                    ["git", "diff", "--binary"], cwd=clone, text=True,
                    capture_output=True, timeout=15, check=False,
                )
                status = subprocess.run(
                    ["git", "status", "--porcelain=v1"], cwd=clone, text=True,
                    capture_output=True, timeout=15, check=False,
                )
                return {"diff": diff.stdout, "status": status.stdout}

            changes = await asyncio.to_thread(_sandbox_changes)
            return {
                "command": argv,
                "exitCode": proc.returncode,
                "output": _truncate_text(output.decode("utf-8", errors="replace"), 40_000),
                "sandbox": "disposable-git-clone",
                "snapshot": snapshot,
                "sandboxChanges": {
                    "diff": _truncate_text(changes["diff"], 20_000),
                    "status": _truncate_text(changes["status"], 10_000),
                },
            }

    async def run_tests_workspace(
        self, args: dict[str, Any], plan_step_id: str | None = None
    ) -> dict[str, Any]:
        """Final verification: fixed argv directly in the REAL workspace.

        Distinct from run_tests on purpose: no sandbox, no snapshot, and the
        outcome is journalled as its own workspace-tests transaction so the
        approval and audit trail can tell the two runs apart.
        """
        key = str(args.get("command") or "")
        if key not in TEST_COMMANDS:
            raise ValueError(f"unsupported test command: {key}")
        argv = list(TEST_COMMANDS[key])
        timeout = min(max(int(args.get("timeout_seconds") or 600), 5), 900)

        before_status = await self._git("status", "--porcelain=v1")
        transaction_id = f"tests-{uuid.uuid4().hex[:16]}"
        transaction: dict[str, Any] | None = None
        if self.transaction_root is not None:
            transaction = {
                "version": 1,
                "id": transaction_id,
                "kind": "workspace-tests",
                "createdAt": _now_iso(),
                "workspace": str(self.workspace),
                "command": argv,
                "reason": _truncate_text(str(args.get("reason") or ""), 2000),
                "before": {"status": before_status},
                "state": "running",
            }
            if plan_step_id:
                transaction["planStepId"] = plan_step_id
            TransactionStore(self.transaction_root).save(transaction)

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=self.workspace,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                output, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                try:
                    os.killpg(os.getpgid(proc.pid), 15)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
                await proc.wait()
                raise RuntimeError(f"workspace test command timed out after {timeout}s")
        except Exception:
            if transaction is not None:
                transaction["state"] = "failed"
                transaction["finishedAt"] = _now_iso()
                TransactionStore(self.transaction_root or self.workspace).save(transaction)
            raise

        after_status = await self._git("status", "--porcelain=v1")
        state = "verified" if proc.returncode == 0 else "failed"
        if transaction is not None:
            transaction.update({
                "state": state,
                "finishedAt": _now_iso(),
                "exitCode": proc.returncode,
                "after": {"status": after_status},
            })
            TransactionStore(self.transaction_root).save(transaction)
        result: dict[str, Any] = {
            "command": argv,
            "exitCode": proc.returncode,
            "output": _truncate_text(output.decode("utf-8", errors="replace"), 40_000),
            "scope": "workspace",
            "state": state,
            "transactionId": transaction_id,
            "before": {"status": before_status},
            "after": {"status": after_status},
        }
        if plan_step_id:
            result["planStepId"] = plan_step_id
        return result

    async def delegate_harness(self, args: dict[str, Any]) -> dict[str, Any]:
        """Consult an external harness in a disposable git clone.

        The original workspace is never passed to the external process. The
        runner layer constructs provider-specific argv without a shell, and any
        resulting clone-local diff is returned for halter's approval flow.
        """
        provider = str(args.get("provider") or "")
        if provider not in RUNNERS or provider == "halter":
            raise ValueError(f"unsupported delegate provider: {provider}")
        spec = RUNNERS[provider]
        prefix = spec.resolve()
        if prefix is None:
            raise RuntimeError(f"@{provider} runner unavailable")
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("delegate prompt must not be empty")
        model = str(args.get("model") or "") or None
        timeout = min(max(int(args.get("timeout_seconds") or 120), 5), 300)

        with tempfile.TemporaryDirectory(prefix="halter-delegate-") as raw_root:
            root = Path(raw_root)
            clone, snapshot = await asyncio.to_thread(self._prepare_sandbox, root)
            argv = spec.build_argv(prefix, prompt, clone, "safe", model)
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=clone,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                try:
                    os.killpg(os.getpgid(proc.pid), 15)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
                await proc.wait()
                raise RuntimeError(f"@{provider} delegate timed out after {timeout}s")

            def _clone_result() -> dict[str, Any]:
                diff = subprocess.run(
                    ["git", "diff", "--binary"], cwd=clone, text=True,
                    capture_output=True, timeout=15, check=False,
                )
                status = subprocess.run(
                    ["git", "status", "--porcelain=v1"], cwd=clone, text=True,
                    capture_output=True, timeout=15, check=False,
                )
                return {
                    "diff": diff.stdout,
                    "status": status.stdout,
                }
            outcome = await asyncio.to_thread(_clone_result)
        return {
            "provider": provider,
            "exitCode": proc.returncode,
            "stdout": _truncate_text(stdout.decode("utf-8", errors="replace"), 40_000),
            "stderr": _truncate_text(stderr.decode("utf-8", errors="replace"), 10_000),
            "diff": _truncate_text(outcome["diff"], 60_000),
            "status": _truncate_text(outcome["status"], 10_000),
            "sandbox": "disposable-git-clone",
            "snapshot": snapshot,
        }

    async def apply_patch(
        self, args: dict[str, Any], plan_step_id: str | None = None
    ) -> dict[str, Any]:
        patch = str(args.get("patch") or "")
        if not patch.strip():
            raise ValueError("patch must not be empty")
        if len(patch) > MAX_PATCH_CHARS or len(patch.splitlines()) > MAX_PATCH_LINES:
            raise ValueError("patch exceeds the safety size limit")
        self._validate_patch_paths(patch)
        before_status = await self._git("status", "--porcelain=v1")
        before_diff = await self._git("diff", "--binary")
        transaction_id = f"patch-{uuid.uuid4().hex[:16]}"
        transaction: dict[str, Any] | None = None
        transaction_path: Path | None = None
        if self.transaction_root is not None:
            transaction = {
                "version": 1,
                "id": transaction_id,
                "createdAt": _now_iso(),
                "workspace": str(self.workspace),
                "patch": patch,
                "before": {"status": before_status, "diff": before_diff},
                "state": "pending",
            }
            if plan_step_id:
                transaction["planStepId"] = plan_step_id
            transaction_path = TransactionStore(self.transaction_root).save(transaction)

        def _run() -> subprocess.CompletedProcess[str]:
            checked = subprocess.run(
                ["git", "apply", "--check", "--whitespace=nowarn"],
                input=patch, cwd=self.workspace, text=True, capture_output=True,
                timeout=15, check=False,
            )
            if checked.returncode != 0:
                return checked
            return subprocess.run(
                ["git", "apply", "--whitespace=nowarn"],
                input=patch, cwd=self.workspace, text=True, capture_output=True,
                timeout=15, check=False,
            )
        proc = await asyncio.to_thread(_run)
        after_status = await self._git("status", "--porcelain=v1")
        after_diff = await self._git("diff", "--binary")
        if proc.returncode != 0:
            if transaction is not None:
                transaction["state"] = "failed"
                transaction["after"] = {"status": after_status, "diff": after_diff}
                TransactionStore(self.transaction_root or self.workspace).save(transaction)
            raise RuntimeError(_truncate_text(proc.stderr.strip() or proc.stdout.strip() or "git apply failed"))
        if transaction is not None:
            transaction.update({
                "state": "applied",
                "appliedAt": _now_iso(),
                "after": {"status": after_status, "diff": after_diff},
                "rollback": {
                    "fixedArgv": ["git", "apply", "-R"],
                    "exactPatch": patch,
                },
            })
            transaction_path = TransactionStore(self.transaction_root or self.workspace).save(transaction)
        result = {
            "applied": True,
            "summary": str(args.get("summary") or ""),
            "transactionId": transaction_id,
            "transactionPath": str(transaction_path) if transaction_path else None,
            "before": {"status": before_status, "diff": before_diff},
            "after": {"status": after_status, "diff": after_diff},
            "rollback": {
                "method": "reverse-exact-patch",
                "fixedArgv": ["git", "apply", "-R"],
                "transactionId": transaction_id,
                "requiresReview": True,
            },
        }
        if plan_step_id:
            result["planStepId"] = plan_step_id
        return result

    async def _git(self, *argv: str) -> dict[str, Any]:
        def _run() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["git", *argv], cwd=self.workspace, text=True, capture_output=True,
                timeout=15, check=False,
            )
        proc = await asyncio.to_thread(_run)
        return {
            "exitCode": proc.returncode,
            "stdout": _truncate_text(proc.stdout),
            "stderr": _truncate_text(proc.stderr),
        }

    async def git_status(self, args: dict[str, Any]) -> dict[str, Any]:
        del args
        return await self._git("status", "--porcelain=v1", "--branch")

    async def git_diff(self, args: dict[str, Any]) -> dict[str, Any]:
        argv = ["diff", "--no-ext-diff"]
        if args.get("staged"):
            argv.append("--cached")
        path = args.get("path")
        argv.append("--")
        if path:
            argv.append(str(self.resolve_path(str(path))))
        return await self._git(*argv)

    async def execute(
        self,
        call: ToolCall,
        *,
        known_steps: dict[str, str] | None = None,
        plan_step_id: str | None = None,
    ) -> dict[str, Any]:
        if call.name not in self.names:
            raise ValueError(f"unknown tool: {call.name}")
        if call.name == "update_plan":
            return await self.update_plan(call.arguments, known_steps=known_steps)
        if call.name == "apply_patch":
            return await self.apply_patch(call.arguments, plan_step_id=plan_step_id)
        if call.name == "run_tests_workspace":
            return await self.run_tests_workspace(call.arguments, plan_step_id=plan_step_id)
        handler = getattr(self, call.name)
        return await handler(call.arguments)


class FunctionModelClient:
    """Test/local extension point for injecting a deterministic model."""

    def __init__(
        self,
        complete_fn: Callable[..., Awaitable[ModelResponse]],
    ) -> None:
        self._complete_fn = complete_fn

    async def complete(self, **kwargs: Any) -> ModelResponse:
        return await self._complete_fn(**kwargs)


class OpenAICompatibleModelClient:
    """Minimal multi-provider client for OpenAI-compatible chat completions.

    Model prefixes currently select OpenAI or Zhipu credentials/base URLs:
    ``openai/gpt-5`` and ``zhipu/glm-4.7``; custom prefixes come from
    ``[model_providers]``.  Unprefixed models use OpenAI when
    ``OPENAI_API_KEY`` is present.  API keys are never emitted to events/audit.
    """

    def __init__(self, config: HalterConfig | None = None) -> None:
        self.config = config or HalterConfig()

    def _provider(self, model: str | None) -> tuple[str, str, str]:
        route = resolve_route(model, self.config)
        if route.error or not route.base_url:
            raise RuntimeError(route.error or "model provider has no base_url")
        key = os.environ.get(route.api_key_env, "") if route.api_key_env else ""
        return route.model, key, route.base_url

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str | None,
        stream_delta: StreamCallback | None = None,
    ) -> ModelResponse:
        requested_model, api_key, base_url = self._provider(model)
        if not api_key and not is_local_base_url(base_url):
            provider = "openai" if not model or not model.startswith("zhipu/") else "zhipu"
            raise RuntimeError(f"missing {provider} API key; configure the model provider first")
        payload = {
            "model": requested_model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "max_tokens": 4096,
            "stream": True,
        }
        async with aiohttp.ClientSession() as http:
            async with http.post(
                f"{base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers={
                    **({"Authorization": f"Bearer {api_key}"} if api_key else {}),
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=120),
            ) as response:
                if response.status >= 400:
                    body = await response.text()
                    raise RuntimeError(f"model API HTTP {response.status}: {_truncate_text(body, 2000)}")
                content_type = response.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    try:
                        doc = json.loads(await response.text())
                        message = doc["choices"][0]["message"]
                    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                        raise RuntimeError("invalid model API response") from exc
                    return self._response_from_message(message)
                if "text/event-stream" not in content_type:
                    body = await response.text()
                    raise RuntimeError(
                        f"unsupported model API content type: {content_type or 'unknown'}; "
                        f"{_truncate_text(body, 500)}"
                    )
                return await self._consume_stream(response, stream_delta)

    def _response_from_message(self, message: dict[str, Any]) -> ModelResponse:
        calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            function = raw.get("function") or {}
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {"_raw_arguments": function.get("arguments") or ""}
            calls.append(ToolCall(
                id=str(raw.get("id") or f"call-{len(calls)}"),
                name=str(function.get("name") or ""),
                arguments=arguments if isinstance(arguments, dict) else {},
            ))
        return ModelResponse(
            content=str(message.get("content") or ""),
            tool_calls=tuple(calls),
            streamed=False,
        )

    async def _consume_stream(
        self,
        response: aiohttp.ClientResponse,
        stream_delta: StreamCallback | None,
    ) -> ModelResponse:
        content_parts: list[str] = []
        tool_calls: dict[int, dict[str, str]] = {}
        event_lines: list[str] = []
        delta_tasks: list[asyncio.Task[None]] = []
        buffer = ""

        def process_event(lines: list[str]) -> None:
            data = "\n".join(line[5:].lstrip() for line in lines if line.startswith("data:"))
            if not data or data == "[DONE]":
                return
            try:
                doc = json.loads(data)
                delta = doc.get("choices", [{}])[0].get("delta") or {}
            except (json.JSONDecodeError, IndexError, TypeError):
                return
            content = delta.get("content")
            if isinstance(content, str) and content:
                content_parts.append(content)
                if stream_delta is not None:
                    task = asyncio.ensure_future(stream_delta(content))
                    delta_tasks.append(task)
                    def _done(future: asyncio.Future[None]) -> None:
                        if not future.cancelled() and future.exception() is not None:
                            future.exception()
                    task.add_done_callback(_done)
            for raw in delta.get("tool_calls") or []:
                try:
                    index = int(raw.get("index", len(tool_calls)))
                except (TypeError, ValueError):
                    index = len(tool_calls)
                item = tool_calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                if raw.get("id"):
                    item["id"] = str(raw["id"])
                function = raw.get("function") or {}
                if function.get("name"):
                    item["name"] += str(function["name"])
                if function.get("arguments"):
                    item["arguments"] += str(function["arguments"])

        async for raw in response.content.iter_any():
            buffer += raw.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.rstrip("\r")
                if line:
                    event_lines.append(line)
                    continue
                if event_lines:
                    process_event(event_lines)
                    event_lines = []

        if buffer:
            event_lines.append(buffer.rstrip("\r"))
        if event_lines:
            process_event(event_lines)

        if delta_tasks:
            await asyncio.gather(*delta_tasks)

        calls: list[ToolCall] = []
        for index in sorted(tool_calls):
            item = tool_calls[index]
            try:
                arguments = json.loads(item["arguments"] or "{}")
            except json.JSONDecodeError:
                arguments = {"_raw_arguments": item["arguments"]}
            calls.append(ToolCall(
                id=item["id"] or f"call-{index}",
                name=item["name"],
                arguments=arguments if isinstance(arguments, dict) else {},
            ))
        return ModelResponse(
            content="".join(content_parts),
            tool_calls=tuple(calls),
            streamed=True,
        )


def compact_history(history: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Keep complete prior turns and summarize the oldest overflow.

    Turns are split at user messages so an assistant ``tool_calls`` message is
    never separated from its tool responses. The summary explicitly tells the
    model that current workspace evidence wins over omitted history.
    """
    encoded_len = sum(len(json.dumps(message, ensure_ascii=False)) for message in history)
    if len(history) <= MAX_HISTORY_TURNS * 2 and encoded_len <= MAX_HISTORY_CHARS:
        return history, 0

    user_indices = [i for i, message in enumerate(history) if message.get("role") == "user"]
    if not user_indices:
        return history[-MAX_HISTORY_TURNS:], max(0, len(history) - MAX_HISTORY_TURNS)

    boundaries = [*user_indices, len(history)]
    turns = [history[boundaries[i]:boundaries[i + 1]] for i in range(len(boundaries) - 1)]
    # A prior compaction summary is replaced rather than accumulated.
    prefix = [
        message for message in history[:user_indices[0]]
        if message.get("role") != "system"
    ]
    kept: list[list[dict[str, Any]]] = []
    kept_chars = sum(len(json.dumps(message, ensure_ascii=False)) for message in prefix)
    dropped = 0
    for turn in reversed(turns):
        turn_chars = sum(len(json.dumps(message, ensure_ascii=False)) for message in turn)
        if kept and (len(kept) >= MAX_HISTORY_TURNS or kept_chars + turn_chars > MAX_HISTORY_CHARS):
            break
        kept.append(turn)
        kept_chars += turn_chars
    kept.reverse()
    if len(kept) == len(turns):
        return history, 0
    dropped = len(turns) - len(kept)
    summary = {
        "role": "system",
        "content": (
            f"{dropped} earlier conversation turn(s) were omitted to bound context. "
            "Current workspace files, git state, and the retained turns are authoritative; "
            "re-inspect files instead of relying on omitted details."
        ),
    }
    return [*prefix, summary, *sum(kept, [])], dropped


def tools_for_permission(mode: str) -> list[dict[str, Any]]:
    if mode == "workspace-write":
        return TOOL_SCHEMAS
    return [
        schema for schema in TOOL_SCHEMAS
        if schema["function"]["name"] not in WRITE_TOOL_NAMES
    ]


class AgentRuntime:
    """Owns the model/tool decision loop and emits auditable events."""

    def __init__(
        self,
        *,
        workspace: Path,
        model_client: ModelClient,
        session_channel: str,
        home: Path | None = None,
        max_iterations: int = MAX_ITERATIONS,
        request_approval: ApprovalCallback | None = None,
        config: HalterConfig | None = None,
    ) -> None:
        self.workspace = workspace.expanduser().resolve(strict=True)
        self.model = model_client
        transaction_root = transaction_root_for_session(session_channel, home)
        self.tools = WorkspaceTools(self.workspace, transaction_root=transaction_root)
        self.audit = AuditLog(session_channel, home=home)
        self.max_iterations = max_iterations
        self.request_approval = request_approval
        self.config = config or HalterConfig()
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        # plan step id -> title, plus step id -> linked evidence collected
        # during this runtime's turns.  Both survive across run() calls so
        # follow-up turns keep linking evidence to the same steps.
        self.plan_steps: dict[str, str] = {}
        self.plan_evidence: dict[str, dict[str, Any]] = {}
        # provider -> latest delegate diff of the current run(); feeds the
        # deterministic multi-harness comparison attached to tool results.
        self._turn_delegate_diffs: dict[str, str] = {}
        # Declared MCP tools discovered on the first run of this runtime.
        self.mcp_tools: dict[str, McpToolDescriptor] = {}
        self._mcp_loaded = False

    async def _load_mcp_tools(self) -> list[McpToolDescriptor]:
        """Discover declared MCP tools once; broken servers yield nothing."""
        if self._mcp_loaded:
            return list(self.mcp_tools.values())
        self._mcp_loaded = True
        try:
            descriptors = await list_mcp_tools()
        except Exception:
            descriptors = []
        self.mcp_tools = {descriptor.name: descriptor for descriptor in descriptors}
        return list(self.mcp_tools.values())

    def _mcp_tool_schemas(self, permission_mode: str) -> list[dict[str, Any]]:
        read_allow, write_allow = mcp_allowlists(self.config)
        schemas: list[dict[str, Any]] = []
        for descriptor in self.mcp_tools.values():
            if descriptor.read_only:
                if read_allow is None or descriptor.name in read_allow:
                    schemas.append(descriptor.schema)
            elif permission_mode == "workspace-write" and descriptor.name in write_allow:
                schemas.append(descriptor.schema)
        return schemas

    async def _emit(self, emit: EventCallback | None, event: AgentEvent) -> None:
        if emit is not None:
            await emit(event)

    @staticmethod
    def _plan_step_id(call: ToolCall) -> str | None:
        """Read the optional plan_step_id argument from a tool call."""
        raw = call.arguments.get("plan_step_id")
        if raw is None:
            return None
        value = str(raw).strip()
        return value if PLAN_STEP_ID_PATTERN.fullmatch(value) else None

    def _evidence_bucket(self, step_id: str) -> dict[str, Any]:
        bucket = self.plan_evidence.get(step_id)
        if bucket is None:
            bucket = {
                "toolCallIds": [],
                "approvalIds": [],
                "transactionIds": [],
                "auditTimestamps": [],
                "updatedAt": _now_iso(),
            }
            self.plan_evidence[step_id] = bucket
        return bucket

    def _touch_evidence(self, bucket: dict[str, Any]) -> None:
        now = _now_iso()
        bucket["auditTimestamps"].append(now)
        bucket["updatedAt"] = now

    def _evidence_tool_call(self, step_id: str | None, call: ToolCall) -> None:
        if not step_id:
            return
        bucket = self._evidence_bucket(step_id)
        bucket["toolCallIds"].append({"id": call.id, "name": call.name, "ts": _now_iso()})
        self._touch_evidence(bucket)

    def _evidence_approval(
        self, step_id: str | None, approval_id: str, tool: str, approved: bool
    ) -> None:
        if not step_id:
            return
        bucket = self._evidence_bucket(step_id)
        bucket["approvalIds"].append({
            "id": approval_id, "tool": tool, "approved": approved, "ts": _now_iso(),
        })
        self._touch_evidence(bucket)

    def _evidence_transaction(self, step_id: str | None, transaction_id: str) -> None:
        if not step_id:
            return
        bucket = self._evidence_bucket(step_id)
        bucket["transactionIds"].append({"id": transaction_id, "ts": _now_iso()})
        self._touch_evidence(bucket)

    async def _execute_call(
        self,
        call: ToolCall,
        permission_mode: str,
        emit: EventCallback | None,
    ) -> dict[str, Any]:
        plan_step_id = self._plan_step_id(call)
        descriptor = self.mcp_tools.get(call.name)
        mcp_arguments = {
            key: value for key, value in call.arguments.items()
            if key != "plan_step_id"
        }
        needs_approval = (
            call.name in WRITE_TOOL_NAMES
            or (descriptor is not None and not descriptor.read_only)
        )
        if needs_approval:
            if permission_mode != "workspace-write":
                raise PermissionError(
                    f"tool {call.name} requires workspace-write mode; current mode is {permission_mode}"
                )
            if self.request_approval is None:
                raise PermissionError("no approval handler is connected")
            approval_id = f"approval-{uuid.uuid4().hex[:12]}"
            approval_request = {
                "id": approval_id,
                "toolCallId": call.id,
                "tool": call.name,
                "planStepId": plan_step_id,
                "summary": str(call.arguments.get("summary") or ""),
                "input": (
                    {"server": descriptor.server, "tool": descriptor.tool,
                     "arguments": dict(mcp_arguments)}
                    if descriptor is not None else
                    (
                        {"patch": str(call.arguments.get("patch") or "")}
                        if call.name == "apply_patch"
                        else {"arguments": dict(call.arguments)}
                    )
                ),
            }
            await self.audit.append("approval.requested", **approval_request)
            approved = await self.request_approval(approval_request)
            await self.audit.append(
                "approval.response", id=approval_id,
                toolCallId=call.id, approved=approved, planStepId=plan_step_id,
            )
            self._evidence_approval(plan_step_id, approval_id, call.name, approved)
            await self._emit(emit, {
                "type": "approval_result", "id": approval_id,
                "toolCallId": call.id, "approved": approved,
                "planStepId": plan_step_id,
            })
            if not approved:
                raise PermissionError("user denied the tool request")
        if call.name == "update_plan":
            validated = await self.tools.execute(call, known_steps=self.plan_steps)
            self.plan_steps = {step["id"]: step["title"] for step in validated["steps"]}
            await self._emit(emit, {
                "type": "plan_updated",
                "steps": validated["steps"],
                "note": validated["note"],
            })
            return validated
        if descriptor is not None:
            return await call_mcp_tool(
                descriptor.server, descriptor.tool, mcp_arguments,
            )
        output = await self.tools.execute(call, plan_step_id=plan_step_id)
        transaction_id = output.get("transactionId") if isinstance(output, dict) else None
        if transaction_id:
            self._evidence_transaction(plan_step_id, str(transaction_id))
        return output

    async def run(
        self,
        prompt: str,
        *,
        model: str | None = None,
        history: list[dict[str, Any]] | None = None,
        emit: EventCallback | None = None,
        permission_mode: str = "read-only",
    ) -> AgentResult:
        original_history = [
            message for message in (history or [])
            if message.get("name") != "halter_workspace_context"
        ]
        compacted_history, dropped_turns = compact_history(original_history)
        workspace_context = await self.tools.workspace_context()
        await self.audit.append("context.workspace", **workspace_context)
        context_message = {
            "role": "system",
            "name": "halter_workspace_context",
            "content": json.dumps(workspace_context, ensure_ascii=False),
        }
        messages = [
            self.messages[0], context_message, *compacted_history,
            {"role": "user", "content": prompt},
        ]
        available_tools = tools_for_permission(permission_mode)
        mcp_descriptors = await self._load_mcp_tools()
        if mcp_descriptors:
            available_tools = [
                *available_tools, *self._mcp_tool_schemas(permission_mode),
            ]
        if dropped_turns:
            await self.audit.append(
                "context.compacted", droppedTurns=dropped_turns,
                originalMessages=len(original_history),
                retainedMessages=len(compacted_history),
            )
        await self.audit.append(
            "turn.started", model=model or "default", workspace=str(self.workspace),
            prompt=redact_text(prompt), tools=sorted(
                schema["function"]["name"] for schema in available_tools
            ),
            permissionMode=permission_mode,
        )
        calls_executed = 0
        self._turn_delegate_diffs = {}
        try:
            for iteration in range(1, self.max_iterations + 1):
                async def stream_delta(text: str) -> None:
                    await self._emit(emit, {"type": "assistant_delta", "content": text})

                response = await self.model.complete(
                    messages=messages, tools=available_tools, model=model,
                    stream_delta=stream_delta,
                )
                assistant: dict[str, Any] = {"role": "assistant", "content": response.content or ""}
                if response.tool_calls:
                    assistant["tool_calls"] = [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)},
                        }
                        for call in response.tool_calls
                    ]
                messages.append(assistant)
                await self.audit.append(
                    "model.response", iteration=iteration, model=model or "default",
                    content=redact_text(response.content),
                    toolCalls=[{"id": c.id, "name": c.name, "arguments": c.arguments} for c in response.tool_calls],
                )
                if response.content and not response.streamed:
                    await self._emit(emit, {"type": "assistant_delta", "content": response.content + "\n"})
                if not response.tool_calls:
                    result = AgentResult(
                        response.content, messages, iteration, calls_executed,
                        dict(self.plan_evidence),
                    )
                    await self.audit.append("turn.completed", iterations=iteration, toolCalls=calls_executed)
                    return result

                calls = list(response.tool_calls)
                calls_executed += len(calls)
                for call in calls:
                    plan_step_id = self._plan_step_id(call)
                    self._evidence_tool_call(plan_step_id, call)
                    await self._emit(emit, {
                        "type": "tool_call", "id": call.id, "name": call.name,
                        "arguments": call.arguments, "planStepId": plan_step_id,
                    })
                    await self.audit.append(
                        "tool.call", id=call.id, name=call.name,
                        arguments=call.arguments, planStepId=plan_step_id,
                    )

                async def run_call(call: ToolCall) -> tuple[ToolCall, bool, dict[str, Any]]:
                    try:
                        output = await self._execute_call(call, permission_mode, emit)
                        return call, True, output
                    except Exception as exc:
                        return call, False, {"error": type(exc).__name__, "message": str(exc)}

                # Multiple read-only/delegate calls in one model response can
                # run concurrently (for example Claude + Codex sandbox reviews).
                # Any write/approval call forces sequential execution to avoid
                # patch/test conflicts and ambiguous approval ordering.
                if any(call.name in WRITE_TOOL_NAMES for call in calls):
                    outcomes = [await run_call(call) for call in calls]
                else:
                    tasks = [asyncio.create_task(run_call(call)) for call in calls]
                    outcomes = list(await asyncio.gather(*tasks))

                # Attach a deterministic comparison of this run's delegate
                # diffs to every delegate result so the model (and the audit
                # trail) sees identical/conflicting/overlapping/unique files
                # without re-reading raw diff text.
                for call, ok, output in outcomes:
                    if ok and call.name == "delegate_harness" and isinstance(output, dict):
                        diff = str(output.get("diff") or "")
                        provider = str(output.get("provider") or "")
                        if diff.strip() and provider:
                            self._turn_delegate_diffs[provider] = diff
                if len(self._turn_delegate_diffs) >= 2:
                    comparison = compare_provider_diffs(
                        dict(self._turn_delegate_diffs)
                    ).to_dict()
                    for call, ok, output in outcomes:
                        if call.name == "delegate_harness" and isinstance(output, dict):
                            output["comparison"] = comparison

                for call, ok, output in outcomes:
                    plan_step_id = self._plan_step_id(call)
                    serialized = json.dumps(output, ensure_ascii=False)
                    await self._emit(emit, {
                        "type": "tool_result", "id": call.id, "name": call.name,
                        "ok": ok, "result": output, "planStepId": plan_step_id,
                    })
                    await self.audit.append(
                        "tool.result", id=call.id, name=call.name, ok=ok,
                        result=output, planStepId=plan_step_id,
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": serialized,
                    })
            raise RuntimeError(f"agent exceeded the maximum tool-decision iterations ({self.max_iterations})")
        except Exception as exc:
            await self.audit.append("turn.failed", errorType=type(exc).__name__, message=str(exc))
            raise
