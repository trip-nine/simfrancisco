#!/usr/bin/env python3
"""Bake the pixel world into api/mapdata.py (supersedes build_mapdata.py).

Equirectangular 1024x512 grid of the whole planet, horizontal wrap. Countries
are filled via PIL polygon rasterization (C-speed), US states are overlaid so
the lower 48 keep their state-border look, and every land cell gets a climate
code from latitude bands + coherent hash noise so the client can texture it.

Cell codes:
  o deep ocean   w shallow/coastal water   z polar sea ice
  g grass        f forest                  d desert
  t tundra       i ice/snow                s beach (warm coast)
  c cool coast   b border (country, or US state)

Also emits: METROS (55 US metros in grid coords), US_VIEW (CONUS bbox for the
initial camera), WRAP=True. Run: python3 scripts/build_worlddata.py
"""

from __future__ import annotations
import json
import sys
from PIL import Image, ImageDraw

W, H = 1024, 512
US_STATE_BASE = 1000
SKIP_STATES = {"Alaska", "Hawaii", "Puerto Rico"}


def gx(lon: float) -> float: return (lon + 180.0) / 360.0 * W
def gy(lat: float) -> float: return (90.0 - lat) / 180.0 * H


def hash2(x: int, y: int, salt: int = 0) -> float:
    h = (x * 374761393 + y * 668265263 + salt * 2246822519) & 0xFFFFFFFF
    h = (h ^ (h >> 13)) * 1274126177 & 0xFFFFFFFF
    return ((h ^ (h >> 16)) % 10000) / 10000.0


def noise(x: int, y: int, scale: int, salt: int) -> float:
    """Coherent-ish patch noise: bilinear blend of lattice hashes."""
    x0, y0 = x // scale, y // scale
    fx, fy = (x % scale) / scale, (y % scale) / scale
    a = hash2(x0, y0, salt); b = hash2(x0 + 1, y0, salt)
    c = hash2(x0, y0 + 1, salt); d = hash2(x0 + 1, y0 + 1, salt)
    return a * (1-fx) * (1-fy) + b * fx * (1-fy) + c * (1-fx) * fy + d * fx * fy


def polys(geom):
    return geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]


def draw_feature(dr, geom, fill):
    for poly in polys(geom):
        ext = [(gx(x), gy(y)) for x, y in poly[0]]
        if len(ext) >= 3:
            dr.polygon(ext, fill=fill)
        for hole in poly[1:]:
            pts = [(gx(x), gy(y)) for x, y in hole]
            if len(pts) >= 3:
                dr.polygon(pts, fill=0)


METROS = [
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
    # --- international (pass 2): EMEA / APAC / Americas ex-US ---
    ("London", -0.13, 51.51, "emea", 8), ("Manchester", -2.24, 53.48, "emea", 2),
    ("Dublin", -6.26, 53.35, "emea", 2), ("Paris", 2.35, 48.86, "emea", 5),
    ("Amsterdam", 4.90, 52.37, "emea", 3), ("Frankfurt", 8.68, 50.11, "emea", 4),
    ("Munich", 11.58, 48.14, "emea", 3), ("Zurich", 8.54, 47.37, "emea", 2),
    ("Stockholm", 18.07, 59.33, "emea", 2), ("Madrid", -3.70, 40.42, "emea", 2),
    ("Milan", 9.19, 45.46, "emea", 2), ("Tel Aviv", 34.78, 32.08, "emea", 3),
    ("Dubai", 55.27, 25.20, "emea", 2), ("Johannesburg", 28.05, -26.20, "emea", 1),
    ("Tokyo", 139.69, 35.68, "apac", 7), ("Osaka", 135.50, 34.69, "apac", 2),
    ("Seoul", 126.98, 37.57, "apac", 3), ("Singapore", 103.82, 1.35, "apac", 5),
    ("Sydney", 151.21, -33.87, "apac", 4), ("Melbourne", 144.96, -37.81, "apac", 2),
    ("Bangalore", 77.59, 12.97, "apac", 3), ("Mumbai", 72.88, 19.08, "apac", 2),
    ("Hong Kong", 114.17, 22.32, "apac", 2),
    ("Toronto", -79.38, 43.65, "americas_x", 5), ("Vancouver", -123.12, 49.28, "americas_x", 2),
    ("Montreal", -73.57, 45.50, "americas_x", 2), ("Mexico City", -99.13, 19.43, "americas_x", 2),
    ("São Paulo", -46.63, -23.55, "americas_x", 3), ("Santiago", -70.67, -33.45, "americas_x", 1),
    ("Bogotá", -74.07, 4.71, "americas_x", 1),
]


