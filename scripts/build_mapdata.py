#!/usr/bin/env python3
"""Bake the pixel-art USA into api/mapdata.py.

Rasterizes the lower-48 states from a public GeoJSON onto a coarse grid
using an Albers equal-area conic projection (the classic "USA map" look),
marks state-border and coastline cells, projects ~56 metro areas into grid
coordinates, and writes everything as an importable module so the deployed
function needs zero geo dependencies and zero network at runtime.

Run from repo root:  python3 scripts/build_mapdata.py path/to/us-states.json
Cell codes in GRID (row-major, RLE "<count><char>"):
  w water   l land   b state border   c coast
"""

from __future__ import annotations
import json
import math
import sys

W, H = 336, 210
SKIP = {"Alaska", "Hawaii", "Puerto Rico"}

# Albers equal-area conic, standard USA parameters (d3.geoAlbers defaults).
_P1, _P2 = math.radians(29.5), math.radians(45.5)
_LON0, _LAT0 = math.radians(-96), math.radians(37.5)
_N = (math.sin(_P1) + math.sin(_P2)) / 2
_C = math.cos(_P1) ** 2 + 2 * _N * math.sin(_P1)
_R0 = math.sqrt(_C - 2 * _N * math.sin(_LAT0)) / _N


def albers(lon: float, lat: float) -> tuple[float, float]:
    lam, phi = math.radians(lon), math.radians(lat)
    rho = math.sqrt(_C - 2 * _N * math.sin(phi)) / _N
    th = _N * (lam - _LON0)
    return rho * math.sin(th), _R0 - rho * math.cos(th)


def _rings(geom) -> list[list[tuple[float, float]]]:
    if geom["type"] == "Polygon":
        return geom["coordinates"]
    return [r for poly in geom["coordinates"] for r in poly]


def _pip(x: float, y: float, ring) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


METROS = [  # name, lon, lat, region, weight (rough metro-size prior)
    ("New York", -74.0, 40.7, "northeast", 10), ("Boston", -71.06, 42.36, "northeast", 5),
    ("Philadelphia", -75.16, 39.95, "northeast", 5), ("Pittsburgh", -79.99, 40.44, "northeast", 3),
    ("Hartford", -72.68, 41.76, "northeast", 2), ("Buffalo", -78.87, 42.89, "northeast", 2),
    ("Newark", -74.17, 40.73, "northeast", 3), ("Baltimore", -76.61, 39.29, "northeast", 3),
    ("Washington DC", -77.04, 38.9, "southeast", 7), ("Atlanta", -84.39, 33.75, "southeast", 7),
    ("Miami", -80.19, 25.76, "southeast", 5), ("Tampa", -82.46, 27.95, "southeast", 4),
    ("Orlando", -81.38, 28.54, "southeast", 3), ("Charlotte", -80.84, 35.23, "southeast", 4),
    ("Raleigh", -78.64, 35.78, "southeast", 3), ("Nashville", -86.78, 36.16, "southeast", 3),
    ("Richmond", -77.44, 37.54, "southeast", 2), ("Jacksonville", -81.66, 30.33, "southeast", 2),
    ("Birmingham", -86.8, 33.52, "southeast", 2), ("New Orleans", -90.07, 29.95, "southeast", 2),
    ("Memphis", -90.05, 35.15, "southeast", 2), ("Louisville", -85.76, 38.25, "southeast", 2),
    ("Chicago", -87.63, 41.88, "midwest", 8), ("Minneapolis", -93.27, 44.98, "midwest", 4),
    ("Detroit", -83.05, 42.33, "midwest", 4), ("Columbus", -83.0, 39.96, "midwest", 3),
    ("Cleveland", -81.69, 41.5, "midwest", 3), ("Indianapolis", -86.16, 39.77, "midwest", 3),
    ("Cincinnati", -84.51, 39.1, "midwest", 3), ("Kansas City", -94.58, 39.1, "midwest", 3),
    ("St. Louis", -90.2, 38.63, "midwest", 3), ("Milwaukee", -87.91, 43.04, "midwest", 2),
    ("Omaha", -95.93, 41.26, "midwest", 2), ("Des Moines", -93.62, 41.59, "midwest", 1),
    ("Dallas", -96.8, 32.78, "southwest", 7), ("Houston", -95.37, 29.76, "southwest", 6),
    ("Austin", -97.74, 30.27, "southwest", 5), ("San Antonio", -98.49, 29.42, "southwest", 3),
    ("Phoenix", -112.07, 33.45, "southwest", 5), ("Tucson", -110.97, 32.22, "southwest", 1),
    ("Albuquerque", -106.65, 35.08, "southwest", 1), ("Oklahoma City", -97.52, 35.47, "southwest", 2),
    ("Tulsa", -95.99, 36.15, "southwest", 1), ("El Paso", -106.49, 31.76, "southwest", 1),
    ("San Francisco", -122.42, 37.77, "west", 7), ("San Jose", -121.89, 37.34, "west", 5),
    ("Los Angeles", -118.24, 34.05, "west", 8), ("San Diego", -117.16, 32.72, "west", 4),
    ("Seattle", -122.33, 47.61, "west", 6), ("Portland", -122.68, 45.52, "west", 3),
    ("Denver", -104.99, 39.74, "west", 5), ("Salt Lake City", -111.89, 40.76, "west", 3),
    ("Las Vegas", -115.14, 36.17, "west", 2), ("Boise", -116.2, 43.62, "west", 1),
    ("Sacramento", -121.49, 38.58, "west", 2),
]


