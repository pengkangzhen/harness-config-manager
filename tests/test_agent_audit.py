"""Read-only native-agent audit inspection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_config_manager.agent_audit import (
    list_audits,
    search_audits,
    show_audit,
    summarize_audit,
)


def _write_audit(fake_home: Path, session_id: str = "ahp-test") -> Path:
    audit = fake_home / ".config/halter/agent-audit" / session_id / "audit.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    events = [
        {"ts": "2026-01-01T00:00:00Z", "kind": "turn.started", "prompt": "hello"},
        {"ts": "2026-01-01T00:00:01Z", "kind": "tool.call", "name": "read_file"},
        {"ts": "2026-01-01T00:00:02Z", "kind": "approval.response", "approved": True},
    ]
    audit.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")

    state = fake_home / ".config/halter/agent-sessions" / session_id / "session.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({
        "session": {
            "resource": "ahp-session:/test",
            "provider": "halter",
            "title": "Audit test",
            "workingDirectories": ["file:///tmp/project"],
        },
        "chats": [],
    }), encoding="utf-8")
    return audit


def test_list_and_summarize_audits(fake_home: Path) -> None:
    _write_audit(fake_home)
    summaries = list_audits(home=fake_home)
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.session_id == "ahp-test"
    assert summary.provider == "halter"
    assert summary.title == "Audit test"
    assert summary.workspace == "file:///tmp/project"
    assert summary.event_count == 3
    assert summary.kinds == {
        "approval.response": 1,
        "tool.call": 1,
        "turn.started": 1,
    }


def test_show_audit_filters_and_tails(fake_home: Path) -> None:
    _write_audit(fake_home)
    result = show_audit("ahp-test", kind="tool.call", tail=10, home=fake_home)
    assert result["total_events"] == 1
    assert result["events"][0]["name"] == "read_file"
    all_events = show_audit("ahp-test", tail=2, home=fake_home)
    assert [event["kind"] for event in all_events["events"]] == [
        "tool.call", "approval.response",
    ]


def test_audit_session_id_is_validated(fake_home: Path) -> None:
    _write_audit(fake_home)
    with pytest.raises(ValueError):
        summarize_audit("../escape", home=fake_home)


def test_audit_cli_json_contracts(fake_home: Path) -> None:
    from typer.testing import CliRunner

    from harness_config_manager.cli import app

    _write_audit(fake_home)
    runner = CliRunner()
    listed = runner.invoke(app, ["audit", "list", "--json"])
    assert listed.exit_code == 0
    payload = json.loads(listed.output)
    assert payload["count"] == 1
    assert payload["audits"][0]["session_id"] == "ahp-test"

    shown = runner.invoke(app, [
        "audit", "show", "--kind", "tool.call", "--tail", "10", "--json", "--", "ahp-test",
    ])
    assert shown.exit_code == 0
    detail = json.loads(shown.output)
    assert detail["summary"]["provider"] == "halter"
    assert detail["events"][0]["kind"] == "tool.call"


def _write_rich_audit(fake_home: Path, session_id: str = "ahp-rich") -> Path:
    audit = fake_home / ".config/halter/agent-audit" / session_id / "audit.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    events = [
        {"ts": "2026-02-01T10:00:00Z", "kind": "turn.started", "permissionMode": "workspace-write"},
        {"ts": "2026-02-01T10:00:01Z", "kind": "tool.call", "id": "call-1",
         "name": "apply_patch", "planStepId": "step-a"},
        {"ts": "2026-02-01T10:00:02Z", "kind": "approval.requested", "id": "approval-1",
         "tool": "apply_patch", "planStepId": "step-a"},
        {"ts": "2026-02-01T10:00:03Z", "kind": "tool.result", "id": "call-1",
         "name": "apply_patch", "ok": True,
         "result": {"transactionId": "patch-abc123", "applied": True}},
        {"ts": "2026-02-01T10:00:04Z", "kind": "transaction.rolledBack", "id": "patch-abc123"},
        {"ts": "2026-02-02T11:00:00Z", "kind": "tool.call", "id": "call-2",
         "name": "run_tests"},
    ]
    audit.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    return audit


def test_search_locates_events_by_transaction_id(fake_home: Path) -> None:
    _write_rich_audit(fake_home)
    result = search_audits(transaction_id="patch-abc123", home=fake_home)
    kinds = [match["event"]["kind"] for match in result["matches"]]
    assert "tool.result" in kinds
    assert "transaction.rolledBack" in kinds
    assert all(match["session_id"] == "ahp-rich" for match in result["matches"])
    assert result["total"] == 2


def test_search_filters_by_tool_name_and_kind(fake_home: Path) -> None:
    _write_rich_audit(fake_home)
    by_tool = search_audits(tool_name="apply_patch", home=fake_home)
    assert {match["event"]["kind"] for match in by_tool["matches"]} == {
        "tool.call", "approval.requested", "tool.result",
    }
    narrowed = search_audits(tool_name="apply_patch", kind="tool.call", home=fake_home)
    assert [match["event"]["id"] for match in narrowed["matches"]] == ["call-1"]


def test_search_text_date_and_session_filters(fake_home: Path) -> None:
    _write_rich_audit(fake_home)
    text = search_audits(text="run_tests", home=fake_home)
    assert text["total"] == 1
    assert text["matches"][0]["event"]["id"] == "call-2"

    window = search_audits(date_from="2026-02-01", date_to="2026-02-01", home=fake_home)
    assert window["total"] == 5
    assert all(match["event"]["ts"].startswith("2026-02-01") for match in window["matches"])

    _write_audit(fake_home)  # 另一个 session（provider=halter，workspace /tmp/project）
    scoped = search_audits(session_id="ahp-rich", tool_name="run_tests", home=fake_home)
    assert scoped["sessions_scanned"] == 1
    assert scoped["matches"][0]["session_id"] == "ahp-rich"

    by_workspace = search_audits(workspace="/tmp/project", home=fake_home)
    assert by_workspace["sessions_scanned"] == 1  # 只命中 ahp-test 的 session store

    by_provider = search_audits(provider="halter", home=fake_home)
    assert by_provider["sessions_scanned"] == 1


def test_search_paginates_and_caps_large_files(fake_home: Path, monkeypatch) -> None:
    import harness_config_manager.agent_audit as audit_module

    _write_rich_audit(fake_home)
    page1 = search_audits(home=fake_home, limit=2, offset=0)
    page2 = search_audits(home=fake_home, limit=2, offset=2)
    assert page1["total"] == 6 and page1["returned"] == 2
    assert page2["returned"] == 2
    assert page1["matches"][0]["event"]["ts"] != page2["matches"][0]["event"]["ts"]

    monkeypatch.setattr(audit_module, "MAX_SEARCH_LINES", 3)
    capped = search_audits(home=fake_home)
    assert capped["truncated_files"] == ["ahp-rich"]
    assert capped["total"] == 3  # 前 3 行内的事件


def test_search_skips_malformed_files_and_rejects_traversal(fake_home: Path) -> None:
    _write_rich_audit(fake_home)
    broken = fake_home / ".config/halter/agent-audit" / "ahp-broken" / "audit.jsonl"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text('{"ts": "2026-01-01T00:00:00Z", "kind": "ok"}\nnot-json\n', encoding="utf-8")

    result = search_audits(text="apply_patch", home=fake_home)
    assert result["skipped_files"] == ["ahp-broken"]
    assert all(match["session_id"] == "ahp-rich" for match in result["matches"])

    with pytest.raises(ValueError):
        search_audits(session_id="../escape", home=fake_home)


def test_audit_search_cli_json(fake_home: Path) -> None:
    from typer.testing import CliRunner

    from harness_config_manager.cli import app

    _write_rich_audit(fake_home)
    runner = CliRunner()
    result = runner.invoke(app, [
        "audit", "search", "--transaction-id", "patch-abc123", "--json",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["total"] == 2
    kinds = {match["event"]["kind"] for match in payload["matches"]}
    assert "transaction.rolledBack" in kinds

    tool_result = runner.invoke(app, [
        "audit", "search", "--tool", "apply_patch", "--kind", "tool.call", "--json",
    ])
    assert tool_result.exit_code == 0
    assert json.loads(tool_result.output)["total"] == 1
