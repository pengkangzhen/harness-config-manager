"""hooks 方言写入器：canonical -> 各工具配置（读-改-写，保留其余键；原子替换）。

归属跟踪：halter 写入的条目在其条目对象上携带 "halter": "<id>" 标记键；
同步时仅 upsert 自家条目，第三方注入的无标记条目一律不触碰。
claude/zcode 为三层结构（事件 -> [matcher 层条目 -> hooks 数组]，超时分别为秒/毫秒），
cursor 为扁平两层（事件 -> [{command,...}]，必须保留根上 version:1）。
"""

from __future__ import annotations

from .hooks_manifest import EVENT_TO_CURSOR, HOOK_CAPABLE, HookSpec
from .mcp_write import _atomic_write_json, _load_json_dict
from .registry import expand


def _event_for_cursor(event: str) -> str | None:
    """canonical -> cursor 事件名；cursor 不支持的事件返回 None。"""
    if event in EVENT_TO_CURSOR:
        return EVENT_TO_CURSOR[event]
    return event if event[:1].islower() else None   # 小驼峰名为 cursor 独有


def _event_for_claude_family(event: str) -> bool:
    """canonical 事件是否被 claude/zcode 支持（cursor 独有的小驼峰名不支持）。"""
    return event in EVENT_TO_CURSOR or event[:1].isupper()


# ---------------------------------------------------------------------------
# 各方言条目形态


def _strip_managed(entries: list, id_: str) -> list:
    return [e for e in entries if not (isinstance(e, dict) and e.get("halter") == id_)]


def _inner_common(spec: HookSpec) -> dict:
    inner: dict = {"type": spec.type}
    if spec.type == "prompt":
        inner["prompt"] = spec.extra.get("prompt", "")
        if spec.extra.get("model"):
            inner["model"] = spec.extra["model"]
    else:
        inner["command"] = spec.command
    return inner


def write_claude(specs: list[HookSpec]) -> list[str]:
    """~/.claude/settings.json 顶层 hooks 读-改-写（不动 permissions.hooks）。"""
    path = expand(".claude/settings.json")
    data = _load_json_dict(path)
    if not data:
        return ["claude: 跳过（settings.json 不存在或不可解析）"]
    hooks = data.setdefault("hooks", {})
    for s in specs:
        for ev in s.events:
            entries = hooks.setdefault(ev, [])
            entries[:] = _strip_managed([e for e in entries if isinstance(e, dict)], s.id)
            entry: dict = {"hooks": [_inner_common(s)]}
            if s.matcher is not None:
                entry["matcher"] = s.matcher
            if s.timeout is not None:
                entry["hooks"][0]["timeout"] = max(1, int(round(s.timeout)))
            for k, v in s.extra.items():
                entry.setdefault(k, v)
            entry["halter"] = s.id
            entries.append(entry)
    _atomic_write_json(path, data)
    ids = ", ".join(s.id for s in specs)
    return [f"claude: 写入 {ids} → ~/.claude/settings.json"]


def write_zcode(specs: list[HookSpec]) -> list[str]:
    """~/.zcode/cli/config.json 的 hooks.events 读-改-写（不碰 hooks.enabled 与其他键）。"""
    path = expand(".zcode/cli/config.json")
    data = _load_json_dict(path)
    if not data:
        return ["zcode: 跳过（cli/config.json 不存在或不可解析）"]
    events = data.setdefault("hooks", {}).setdefault("events", {})
    for s in specs:
        for ev in s.events:
            entries = events.setdefault(ev, [])
            entries[:] = _strip_managed([e for e in entries if isinstance(e, dict)], s.id)
            inner = _inner_common(s)
            if s.timeout is not None:
                inner["timeoutMs"] = max(1000, int(round(s.timeout * 1000)))
            if s.description:
                inner["statusMessage"] = s.description
            entry: dict = {"hooks": [inner]}
            if s.matcher is not None:
                entry["matcher"] = s.matcher
            for k, v in s.extra.items():
                entry.setdefault(k, v)
            entry["halter"] = s.id
            entries.append(entry)
    _atomic_write_json(path, data)
    ids = ", ".join(s.id for s in specs)
    return [f"zcode: 写入 {ids} → ~/.zcode/cli/config.json"]


def write_cursor(specs: list[HookSpec]) -> list[str]:
    """~/.cursor/hooks.json 扁平两层读-改-写（根上保留 version:1）。"""
    path = expand(".cursor/hooks.json")
    data = _load_json_dict(path)
    data.setdefault("version", 1)
    hooks = data.setdefault("hooks", {})
    for s in specs:
        for ev in s.events:
            cev = _event_for_cursor(ev)
            if cev is None:
                continue
            entries = hooks.setdefault(cev, [])
            entries[:] = _strip_managed([e for e in entries if isinstance(e, dict)], s.id)
            entry = _inner_common(s)
            if s.matcher is not None:
                entry["matcher"] = s.matcher
            if s.timeout is not None:
                entry["timeout"] = s.timeout
            for k, v in s.extra.items():
                entry.setdefault(k, v)
            entry["halter"] = s.id
            entries.append(entry)
    _atomic_write_json(path, data)
    ids = ", ".join(s.id for s in specs)
    return [f"cursor: 写入 {ids} → ~/.cursor/hooks.json"]