def main(path: str, out: str) -> None:
    feats = [f for f in json.load(open(path))["features"]
             if f["properties"].get("name") not in SKIP]
    states = []
    for i, f in enumerate(feats):
        rings = [[albers(x, y) for x, y in ring] for ring in _rings(f["geometry"])]
        xs = [p[0] for r in rings for p in r]
        ys = [p[1] for r in rings for p in r]
        states.append((i, rings, (min(xs), min(ys), max(xs), max(ys))))

    gx0 = min(s[2][0] for s in states)
    gx1 = max(s[2][2] for s in states)
    gy0 = min(s[2][1] for s in states)
    gy1 = max(s[2][3] for s in states)
    pad = 0.03 * (gx1 - gx0)
    gx0, gx1, gy0, gy1 = gx0 - pad, gx1 + pad, gy0 - pad, gy1 + pad
    sx = (W - 1) / (gx1 - gx0)
    sy = (H - 1) / (gy1 - gy0)
    s = min(sx, sy)  # uniform scale, y flipped (grid y grows downward)

    def to_grid(px: float, py: float) -> tuple[float, float]:
        return (px - gx0) * s, (gy1 - py) * s

    owner = [[-1] * W for _ in range(H)]
    for sid, rings, (bx0, by0, bx1, by1) in states:
        cx0 = max(0, int((bx0 - gx0) * s) - 1)
        cx1 = min(W - 1, int((bx1 - gx0) * s) + 1)
        cy0 = max(0, int((gy1 - by1) * s) - 1)
        cy1 = min(H - 1, int((gy1 - by0) * s) + 1)
        for gy in range(cy0, cy1 + 1):
            py = gy1 - (gy + 0.5) / s
            for gx in range(cx0, cx1 + 1):
                if owner[gy][gx] != -1:
                    continue
                px = gx0 + (gx + 0.5) / s
                hits = sum(1 for ring in rings if _pip(px, py, ring))
                if hits % 2:
                    owner[gy][gx] = sid

    grid = [["w"] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            o = owner[y][x]
            if o == -1:
                continue
            edge_state = edge_water = False
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                n = owner[ny][nx] if 0 <= nx < W and 0 <= ny < H else -1
                if n == -1:
                    edge_water = True
                elif n != o:
                    edge_state = True
            grid[y][x] = "c" if edge_water else ("b" if edge_state else "l")

    flat = "".join("".join(r) for r in grid)
    rle, i = [], 0
    while i < len(flat):
        j = i
        while j < len(flat) and flat[j] == flat[i]:
            j += 1
        rle.append(f"{j - i}{flat[i]}")
        i = j
    rle_s = "".join(rle)

    metros = []
    for name, lon, lat, region, wgt in METROS:
        mx, my = to_grid(*albers(lon, lat))
        gx, gy = int(round(mx)), int(round(my))
        # nudge coastal metros onto the nearest land cell
        if grid[gy][gx] == "w":
            best = min(((x, y) for y in range(max(0, gy - 4), min(H, gy + 5))
                        for x in range(max(0, gx - 4), min(W, gx + 5))
                        if grid[y][x] != "w"),
                       key=lambda p: (p[0] - gx) ** 2 + (p[1] - gy) ** 2, default=(gx, gy))
            gx, gy = best
        metros.append({"name": name, "x": gx, "y": gy, "region": region, "w": wgt})

    land = flat.count("l") + flat.count("b") + flat.count("c")
    with open(out, "w") as f:
        f.write('"""Generated by scripts/build_mapdata.py — do not hand-edit.\n'
                f'Pixel USA: {W}x{H} Albers grid, {land} land cells, {len(metros)} metros."""\n\n'
                f"W, H = {W}, {H}\n\nRLE = \"{rle_s}\"\n\nMETROS = {json.dumps(metros)}\n\n"
                "def decode():\n"
                "    out, i = [], 0\n"
                "    while i < len(RLE):\n"
                "        j = i\n"
                "        while RLE[j].isdigit(): j += 1\n"
                "        out.append(RLE[j] * int(RLE[i:j])); i = j + 1\n"
                "    return ''.join(out)\n")
    print(f"wrote {out}: {W}x{H}, {land} land cells, rle {len(rle_s):,} chars, {len(metros)} metros")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "us-states.json",
         sys.argv[2] if len(sys.argv) > 2 else "api/mapdata.py")
