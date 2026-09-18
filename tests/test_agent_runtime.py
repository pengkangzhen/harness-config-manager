"""Halter-native agent runtime: tools, confinement, model/tool loop, audit."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from harness_config_manager.agent import (
    AgentRuntime,
    FunctionModelClient,
    ModelResponse,
    ToolCall,
    WorkspaceTools,
)


def _sequential_model(responses: list[ModelResponse]):
    remaining = iter(responses)

    async def complete(**_kwargs):
        return next(remaining)

    return FunctionModelClient(complete)


def test_workspace_tools_are_read_only_and_confined(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("hello agent\n", encoding="utf-8")
    tools = WorkspaceTools(tmp_path)

    result = asyncio.run(tools.read_file({"path": "notes.md"}))
    assert result["content"] == "hello agent\n"

    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    with pytest.raises(PermissionError):
        tools.resolve_path("../outside.txt")


def test_agent_runtime_executes_audited_tool_calls(fake_home: Path, tmp_path: Path) -> None:
    source = tmp_path / "README.md"
    source.write_text(
        "# Project\n\nUse api_key=super-secret-value-123 in the example.\n",
        encoding="utf-8",
    )
    model = _sequential_model([
        ModelResponse(tool_calls=(ToolCall("call-1", "read_file", {"path": "README.md"}),)),
        ModelResponse(content="I inspected README.md and redacted the secret."),
    ])
    runtime = AgentRuntime(
        workspace=tmp_path,
        model_client=model,
        session_channel="ahp-session:/native-test",
        home=fake_home,
    )
    events: list[dict] = []

    async def run() -> None:
        async def emit(event: dict) -> None:
            events.append(event)
        result = await runtime.run("inspect the README", model="openai/test", emit=emit)
        assert result.content == "I inspected README.md and redacted the secret."
        assert result.iterations == 2
        assert result.tool_calls == 1

    asyncio.run(run())
    kinds = [event["type"] for event in events]
    assert kinds == ["tool_call", "tool_result", "assistant_delta"]

    audit_files = list((fake_home / ".config/halter/agent-audit").rglob("audit.jsonl"))
    assert len(audit_files) == 1
    audit_path = audit_files[0]
    assert audit_path.stat().st_mode & 0o777 == 0o600
    records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    by_kind = {record["kind"]: record for record in records}
    assert by_kind["turn.started"]["tools"] == sorted({
        "read_file", "list_dir", "search_files", "git_status", "git_diff", "list_project_sessions", "read_project_session", "delegate_harness", "update_plan",
    })
    assert by_kind["tool.call"]["name"] == "read_file"
    assert "super-secret-value-123" not in audit_path.read_text(encoding="utf-8")
    assert "<REDACTED>" in by_kind["tool.result"]["result"]["content"]
    assert by_kind["turn.completed"]["toolCalls"] == 1


def test_agent_runtime_reports_tool_error_to_model(fake_home: Path, tmp_path: Path) -> None:
    model = _sequential_model([
        ModelResponse(tool_calls=(ToolCall("bad", "read_file", {"path": "../escape.txt"}),)),
        ModelResponse(content="The path was rejected."),
    ])
    runtime = AgentRuntime(
        workspace=tmp_path,
        model_client=model,
        session_channel="ahp-session:/error-test",
        home=fake_home,
    )

    async def run() -> None:
        result = await runtime.run("read outside")
        assert result.content == "The path was rejected."
        tool_message = [m for m in result.messages if m.get("role") == "tool"][0]
        assert "path escapes workspace" in tool_message["content"]

    asyncio.run(run())


def test_workspace_patch_validation_rejects_parent_escape(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path)
    patch = """diff --git a/inside.txt b/../outside.txt