HOOK_WRITERS = {
    "claude": write_claude,
    "zcode": write_zcode,
    "cursor": write_cursor,
}


# ---------------------------------------------------------------------------
# 冲突比较：工具侧 halter 打标条目 <-> manifest 期望


def _managed_form(tool: str) -> dict[str, tuple]:
    """工具侧 halter 标记条目 -> {id: (events有序组, matcher, command/type载体, timeout)}。"""
    found: dict[str, dict] = {}

    def _put(id_, ev, matcher, carrier, timeout):
        rec = found.setdefault(id_, {"events": [], "pairs": []})
        rec["events"].append(ev)
        rec["pairs"].append((matcher, carrier, timeout))

    if tool == "claude":
        raw = _load_json_dict(expand(".claude/settings.json")).get("hooks") or {}
        for ev, entries in raw.items():
            if not isinstance(entries, list):
                continue
            for e in entries:
                if isinstance(e, dict) and isinstance(e.get("halter"), str):
                    for inner in e.get("hooks") or []:
                        t = inner.get("timeout")
                        _put(e["halter"], ev, e.get("matcher"),
                             (inner.get("type", "command"), inner.get("command")),
                             float(t) if t is not None else None)
    elif tool == "zcode":
        hk = _load_json_dict(expand(".zcode/cli/config.json")).get("hooks") or {}
        raw = hk.get("events") or {}
        for ev, entries in raw.items():
            if not isinstance(entries, list):
                continue
            for e in entries:
                if isinstance(e, dict) and isinstance(e.get("halter"), str):
                    for inner in e.get("hooks") or []:
                        t = inner.get("timeoutMs")
                        sec = t / 1000 if t is not None else inner.get("timeout")
                        _put(e["halter"], ev, e.get("matcher"),
                             (inner.get("type", "command"), inner.get("command")),
                             float(sec) if sec is not None else None)
    elif tool == "cursor":
        raw = _load_json_dict(expand(".cursor/hooks.json")).get("hooks") or {}
        rev = {v: k for k, v in EVENT_TO_CURSOR.items()}
        for ev, entries in raw.items():
            if not isinstance(entries, list):
                continue
            canonical = rev.get(ev, ev)
            for e in entries:
                if isinstance(e, dict) and isinstance(e.get("halter"), str):
                    t = e.get("timeout")
                    _put(e["halter"], canonical, e.get("matcher"),
                         (e.get("type", "command"), e.get("command")),
                         float(t) if t is not None else None)

    forms: dict[str, tuple] = {}
    for id_, rec in found.items():
        pairs = tuple(sorted(rec["pairs"]))
        forms[id_] = (tuple(sorted(rec["events"])), pairs)
    return forms


def _expected_form(spec: HookSpec) -> tuple:
    """与 _managed_form 同构：(events, sorted[(matcher, (type, command|None), timeout)])。
    每个 event 下写一条相同 inner，故 pairs 长度 = events 数。"""
    timeout = round(spec.timeout, 3) if spec.timeout is not None else None
    pair = (spec.matcher, (spec.type, spec.command), timeout)
    return (tuple(sorted(spec.events)), tuple(sorted(pair for _ in spec.events)))


def sync_hooks(specs: list[HookSpec], installed_tools: list[str],
               apply: bool, prefer: str) -> list[str]:
    """分发 canonical 清单到各目标工具。返回输出行。"""
    lines: list[str] = []
    for tool in HOOK_CAPABLE:
        if tool not in installed_tools:
            continue
        current = _managed_form(tool)
        to_write: list[HookSpec] = []
        for s in specs:
            if s.targets is not None and tool not in s.targets:
                continue
            if s.type == "prompt" and tool != "cursor":
                lines.append(f"[yellow]跳过 {tool}:{s.id}（prompt 型 hook 仅 Cursor 支持）[/yellow]")
                continue
            ok: list[str] = []
            for ev in s.events:
                good = (_event_for_claude_family(ev) if tool != "cursor"
                        else _event_for_cursor(ev) is not None)
                if good:
                    ok.append(ev)
                else:
                    lines.append(f"[yellow]跳过 {tool}:{s.id} @ {ev}"
                                 f"（该工具无此事件）[/yellow]")
            if not ok:
                continue
            scoped = HookSpec(**{**s.__dict__, "events": ok})
            existing = current.get(s.id)
            if existing is not None:
                if existing == _expected_form(scoped):
                    continue          # 已是期望状态
                if prefer != "library":
                    lines.append(f"[yellow]conflict {tool}:{s.id} 已存在且定义不同"
                                 f" → 跳过（--prefer library 覆盖）[/yellow]")
                    continue
            to_write.append(scoped)
        if to_write:
            if apply:
                lines.extend(HOOK_WRITERS[tool](to_write))
            else:
                lines.append(f"[plan] {tool}: 写入 {', '.join(x.id for x in to_write)}")
    return lines
