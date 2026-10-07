"""跨机器条目传输：encode（本机条目 -> 传输字节）/ apply（传输字节 -> 本机库/清单）。

传输格式由层约定：skills / agents 为 tar 流（arcname = 条目名），
mcp / hooks 为 TOML 文档（[[server]] / [[hook]] 与清单同构，可选 [secrets] 平板），
statusline 为 tar 流（fragment.json + 脚本本体；command 已是 {script} 占位，
天然跨机器可移植）。
幂等语义：同名同内容 -> ok；同名不同内容 -> conflict（--prefer replace 备份后覆盖）。
"""

from __future__ import annotations

import io
import json
import re
import shutil
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

import tomlkit

from .config import load_config
from .registry import expand

LAYERS = ("skills", "agents", "mcp", "hooks", "statusline")

_PLACEHOLDER_RE = re.compile(r"\$\{([^}]+)\}")


def _backup_file(src: Path, layer: str) -> Path:
    """清单/库条目替换前的备份目标（backups/<时间戳>/<layer>/）。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    d = expand(f".config/halter/backups/{stamp}/{layer}")
    d.mkdir(parents=True, exist_ok=True)
    return d / src.name


def _referenced_secret_vars(spec) -> set[str]:
    """收集一个 McpSpec 里所有 ${VAR} 占位符变量名（env / headers / url）。"""
    vars_: set[str] = set()
    for value in list(spec.env.values()) + list(spec.headers.values()):
        if isinstance(value, str):
            vars_.update(_PLACEHOLDER_RE.findall(value))
    if spec.url:
        vars_.update(_PLACEHOLDER_RE.findall(spec.url))
    return vars_


# ---------------------------------------------------------------------------
# encode：本机条目 -> (payload, warnings)


def encode_entry(layer: str, name: str, with_secrets: bool = False) -> tuple[bytes, list[str]]:
    """把本机一个条目编码成传输字节。条目不存在 -> LookupError；层非法 -> ValueError。"""
    if layer == "skills":
        from .skills import resolve_library

        src = resolve_library(load_config()) / name
        if not (src / "SKILL.md").is_file():
            raise LookupError(f"本机 skills 库没有 {name}")
        return _tar(src), []

    if layer == "agents":
        from .agents import resolve_agents_library

        src = resolve_agents_library(load_config()) / f"{name}.md"
        if not src.is_file():
            raise LookupError(f"本机 subagents 库没有 {name}")
        return _tar(src), []

    if layer == "mcp":
        from .mcp_manifest import _load_secrets, load_manifest, spec_to_table

        match = [s for s in load_manifest() if s.name == name]
        if not match:
            raise LookupError(f"本机 MCP 清单没有 server {name}")
        doc = tomlkit.document()
        aot = tomlkit.aot()
        aot.append(spec_to_table(match[0]))
        doc["server"] = aot
        if with_secrets:
            secrets = _load_secrets()
            picked = {v: secrets[v] for v in sorted(_referenced_secret_vars(match[0])) if v in secrets}
            if picked:
                sec_tbl = tomlkit.table()
                for k, v in picked.items():
                    sec_tbl[k] = v
                doc["secrets"] = sec_tbl
        return tomlkit.dumps(doc).encode("utf-8"), []

    if layer == "hooks":
        from .hooks_manifest import load_manifest, spec_to_table

        specs = [s for s in load_manifest() if s.id == name or s.id.startswith(name + "-")]
        if not specs:
            raise LookupError(f"本机 hooks 清单没有 id 基名 {name} 的条目")
        doc = tomlkit.document()
        aot = tomlkit.aot()
        warnings: list[str] = []
        for s in specs:
            aot.append(spec_to_table(s))
            if s.command and ("/home/" in s.command or s.command.startswith("~")):
                warnings.append(f"hook {s.id} 的命令含本机路径，目标机器可能不可用：{s.command}")
        doc["hook"] = aot
        return tomlkit.dumps(doc).encode("utf-8"), warnings

    if layer == "statusline":
        from .statusline import library_script, load_manifest, resolve_statusline_library

        lib = resolve_statusline_library(load_config())
        fragment = load_manifest(lib).get(name)
        if fragment is None:
            raise LookupError(f"本机 statusline 库没有 {name} 的片段")
        warnings = []
        command = (fragment.get("statusLine") or {}).get("command")
        if isinstance(command, str) and ("/home/" in command or command.startswith("~")):
            warnings.append(f"statusline {name} 的 command 含本机路径（应为 {{script}} 占位），"
                            f"目标机器可能不可用：{command}")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            _tar_add_bytes(tf, "fragment.json",
                           json.dumps({"tool": name, "fragment": fragment},
                                      ensure_ascii=False, indent=2).encode("utf-8"))
            script = library_script(lib, name, fragment)
            if script is not None and script.is_file():
                _tar_add_bytes(tf, script.name, script.read_bytes(), executable=True)
        return buf.getvalue(), warnings

    raise ValueError(f"未知层 {layer}（可选 {', '.join(LAYERS)}）")


def _tar_add_bytes(tf: tarfile.TarFile, arcname: str, data: bytes,
                   executable: bool = False) -> None:
    info = tarfile.TarInfo(arcname)
    info.size = len(data)
    info.mode = 0o755 if executable else 0o644
    import time

    info.mtime = int(time.time())
    tf.addfile(info, io.BytesIO(data))


def _tar(src: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        tf.add(src, arcname=src.name)
    return buf.getvalue()


def _untar(payload: bytes, dest: Path) -> None:
    """解传输流到 dest（filter=data 拒绝路径穿越与设备文件）。"""
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r") as tf:
        tf.extractall(dest, filter="data")


# ---------------------------------------------------------------------------
# apply：传输字节 -> 本机库/清单（幂等 / 冲突 / 备份）


def apply_entry(layer: str, name: str, payload: bytes, prefer: str = "skip",
                with_secrets: bool = False, apply: bool = False) -> list[str]:
    cfg = load_config()
    if layer == "skills":
        return _apply_skill(name, payload, prefer, apply, cfg)
    if layer == "agents":
        return _apply_agent(name, payload, prefer, apply, cfg)
    if layer == "mcp":
        return _apply_mcp(name, payload, prefer, with_secrets, apply)
    if layer == "hooks":
        return _apply_hooks(name, payload, prefer, apply)
    if layer == "statusline":
        return _apply_statusline(name, payload, prefer, apply, cfg)
    raise ValueError(f"未知层 {layer}（可选 {', '.join(LAYERS)}）")


def _apply_skill(name: str, payload: bytes, prefer: str, apply: bool, cfg) -> list[str]:
    from .skills import backup_dir, dirs_equal, resolve_library

    lib = resolve_library(cfg, create=apply)
    dest = lib / name
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        _untar(payload, staging)
        src = staging / name
        if not (src / "SKILL.md").is_file():
            raise ValueError("传输内容不是合法 skill（缺 SKILL.md）")
        if dest.exists():
            if dirs_equal(src, dest):
                return [f"ok     {name}（内容一致）"]
            if prefer != "replace":
                return [f"conflict {name} → 跳过（--prefer replace 可覆盖）"]
            if apply:
                bak = backup_dir() / name
                shutil.move(str(dest), str(bak))
                shutil.copytree(src, dest)
                return [f"replace {name}（旧条目已备份）"]
            return [f"replace {name}（备份库中旧条目）"]
        if apply:
            shutil.copytree(src, dest)
        return [f"add    {name} -> {dest}"]


def _apply_agent(name: str, payload: bytes, prefer: str, apply: bool, cfg) -> list[str]:
    from .agents import backup_dir, resolve_agents_library

    lib = resolve_agents_library(cfg, create=apply)
    dest = lib / f"{name}.md"
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        _untar(payload, staging)
        src = staging / f"{name}.md"
        if not src.is_file():
            raise ValueError(f"传输内容没有 {name}.md")
        if dest.is_file():
            if src.read_bytes() == dest.read_bytes():
                return [f"ok     {name}（内容一致）"]
            if prefer != "replace":
                return [f"conflict {name} → 跳过（--prefer replace 可覆盖）"]
            if apply:
                bak = backup_dir() / dest.name
                shutil.move(str(dest), str(bak))
                shutil.copy2(src, dest)
                return [f"replace {name}（旧文件已备份）"]
            return [f"replace {name}（备份库中旧文件）"]
        if apply:
            shutil.copy2(src, dest)
        return [f"add    {name} -> {dest}"]


def _apply_mcp(name: str, payload: bytes, prefer: str, with_secrets: bool,
               apply: bool) -> list[str]:
    from .mcp_manifest import (_load_secrets, MCP_MANIFEST, load_manifest,
                               save_manifest, save_secrets, spec_from_table)

    doc = tomlkit.parse(payload.decode("utf-8"))
    tables = doc.get("server", [])
    if not tables:
        raise ValueError("传输内容没有 [[server]]")
    new = spec_from_table(tables[0])
    if new.name != name:
        raise ValueError(f"传输内容条目名 {new.name} 与 --name {name} 不符")

    existing = load_manifest()
    old = next((s for s in existing if s.name == name), None)
    lines: list[str] = []
    if old is not None and old == new:
        lines.append(f"ok     mcp:{name}（定义一致）")
    elif old is not None and prefer != "replace":
        return [f"conflict mcp:{name} → 跳过（--prefer replace 可覆盖）"]
    else:
        verb = "replace" if old is not None else "add"
        if apply:
            if old is not None:
                shutil.copy2(MCP_MANIFEST(), _backup_file(MCP_MANIFEST(), "mcp"))
            specs = ([new if s.name == name else s for s in existing]
                     if old is not None else existing + [new])
            save_manifest(specs)
            lines.append(f"{verb}    mcp:{name}" + ("（旧清单已备份）" if old else ""))
        else:
            lines.append(f"{verb}    mcp:{name}")

    if with_secrets:
        got = {k: str(v) for k, v in dict(doc.get("secrets", {})).items()}
        if got:
            lines.append(f"secrets mcp:{name} 合并 {len(got)} 项入 secrets.toml")
            if apply:
                save_secrets(_load_secrets() | got)
    return lines


def _apply_hooks(name: str, payload: bytes, prefer: str, apply: bool) -> list[str]:
    from .hooks_manifest import HOOK_MANIFEST, load_manifest, save_manifest, spec_from_table

    doc = tomlkit.parse(payload.decode("utf-8"))
    tables = doc.get("hook", [])
    if not tables:
        raise ValueError("传输内容没有 [[hook]]")
    incoming = [spec_from_table(t) for t in tables]
    for s in incoming:
        if s.id != name and not s.id.startswith(name + "-"):
            raise ValueError(f"传输内容 id {s.id} 与 --name 基名 {name} 不符")

    by_id = {s.id: s for s in load_manifest()}
    lines: list[str] = []
    changed = False
    for new in incoming:
        old = by_id.get(new.id)
        if old is not None and old == new:
            lines.append(f"ok     hook:{new.id}（定义一致）")
            continue
        if old is not None and prefer != "replace":
            lines.append(f"conflict hook:{new.id} → 跳过（--prefer replace 可覆盖）")
            continue
        verb = "replace" if old is not None else "add"
        if apply and old is not None and not changed:
            shutil.copy2(HOOK_MANIFEST(), _backup_file(HOOK_MANIFEST(), "hooks"))
        by_id[new.id] = new
        changed = True
        lines.append(f"{verb}    hook:{new.id}")
    if apply and changed:
        save_manifest(list(by_id.values()))
    return lines


def _apply_statusline(name: str, payload: bytes, prefer: str, apply: bool, cfg) -> list[str]:
    """statusline 片段入库：manifest.json 合并写入该工具片段 + 脚本落位。

    其它工具的片段与脚本永不触碰；manifest 替换前整体备份。
    """
    from .statusline import library_script, load_manifest, resolve_statusline_library

    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        _untar(payload, staging)
        frag_file = staging / "fragment.json"
        if not frag_file.is_file():
            raise ValueError("传输内容不是合法 statusline 片段（缺 fragment.json）")
        bundle = json.loads(frag_file.read_text(encoding="utf-8"))
        if bundle.get("tool") != name:
            raise ValueError(f"传输内容条目名 {bundle.get('tool')} 与 --name {name} 不符")
        fragment = bundle.get("fragment")
        if not isinstance(fragment, dict):
            raise ValueError("传输内容的 fragment 不是对象")
        script_name = fragment.get("script")
        script_src = staging / script_name if script_name else None
        if script_name and not script_src.is_file():
            raise ValueError(f"传输内容缺脚本本体 {script_name}")

        lib = resolve_statusline_library(cfg, create=apply)
        existing = load_manifest(lib).get(name)
        dest_script = library_script(lib, name, fragment)
        same_fragment = existing == fragment
        same_script = (dest_script is None or not dest_script.exists()) \
            if script_src is None else \
            (dest_script is not None and dest_script.is_file()
             and dest_script.read_bytes() == script_src.read_bytes())
        if existing is not None and same_fragment and same_script:
            return [f"ok     statusline:{name}（片段一致）"]
        if existing is not None and prefer != "replace":
            return [f"conflict statusline:{name} → 跳过（--prefer replace 可覆盖）"]
        verb = "replace" if existing is not None else "add"
        if apply:
            manifest_path = lib / "manifest.json"
            if existing is not None and manifest_path.is_file():
                shutil.copy2(manifest_path, _backup_file(manifest_path, "statusline"))
            if dest_script is not None and script_src is not None:
                dest_script.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(script_src, dest_script)
            manifest = load_manifest(lib)
            manifest[name] = fragment
            from .io_utils import atomic_write_json

            atomic_write_json(manifest_path, manifest)
            return [f"{verb}    statusline:{name}" + ("（旧清单已备份）" if existing else "")]
        return [f"{verb}    statusline:{name}"]


# ---------------------------------------------------------------------------
# distribute：ingest 落盘后，把该条目分发到本机所有已装工具


def distribute_item(layer: str, name: str, apply: bool) -> list[str]:
    from .detect import detect_tools
    from .scan import scan_all

    detections = detect_tools()
    installed = [d.tool for d in detections if d.installed]
    reports = scan_all(detections)
    cfg = load_config()

    if layer == "skills":
        from .skills import plan_sync, resolve_library, run_sync

        lib = resolve_library(cfg)
        actions = [a for a in plan_sync(lib, reports, cfg.exclude_skills) if a.skill == name]
        return run_sync(actions, lib, apply, "skip")

    if layer == "agents":
        from .agents import plan_sync, resolve_agents_library, run_sync

        lib = resolve_agents_library(cfg)
        actions = [a for a in plan_sync(lib, reports, cfg.exclude_agents) if a.agent == name]
        return run_sync(actions, lib, apply, "skip")

    if layer == "mcp":
        from .mcp_manifest import _load_secrets, load_manifest
        from .mcp_write import sync_mcp

        specs = [s for s in load_manifest() if s.name == name]
        return sync_mcp(specs, _load_secrets(), installed, apply, "skip")

    if layer == "hooks":
        from .hooks_manifest import load_manifest
        from .hooks_write import sync_hooks

        specs = [s for s in load_manifest() if s.id == name or s.id.startswith(name + "-")]
        return sync_hooks(specs, installed, apply, "skip")

    if layer == "statusline":
        from .statusline import plan_sync_statusline, resolve_statusline_library, run_sync_statusline

        lib = resolve_statusline_library(cfg)
        actions = [a for a in plan_sync_statusline(lib, reports) if a.tool == name]
        return run_sync_statusline(actions, lib, apply, "skip")

    raise ValueError(f"未知层 {layer}（可选 {', '.join(LAYERS)}）")
