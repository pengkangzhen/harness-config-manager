"""statusline 层：各 harness 状态栏配置的跨机器一致性管理。

各工具的 statusline 形态不同构、无法互译，但可归为四个家族（ dialect 表）：

    statusline_key     JSON 根级 statusLine 对象，command 驱动 + 本地脚本
                       （claude / zcode / cursor 的 cli-config.json）
    ui_statusline_key  JSON 嵌套 ui.statusLine 对象（qwen：官方明确必须嵌在
                       ui 下，直接粘贴 Claude Code 配置不生效），command 驱动
                       + 本地脚本；对象内的 refreshInterval 等扩展键随片段走
    ui_footer          JSON 嵌套 ui.footer 对象（gemini 的 /footer 设置：
                       items 声明式条目数组），无脚本
    tui_status_line    TOML [tui] 表托管键（codex 的 /statusline 设置：
                       status_line 声明式条目数组），无脚本
    kimi 混合          TOML [status_line] 表（~/.kimi-code/tui.toml）：
                       items 声明式槽位 + command 脚本式并存（脚本家族语义
                       套 TOML 写入器；command 为空串时退化为纯声明式）

因此事实源不是「一个 canonical statusline」，而是**每工具一个片段**：

    ~/.agents/statusline/
      manifest.json         # {"claude": {"statusLine": {...}, "script": "statusline.py"},
                            #  "codex": {"tui": {"status_line": [...], ...}},
                            #  "gemini": {"footer": {"items": [...]}}}
      claude/statusline.py  # 各工具片段引用的脚本本体（按工具分目录，避免
      qwen/statusline.py    # 同名脚本互相覆盖；command 中以 {script} 占位）

同步目标是「同一工具在各机器上一致」，不是「各工具互译」。片段按工具独立
收养（各自唯一来源工具，天然无分歧，无需 --from 裁决）。

分发语义：
- 脚本家族（claude / zcode / cursor / qwen）：脚本以 symlink 分发到工具配置
  目录（工具侧编辑即改库）；配置键按本机路径渲染后读-改-写（写入前整文件
  备份）。command 里的本机绝对路径写成 {script} 占位——这正是 statusline
  跨机器漂移的根源。
- 声明式家族（gemini / codex）：只动托管区域内的键（codex [tui] 仅
  status_line / status_line_use_colors，theme/pet/keymap 永不碰），读-改-写
  前整文件备份。

冲突语义：工具侧脚本是内容分歧的真实文件时记 conflict（默认跳过，
--prefer library 备份后覆盖）；配置键与期望不同一律 update（整键覆盖，
写入前备份）。
"""

from __future__ import annotations

import json
import shlex
import shutil
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import tomlkit

from .config import HalterConfig, load_config
from .io_utils import atomic_write_json, atomic_write_text, load_json_object
from .model import StatuslineInfo, ToolReport
from .registry import BY_KEY, expand

DEFAULT_STATUSLINE_LIBRARY = ".agents/statusline"
MANIFEST_NAME = "manifest.json"
SCRIPT_PLACEHOLDER = "{script}"

# 每工具方言：statusline 配置所在文件 + 托管区域（region 为自文件根起的键路径）
# script=True 的家族 command 驱动、可引用本地脚本；toml=True 走 TOML 读改写。
# zcode 的 statusLine 同构为 Claude Code 同源推断（写入无害）。
DIALECTS: dict[str, dict] = {
    "claude": {"path": ".claude/settings.json", "region": ("statusLine",), "script": True},
    "zcode": {"path": ".zcode/cli/config.json", "region": ("statusLine",), "script": True},
    "cursor": {"path": ".cursor/cli-config.json", "region": ("statusLine",), "script": True},
    "qwen": {"path": ".qwen/settings.json", "region": ("ui", "statusLine"), "script": True},
    "codex": {"path": ".codex/config.toml", "region": ("tui",), "script": False,
              "toml": True, "managed": ("status_line", "status_line_use_colors")},
    "gemini": {"path": ".gemini/settings.json", "region": ("ui", "footer"), "script": False},
    "kimi": {"path": ".kimi-code/tui.toml", "region": ("status_line",), "script": True,
             "toml": True, "managed": ("items", "command")},
}

