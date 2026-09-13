"""Nav2 map-server artifacts, independent of presentation colours and LOD."""

import json
import math

import numpy as np

MAX_GRID_CELLS = 4_000_000


def validate_grid(grid):
    width, height = grid.get("width"), grid.get("height")
    resolution = grid.get("resolution")
    origin = grid.get("origin")
    if (type(width) is not int or type(height) is not int or min(width, height) <= 0
            or width * height > MAX_GRID_CELLS):
        raise ValueError("occupancy grid dimensions exceed the cell budget")
    if type(resolution) not in (int, float) or not math.isfinite(resolution) or resolution <= 0:
        raise ValueError("occupancy resolution must be positive and finite")
    if (not isinstance(origin, (list, tuple)) or len(origin) != 3
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in origin)):
        raise ValueError("occupancy origin must contain finite map x, y, yaw")
    cells = np.asarray(grid.get("cells"))
    if cells.shape != (height, width) or cells.dtype.kind not in "iu" or np.any((cells < -1) | (cells > 100)):
        raise ValueError("occupancy cells must be integers in [-1,100] with matching dimensions")
    return {"width": width, "height": height, "resolution": float(resolution),
            "origin": list(origin), "cells": cells.astype(np.int16, copy=True)}


def nav2_artifacts(grid):
    """Return PGM and YAML (JSON is valid YAML) for Nav2's trinary loader.

    Only cell 0 is accepted as certainly free; uncertain probabilities remain
    unknown. Image rows run top-down, OccupancyGrid rows bottom-up.
    """
    grid = validate_grid(grid)
    cells = grid["cells"]
    pixels = np.where(cells == 0, 254, np.where(cells >= 65, 0, 205)).astype(np.uint8)
    pgm = f"P5\n{grid['width']} {grid['height']}\n255\n".encode() + np.flipud(pixels).tobytes()
    yaml = json.dumps({"image": "map.pgm", "mode": "trinary", "resolution": grid["resolution"],
                       "origin": grid["origin"], "negate": 0,
                       "occupied_thresh": .65, "free_thresh": .196}, indent=2).encode()
    return pgm, yaml


def read_nav2_grid(pgm, metadata):
    """Inverse of :func:`nav2_artifacts`, so a published map can be re-read.

    Only the three trinary states survive the round trip. That is deliberate:
    a stored map is evidence about free, occupied and unknown space, and the
    exact occupancy probability was never measured anyway.
    """
    try:
        document = json.loads(metadata.decode("utf-8") if isinstance(metadata, (bytes, bytearray))
                              else metadata)
        resolution = float(document["resolution"])
        origin = [float(value) for value in document["origin"]]
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("navigation metadata is not a usable nav2 map description") from exc
    payload = bytes(pgm)
    if not payload.startswith(b"P5"):
        raise ValueError("navigation grid is not a binary PGM")
    fields, offset = [], 0
    while len(fields) < 4:
        end = payload.find(b"\n", offset)
        if end < 0:
            raise ValueError("navigation grid header is truncated")
        fields.extend(payload[offset:end].split())
        offset = end + 1
    try:
        width, height, maximum = (int(fields[1]), int(fields[2]), int(fields[3]))
    except (IndexError, ValueError) as exc:
        raise ValueError("navigation grid header is malformed") from exc
    if maximum != 255 or min(width, height) <= 0:
        raise ValueError("navigation grid header declares an unsupported image")
    body = payload[offset:]
    if len(body) != width * height:
        raise ValueError(f"navigation grid declares {width}x{height} but carries {len(body)} bytes")
    image = np.frombuffer(body, dtype=np.uint8).reshape(height, width)
    # PGM rows run top-down and OccupancyGrid rows bottom-up; the encoder flips,
    # so the reader flips back rather than relying on a coincidental symmetry.
    image = np.flipud(image)
    cells = np.where(image >= 250, 0, np.where(image <= 55, 100, -1)).astype(np.int16)
    return validate_grid({"width": width, "height": height, "resolution": resolution,
                          "origin": origin, "cells": cells})


def merge_grids(base, extra):
    """Paint ``extra``'s evidence into ``base``'s frame, returning their union.

    Free space is the union and obstacles are the union, because each grid is
    evidence: a cell one survey drove through is free, and a cell one survey saw
    a wall in is occupied, whatever the other survey managed to observe. Only a
    cell neither survey knows anything about stays unknown.

    This is what makes a continuation usable rather than merely larger. The
    cloud union alone is not enough: a survey's free corridors come partly from
    its own verified travel, so a continuation that kept only the new session's
    clearance would hand back a map whose old rooms became unknown and whose
    navigation planner could no longer route through them.
    """
    base, extra = validate_grid(base), validate_grid(extra)
    resolution = base["resolution"]
    if abs(extra["resolution"] - resolution) > 1e-9:
        raise ValueError("occupancy grids must share a resolution to be merged")
    low = [min(base["origin"][i], extra["origin"][i]) for i in range(2)]
    high = [max(base["origin"][i] + base["cells"].shape[1 - i] * resolution,
                extra["origin"][i] + extra["cells"].shape[1 - i] * resolution) for i in range(2)]
    width = max(math.ceil((high[0] - low[0]) / resolution), 1)
    height = max(math.ceil((high[1] - low[1]) / resolution), 1)
    if width * height > MAX_GRID_CELLS:
        raise ValueError("merged occupancy grid exceeds the cell budget")
    cells = np.full((height, width), -1, dtype=np.int16)
    for grid in (base, extra):
        column = round((grid["origin"][0] - low[0]) / resolution)
        row = round((grid["origin"][1] - low[1]) / resolution)
        if column < 0 or row < 0:
            raise ValueError("occupancy grid origin is not on the merged lattice")
        source = grid["cells"]
        window = cells[row:row + source.shape[0], column:column + source.shape[1]]
        np.maximum(window, source, out=window)
    return validate_grid({"width": width, "height": height, "resolution": resolution,
                          "origin": [low[0], low[1], base["origin"][2]], "cells": cells})
