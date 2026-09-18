"""Read-only native-agent audit inspection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_config_manager.agent_audit import list_audits, show_audit, summarize_audit


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