# codex [tui] 表中归本层托管的键；其余键（theme / pet / keymap…）永不碰
CODEX_MANAGED_TUI_KEYS = ("status_line", "status_line_use_colors")


def dialect(tool_key: str) -> dict | None:
    return DIALECTS.get(tool_key)


def statusline_settings_path(tool_key: str) -> Path | None:
    d = dialect(tool_key)
    return expand(d["path"]) if d else None


def _region_node(data: dict, region: tuple[str, ...]) -> dict | None:
    node: object = data
    for key in region:
        node = node.get(key) if isinstance(node, dict) else None
    return node if isinstance(node, dict) else None


# ---------------------------------------------------------------------------
# 事实源解析：显式配置 > ~/.agents/statusline/


def resolve_statusline_library(cfg: HalterConfig, create: bool = False) -> Path:
    if cfg.statusline_library:
        lib = Path(cfg.statusline_library).expanduser()
    else:
        lib = expand(DEFAULT_STATUSLINE_LIBRARY)
    if create:
        lib.mkdir(parents=True, exist_ok=True)
    return lib


def load_manifest(library: Path) -> dict[str, dict]:
    """库清单：tool -> 片段。库目录 / manifest.json 缺失或结构不对返回 {}。"""
    path = library / MANIFEST_NAME
    if not path.is_file():
        return {}
    try:
        data = load_json_object(path)
    except ValueError:
        return {}
    if not isinstance(data, dict) or not all(
        isinstance(v, dict) for v in data.values()
    ):
        return {}
    return {k: v for k, v in data.items() if k in DIALECTS}


def library_script(library: Path, tool: str, fragment: dict) -> Path | None:
    """片段引用的库脚本本体路径（库内按工具分目录）；无脚本需求返回 None。"""
    name = fragment.get("script")
    if not name:
        return None
    return library / tool / name


# ---------------------------------------------------------------------------
# 工具侧读取（各方言 -> canonical 片段）


def command_script(command: str) -> Path | None:
    """从 statusLine.command 中解析本地脚本路径：首个存在的文件型 token。

    相对路径 / 裸命令名（npx ccstatusline 等）不指向本地脚本，返回 None。
    """
    try:
        tokens = shlex.split(command)
    except ValueError:  # 引号不闭合等
        return None
    for token in tokens:
        path = Path(token).expanduser()
        if path.is_absolute() and path.is_file():
            return path
    return None