def main(world_path: str, states_path: str, out: str) -> None:
    owner = Image.new("I", (W, H), 0)
    dr = ImageDraw.Draw(owner)
    countries = json.load(open(world_path))["features"]
    us_idx = None
    for i, f in enumerate(countries, start=1):
        if f["properties"].get("name") == "United States of America":
            us_idx = i
        draw_feature(dr, f["geometry"], i)
    states = [f for f in json.load(open(states_path))["features"]
              if f["properties"].get("name") not in SKIP_STATES]
    for j, f in enumerate(states):
        draw_feature(dr, f["geometry"], US_STATE_BASE + j)
    o = list(owner.getdata())

    def nation(v: int) -> int:
        return us_idx if v >= US_STATE_BASE else v

    def at(x: int, y: int) -> int:
        return o[(y % H) * W + (x % W)] if 0 <= y < H else 0

    grid = bytearray(b"o" * (W * H))
    sxs, sys_, sxe, sye = W, H, 0, 0
    for y in range(H):
        lat = 90.0 - (y + 0.5) / H * 180.0
        al = abs(lat)
        for x in range(W):
            v = at(x, y)
            i = y * W + x
            if v == 0:  # water
                near_land = any(at(x+dx, y+dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1))
                if al > 67 and noise(x, y, 5, 9) > 0.42:
                    grid[i] = ord("z")
                elif near_land:
                    grid[i] = ord("w")
                continue
            if v >= US_STATE_BASE:
                sxs, sys_ = min(sxs, x), min(sys_, y)
                sxe, sye = max(sxe, x), max(sye, y)
            border = False
            coast = False
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = at(x+dx, y+dy)
                if n == 0:
                    coast = True
                elif nation(n) != nation(v) or (v >= US_STATE_BASE and n >= US_STATE_BASE and n != v):
                    border = True
            if border:
                grid[i] = ord("b")
                continue
            if al >= 70 or (al >= 60 and noise(x, y, 6, 1) > 0.5):
                grid[i] = ord("i")
            elif al >= 52:
                grid[i] = ord("t") if noise(x, y, 6, 2) > 0.35 else ord("f")
            elif coast:
                grid[i] = ord("s") if al < 42 else ord("c")
            elif 12 <= al <= 34 and noise(x, y, 8, 3) > 0.52:
                grid[i] = ord("d")
            elif noise(x, y, 6, 4) > 0.62:
                grid[i] = ord("f")
            else:
                grid[i] = ord("g")

    flat = grid.decode()
    rle, i = [], 0
    while i < len(flat):
        j = i
        while j < len(flat) and flat[j] == flat[i]:
            j += 1
        rle.append(f"{j-i}{flat[i]}")
        i = j
    rle_s = "".join(rle)

    metros = []
    for name, lon, lat, region, wgt in METROS:
        x, y = int(round(gx(lon))), int(round(gy(lat)))
        if flat[y*W+x] in "owz":
            best = min(((xx, yy) for yy in range(y-3, y+4) for xx in range(x-3, x+4)
                        if flat[(yy % H)*W + (xx % W)] not in "owz"),
                       key=lambda p: (p[0]-x)**2 + (p[1]-y)**2, default=(x, y))
            x, y = best
        metros.append({"name": name, "x": x, "y": y, "region": region, "w": wgt})

    land = sum(flat.count(ch) for ch in "gfdtiscb")
    with open(out, "w") as f:
        f.write('"""Generated by scripts/build_worlddata.py — do not hand-edit.\n'
                f'Pixel Earth: {W}x{H} equirectangular, wraps horizontally.\n'
                'Codes: o ocean, w shallow, z sea-ice, g grass, f forest, d desert,\n'
                '       t tundra, i ice, s beach, c cool coast, b border."""\n\n'
                f"W, H = {W}, {H}\nWRAP = True\n"
                f"US_VIEW = {json.dumps({'x0': sxs, 'y0': sys_, 'x1': sxe, 'y1': sye})}\n\n"
                f"RLE = \"{rle_s}\"\n\nMETROS = {json.dumps(metros)}\n\n"
                "def decode():\n"
                "    out, i = [], 0\n"
                "    while i < len(RLE):\n"
                "        j = i\n"
                "        while RLE[j].isdigit(): j += 1\n"
                "        out.append(RLE[j] * int(RLE[i:j])); i = j + 1\n"
                "    return ''.join(out)\n")
    print(f"wrote {out}: {W}x{H}, {land:,} land cells, rle {len(rle_s):,} chars, "
          f"US bbox {sxs},{sys_} -> {sxe},{sye}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/home/claude/world.geo.json",
         sys.argv[2] if len(sys.argv) > 2 else "/home/claude/us-states.json",
         sys.argv[3] if len(sys.argv) > 3 else "api/mapdata.py")
