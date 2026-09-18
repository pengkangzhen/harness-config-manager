#!/bin/sh
# 打包 halter 为 PyInstaller onefile sidecar，供 Tauri bundle 携带。
# Tauri externalBin 约定：binaries/halter-<target-triple>
set -eu
cd "$(dirname "$0")/.."

OUT="desktop/src-tauri/binaries"
mkdir -p "$OUT"
TRIPLE="$(rustc -vV 2>/dev/null | sed -n 's/^host: //p')"
[ -n "$TRIPLE" ] || TRIPLE="aarch64-apple-darwin"
FINAL="$OUT/halter-$TRIPLE"

# 跳过条件：产物存在且不比源码旧（FORCE_SIDECAR=1 强制重打）
if [ -f "$FINAL" ] && [ -z "${FORCE_SIDECAR:-}" ]; then
    newer=$(find src desktop/pyinstaller_entry.py -name '*.py' -newer "$FINAL" 2>/dev/null | head -1 || true)
    if [ -z "$newer" ]; then
        echo "sidecar up-to-date: $FINAL"
        exit 0
    fi
fi

echo "building PyInstaller sidecar -> $FINAL"
PYINSTALLER_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-/tmp/pyi-config}" \
UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uvcache}" \
uv run pyinstaller \
    --noconfirm --onefile --name halter \
    --distpath "$OUT" \
    --workpath /tmp/halter-build \
    --specpath /tmp/halter-build \
    --hidden-import websockets --hidden-import aiohttp \
    desktop/pyinstaller_entry.py
mv "$OUT/halter" "$FINAL"
echo "sidecar built: $FINAL"
