#!/usr/bin/env python3
"""Generate Halter desktop app icons (PNG / ICNS / ICO) with no third-party deps.

Renders a rounded-square "config distribution" mark — three nodes linked by
lines — at 4x supersampling, then packages the PNGs into macOS .icns and
Windows .ico containers (both accept PNG-encoded entries).
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "desktop" / "src-tauri" / "icons"
SS = 4  # supersampling factor


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def rounded_rect_sdf(px: float, py: float, size: float, radius: float) -> float:
    cx, cy = size / 2, size / 2
    dx, dy = abs(px - cx), abs(py - cy)
    ox, oy = max(dx - (size / 2 - radius), 0), max(dy - (size / 2 - radius), 0)
    return (ox * ox + oy * oy) ** 0.5 - radius


def circle_sdf(px: float, py: float, cx: float, cy: float, r: float) -> float:
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5 - r


def segment_sdf(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    abx, aby = bx - ax, by - ay
    t = clamp(((px - ax) * abx + (py - ay) * aby) / (abx * abx + aby * aby))
    dx, dy = px - (ax + abx * t), py - (ay + aby * t)
    return (dx * dx + dy * dy) ** 0.5


def lerp(a: float, b: float, t: float) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore[return-value]


def render(size: int) -> bytes:
    """Render the icon at `size` px and return PNG bytes."""
    big = size * SS
    bg_top, bg_bot = (18, 27, 51), (9, 14, 30)
    border = (76, 194, 255)
    nodes = [
        (0.28, 0.30, 0.075, (76, 194, 255)),   # cyan
        (0.72, 0.38, 0.062, (167, 139, 250)),  # violet
        (0.46, 0.72, 0.062, (255, 198, 92)),   # amber
    ]
    links = [(0, 1), (0, 2), (1, 2)]
    link_w = size * 0.030
    border_w = size * 0.012
    radius = size * 0.225

    rows: list[bytearray] = []
    for yy in range(size):
        row = bytearray()
        for xx in range(size):
            # supersample
            r = g = b = a = 0.0
            for sy in range(SS):
                for sx in range(SS):
                    px = xx * SS + sx + 0.5
                    py = yy * SS + sy + 0.5
                    base = lerp(bg_top, bg_bot, clamp(py / big))
                    rr, gg, bb = float(base[0]), float(base[1]), float(base[2])
                    # card shape (rounded square)
                    d_card = rounded_rect_sdf(px, py, big, radius * SS)
                    alpha = clamp(0.5 - d_card)
                    if alpha <= 0:
                        continue
                    # thin border glow
                    edge = clamp(0.5 - abs(d_card) / (border_w * SS))
                    rr = rr * (1 - edge * 0.55) + border[0] * edge * 0.55
                    gg = gg * (1 - edge * 0.55) + border[1] * edge * 0.55
                    bb = bb * (1 - edge * 0.55) + border[2] * edge * 0.55
                    # links
                    for i, j in links:
                        ax, ay = nodes[i][0] * big, nodes[i][1] * big
                        bx, by = nodes[j][0] * big, nodes[j][1] * big
                        d = segment_sdf(px, py, ax, ay, bx, by)
                        cov = clamp(0.5 - d / (link_w * SS)) * 0.45
                        rr = rr * (1 - cov) + 150 * cov
                        gg = gg * (1 - cov) + 170 * cov
                        bb = bb * (1 - cov) + 210 * cov
                    # nodes
                    for nx, ny, nr, color in nodes:
                        d = circle_sdf(px, py, nx * big, ny * big, nr * size * SS)
                        cov = clamp(0.5 - d)
                        if cov > 0:
                            rr = rr * (1 - cov) + color[0] * cov
                            gg = gg * (1 - cov) + color[1] * cov
                            bb = bb * (1 - cov) + color[2] * cov
                    r += rr * alpha
                    g += gg * alpha
                    b += bb * alpha
                    a += alpha
            n = SS * SS
            row += bytes((int(clamp(r / n, 0, 255)), int(clamp(g / n, 0, 255)), int(clamp(b / n, 0, 255)), int(clamp(a / n * 255))))
        rows.append(row)

    # PNG (color type 6 = RGBA, 8-bit)
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def build_icns(entries: list[tuple[bytes, bytes]]) -> bytes:
    body = b""
    for typ, png in entries:
        body += typ + struct.pack(">I", 8 + len(png)) + png
    return b"icns" + struct.pack(">I", 8 + len(body)) + body


def build_ico(png: bytes, size: int) -> bytes:
    # one PNG-encoded entry (valid for Vista+)
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), 6 + 16)
    return header + entry + png


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pngs = {
        32: render(32),
        128: render(128),
        256: render(256),
        512: render(512),
    }
    (OUT / "32x32.png").write_bytes(pngs[32])
    (OUT / "128x128.png").write_bytes(pngs[128])
    (OUT / "128x128@2x.png").write_bytes(pngs[256])
    (OUT / "icon.png").write_bytes(pngs[512])
    (OUT / "icon.icns").write_bytes(
        build_icns([
            (b"ic07", pngs[128]),
            (b"ic08", pngs[256]),
            (b"ic09", pngs[512]),
        ])
    )
    (OUT / "icon.ico").write_bytes(build_ico(pngs[256], 256))
    for f in sorted(OUT.iterdir()):
        print(f"{f.name:20s} {f.stat().st_size:>8,d} bytes")


if __name__ == "__main__":
    main()
