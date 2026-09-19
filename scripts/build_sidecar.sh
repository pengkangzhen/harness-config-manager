#!/bin/sh
# Build the halter PyInstaller onefile sidecar for Tauri.
# Tauri externalBin convention: binaries/halter-<target-triple>.
# Set SIDECAR_TARGET (or TAURI_TARGET) when cross-building with --target.
set -eu
cd "$(dirname "$0")/.."

OUT="desktop/src-tauri/binaries"
mkdir -p "$OUT"

TRIPLE="${SIDECAR_TARGET:-${TAURI_TARGET:-}}"
if [ -z "$TRIPLE" ]; then
    if ! command -v rustc >/dev/null 2>&1; then
        echo "build_sidecar: rustc is required to detect the Tauri target triple" >&2
        exit 1
    fi
    TRIPLE="$(rustc -vV | sed -n 's/^host: //p')"
fi
if [ -z "$TRIPLE" ]; then
    echo "build_sidecar: unable to determine target triple" >&2
    exit 1
fi
BIN_NAME="halter"
case "$TRIPLE" in
    *windows-msvc|*windows-gnu) FINAL="$OUT/halter-$TRIPLE.exe"; BIN_NAME="halter.exe" ;;
    *) FINAL="$OUT/halter-$TRIPLE" ;;
esac
HASH="$FINAL.sha256"

# Git Bash 没有 shasum（perl 脚本）时用 coreutils 的 sha256sum。
SHA256_CMD="shasum -a 256"
command -v sha256sum >/dev/null 2>&1 && SHA256_CMD="sha256sum"

# Hash all build inputs instead of relying on mtimes. Include the lock files and
# this script so dependency/tooling changes always invalidate the old artifact.
new_hash="$(
    find src pyproject.toml uv.lock desktop/pyinstaller_entry.py scripts/build_sidecar.sh \
        -type f -print0 2>/dev/null |
    LC_ALL=C sort -z |
    xargs -0 $SHA256_CMD |
    $SHA256_CMD |
    sed 's/ .*$//'
)"
if [ -f "$FINAL" ] && [ -f "$HASH" ] && [ -z "${FORCE_SIDECAR:-}" ]; then
    old_hash="$(cat "$HASH" 2>/dev/null || true)"
    if [ "$new_hash" = "$old_hash" ]; then
        echo "sidecar up-to-date: $FINAL"
        exit 0
    fi
fi

echo "building PyInstaller sidecar -> $FINAL"
WORK="$OUT/.build.$$"
PYI_CACHE="$OUT/.pyi-cache"
mkdir -p "$WORK" "$PYI_CACHE"
cleanup() {
    rm -rf "$WORK"
}
trap cleanup EXIT HUP INT TERM

PYINSTALLER_CONFIG_DIR="$PYI_CACHE" \
UV_CACHE_DIR="${UV_CACHE_DIR:-$OUT/.uv-cache}" \
uv run pyinstaller \
    --noconfirm --onefile --name halter \
    --distpath "$WORK/dist" \
    --workpath "$WORK/build" \
    --specpath "$WORK" \
    --copy-metadata harness-config-manager \
    desktop/pyinstaller_entry.py

# Move through a private temporary name, then record the hash only after the
# final artifact is in place.
cp "$WORK/dist/$BIN_NAME" "$FINAL.new.$$"
chmod 755 "$FINAL.new.$$"
mv "$FINAL.new.$$" "$FINAL"
printf '%s\n' "$new_hash" > "$HASH"
echo "sidecar built: $FINAL"
