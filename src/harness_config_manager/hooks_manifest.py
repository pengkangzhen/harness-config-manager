"""hooks canonical 清单：~/.config/halter/hooks.toml（期望状态的事实源）。

每个 [[hook]] 是一次注册：一个命令挂到一（或多）个事件、可选 matcher。
canonical 事件名 = Claude/ZCode 大写驼峰；cursor 小驼峰通过 EVENT_TO_CURSOR 互转，
仅 cursor 存在的事件名（beforeShellExecution 等）原样保留。

adopt 策略：全量收集源工具的条目（含 _otty 等第三方注入），排除项走
config.toml 的 exclude_hooks 黑名单。钩子无密钥字段，不涉及 secrets 展开。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import tomlkit

from .model import HookInfo, redact
from .registry import expand

HOOK_MANIFEST = lambda: expand(".config/halter/hooks.toml")  # noqa: E731

HOOK_CAPABLE = ("claude", "zcode", "cursor")   # 分发遍历顺序即此序

# canonical -> cursor 事件名；不在表内且非小写原名者视为 claude/zcode 独有事件
EVENT_TO_CURSOR = {
    "SessionStart": "sessionStart",
    "SessionEnd": "sessionEnd",
    "PreToolUse": "preToolUse",
    "PostToolUse": "postToolUse",
    "PostToolUseFailure": "postToolUseFailure",
    "SubagentStart": "subagentStart",
    "SubagentStop": "subagentStop",
    "PreCompact": "preCompact",
    "Stop": "stop",
    "UserPromptSubmit": "beforeSubmitPrompt",
}


@dataclass
class HookSpec:
    id: str
    events: list[str]
    type: str = "command"              # command / prompt（prompt 仅 cursor 支持）
    matcher: str | None = None
    command: str | None = None         # prompt 型为 None，提示词在 extra["prompt"]
    timeout: float | None = None       # 秒；zcode 写入时 ×1000
    description: str | None = None     # zcode statusMessage
    extra: dict = field(default_factory=dict)      # 方言特有字段原样保留
    targets: list[str] | None = None   # None = 所有已装目标工具


_SHELL_KEYWORDS = {"if", "then", "else", "fi", "for", "while", "case", "esac", "do", "done"}
_SCRIPT_PATH_RE = re.compile(r"[\w./$~-]{2,}\.(?:sh|py|mjs|cmd|ps1|bash|zsh)\b")


def gen_hook_id(info: HookInfo) -> str:
    """从盘点条目生成稳定 id：归属标记 > 脚本路径 stem > 命令首段 > 哈希。"""
    for k in ("halter", "_otty", "_orca"):
        v = info.extra.get(k)
        if isinstance(v, str) and v:
            return v
        if v is True:
            return k.lstrip("_")
    cmd = info.command or ""
    m = _SCRIPT_PATH_RE.search(cmd)
    if m:
        return Path(m.group(0)).stem
    token = cmd.split()[0].strip("\"'") if cmd.split() else ""
    name = Path(token).name if "/" in token else token
    if not name or name in _SHELL_KEYWORDS:
        import hashlib

        return "hook-" + hashlib.sha1((info.event + cmd).encode()).hexdigest()[:8]
    return name


def spec_from_info(info: HookInfo, uid: str) -> HookSpec:
    extra = {k: v for k, v in info.extra.items()}
    desc = extra.pop("statusMessage", None)
    return HookSpec(
        id=uid,
        events=[info.event],
        type=info.type,
        matcher=info.matcher,
        command=info.command,
        timeout=info.timeout,
        description=desc if isinstance(desc, str) else None,
        extra=extra,
    )


def load_manifest(path: Path | None = None) -> list[HookSpec]:
    path = path or HOOK_MANIFEST()
    if not path.exists():
        return []
    doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    specs: list[HookSpec] = []
    for tbl in doc.get("hook", []):
        specs.append(HookSpec(
            id=tbl["id"],
            events=list(tbl.get("events", [])),
            type=tbl.get("type", "command"),
            matcher=tbl.get("matcher"),
            command=tbl.get("command"),
            timeout=tbl.get("timeout"),
            description=tbl.get("description"),
            extra=dict(tbl.get("extra", {})),
            targets=list(tbl["targets"]) if "targets" in tbl else None,
        ))
    return specs


def save_manifest(specs: list[HookSpec], path: Path | None = None) -> None:
    path = path or HOOK_MANIFEST()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = tomlkit.document()
    aot = tomlkit.aot()
    for s in specs:
        tbl = tomlkit.table()
        tbl["id"] = s.id
        tbl["events"] = s.events
        if s.type != "command":
            tbl["type"] = s.type
        if s.matcher is not None:
            tbl["matcher"] = s.matcher
        if s.command is not None:
            tbl["command"] = s.command
        if s.timeout is not None:
            tbl["timeout"] = s.timeout
        if s.description:
            tbl["description"] = s.description
        if s.targets is not None:
            tbl["targets"] = s.targets
        if s.extra:
            e_tbl = tomlkit.table()
            for k, v in redact(s.extra).items():
                e_tbl[k] = v
            tbl["extra"] = e_tbl
        aot.append(tbl)
    doc["hook"] = aot
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")


def auto_hook_source(installed_tools: list[str]) -> str:
    """自动选 hooks 收集源：条目最多的已装目标工具；全空则退回 claude。"""
    from .hooks import HOOK_READERS

    best, best_n = "claude", -1
    for tool in installed_tools:
        reader = HOOK_READERS.get(tool)
        if reader is None:
            continue
        infos: list[HookInfo] = []
        notes: list[str] = []
        reader(infos, notes)
        if len(infos) > best_n:
            best, best_n = tool, len(infos)
    return best


def adopt_hooks(source: str, apply: bool) -> list[str]:
    """从源工具全量收集 hook 条目进 manifest（第三方注入的条目一并收编）。"""
    from .hooks import HOOK_READERS

    infos: list[HookInfo] = []
    notes: list[str] = []
    reader = HOOK_READERS.get(source)
    if reader is None:
        return [f"{source}: 不支持 hooks 层收集"]
    reader(infos, notes)
    if not infos:
        return [f"{source}: 未发现 hooks 配置"]

    existing = {s.id: s for s in load_manifest()}
    lines: list[str] = [f"[dim]{n}[/dim]" for n in notes]
    adopted = 0
    for info in infos:
        base = gen_hook_id(info)
        uid, n = base, 2
        while uid in existing:
            uid = f"{base}-{n}"
            n += 1
        if base in {s.id for s in existing.values()} and len(info.command or "") and any(
                s.command == info.command and s.events == [info.event]
                for s in existing.values()):
            lines.append(f"保留现有 manifest 定义 {base}")
            continue
        spec = spec_from_info(info, uid)
        existing[uid] = spec
        adopted += 1
        lines.append(f"adopt {uid} ({info.event}{', ' + info.matcher if info.matcher else ''})")

    if apply:
        save_manifest(list(existing.values()))
        if adopted:
            lines.append(f"共入库 {adopted} 条")
    elif adopted:
        lines.insert(1, "[dim]dry-run 未写入 hooks.toml（--apply 生效）[/dim]")
    return lines
