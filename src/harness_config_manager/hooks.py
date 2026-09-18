"""hooks 层：各工具配置方言的读取器（写入器见 hooks_write）。

方言一览（macOS 实测，2026-08）：
  claude   ~/.claude/settings.json   -> hooks{Event: [{matcher, halter?, hooks:[{type,command,timeout}]}]}
             三层结构；matcher 层容忍任意自定义键（_otty 等第三方标记先例）
  zcode    ~/.zcode/cli/config.json  -> hooks.events{Event: [...]}（同 claude 三层；
             另有 hooks.enabled 全局开关与 timeoutMs 毫秒超时、statusMessage 描述字段）
  cursor   ~/.cursor/hooks.json      -> {version:1, hooks:{event: [{command, matcher?, timeout?}]}}
             扁平两层；事件名小驼峰；另有 failClosed / prompt 型 hook（v1 原样保留不建模）

canonical 事件名采用 Claude/ZCode 的大写驼峰；cursor 小驼峰除显式映射外按大小写互转。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .model import HookInfo, redact
from .registry import expand

HookReader = Callable[[list[HookInfo], list[str]], None]
"""方言读取器：向 out 追加 hook 条目，向 notes 追加扫描说明。"""


# cursor 事件名（小驼峰）-> canonical（大写驼峰）；缺省规则为其余 cursor-only 名原样保留
CURSOR_EVENT_TO_CANONICAL = {
    "sessionStart": "SessionStart",
    "sessionEnd": "SessionEnd",
    "preToolUse": "PreToolUse",
    "postToolUse": "PostToolUse",
    "postToolUseFailure": "PostToolUseFailure",
    "subagentStart": "SubagentStart",
    "subagentStop": "SubagentStop",
    "preCompact": "PreCompact",
    "stop": "Stop",
    "beforeSubmitPrompt": "UserPromptSubmit",
}


def _load_json(path: Path) -> dict | None:
    try:
        with path.open("rb") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _named(info: HookInfo, counts: dict[str, int]) -> str:
    """条目展示名 = canonical id 规则（gen_hook_id），同 tool 内重名追加 #N 序号。"""
    from .hooks_manifest import gen_hook_id

    base = gen_hook_id(info)
    counts[base] = counts.get(base, 0) + 1
    return base if counts[base] == 1 else f"{base}#{counts[base]}"


def _expand_nested(events_map: dict, notes: list[str], source: str,
                   enabled_note: bool = False) -> list[HookInfo]:
    """展开 claude / zcode 三层结构：事件 -> [matcher 层条目] -> [hooks 数组]。"""
    infos: list[HookInfo] = []
    counts: dict[str, int] = {}
    for event, entries in events_map.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            inners = [c for c in (entry.get("hooks") or []) if isinstance(c, dict)]
            extra = {k: v for k, v in redact(entry).items()
                     if k not in ("matcher", "hooks")}
            timeout_s = None
            for inner in inners:
                if not isinstance(inner, dict):
                    continue
                t = inner.get("timeoutMs")  # zcode 毫秒
                if t is None:
                    t = inner.get("timeout")
                timeout_s = float(t) / 1000 if source == "zcode" and t is not None else (
                    float(t) if t is not None else None)
                info = HookInfo(
                    event=event,
                    label="?",
                    type=str(inner.get("type", "command")),
                    matcher=entry.get("matcher"),
                    command=inner.get("command"),
                    timeout=timeout_s,
                    extra={**extra, **{k: v for k, v in redact(inner).items()
                                       if k not in ("type", "command", "timeout",
                                                    "timeoutMs")}},
                )
                info.label = _named(info, counts)
                infos.append(info)
            if not inners:
                info = HookInfo(event=event, label="?", matcher=entry.get("matcher"),
                                extra=extra)
                info.label = _named(info, counts)
                infos.append(info)
    if enabled_note and infos:
        notes.append("hooks 全局开关由 ~/.zcode/cli/config.json hooks.enabled 控制，halter 不代管")
    return infos


def read_claude(out: list[HookInfo], notes: list[str]) -> None:
    path = expand(".claude/settings.json")
    data = _load_json(path)
    if data is None:
        if path.exists():
            notes.append("hooks 配置解析失败: ~/.claude/settings.json")
        return
    raw = data.get("hooks") or {}
    if isinstance(raw.get("PermissionRequest"), dict):  # permissions.hooks 形态非本层目标
        notes.append("~/.claude/settings.json permissions.hooks 未纳入盘点（权限挂钩非命令钩子）")
    out.extend(_expand_nested({k: v for k, v in raw.items() if isinstance(v, list)},
                              notes, "claude"))


def read_zcode(out: list[HookInfo], notes: list[str]) -> None:
    path = expand(".zcode/cli/config.json")
    data = _load_json(path)
    if data is None:
        if path.exists():
            notes.append("hooks 配置解析失败: ~/.zcode/cli/config.json")
        return
    hk = data.get("hooks") or {}
    raw = hk.get("events") or {}
    out.extend(_expand_nested(
        {k: v for k, v in raw.items() if isinstance(v, list)},
        notes, "zcode", enabled_note=bool(hk.get("enabled"))))


def read_cursor(out: list[HookInfo], notes: list[str]) -> None:
    path = expand(".cursor/hooks.json")
    data = _load_json(path)
    if data is None:
        if path.exists():
            notes.append("hooks 配置解析失败: ~/.cursor/hooks.json")
        return
    raw = data.get("hooks") or {}
    counts: dict[str, int] = {}
    for event, entries in raw.items():
        canonical = CURSOR_EVENT_TO_CANONICAL.get(event, event)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            info = HookInfo(
                event=canonical,
                label="?",
                type=str(entry.get("type", "command")),
                matcher=entry.get("matcher") if isinstance(entry.get("matcher"), str) else None,
                command=entry.get("command"),
                timeout=float(to) if (to := entry.get("timeout")) is not None else None,
                extra=redact({k: v for k, v in entry.items()
                              if k not in ("command", "timeout", "matcher", "type")}),
            )
            info.label = _named(info, counts)
            out.append(info)


HOOK_READERS: dict[str, HookReader] = {
    "claude": read_claude,
    "zcode": read_zcode,
    "cursor": read_cursor,
}
