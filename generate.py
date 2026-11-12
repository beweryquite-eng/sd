#!/usr/bin/env python3
"""Generates a Minecraft-style voxel CD: cd_disc.vox, cd_disc.obj/.mtl and preview.png.

Pure standard library. Axes follow MagicaVoxel: x right, y depth, z up.
The disc stands upright: y=0 is the shiny data side, y=1 is the label side.
"""
import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).parent
SIZE = (32, 2, 32)
CENTER = 15.5  # between voxels, so the disc is symmetric at an even diameter

HUB = (200, 210, 222)
HUB_RING = (132, 142, 156)
RIM = (108, 116, 128)
SILVER = [(232, 237, 243), (198, 205, 214), (166, 174, 185)]
RAINBOW = [
    (255, 110, 110), (255, 176, 92), (255, 236, 110), (126, 232, 132),
    (104, 222, 236), (112, 150, 255), (190, 128, 255),
]
LABEL = (244, 243, 236)
LABEL_BAND = (58, 110, 214)
LABEL_TEXT = (70, 74, 84)
# (z row, x ranges) of fake "text" on the label
TEXT_ROWS = {8: [(8, 13), (15, 23)], 6: [(10, 16), (18, 21)]}


def data_side(dx, dz):
    """Silver surface with two opposite rainbow reflection wedges."""
    angle = math.degrees(math.atan2(dz, dx))
    delta = (angle - 60 + 90) % 180 - 90  # offset from the nearest wedge axis
    if abs(delta) < 28:
        return RAINBOW[int((delta + 28) // 8)]
    if abs(delta) < 45:
        return SILVER[0]
    return SILVER[1] if abs(delta) < 70 else SILVER[2]


def label_side(x, z):
    if 21 <= z <= 25:
        return LABEL_BAND
    if any(a <= x <= b for a, b in TEXT_ROWS.get(z, [])):
        return LABEL_TEXT
    return LABEL


def build():
    voxels = {}
    for x in range(SIZE[0]):
        for z in range(SIZE[2]):
            dx, dz = x - CENTER, z - CENTER
            d = math.hypot(dx, dz)
            if d < 2.0 or d >= 16.0:
                continue
            if d >= 14.6:
                front = back = RIM
            elif d < 5.0:
                front = back = HUB
            elif d < 6.0:
                front = back = HUB_RING
            else:
                front, back = data_side(dx, dz), label_side(x, z)
            voxels[(x, 0, z)] = front
            voxels[(x, 1, z)] = back
    return voxels


# Normal -> quad corners, counter-clockwise when seen from outside.
FACES = {
    (1, 0, 0): [(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)],
    (-1, 0, 0): [(0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)],
    (0, 1, 0): [(0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)],
    (0, -1, 0): [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)],
    (0, 0, 1): [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)],
    (0, 0, -1): [(0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)],
}


def exposed_faces(voxels):
    """Yields (normal, corners, color) for faces not hidden by a neighbour."""
    for (x, y, z), color in voxels.items():
        for n, corners in FACES.items():
            if (x + n[0], y + n[1], z + n[2]) not in voxels:
                yield n, [(x + a, y + b, z + c) for a, b, c in corners], color


def write_vox(voxels, path):
    palette = sorted(set(voxels.values()))
    index = {c: i + 1 for i, c in enumerate(palette)}  # index 0 means empty

    def chunk(tag, content, children=b""):
        return tag + struct.pack("<ii", len(content), len(children)) + content + children

    xyzi = struct.pack("<i", len(voxels)) + b"".join(
        struct.pack("<4B", x, y, z, index[c]) for (x, y, z), c in sorted(voxels.items())
    )
    rgba = b"".join(struct.pack("<4B", *c, 255) for c in palette).ljust(1024, b"\0")
    children = chunk(b"SIZE", struct.pack("<3i", *SIZE)) + chunk(b"XYZI", xyzi) + chunk(b"RGBA", rgba)
    path.write_bytes(b"VOX " + struct.pack("<i", 150) + chunk(b"MAIN", b"", children))