def _read_toml_dict(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return data if isinstance(data, dict) else {}


def _read_json_family(info: StatuslineInfo, d: dict) -> tuple[StatuslineInfo, list[str]]:
    try:
        data = load_json_object(info.settings_path)
    except ValueError:
        return info, [f"statusline 配置解析失败: {info.settings_path}"]
    node = _region_node(data, d["region"])
    if node is not None:
        info.present = True
        info.payload = {d["region"][-1]: node}
        if d["script"] and isinstance(node.get("command"), str):
            info.command = node["command"]
            info.script = command_script(info.command)
            info.script_linked = info.script is not None and info.script.is_symlink()
        elif not d["script"]:
            items = node.get("items")
            if isinstance(items, list):
                info.items = [str(x) for x in items]
    return info, []


def _read_toml_family(info: StatuslineInfo, d: dict) -> tuple[StatuslineInfo, list[str]]:
    try:
        data = _read_toml_dict(info.settings_path)
    except tomllib.TOMLDecodeError:
        return info, [f"statusline 配置解析失败: {info.settings_path}"]
    node = _region_node(data, d["region"])
    if node is not None and any(k in node for k in d["managed"]):
        info.present = True
        info.payload = {d["region"][-1]: {k: node[k] for k in d["managed"] if k in node}}
        items = node.get("items") if "items" in d["managed"] else node.get("status_line")
        if isinstance(items, list):
            info.items = [str(x) for x in items]
        if d["script"] and isinstance(node.get("command"), str) and node["command"]:
            info.command = node["command"]
            info.script = command_script(info.command)
            info.script_linked = info.script is not None and info.script.is_symlink()
    return info, []


def scan_statusline(spec) -> tuple[StatuslineInfo | None, list[str]]:
    """扫描一个工具的 statusline 配置，返回 (info, notes)。

    同时对照事实源计算 synced（None = 库缺失无从判定）；linked 输出口径 =
    脚本已链接 或 与库片段一致。
    """
    d = dialect(spec.key)
    if d is None or not expand(d["path"]).parent.is_dir():
        return None, []
    info = StatuslineInfo(settings_path=expand(d["path"]))
    reader = _read_toml_family if d.get("toml") else _read_json_family
    info, notes = reader(info, d)
    # synced 即使工具侧未配置也要算：库有片段而工具缺失（False）是可同步的缺口，
    # 库无片段（None）才是无从判定——桌面矩阵按此区分「出行的 ○」与「不出行」
    library = resolve_statusline_library(load_config())
    fragment = load_manifest(library).get(spec.key)
    if fragment is not None:
        info.synced = _matches_library(spec.key, d, fragment, info, library)
    return info, notes


def _matches_library(tool_key: str, d: dict, fragment: dict, info: StatuslineInfo,
                     library: Path) -> bool:
    """工具侧现状是否与库片段一致（分发后的 ● 判定）。"""
    key = d["region"][-1]
    if not d["script"]:
        return info.payload.get(key) == fragment.get(key)
    # 脚本家族：配置键与渲染期望一致，且脚本（若有）正确链接
    script = library_script(library, tool_key, fragment)
    target = _tool_script_path(tool_key, fragment)
    expected = render_fragment(fragment, key, target)
    settings_ok = info.payload.get(key) == expected
    if script is None:
        return settings_ok
    return settings_ok and target is not None and target.is_symlink() \
        and target.resolve() == script.resolve()


def _tool_script_path(tool_key: str, fragment: dict) -> Path | None:
    """片段脚本在该工具侧的分发位置（工具配置目录根，非 settings 所在目录——
    zcode 的 cli/ 是会话数据区；qwen 无 config_dirs 时退回 settings 父目录）。"""
    name = fragment.get("script")
    if not name:
        return None
    spec = BY_KEY.get(tool_key)
    base = (expand(spec.config_dirs[0])
            if spec and spec.config_dirs
            else statusline_settings_path(tool_key).parent)
    return base / name


# ---------------------------------------------------------------------------
# 期望值渲染：{script} 占位 -> 目标工具侧的脚本绝对路径


def render_fragment(fragment: dict, key: str, script_path: Path | None) -> dict:
    """脚本家族片段的期望值（command 占位符按本机渲染）。"""
    node = fragment.get(key)
    if not isinstance(node, dict):
        return {}
    expected = json.loads(json.dumps(node))  # 深拷贝，不污染清单
    command = expected.get("command")
    if isinstance(command, str) and SCRIPT_PLACEHOLDER in command and script_path is not None:
        expected["command"] = command.replace(SCRIPT_PLACEHOLDER, str(script_path))
    return expected


def _fragment_from_info(info: StatuslineInfo, d: dict) -> dict:
    """收养用：工具侧现状 -> canonical 片段（command 路径模板化 + 脚本入库）。"""
    fragment = json.loads(json.dumps(info.payload))  # 深拷贝
    node = fragment.get(d["region"][-1])
    if not d["script"]:
        return fragment
    if isinstance(node, dict) and info.script is not None \
            and isinstance(node.get("command"), str):
        node["command"] = node["command"].replace(str(info.script), SCRIPT_PLACEHOLDER)
        fragment["script"] = info.script.name
    elif isinstance(node, dict):
        fragment["script"] = None
    return fragment


# ---------------------------------------------------------------------------
# adopt：库缺某工具片段时从该工具收养（每片段唯一来源，无分歧）


def plan_adopt_statusline(reports: list[ToolReport]) -> list[tuple[str, StatuslineInfo]]:
    """可收养的 (tool, info)：有 statusline 配置的各工具（是否入库由调用方
    对照 manifest 中已有片段决定）。"""
    return [(r.tool, r.statusline) for r in reports
            if r.statusline is not None and r.statusline.present
            and r.tool in DIALECTS]


def run_adopt_statusline(tool: str, info: StatuslineInfo, library: Path,
                         apply: bool) -> list[str]:
    """把工具侧现状收养为库中该工具的片段；脚本本体 copy 入库（保留原名）。"""
    fragment = _fragment_from_info(info, DIALECTS[tool])
    lines = [f"adopt statusline {tool} <- {info.settings_path}"]
    script = library_script(library, tool, fragment)
    if script is not None:
        lines.append(f"adopt script {info.script} -> {script}")
    if apply:
        library.mkdir(parents=True, exist_ok=True)
        if script is not None:
            script.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(info.script, script)
        manifest = load_manifest(library)
        manifest[tool] = fragment
        atomic_write_json(library / MANIFEST_NAME, manifest)
    return lines


# ---------------------------------------------------------------------------
# sync：脚本 symlink 分发 + 各家族方言写入


@dataclass
class StatuslineAction:
    kind: str            # ok / link / relink / replace / update / conflict
    tool: str
    script: Path | None  # 工具侧脚本目标路径（无脚本需求为 None）
    settings: Path       # 工具侧配置文件
    expected: dict       # 期望值：脚本家族 = 渲染后 statusLine；
                         # 声明式家族 = {"tui"/"footer": {托管内容}}
    settings_update: bool = False  # 是否需要写配置键
    detail: str = ""


def _current_region(info: StatuslineInfo, d: dict) -> object:
    """工具侧托管区域的当前值（与片段顶层键对齐的比较口径）。"""
    key = d["region"][-1]
    if d.get("toml"):
        try:
            node = _region_node(_read_toml_dict(info.settings_path), d["region"])
        except tomllib.TOMLDecodeError:
            return None
        return {k: node[k] for k in d["managed"] if isinstance(node, dict) and k in node} \
            if isinstance(node, dict) else None
    try:
        data = load_json_object(info.settings_path)
    except ValueError:
        return None
    node = _region_node(data, d["region"])
    return {k: node[k] for k in d["managed"] if isinstance(node, dict) and k in node} \
        if d.get("managed") else node


def plan_sync_statusline(library: Path, reports: list[ToolReport]) -> list[StatuslineAction]:
    """库 -> 各工具的分发计划。库无该工具片段 / 工具未安装时自动跳过。"""
    manifest = load_manifest(library)
    actions: list[StatuslineAction] = []
    for r in reports:
        fragment = manifest.get(r.tool)
        d = dialect(r.tool)
        if fragment is None or d is None or r.statusline is None:
            continue
        key = d["region"][-1]
        expected_node = fragment.get(key) or {}

        if not d["script"]:
            settings_update = _current_region(r.statusline, d) != expected_node
            kind = "update" if settings_update else "ok"
            actions.append(StatuslineAction(kind, r.tool, None, r.statusline.settings_path,
                                            {key: expected_node}, settings_update))
            continue

        script = library_script(library, r.tool, fragment)
        target = _tool_script_path(r.tool, fragment)
        rendered = render_fragment(fragment, key, target)
        expected = {key: rendered}
        # 脚本侧状态
        if target is None:
            s_state = "none"
        elif not target.exists() and not target.is_symlink():
            s_state = "link"
        elif target.is_symlink():
            s_state = "ok" if target.resolve() == (
                script.resolve() if script else None) else "relink"
        elif target.is_file():
            try:
                same = script is not None and target.read_bytes() == script.read_bytes()
            except OSError:
                same = False
            s_state = "replace" if same else "conflict"
        else:  # 目录占位同名等罕见情形：跳过该工具
            continue
        # 配置键侧状态
        settings_update = _current_region(r.statusline, d) != rendered
        if s_state == "conflict":
            kind, detail = "conflict", "脚本内容与库不同"
        elif s_state in ("link", "relink", "replace"):
            kind, detail = s_state, ""
        elif settings_update:
            kind, detail = "update", "statusLine 键与库期望不同"
        else:
            kind, detail = "ok", ""
        actions.append(StatuslineAction(kind, r.tool, target, r.statusline.settings_path,
                                        expected, settings_update, detail))
    return actions


def _backup_statusline(path: Path, tool: str) -> None:
    """写配置前备份整个文件（providers 写入器同款安全垫）。"""
    if not path.exists():
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_dir = expand(".config/halter/backups/statusline")
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_dir / f"{stamp}-{tool}-{path.name}")