--- a/inside.txt
+++ b/../outside.txt
@@ -1 +1 @@
-old
+new
"""
    with pytest.raises(PermissionError):
        tools._validate_patch_paths(patch)


def test_delegate_harness_runs_in_disposable_clone(fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    import stat
    import subprocess

    source = tmp_path / "source.txt"
    source.write_text("original\n", encoding="utf-8")
    untracked = tmp_path / "notes.txt"
    untracked.write_text("untrusted local note\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "source.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    bin_dir = tmp_path.parent / f"{tmp_path.name}-delegate-bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text("#!/bin/sh\necho delegated-output\ncat notes.txt\nprintf 'diff --git a/source.txt b/source.txt\n--- a/source.txt\n+++ b/source.txt\n@@ -1 +1 @@\n-original\n+delegated\n' > source.txt\n", encoding="utf-8")
    claude.chmod(claude.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    model = _sequential_model([
        ModelResponse(tool_calls=(ToolCall("delegate-1", "delegate_harness", {
            "provider": "claude", "prompt": "review and fix source.txt",
        }),)),
        ModelResponse(content="delegated harness returned a diff"),
    ])
    runtime = AgentRuntime(
        workspace=tmp_path, model_client=model,
        session_channel="ahp-session:/delegate-test", home=fake_home,
    )

    async def run() -> None:
        result = await runtime.run("consult claude")
        tool_messages = [m for m in result.messages if m.get("role") == "tool"]
        assert tool_messages
        payload = json.loads(tool_messages[0]["content"])
        assert payload["provider"] == "claude"
        assert payload["exitCode"] == 0
        assert "delegated-output" in payload["stdout"]
        assert "-original" in payload["diff"]
        assert "+delegated" in payload["diff"]
        assert "untrusted local note" in payload["stdout"]
        assert payload["snapshot"]["untrackedCopied"] == ["notes.txt"]

    asyncio.run(run())
    assert source.read_text(encoding="utf-8") == "original\n"


def test_run_tests_requires_approval_and_uses_fixed_argv(fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    import stat
    import subprocess

    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "pyproject.toml"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pytest_bin = bin_dir / "pytest"
    pytest_bin.write_text("#!/bin/sh\necho TEST-OK\n", encoding="utf-8")
    pytest_bin.chmod(pytest_bin.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    requests: list[dict] = []

    async def approve(request: dict) -> bool:
        requests.append(request)
        return True

    model = _sequential_model([
        ModelResponse(tool_calls=(ToolCall("test-1", "run_tests", {"command": "pytest"}),)),
        ModelResponse(content="tests passed"),
    ])
    runtime = AgentRuntime(
        workspace=tmp_path, model_client=model,
        session_channel="ahp-session:/test-approval", home=fake_home,
        request_approval=approve,
    )

    async def run() -> None:
        result = await runtime.run("run tests", permission_mode="workspace-write")
        tool = [m for m in result.messages if m.get("role") == "tool"][0]
        payload = json.loads(tool["content"])
        assert payload["command"] == ["pytest"]
        assert payload["exitCode"] == 0
        assert "TEST-OK" in payload["output"]
        assert payload["sandbox"] == "disposable-git-clone"

    asyncio.run(run())
    assert requests[0]["tool"] == "run_tests"
    assert requests[0]["input"]["arguments"]["command"] == "pytest"


def test_tool_advertisements_follow_permission_mode(fake_home: Path, tmp_path: Path) -> None:
    seen_tools: list[list[dict]] = []

    def factory() -> FunctionModelClient:
        async def complete(**kwargs):
            seen_tools.append(list(kwargs["tools"]))
            return ModelResponse(content="done")
        return FunctionModelClient(complete)

    runtime = AgentRuntime(
        workspace=tmp_path, model_client=factory(),
        session_channel="ahp-session:/tool-modes", home=fake_home,
    )
    asyncio.run(runtime.run("check", permission_mode="read-only"))
    asyncio.run(runtime.run("check", permission_mode="workspace-write"))
    read_only = {x["function"]["name"] for x in seen_tools[0]}
    writable = {x["function"]["name"] for x in seen_tools[1]}
    assert {"apply_patch", "run_tests"}.isdisjoint(read_only)
    assert {"apply_patch", "run_tests"}.issubset(writable)


def test_read_only_tool_calls_run_concurrently(fake_home: Path, tmp_path: Path) -> None:
    started = asyncio.Event()
    released = asyncio.Event()

    async def blocked(_args):
        started.set()
        await asyncio.wait_for(released.wait(), timeout=2)
        return {"value": "blocked"}

    async def release(_args):
        await asyncio.wait_for(started.wait(), timeout=2)
        released.set()
        return {"value": "release"}

    model = _sequential_model([
        ModelResponse(tool_calls=(
            ToolCall("blocked", "search_files", {"query": "first"}),
            ToolCall("release", "list_dir", {"path": "."}),
        )),
        ModelResponse(content="both completed"),
    ])
    runtime = AgentRuntime(
        workspace=tmp_path, model_client=model,
        session_channel="ahp-session:/parallel-tools", home=fake_home,
    )
    runtime.tools.search_files = blocked
    runtime.tools.list_dir = release

    async def run() -> None:
        result = await runtime.run("parallel checks")
        assert result.tool_calls == 2

    asyncio.run(run())
    assert released.is_set()


def test_project_session_tools_are_workspace_scoped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime, timezone
    import harness_config_manager.agent as agent_module
    from harness_config_manager.sessions import SessionInfo

    item = SessionInfo(
        tool="claude", session_id="abc", path=tmp_path / "vendor.jsonl",
        project=tmp_path, title="Prior work",
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc), message_count=4,
    )
    monkeypatch.setattr(agent_module, "scan_sessions", lambda project: [item])

    def fake_context(ref, project=None, tail=40):
        assert ref == "claude:abc"
        assert project == tmp_path
        assert tail == 12
        return "REDACTED HANDOFF"

    monkeypatch.setattr(agent_module, "build_context", fake_context)
    tools = WorkspaceTools(tmp_path)
    listed = asyncio.run(tools.list_project_sessions({}))
    assert listed["items"][0]["ref"] == "claude:abc"
    read = asyncio.run(tools.read_project_session({"ref": "claude:abc", "tail": 12}))
    assert read["content"] == "REDACTED HANDOFF"
    with pytest.raises(PermissionError):
        asyncio.run(tools.read_project_session({"ref": "codex:other"}))


def test_history_compaction_keeps_complete_turns_and_adds_summary() -> None:
    from harness_config_manager.agent import compact_history

    history: list[dict] = []
    for i in range(20):
        history.append({"role": "user", "content": f"task {i}"})
        history.append({"role": "assistant", "content": f"answer {i}"})
    compacted, dropped = compact_history(history)
    assert dropped == 8
    assert len(compacted) == 25
    assert compacted[0]["role"] == "system"
    assert "8 earlier conversation turn(s)" in compacted[0]["content"]
    assert compacted[1] == {"role": "user", "content": "task 8"}
    assert compacted[-1] == {"role": "assistant", "content": "answer 19"}


def test_history_compaction_preserves_small_history() -> None:
    from harness_config_manager.agent import compact_history

    history = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "tool_calls": [{"id": "x", "type": "function", "function": {}}]},
        {"role": "tool", "tool_call_id": "x", "name": "read_file", "content": "{}"},
        {"role": "assistant", "content": "done"},
    ]
    compacted, dropped = compact_history(history)
    assert compacted == history
    assert dropped == 0


def test_update_plan_emits_structured_event(fake_home: Path, tmp_path: Path) -> None:
    events: list[dict] = []
    model = _sequential_model([
        ModelResponse(tool_calls=(ToolCall("plan-1", "update_plan", {
            "steps": [
                {"title": "Inspect workspace", "status": "done"},
                {"title": "Apply approved patch", "status": "pending", "detail": "Needs user approval"},
            ],
            "note": "Ready for review",
        }),)),
        ModelResponse(content="plan ready"),
    ])
    runtime = AgentRuntime(
        workspace=tmp_path, model_client=model,
        session_channel="ahp-session:/plan-test", home=fake_home,
    )

    async def run() -> None:
        async def emit(event):
            events.append(event)
        result = await runtime.run("make a plan", emit=emit)
        tool = [m for m in result.messages if m.get("role") == "tool"][0]
        payload = json.loads(tool["content"])
        assert payload["updated"] is True
        assert payload["steps"][1]["status"] == "pending"

    asyncio.run(run())
    plan_event = next(event for event in events if event["type"] == "plan_updated")
    assert plan_event["note"] == "Ready for review"
    assert plan_event["steps"][0]["status"] == "done"


def test_workspace_context_is_automatically_bounded_and_redacted(fake_home: Path, tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "# Demo\\n\\napi_key=super-secret-value-123\\n", encoding="utf-8"
    )
    (tmp_path / "pyproject.toml").write_text('[project]\\nname = "demo"\\n', encoding="utf-8")
    seen_messages: list[list[dict]] = []

    def factory() -> FunctionModelClient:
        async def complete(**kwargs):
            seen_messages.append(list(kwargs["messages"]))
            return ModelResponse(content="ready")
        return FunctionModelClient(complete)

    runtime = AgentRuntime(
        workspace=tmp_path, model_client=factory(),
        session_channel="ahp-session:/workspace-context", home=fake_home,
    )
    asyncio.run(runtime.run("summarize project"))
    context = [
        message for message in seen_messages[0]
        if message.get("name") == "halter_workspace_context"
    ]
    assert len(context) == 1
    payload = json.loads(context[0]["content"])
    assert 'name = "demo"' in payload["manifests"]["pyproject.toml"]
    assert "super-secret-value-123" not in context[0]["content"]
    assert "<REDACTED>" in context[0]["content"]
    assert seen_messages[0][-1] == {"role": "user", "content": "summarize project"}

    audit_files = list((fake_home / ".config/halter/agent-audit").rglob("audit.jsonl"))
    records = [json.loads(line) for line in audit_files[0].read_text(encoding="utf-8").splitlines()]
    assert any(record["kind"] == "context.workspace" for record in records)
