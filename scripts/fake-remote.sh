#!/usr/bin/env bash
# 假远端机器：用本机 sshd + HOME 隔离 wrapper 模拟一台装有 halter 的远程机器，
# 供探索式测试与 e2e 演练 push/pull/scan --machine 等跨机链路，绝不触碰真实配置。
#
# 依赖：本机 sshd 监听 127.0.0.1:2222 且可免密登录自己（BatchMode）。
# 远端 HOME 隔离原理：machines 的 halter_path 指向 wrapper 脚本，wrapper 把
# HOME 重定向到 $FAKE_HOME/.fake-remote/home 后 exec 真 halter。
#
# 用法：
#   scripts/fake-remote.sh setup   <fake-home>   搭建远端环境并注册机器 fake-remote
#   scripts/fake-remote.sh verify  <fake-home>   冒烟：list + push + pull 往返
#   scripts/fake-remote.sh teardown <fake-home>  删除远端环境（fake-home 一并删除）
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
HALTER=$REPO/.venv/bin/halter
CMD=${1:-}
FAKE_HOME=${2:-}
REMOTE_ROOT=$FAKE_HOME/.fake-remote
REMOTE_HOME=$REMOTE_ROOT/home
WRAPPER=$REMOTE_ROOT/bin/halter
SSH_PORT=${FAKE_REMOTE_PORT:-2222}

die() { echo "fake-remote: $*" >&2; exit 1; }

[ -n "$FAKE_HOME" ] || die "用法: $0 setup|verify|teardown <fake-home>"
[ -x "$HALTER" ] || die "halter 不存在: $HALTER（先 uv sync）"

h() { HOME=$FAKE_HOME "$HALTER" "$@"; }

case "$CMD" in
  setup)
    mkdir -p "$REMOTE_ROOT/bin" "$REMOTE_HOME/.agents/skills"
    printf '#!/bin/sh\nHOME=%q exec %q "$@"\n' "$REMOTE_HOME" "$HALTER" > "$WRAPPER"
    chmod +x "$WRAPPER"
    # host key：ssh 读真实 ~/.ssh/known_hosts（不受 HOME 影响），缺失则收录
    ssh-keyscan -p "$SSH_PORT" 127.0.0.1 >/dev/null 2>&1 || true
    h machines add fake-remote --host 127.0.0.1 --port "$SSH_PORT" \
      --halter-path "$WRAPPER"
    echo "fake-remote 就绪：$FAKE_HOME（远端隔离于 $REMOTE_HOME）"
    ;;
  verify)
    h machines list
    mkdir -p "$FAKE_HOME/.agents/skills/verify-skill"
    printf -- '---\nname: verify-skill\ndescription: fake-remote roundtrip\n---\n# v\n' \
      > "$FAKE_HOME/.agents/skills/verify-skill/SKILL.md"
    h push skills verify-skill --to fake-remote --apply
    test -f "$REMOTE_HOME/.agents/skills/verify-skill/SKILL.md" \
      || die "push 后远端没有落盘"
    rm -rf "$FAKE_HOME/.agents/skills/verify-skill"
    h pull skills verify-skill --from fake-remote --apply
    test -f "$FAKE_HOME/.agents/skills/verify-skill/SKILL.md" \
      || die "pull 后本机没有落盘"
    echo "fake-remote 往返验证通过"
    ;;
  teardown)
    rm -rf "$FAKE_HOME"
    echo "已清理 $FAKE_HOME"
    ;;
  *) die "未知命令: $CMD（setup|verify|teardown）" ;;
esac