def write_json_region(path: Path, region: tuple[str, ...], node: dict, tool: str) -> None:
    """JSON 家族写入：读-改-写，只替换托管区域（ui.statusLine / ui.footer…），
    区域外的键（含 ui 下的其它键）永不碰。"""
    data = load_json_object(path)
    parent = data
    for key in region[:-1]:
        parent = parent.setdefault(key, {})
    parent[region[-1]] = node
    _backup_statusline(path, tool)
    atomic_write_json(path, data)


def write_toml_region(path: Path, d: dict, node: dict, tool: str) -> None:
    """TOML 家族写入（codex [tui] / kimi [status_line]）：tomlkit 保注释
    读-改-写，托管区域内只动 managed 键，其余键永不碰。"""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    doc = tomlkit.parse(text)
    table = doc
    for key in d["region"][:-1]:
        table = table.setdefault(key, tomlkit.table())
    leaf = table.get(d["region"][-1])
    if not isinstance(leaf, tomlkit.items.Table):
        leaf = tomlkit.table()
        table[d["region"][-1]] = leaf
    for k in d["managed"]:
        if k in node:
            leaf[k] = node[k]
        else:
            leaf.pop(k, None)  # 库不托管的键从工具侧撤下
    _backup_statusline(path, tool)
    atomic_write_text(path, tomlkit.dumps(doc))


