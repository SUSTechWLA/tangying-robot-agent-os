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
