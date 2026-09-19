from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from harness_config_manager.config import HalterConfig
from harness_config_manager.sessions import (
    build_context,
    format_transcript,
    install_session_skill,
    read_session,
    scan_sessions,
)


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def make_claude_session(home: Path, project: Path, sid: str = "claude-sid") -> Path:
    encoded = "-" + str(project)[1:].replace("/", "-")
    return _write_jsonl(home / ".claude/projects" / encoded / f"{sid}.jsonl", [
        {"type": "user", "timestamp": "2026-09-18T01:00:00Z", "cwd": str(project),
         "gitBranch": "main", "message": {"content": "fix login bug"}},
        {"type": "assistant", "timestamp": "2026-09-18T01:01:00Z",
         "message": {"content": [{"type": "text", "text": "I will inspect auth."}]}},
    ])


def make_codex_session(home: Path, project: Path, sid: str = "codex-sid") -> Path:
    path = home / ".codex/sessions/2026/09/18" / f"rollout-2026-09-18T02-00-00-{sid}.jsonl"
    return _write_jsonl(path, [
        {"type": "session_meta", "timestamp": "2026-09-18T02:00:00Z", "payload": {
            "id": sid, "cwd": str(project), "model_provider": "test",
            "git": {"branch": "feature/x"},
        }},
        {"type": "response_item", "timestamp": "2026-09-18T02:01:00Z", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "api_key = abcdefghijklmnop"}],
        }},
        {"type": "response_item", "timestamp": "2026-09-18T02:02:00Z", "payload": {
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "Check config."}],
        }},
    ])