def _script_backup_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    d = expand(f".config/halter/backups/{stamp}/statusline")
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_sync_statusline(actions: list[StatuslineAction], library: Path, apply: bool,
                        prefer: str = "skip") -> list[str]:
    """执行分发计划。prefer 仅影响 conflict：skip（默认跳过）/ library（备份工具侧脚本后覆盖）。"""
    lines: list[str] = []
    bak = _script_backup_dir() if apply else None
    manifest = load_manifest(library)

    def place_script(act: StatuslineAction) -> None:
        """把工具侧脚本就位为指向库的 symlink（必要时先备份原文件）。"""
        lib_script = library_script(library, act.tool, manifest.get(act.tool, {}))
        assert act.script is not None and lib_script is not None
        if act.script.exists() and not act.script.is_symlink() and bak is not None:
            dest = bak / act.tool / act.script.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(act.script), str(dest))
        elif act.script.is_symlink():
            act.script.unlink()
        act.script.symlink_to(lib_script)
        if act.settings_update:
            d = dialect(act.tool)
            write_region(act.settings, d, act.expected[d["region"][-1]], act.tool)

    def write_region(path: Path, d: dict, node: dict, tool: str) -> None:
        if d.get("toml"):
            write_toml_region(path, d, node, tool)
        else:
            write_json_region(path, d["region"], node, tool)

    for act in actions:
        if act.kind == "ok":
            lines.append(f"ok      {act.tool}: statusline 已同步")
        elif act.kind in ("link", "relink", "replace"):
            note = {"link": "", "relink": "（重建链接）",
                    "replace": "（内容与库一致，备份后链接）"}[act.kind]
            lines.append(f"{act.kind:<7} {act.tool}: 脚本 {act.script.name}{note}"
                         + (" + statusline 键" if act.settings_update else ""))
            if apply:
                place_script(act)
        elif act.kind == "update":
            d = dialect(act.tool)
            key = d["region"][-1]
            lines.append(f"update  {act.tool}: {d['path']} 托管区域 -> 库期望（备份配置）")
            if apply:
                write_region(act.settings, d, act.expected[key], act.tool)
        elif act.kind == "conflict":
            if prefer == "library":
                lines.append(f"conflict {act.tool}: 脚本内容与库不同 -> 按库覆盖（备份工具侧）")
                if apply:
                    place_script(act)
            else:
                lines.append(f"conflict {act.tool}: 脚本内容与库不同"
                             f" -> 跳过（--prefer library 可覆盖）")
    return lines