def write_obj(voxels, obj_path, mtl_path):
    palette = sorted(set(voxels.values()))
    name = {c: f"color_{i:02d}" for i, c in enumerate(palette)}
    mtl_path.write_text("".join(
        f"newmtl {name[c]}\nKd {c[0] / 255:.4f} {c[1] / 255:.4f} {c[2] / 255:.4f}\n\n" for c in palette
    ))

    verts, by_color = {}, {}
    for _, corners, color in exposed_faces(voxels):
        ids = [verts.setdefault(p, len(verts) + 1) for p in corners]
        by_color.setdefault(color, []).append(ids)

    lines = [f"mtllib {mtl_path.name}", "o cd_disc"]
    # Centered at the origin and rotated to the usual Y-up convention.
    lines += [f"v {x - 16} {z - 16} {1 - y}" for x, y, z in verts]
    for color in palette:
        lines.append(f"usemtl {name[color]}")
        lines += ["f " + " ".join(map(str, ids)) for ids in by_color[color]]
    obj_path.write_text("\n".join(lines) + "\n")


def render(voxels, path, width=1200, height=640, scale=15, bg=(34, 36, 48)):
    """Orthographic painter's-algorithm render: data side on the left, label side on the right."""
    rows = [bytearray(bytes(bg) * width) for _ in range(height)]
    shade = {(0, 1, 0): 1.0, (0, -1, 0): 1.0, (0, 0, 1): 1.1, (0, 0, -1): 0.55}
    faces = list(exposed_faces(voxels))

    def fill(points, color):
        ys = [p[1] for p in points]
        span = bytes(color)
        for y in range(max(0, math.ceil(min(ys) - 0.5)), min(height, math.ceil(max(ys) - 0.5))):
            yc, xs = y + 0.5, []
            for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
                if (y1 <= yc < y2) or (y2 <= yc < y1):
                    xs.append(x1 + (yc - y1) * (x2 - x1) / (y2 - y1))
            if len(xs) >= 2:
                x0, x1 = max(0, math.ceil(min(xs) - 0.5)), min(width, math.ceil(max(xs) - 0.5))
                if x1 > x0:
                    rows[y][x0 * 3:x1 * 3] = span * (x1 - x0)

    def dot(a, b):
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

    for yaw, origin_x in ((-32, width * 0.25), (148, width * 0.75)):
        a, b = math.radians(yaw), math.radians(18)
        forward = (math.sin(a) * math.cos(b), math.cos(a) * math.cos(b), -math.sin(b))
        right = (math.cos(a), -math.sin(a), 0.0)
        up = (
            right[1] * forward[2] - right[2] * forward[1],
            right[2] * forward[0] - right[0] * forward[2],
            right[0] * forward[1] - right[1] * forward[0],
        )
        visible = []
        for n, corners, color in faces:
            if dot(n, forward) >= 0:
                continue
            pts = [(x - 16, y - 1, z - 16) for x, y, z in corners]
            depth = sum(dot(p, forward) for p in pts) / 4
            poly = [(origin_x + dot(p, right) * scale, height / 2 - dot(p, up) * scale) for p in pts]
            k = shade.get(n, 0.7)
            visible.append((depth, poly, tuple(min(255, round(c * k)) for c in color)))
        for _, poly, color in sorted(visible, key=lambda f: -f[0]):
            fill(poly, color)

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\0" + bytes(r) for r in rows)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


if __name__ == "__main__":
    model = build()
    write_vox(model, OUT / "cd_disc.vox")
    write_obj(model, OUT / "cd_disc.obj", OUT / "cd_disc.mtl")
    render(model, OUT / "preview.png")
    print(f"{len(model)} voxels, {len(set(model.values()))} colors")
