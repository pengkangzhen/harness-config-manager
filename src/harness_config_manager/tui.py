"""halter tui：交互式全屏矩阵浏览器（htop/k9s 风格）。

1-6 切层 · 方向键选择条目 · 右侧详情面板（描述/路径/形态）
g 只看缺口 · / 过滤 · q 退出。数据来自 scan_all，只读。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Static

from .model import ToolReport

LAYERS: list[tuple[str, str, str]] = [
    ("skills", "SKILLS", "1"),
    ("agents", "SUBAGENTS", "2"),
    ("mcp", "MCP", "3"),
    ("plugins", "PLUGINS", "4"),
    ("hooks", "HOOKS", "5"),
    ("sessions", "SESSIONS", "6"),
]


class HelpScreen(ModalScreen[None]):
    """按 ? 弹出的快捷键帮助浮层。"""

    CSS = """
    Screen { align: center middle; }
    Static { border: heavy $primary; background: $surface; padding: 1 2; }
    """
    BINDINGS = [Binding("escape,question_mark,q", "dismiss", "关闭", show=False)]

    def compose(self) -> ComposeResult:
        lines = [
            "[b cyan]halter tui 快捷键[/b cyan]",
            "[dim]────────────────────────────[/dim]",
            "[b]1-6[/b]   切换层（Skills/Subagents/MCP/Plugins/Hooks/Sessions）",
            "[b]↑↓←→[/b]  移动选择，右侧面板显示详情",
            "[b]g[/b]     只看缺口（再按恢复）",
            "[b]s[/b]     循环排序：缺口 → 名称 → 覆盖",
            "[b]/[/b]     过滤条目名（Enter 应用，Esc 清除）",
            "[b]?[/b]     本帮助",
            "[b]q[/b]     退出",
            "",
            "[dim]● symlink 同步 · ◐ 本地拷贝 · · 缺失[/dim]",
            "",
            "[dim]按任意键关闭[/dim]",
        ]
        yield Static("\n".join(lines), id="help-body")

    def on_key(self) -> None:
        self.dismiss(None)


@dataclass
class Row:
    name: str
    entries: dict[str, object] = field(default_factory=dict)  # tool -> 条目
    merged: list[str] = field(default_factory=list)           # 插件合并前的完整 id


def _layer_rows(layer: str, reports: list[ToolReport]) -> list[Row]:
    """把 ToolReport 列表摊平成「条目 × 工具」行（插件按 @market 基名合并）。"""
    rows: dict[str, Row] = {}

    def put(key: str, tool: str, entry: object, *, merged_id: str | None = None) -> None:
        row = rows.setdefault(key, Row(name=key))
        if tool not in row.entries:
            row.entries[tool] = entry
        if merged_id and merged_id not in row.merged:
            row.merged.append(merged_id)

    for r in reports:
        if not r.installed:
            continue
        if layer == "skills":
            for s in r.skills:
                put(s.name, r.tool, s)
        elif layer == "agents":
            for a in r.agents:
                put(a.name, r.tool, a)
        elif layer == "mcp":
            for m in r.mcp_servers:
                put(m.name, r.tool, m)
        elif layer == "plugins":
            for p in r.plugins:
                put(p.plugin_id.split("@", 1)[0], r.tool, p, merged_id=p.plugin_id)
        elif layer == "hooks":
            for h in r.hooks:
                put(h.label, r.tool, h)
        elif layer == "sessions":
            for s in r.sessions:
                put(s.ref, r.tool, s)

    return list(rows.values())


def _fmt_time(value: datetime | None) -> str:
    if value is None:
        return "-"
    local = value.astimezone()
    return local.strftime("%m-%d %H:%M") if local.year == datetime.now().year else local.strftime("%Y-%m-%d")


def _detail_fields(layer: str, row: Row) -> list[tuple[str, str]]:
    tools = ", ".join(sorted(row.entries))
    first = next(iter(row.entries.values()), None)
    fields: list[tuple[str, str]] = [("工具", tools)]
    if layer == "skills":
        desc = getattr(first, "description", None) or "-"
        fields.insert(0, ("描述", desc))
        path = getattr(first, "path", None)
        if path is not None:
            fields.append(("路径", str(path)))
        linked = [t for t, e in row.entries.items() if getattr(e, "linked", False)]
        if linked:
            fields.append(("库链接", ", ".join(sorted(linked))))
    elif layer == "agents":
        model = getattr(first, "model", None)
        fields.insert(0, ("model", model or "-"))
        path = getattr(first, "path", None)
        if path is not None:
            fields.append(("路径", str(path)))
    elif layer == "mcp":
        transport = getattr(first, "transport", "?")
        command = getattr(first, "command", None)
        url = getattr(first, "url", None)
        endpoint = (url or (command and " ".join(command) if isinstance(command, list) else command) or "-")
        fields.insert(0, ("类型", str(transport)))
        fields.insert(1, ("端点", str(endpoint)[:120]))
    elif layer == "plugins":
        if row.merged:
            fields.insert(0, ("变体", "\n".join(sorted(row.merged))))
    elif layer == "hooks":
        event = getattr(first, "event", "-")
        command = getattr(first, "command", None) or "-"
        fields.insert(0, ("事件", str(event)))
        fields.insert(1, ("命令", str(command)[:160]))
    elif layer == "sessions":
        title = getattr(first, "title", None) or "-"
        updated = _fmt_time(getattr(first, "updated_at", None))
        count = getattr(first, "message_count", 0)
        fields.insert(0, ("标题", title))
        fields.insert(1, ("更新", updated))
        fields.insert(2, ("消息数", str(count)))
    return fields


class HalterTui(App[None]):
    TITLE = "halter tui"
    CSS = """
    #tabbar { dock: top; height: 3; padding: 0 1; background: $surface; }
    #tabs { width: 1fr; height: 3; overflow-x: auto; overflow-y: hidden; }
    #stats { width: auto; height: 3; padding: 1 0 0 2; color: $text-muted; }
    Button.tab { background: $surface; color: $text-muted; border: none;
                 min-width: 10; height: 3; padding: 0 1; }
    Button.tab.-active { background: $primary; color: $text; text-style: bold; }
    Button.tab:hover { background: $boost; }
    #main { height: 1fr; }
    #matrix { width: 58%; border-right: heavy $primary-darken-2; }
    #detail { width: 1fr; padding: 1 2; color: $text; }
    #filter { display: none; }
    #help-body { padding: 1 2; }
    """
    BINDINGS = [
        Binding("q", "quit", "退出"),
        Binding("g", "toggle_gaps", "只看缺口"),
        Binding("s", "cycle_sort", "排序"),
        Binding("question_mark", "help", "帮助"),
        Binding("slash", "focus_filter", "过滤"),
        Binding("escape", "clear_filter", "清除过滤", show=False),
        *[Binding(k, f"layer('{key}')", label) for key, label, k in LAYERS],
    ]

    def __init__(self, reports: list[ToolReport]) -> None:
        super().__init__()
        self._reports = [r for r in reports if r.installed]
        self._layer = "skills"
        self._gaps_only = False
        self._sort = 0  # 0=缺口降序 1=名称 2=覆盖降序
        self._filter = ""
        self._rows: list[Row] = []
        self._visible: list[Row] = []
        self._by_name: dict[str, Row] = {}

    # ---------- 布局 ----------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="tabbar"):
            with Horizontal(id="tabs"):
                for key, label, k in LAYERS:
                    yield Button(f"{k} {label}", id=f"tab-{key}", classes="tab")
            yield Static(id="stats")
        with Horizontal(id="main"):
            yield DataTable(id="matrix")
            yield Static(id="detail")
        yield Input(placeholder="输入条目名子串过滤，Enter 应用，Esc 清除…", id="filter")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#matrix", DataTable)
        table.cursor_type = "row"
        self._reload()
        table.focus()

    # ---------- 数据渲染 ----------

    def _reload(self) -> None:
        rows = _layer_rows(self._layer, self._reports)
        if self._sort == 1:
            rows.sort(key=lambda r: r.name)
        elif self._sort == 2:
            rows.sort(key=lambda r: (-len(r.entries), r.name))
        else:
            rows.sort(key=lambda r: (-(len(self._reports) - len(r.entries)), r.name))
        self._rows = rows
        self._visible = [r for r in self._rows if self._passes(r)]
        self._by_name = {r.name: r for r in self._visible}
        self._render_tabs()
        self._render_table()
        self._render_detail()

    def _passes(self, row: Row) -> bool:
        if self._gaps_only and len(row.entries) == len(self._reports):
            return False
        return not self._filter or self._filter.casefold() in row.name.casefold()

    def _render_tabs(self) -> None:
        counts = {key: len(_layer_rows(key, self._reports)) for key, _, _ in LAYERS}
        for key, label, k in LAYERS:
            btn = self.query_one(f"#tab-{key}", Button)
            btn.label = f"{k} {label}·{counts[key]}"
            btn.set_classes("tab -active" if key == self._layer else "tab")
        gaps = sum(len(self._reports) - len(r.entries) for r in self._rows)
        sort_label = ("gap", "name", "cover")[self._sort]
        state = f"{len(self._visible)}/{len(self._rows)} · gap {gaps} · sort:{sort_label}"
        if self._gaps_only:
            state += " · GAPS"
        if self._filter:
            state += f" · ‘{self._filter}’"
        self.query_one("#stats", Static).update(state)

    def _cell(self, row: Row, tool: str) -> Text:
        entry = row.entries.get(tool)
        if entry is None:
            return Text("·", style="dim")
        if self._layer in ("skills", "agents"):
            return Text("●", style="green") if getattr(entry, "linked", False) else Text("◐", style="yellow")
        return Text("●")

    def _render_table(self) -> None:
        table = self.query_one("#matrix", DataTable)
        table.clear(columns=True)
        table.add_columns("ITEM", *[r.tool for r in self._reports])
        for row in self._visible:
            table.add_row(Text(row.name, style="cyan"), *(self._cell(row, r.tool) for r in self._reports), key=row.name)

    def _render_detail(self, row: Row | None = None) -> None:
        static = self.query_one("#detail", Static)
        if row is None:
            static.update(Text("（方向键选择条目查看详情）", style="dim"))
            return
        segments: list[tuple[str, str]] = [(row.name, "bold cyan"), ("\n" + "─" * 40, "dim")]
        for label, value in _detail_fields(self._layer, row):
            segments.append((f"\n{label:<6}", "dim"))
            segments.append((" " + str(value), ""))
        static.update(Text.assemble(*segments))

    # ---------- 事件 ----------

    @on(DataTable.RowHighlighted)
    def _row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        name = event.row_key.value
        if name:
            self._render_detail(self._by_name.get(name))

    @on(Button.Pressed, ".tab")
    def _tab_pressed(self, event: Button.Pressed) -> None:
        key = str(event.button.id).removeprefix("tab-")
        self.action_layer(key)

    @on(Input.Submitted, "#filter")
    def _filter_submitted(self, event: Input.Submitted) -> None:
        self._filter = event.value.strip()
        self._reload()
        self.query_one("#matrix", DataTable).focus()

    # ---------- 动作 ----------

    def action_layer(self, key: str) -> None:
        if key != self._layer:
            self._layer = key
            self._reload()

    def action_toggle_gaps(self) -> None:
        self._gaps_only = not self._gaps_only
        self._reload()

    def action_cycle_sort(self) -> None:
        self._sort = (self._sort + 1) % 3
        self._reload()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_focus_filter(self) -> None:
        f = self.query_one("#filter", Input)
        f.display = True
        f.focus()

    def action_clear_filter(self) -> None:
        f = self.query_one("#filter", Input)
        if f.display:
            f.value = ""
            f.display = False
            self._filter = ""
            self._reload()
            self.query_one("#matrix", DataTable).focus()
