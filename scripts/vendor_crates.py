#!/usr/bin/env python3
"""Vendor every Cargo.lock dependency into desktop/src-tauri/vendor.

Workaround for environments where cargo's built-in HTTP stack cannot
transfer (statically linked libcurl vs this WSL network): download each
.crate with curl (system stack works), unpack, and emit the layout cargo's
directory source expects (including .cargo-checksum.json).

Usage: python3 scripts/vendor_crates.py [--dl URL_PREFIX]
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "desktop/src-tauri/Cargo.lock"
VENDOR = ROOT / "desktop/src-tauri/vendor"
CACHE = Path("/tmp/halter-crate-cache")
DEFAULT_DL = "https://mirrors.ustc.edu.cn/crates.io/crates"
# The desktop crate itself lives in this workspace and must not be vendored.
WORKSPACE_PACKAGES = {"halter-desktop"}


def parse_lock() -> list[tuple[str, str, str]]:
    packages: list[tuple[str, str, str]] = []
    name = version = checksum = None
    for raw in LOCK.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line == "[[package]]":
            if name and version and name not in WORKSPACE_PACKAGES:
                packages.append((name, version, checksum or ""))
            name = version = checksum = None
        elif line.startswith("name = "):
            name = line.split(" = ", 1)[1].strip('"')
        elif line.startswith("version = "):
            version = line.split(" = ", 1)[1].strip('"')
        elif line.startswith("checksum = "):
            checksum = line.split(" = ", 1)[1].strip('"').removeprefix("sha256:")
    if name and version and name not in WORKSPACE_PACKAGES:
        packages.append((name, version, checksum or ""))
    return packages


def download(name: str, version: str, dl: str) -> Path:
    crate = CACHE / f"{name}-{version}.crate"
    if crate.is_file() and crate.stat().st_size > 0:
        return crate
    url = f"{dl}/{name}/{name}-{version}.crate"
    proc = subprocess.run(
        ["curl", "-sSL", "--retry", "3", "--max-time", "60", "-o", str(crate), url],
        check=False,
    )
    if proc.returncode != 0 or not crate.is_file() or crate.stat().st_size == 0:
        raise RuntimeError(f"download failed: {url}")
    return crate


def vendored_dir(name: str, version: str) -> Path:
    # cargo's directory source expects the flat `name-version` layout
    return VENDOR / f"{name}-{version}"


def unpack(name: str, version: str, crate: Path, checksum: str) -> None:
    target = vendored_dir(name, version)
    if (target / ".cargo-checksum.json").is_file():
        return
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    with tarfile.open(crate, "r:gz") as archive:
        members = [m for m in archive.getmembers() if m.name != "cargo-lock"]
        # strip the {name}-{version}/ prefix
        for member in members:
            stripped = member.name.split("/", 1)
            member.name = stripped[1] if len(stripped) > 1 else stripped[0]
            if member.name:
                archive.extract(member, target)
    files: dict[str, str] = {}
    for path in sorted(target.rglob("*")):
        if path.is_file():
            files[str(path.relative_to(target))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    (target / ".cargo-checksum.json").write_text(
        json.dumps({"files": files, "package": checksum}, separators=(",", ":")),
        encoding="utf-8",
    )


def worker(item: tuple[str, str, str], dl: str) -> str:
    name, version, checksum = item
    crate = download(name, version, dl)
    unpack(name, version, crate, checksum)
    return name


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dl", default=DEFAULT_DL)
    args = parser.parse_args()
    packages = parse_lock()
    CACHE.mkdir(parents=True, exist_ok=True)
    VENDOR.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for result in concurrent.futures.as_completed([
            pool.submit(worker, item, args.dl) for item in packages
        ]):
            try:
                result.result()
            except Exception as exc:  # noqa: BLE001
                failures.append(str(exc))
    print(f"vendored {len(packages) - len(failures)}/{len(packages)} crates")
    for failure in failures:
        print(f"FAILED {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
