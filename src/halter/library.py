"""library 层：~/.agents 库的跨机同步（远端无需安装 halter）。

库即同步：skills / agents / memory / statusline 的事实源都在 ~/.agents，
跨机一致性归结为该目录的一致性 + 无 halter 远端的「接管」。接管只需要
POSIX sh 与 python3（macOS / Linux 开发机标配）：脚本式 statusline 把
settings 里的 statusLine.command 改写为 ~/.agents/... 直连路径（~ 由
shell 展开，跨机器可移植；halter 的收养解析做 expanduser，该形态无缝
兼容将来装 halter）。声明式家族（codex / gemini / kimi）的配置写入需要
halter 方言写入器，远端未装时明确跳过。

- init  : ~/.agents git 化（幂等）并提交现状
- adopt : 远端建库 → 经 bundle 拉入本机库 → statusline 接管；以本机库为准，
          远端原 ~/.agents 先打整包备份（.agents.prehalter-<时间戳>.tgz）
- push  : 本机提交变更 → bundle 推到远端 → 重放接管（幂等刷新）；
          远端自身有新提交时 ff-only 失败并提示先 pull
- pull  : 远端提交变更 → bundle 拉回本机 fast-forward；随后用
          `halter sync --apply` 分发到本机各工具

传输统一走 git bundle 单文件（ssh stdin/stdout），不需要远端 bare 仓，
也避开非交互 ssh 下 git-receive-pack 不在远端 PATH 的坑。
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from .machines import MachineSpec, get_machine, run_ssh
from .statusline import DIALECTS

# 可被无 halter 远端接管的方言：command 直连库路径即可生效
DIRECT_DIALECTS = {k: d for k, d in DIALECTS.items()
                   if d.get("script") and not d.get("toml")}


def library_root() -> Path:
    return Path.home() / ".agents"


# ---------------------------------------------------------------------------
# 本机 git 编排


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=False)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: {proc.stderr.strip()}")
    return proc


def _ensure_identity(root: Path) -> None:
    """全局缺 git 身份时给库配置局部身份（否则 commit 失败）。"""
    if _git(root, "config", "user.email", check=False).returncode != 0:
        _git(root, "config", "user.email", "halter@localhost")
        _git(root, "config", "user.name", "halter")


def library_commit(reason: str) -> str | None:
    """提交库内全部变更，返回提交摘要；无变更返回 None。"""
    root = library_root()
    if not (root / ".git").is_dir():
        return None
    _git(root, "add", "-A")
    if _git(root, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return None
    _git(root, "commit", "-qm", reason)
    return _git(root, "log", "-1", "--oneline").stdout.strip()


def library_init(apply: bool) -> list[str]:
    """~/.agents git 化（幂等）并提交现状。"""
    root = library_root()
    lines: list[str] = []
    if (root / ".git").is_dir():
        lines.append(f"ok      库已是 git 仓库: {root}")
    else:
        lines.append("init    git 化 ~/.agents")
        if not apply and not root.is_dir():
            return lines + ["plan    首次执行将创建目录并做全量快照提交"]
        if apply:
            root.mkdir(parents=True, exist_ok=True)
            _git(root, "init", "-q")
    _ensure_identity(root)
    summary = library_commit("chore: library snapshot")
    if summary:
        lines.append(f"commit  {summary}")
    elif apply or (root / ".git").is_dir():
        lines.append("ok      无待提交变更")
    return lines


def _bundle_bytes(root: Path) -> bytes:
    """把 HEAD 打成 bundle 字节（临时文件中转，git bundle 不保证 stdout 流式）。"""
    with tempfile.NamedTemporaryFile(suffix=".bundle", delete=False) as tmp:
        bundle_path = Path(tmp.name)
    try:
        _git(root, "bundle", "create", str(bundle_path), "HEAD")
        return bundle_path.read_bytes()
    finally:
        bundle_path.unlink(missing_ok=True)


def _bundle_fetch(root: Path, data: bytes) -> tuple[bool, str]:
    """把 bundle 字节 fetch 进库并 fast-forward 到 FETCH_HEAD。

    返回 (成功, 说明)。非 ff（两边分叉）时失败并保留现场供人工裁决。
    """
    with tempfile.NamedTemporaryFile(suffix=".bundle", delete=False) as tmp:
        tmp.write(data)
        bundle_path = Path(tmp.name)
    try:
        proc = _git(root, "fetch", "-q", str(bundle_path), "HEAD", check=False)
        if proc.returncode != 0:
            return False, f"fetch 失败: {proc.stderr.strip()}"
        proc = _git(root, "merge", "--ff-only", "FETCH_HEAD", check=False)
        if proc.returncode != 0:
            return False, "远端与本机历史分叉（ff-only 拒绝合并），需人工裁决"
        return True, ""
    finally:
        bundle_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 远端接管脚本（POSIX sh + python3，经 run_ssh 单参数整体执行）


_REMOTE_ADOPT = r'''
set -e
AG="$HOME/.agents"
mkdir -p "$AG"
if [ ! -d "$AG/.git" ]; then git init -q "$AG"; fi
git -C "$AG" config user.email >/dev/null 2>&1 || {
  git -C "$AG" config user.email "halter@localhost"
  git -C "$AG" config user.name "halter"
}
# 保护远端未提交修改：先快照提交（永不丢失数据，分叉由 ff-only 显式暴露）
git -C "$AG" add -A
git -C "$AG" diff --cached --quiet || git -C "$AG" commit -qm "chore: snapshot before halter sync"
cat > "$AG/.incoming.bundle"
git -C "$AG" fetch -q "$AG/.incoming.bundle" HEAD
if [ ! -f "$AG/.halter-adopted" ]; then
  # 首次接管：整包备份远端原库（排除 .git），以本机库为准强切
  tar -czf "$HOME/.agents.prehalter-$(date +%Y%m%d-%H%M%S).tgz" --exclude='.git' -C "$HOME" .agents 2>/dev/null || true
  git -C "$AG" checkout -q -f -B library FETCH_HEAD
  echo ".halter-adopted" >> "$AG/.git/info/exclude"
  touch "$AG/.halter-adopted"
else
  git -C "$AG" merge --ff-only FETCH_HEAD || { echo "DIVERGED"; exit 3; }
fi
rm -f "$AG/.incoming.bundle"
'''


def _remote_takeover_python(tools: list[str]) -> str:
    """远端 python3 接管脚本：settings 的 statusLine 改写为库直连路径（幂等，备份原文件）。"""
    targets = {tool: DIRECT_DIALECTS[tool]["path"] for tool in tools}
    return (
        "import json, pathlib, shutil, time\n"
        f"targets = {json.dumps(targets)}\n"
        r'''
for tool, rel in targets.items():
    p = pathlib.Path.home() / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(p.read_text()) if p.exists() else {}
    except Exception:
        data = {}
    direct = "python3 ~/.agents/statusline/%s/statusline.py" % tool
    node = data.get("statusLine")
    if isinstance(node, dict) and node.get("command") == direct:
        continue  # 已接管，幂等跳过
    if p.exists():
        shutil.copy2(p, p.with_name(p.name + ".prehalter-" + time.strftime("%Y%m%d-%H%M%S")))
    data["statusLine"] = {"type": "command", "command": direct}
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print("takeover %s -> %s" % (tool, direct))
'''
    )


def _run_remote_sh(machine: MachineSpec, script: str,
                   input_bytes: bytes | None = None, timeout: int = 300
                   ) -> tuple[int | None, bytes, str]:
    """远端整体执行 sh 脚本（单参数，避免 ssh 多参数重分词）。"""
    return run_ssh(machine, [script], input_bytes, timeout)


def _remote_refresh(machine: MachineSpec, tools: list[str],
                    bundle: bytes) -> tuple[list[str], str]:
    """远端：灌 bundle → checkout 库分支 → 接管 settings。返回 (lines, error)。"""
    script = _REMOTE_ADOPT + "python3 - <<'HALTERPY'\n" + _remote_takeover_python(tools) + "\nHALTERPY\n"
    rc, out, err = _run_remote_sh(machine, script, input_bytes=bundle)
    text = out.decode("utf-8", errors="replace")
    if rc is None or rc != 0:
        return [], (err.strip() or text.strip() or f"远端退出码 {rc}")
    lines = [f"remote  库已更新 ({machine.name})"]
    lines += [f"remote  {ln}" for ln in text.splitlines() if ln.strip().startswith("takeover")]
    return lines, ""


# ---------------------------------------------------------------------------
# 子命令编排：adopt / push / pull


def library_adopt(machine_name: str, tools: list[str], apply: bool) -> list[str]:
    """远端建库 + 拉入本机库 + statusline 接管（以本机库为准，远端先备份）。"""
    machine = get_machine(machine_name)
    if machine is None:
        raise SystemExit(f"halter: 机器 {machine_name} 不存在（halter machines list 查看）")
    bad = [t for t in tools if t not in DIRECT_DIALECTS]
    if bad:
        supported = ", ".join(DIRECT_DIALECTS)
        raise SystemExit(f"halter: {'/'.join(bad)} 不支持无 halter 接管"
                         f"（可接管：{supported}；声明式家族需远端安装 halter）")
    root = library_root()
    if not (root / ".git").is_dir():
        raise SystemExit("halter: 本机库未 git 化，先执行 halter library init --apply")
    lines = [f"adopt   {machine_name}（工具：{', '.join(tools)}；远端无需 halter）"]
    if not apply:
        lines.append("plan    本机库打包 -> 远端 ~/.agents + settings 接管（dry-run）")
        return lines
    bundle = _bundle_bytes(root)
    got, err = _remote_refresh(machine, tools, bundle)
    if err:
        raise SystemExit(f"halter: 远端接管失败: {err}")
    lines += got
    lines.append("note    远端首次接管前的旧库已备份为 ~/.agents.prehalter-<时间戳>.tgz")
    return lines


def library_push(machine_name: str, tools: list[str], apply: bool) -> list[str]:
    """本机提交变更 → bundle 推远端 → 重放接管。远端有分叉提交时报错提示 pull。"""
    machine = get_machine(machine_name)
    if machine is None:
        raise SystemExit(f"halter: 机器 {machine_name} 不存在（halter machines list 查看）")
    root = library_root()
    if not (root / ".git").is_dir():
        raise SystemExit("halter: 本机库未 git 化，先执行 halter library init --apply")
    lines: list[str] = []
    if not apply:
        lines.append(f"plan    提交本机变更 -> 推送 {machine_name} -> 刷新接管（dry-run）")
        return lines
    summary = library_commit(f"chore: library push to {machine_name}")
    if summary:
        lines.append(f"commit  {summary}")
    else:
        lines.append("ok      本机库无新变更")
    bundle = _bundle_bytes(root)
    got, err = _remote_refresh(machine, tools, bundle)
    if err:
        raise SystemExit(f"halter: 远端更新失败: {err}（若远端有独立提交，先 halter library pull --from {machine_name}）")
    lines += got
    return lines


def library_pull(machine_name: str, apply: bool) -> list[str]:
    """远端提交变更 → bundle 拉回本机 fast-forward；本机再 sync 分发到各工具。"""
    machine = get_machine(machine_name)
    if machine is None:
        raise SystemExit(f"halter: 机器 {machine_name} 不存在（halter machines list 查看）")
    root = library_root()
    if not (root / ".git").is_dir():
        raise SystemExit("halter: 本机库未 git 化，先执行 halter library init --apply")
    script = (
        'cd "$HOME/.agents" || exit 1\n'
        'git config user.email >/dev/null 2>&1 || { git config user.email "halter@localhost"; git config user.name "halter"; }\n'
        'git add -A\n'
        'git diff --cached --quiet || git commit -qm "chore: library snapshot on remote"\n'
        'B=$(mktemp /tmp/halter-bundle.XXXXXX.bundle)\n'
        'git bundle create -q "$B" HEAD && cat "$B" && rm -f "$B"\n'
    )
    lines: list[str] = [f"pull    {machine_name} -> 本机 ~/.agents"]
    if not apply:
        lines.append("plan    远端快照打 bundle 拉回本机（dry-run）")
        return lines
    rc, out, err = _run_remote_sh(machine, script)
    if rc is None or rc != 0:
        raise SystemExit(f"halter: 远端打包失败: {err.strip() or f'退出码 {rc}'}")
    ok, note = _bundle_fetch(root, out)
    if not ok:
        raise SystemExit(f"halter: {note}（本机也有新提交时先 push，或人工 merge）")
    lines.append(f"ok      {library_root()} 已更新（halter sync --apply 分发到本机各工具）")
    return lines