def make_opencode_session(home: Path, project: Path, sid: str = "ses_opencode") -> None:
    db = home / ".local/share/opencode/opencode.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript("""
      create table project (id text primary key, worktree text not null);
      create table session (
        id text primary key, project_id text not null, directory text not null,
        title text not null, time_created integer not null, time_updated integer not null,
        model text
      );
      create table message (
        id text primary key, session_id text not null, time_created integer not null,
        time_updated integer not null, data text not null
      );
      create table part (
        id text primary key, message_id text not null, session_id text not null,
        time_created integer not null, time_updated integer not null, data text not null
      );
    """)
    conn.execute("insert into project values ('p1', ?)", (str(project),))
    conn.execute(
        "insert into session values (?, 'p1', ?, 'OpenCode task', 1000, 2000, 'test')",
        (sid, str(project)),
    )
    conn.execute("insert into message values ('m1', ?, 1100, 1200, ?)",
                 (sid, json.dumps({"role": "user"})))
    conn.execute("insert into part values ('q1', 'm1', ?, 1150, 1150, ?)",
                 (sid, json.dumps({"type": "text", "text": "OpenCode question"})))
    conn.commit()
    conn.close()


def make_zcode_session(home: Path, project: Path, sid: str = "sess_zcode") -> None:
    db = home / ".zcode/cli/db/db.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript("""
      create table session (
        id text primary key, project_id text not null, workspace_id text, parent_id text,
        slug text not null, directory text not null, path text, title text not null,
        version text not null, share_url text, summary_additions integer,
        summary_deletions integer, summary_files integer, summary_diffs text, revert text,
        permission text, time_created integer not null, time_updated integer not null,
        time_compacting integer, time_archived integer, task_type text not null default 'interactive'
      );
      create table message (
        id text primary key, session_id text not null, time_created integer not null,
        time_updated integer not null, data text not null, sequence integer
      );
      create table part (
        id text primary key, message_id text not null, session_id text not null,
        time_created integer not null, time_updated integer not null, data text not null,
        sequence integer
      );
    """)
    conn.execute(
        "insert into session values (?, 'p', NULL, NULL, ?, ?, NULL, 'ZCode task', '0', "
        "NULL, NULL, NULL, NULL, NULL, NULL, NULL, 3000, 4000, NULL, NULL, 'interactive')",
        (sid, sid, str(project)),
    )
    conn.execute("insert into message values ('m1', ?, 3100, 3200, ?, NULL)",
                 (sid, json.dumps({"role": "user"})))
    conn.execute("insert into part values ('q1', 'm1', ?, 3150, 3150, ?, NULL)",
                 (sid, json.dumps({"type": "text", "text": "ZCode question"})))
    conn.commit()
    conn.close()


def test_project_scoped_cross_tool_session_list(fake_home: Path, tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    make_claude_session(fake_home, project)
    make_codex_session(fake_home, project)
    make_opencode_session(fake_home, project)
    make_zcode_session(fake_home, project)
    _write_jsonl(fake_home / ".claude/projects/-tmp-other/other.jsonl", [
        {"type": "user", "timestamp": "2026-09-18T03:00:00Z", "cwd": "/tmp/other",
         "message": {"content": "different project"}}
    ])

    sessions = scan_sessions(project)
    assert {(s.tool, s.message_count, s.title) for s in sessions} == {
        ("claude", 2, "fix login bug"),
        ("codex", 2, "api_key = <REDACTED>"),
        ("opencode", 1, "OpenCode task"),
        ("zcode", 1, "ZCode task"),
    }
    assert all(s.project == project.resolve() for s in sessions)


def test_transcript_is_explicit_and_redacted(fake_home: Path, tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    make_codex_session(fake_home, project)
    messages = read_session("codex:codex-sid", project)
    text = format_transcript(messages)
    assert "Check config." in text
    assert "abcdefghijklmnop" not in text
    assert "api_key = <REDACTED>" in text


def test_context_is_deterministic_and_scoped(fake_home: Path, tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    make_codex_session(fake_home, project)
    context = build_context("codex:codex-sid", project, tail=2)
    assert context.startswith("# Halter session handoff")
    assert "Source: `codex:codex-sid`" in context
    assert "Check config." in context
    assert "not a model summary" in context


def test_install_builtin_skill_to_detected_tools(fake_home: Path) -> None:
    claude = fake_home / ".claude/skills"
    claude.mkdir(parents=True)
    codex = fake_home / ".codex/skills"
    codex.mkdir(parents=True)

    plan = install_session_skill(HalterConfig(), ["claude", "codex"], apply=False)
    assert not (fake_home / ".agents/skills/halter-sessions/SKILL.md").exists()
    assert any(x.startswith("install halter-sessions") for x in plan)

    applied = install_session_skill(HalterConfig(), ["claude", "codex"], apply=True)
    assert any(x.startswith("install halter-sessions") for x in applied)
    lib = fake_home / ".agents/skills/halter-sessions/SKILL.md"
    assert lib.is_file()
    assert (claude / "halter-sessions").is_symlink()
    assert (codex / "halter-sessions").is_symlink()
    assert "halter sessions list --project . --json" in lib.read_text(encoding="utf-8")


def test_cli_json_list_and_context(fake_home: Path, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from harness_config_manager.cli import app

    project = tmp_path / "repo"
    project.mkdir()
    make_claude_session(fake_home, project)

    result = CliRunner().invoke(app, ["sessions", "list", "--project", str(project), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["count"] == 1
    ref = payload["sessions"][0]["ref"]
    assert ref == "claude:claude-sid"

    shown = CliRunner().invoke(app, [
        "sessions", "show", ref, "--project", str(project), "--transcript", "--json",
    ])
    assert shown.exit_code == 0, shown.output
    assert "I will inspect auth." in json.loads(shown.output)["transcript"]


def test_optional_ctx_provider_is_merged_without_duplication(
    fake_home: Path, tmp_path: Path, monkeypatch
) -> None:
    import harness_config_manager.sessions as mod

    project = tmp_path / "repo"
    project.mkdir()
    make_claude_session(fake_home, project)

    events = [
        {"record_type": "event_range_event", "event": {
            "provider": "claude", "ctx_session_id": "ctx-native", "provider_session_id": "claude-sid",
            "occurred_at_ms": 1_000,
        }},
        {"record_type": "event_range_event", "event": {
            "provider": "cursor", "ctx_session_id": "ctx-cursor", "provider_session_id": "cursor-native",
            "occurred_at_ms": 2_000,
        }},
        {"record_type": "event_range_completion"},
    ]

    class Proc:
        returncode = 0
        stdout = "\n".join(json.dumps(x) for x in events)

    monkeypatch.setattr(mod, "_ctx_executable", lambda: "/fake/ctx")
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: Proc())
    mod._CTX_SCAN_CACHE.clear()
    sessions = mod.scan_sessions(project)
    refs = {s.ref for s in sessions}
    assert "claude:claude-sid" in refs
    assert "cursor:ctx-cursor" in refs
    assert "claude:ctx-native" not in refs
