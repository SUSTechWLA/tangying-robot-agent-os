"""Turn a SLAM result into the artifacts a browser can actually load.

Scope of this module: given a point cloud and a trajectory, produce a map
directory and a manifest that ``map_manifest`` accepts and can verify. It does not
read RTAB-Map's database itself - that step is a thin adapter, deliberately kept
apart so the pipeline can be tested, and load-tested, without a robot or a
database anywhere in sight.

Why a bespoke binary format for the point cloud
-----------------------------------------------
COPC is the target (plan section D1: single file, built-in octree, HTTP Range),
but COPC means LAZ, and this environment has no LAS/LAZ tooling at all. Rather
than add a heavy dependency to the critical path, the MVP writes a header plus
raw float32 positions and uint8 colours per level: twenty-four bytes of header, no
compression, decodable in a worker with a ``DataView`` and no parser library.

The manifest is format-agnostic - it points at files and hashes them - so moving
to COPC later changes this writer and the browser's reader, and nothing else. The
file extension is what tells the client which reader to use, which is the format
adapter seam the plan calls for.

Colours are stored as uint8 rather than float32 on purpose: point colour is a
display concern and four bytes per point instead of twelve is 8 MB saved on a
million-point map.
"""

from __future__ import annotations

import hashlib
import json
import struct
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from tangying_robot_gateway.map_manifest import (
    build_manifest,
    save_manifest,
    validate_manifest,
    verify_artifacts,
)

MAGIC = b"TYPC"
FORMAT_VERSION = 1
HEADER_BYTES = 20

#: Height below which a point is treated as floor rather than obstacle.
FLOOR_HEIGHT_M = 0.15
#: Bounds of the synthetic test house, matching the commissioned home scene.
SYNTHETIC_BOUNDS = {"min": [-3.0, -2.0, -0.2], "max": [3.0, 8.0, 2.6]}


@dataclass(frozen=True)
class PointCloud:
    """Positions in metres and optional 8-bit colours."""

    xyz: np.ndarray
    rgb: np.ndarray | None = None

    def __post_init__(self) -> None:
        if self.xyz.ndim != 2 or self.xyz.shape[1] != 3:
            raise ValueError("xyz must be an (N, 3) array")
        if self.rgb is not None and self.rgb.shape != self.xyz.shape:
            raise ValueError("rgb must match xyz shape")

    @property
    def count(self) -> int:
        return int(self.xyz.shape[0])

    def bounds(self) -> dict[str, list[float]]:
        if self.count == 0:
            raise ValueError("an empty cloud has no bounds")
        return {
            "min": [float(value) for value in self.xyz.min(axis=0)],
            "max": [float(value) for value in self.xyz.max(axis=0)],
        }


def synthetic_home_cloud(point_count: int = 1_000_000, *, seed: int = 7) -> PointCloud:
    """A cloud shaped like a scanned house interior, for load testing.

    Walls, a floor, and furniture blobs, with noise and a fraction of duplicate
    points - because a real RGB-D scan has both, and a downsampler that only looks
    good on uniform random points is not evidence of anything. Deterministic for a
    given seed so a load test can be repeated exactly.
    """
    rng = np.random.default_rng(seed)
    low, high = SYNTHETIC_BOUNDS["min"], SYNTHETIC_BOUNDS["max"]

    shares = {"floor": 0.42, "walls": 0.34, "furniture": 0.18, "noise": 0.06}
    counts = {name: int(point_count * share) for name, share in shares.items()}
    counts["floor"] += point_count - sum(counts.values())

    floor = np.column_stack([
        rng.uniform(low[0], high[0], counts["floor"]),
        rng.uniform(low[1], high[1], counts["floor"]),
        np.full(counts["floor"], low[2]) + rng.normal(0, 0.01, counts["floor"]),
    ])

    # Walls: four sides plus two interior partitions, sampled thinner than the floor.
    wall_points = []
    per_wall = max(counts["walls"] // 6, 1)
    for axis, value in ((0, low[0]), (0, high[0]), (1, low[1]), (1, high[1])):
        other = rng.uniform(low[1 - axis], high[1 - axis], per_wall)
        height = rng.uniform(low[2], high[2], per_wall)
        column = np.full(per_wall, value)
        wall_points.append(np.column_stack([column, other, height]) if axis == 0
                           else np.column_stack([other, column, height]))
    for value in (low[1] + 3.0, low[1] + 6.0):
        other = rng.uniform(low[0], high[0], per_wall)
        height = rng.uniform(low[2], high[2], per_wall)
        wall_points.append(np.column_stack([other, np.full(per_wall, value), height]))
    walls = np.concatenate(wall_points)[: counts["walls"]]

    # Furniture: blocks the robot would have to walk around.
    furniture = []
    centres = rng.uniform([low[0] + 1, low[1] + 1, low[2]], [high[0] - 1, high[1] - 1, low[2] + 0.3],
                          (max(counts["furniture"] // 400, 1), 3))
    per_block = max(counts["furniture"] // max(len(centres), 1), 1)
    for centre in centres:
        furniture.append(centre + rng.uniform([-0.4, -0.4, 0], [0.4, 0.4, 0.75], (per_block, 3)))
    furniture = np.concatenate(furniture)[: counts["furniture"]]

    noise = np.column_stack([
        rng.uniform(low[0], high[0], counts["noise"]),
        rng.uniform(low[1], high[1], counts["noise"]),
        rng.uniform(low[2], high[2], counts["noise"]),
    ])

    xyz = np.concatenate([floor, walls, furniture, noise]).astype(np.float32)
    # A tenth of the points are duplicates, as a real scan produces when the robot
    # lingers. Downsampling has to cope with them.
    duplicates = xyz[rng.choice(xyz.shape[0], xyz.shape[0] // 10, replace=False)]
    xyz = np.concatenate([xyz, duplicates])

    # Height-graded colour: floor bluish, walls neutral, furniture warm.
    height = xyz[:, 2]
    rgb = np.empty((xyz.shape[0], 3), dtype=np.uint8)
    rgb[:, 0] = np.clip(120 + height * 40, 0, 255)
    rgb[:, 1] = np.clip(140 + height * 30, 0, 255)
    rgb[:, 2] = np.clip(220 - height * 50, 0, 255)
    return PointCloud(xyz=xyz, rgb=rgb)


def voxel_downsample(cloud: PointCloud, voxel_size: float) -> PointCloud:
    """One representative point per voxel.

    Real RGB-D scans repeat the same surface many times over, so the raw count
    overstates the map. Averaging inside each voxel keeps a stable surface and
    removes the duplicates; colour travels with the position.
    """
    if voxel_size <= 0:
        raise ValueError("voxel_size must be positive")
    if cloud.count == 0:
        return cloud
    keys = np.floor(cloud.xyz / voxel_size).astype(np.int64)
    _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    inverse = inverse.ravel()
    averaged = np.zeros((counts.shape[0], 3), dtype=np.float64)
    np.add.at(averaged, inverse, cloud.xyz)
    averaged /= counts[:, None]
    rgb = None
    if cloud.rgb is not None:
        summed = np.zeros((counts.shape[0], 3), dtype=np.float64)
        np.add.at(summed, inverse, cloud.rgb.astype(np.float64))
        rgb = np.clip(np.rint(summed / counts[:, None]), 0, 255).astype(np.uint8)
    return PointCloud(xyz=averaged.astype(np.float32), rgb=rgb)


def build_lod(cloud: PointCloud, *, levels: int = 5, base_voxel_m: float = 0.04) -> list[PointCloud]:
    """Coarsest first, so ``lod0`` is what a distant camera draws.

    Voxel size doubles per level, which is the cheapest octree approximation that
    still guarantees a bounded point count at every level.
    """
    if levels < 1:
        raise ValueError("levels must be at least 1")
    result: list[PointCloud] = []
    for level in range(levels):
        voxel = base_voxel_m * (2 ** (levels - 1 - level))
        result.append(voxel_downsample(cloud, voxel))
    return result


def encode_lod(cloud: PointCloud, *, level: int) -> bytes:
    """Header plus raw positions, plus colours when present."""
    header = struct.pack("<4sIIII", MAGIC, FORMAT_VERSION, cloud.count, level,
                         1 if cloud.rgb is not None else 0)
    positions = np.ascontiguousarray(cloud.xyz, dtype="<f4").tobytes()
    colours = b"" if cloud.rgb is None else np.ascontiguousarray(cloud.rgb, dtype=np.uint8).tobytes()
    return header + positions + colours


def decode_lod(payload: bytes) -> tuple[PointCloud, int]:
    """Inverse of :func:`encode_lod`, so the format is testable in-process."""
    if len(payload) < HEADER_BYTES:
        raise ValueError("truncated point cloud chunk")
    magic, version, count, level, has_colour = struct.unpack("<4sIIII", payload[:HEADER_BYTES])
    if magic != MAGIC:
        raise ValueError("not a TYPC chunk")
    if version != FORMAT_VERSION:
        raise ValueError(f"unsupported chunk version {version}")
    body = payload[HEADER_BYTES:]
    expected = count * 12 + (count * 3 if has_colour else 0)
    if len(body) != expected:
        raise ValueError(f"chunk declares {count} points but carries {len(body)} bytes")
    xyz = np.frombuffer(body[: count * 12], dtype="<f4").reshape(count, 3).astype(np.float32)
    rgb = None
    if has_colour:
        rgb = np.frombuffer(body[count * 12:], dtype=np.uint8).reshape(count, 3)
    return PointCloud(xyz=xyz, rgb=rgb), level


def occupancy_from_points(cloud: PointCloud, *, resolution: float = 0.05,
                          bounds: dict | None = None) -> dict:
    """A 2-D occupancy grid derived from the cloud.

    Nav2 publishes the authoritative grid; this is the fallback for a map that has
    only a point cloud, and it is honest about ignorance: a cell with no points at
    all stays unknown rather than being called free.
    """
    if resolution <= 0:
        raise ValueError("resolution must be positive")
    extent = bounds or cloud.bounds()
    low, high = extent["min"], extent["max"]
    width = max(int(np.ceil((high[0] - low[0]) / resolution)), 1)
    height = max(int(np.ceil((high[1] - low[1]) / resolution)), 1)

    columns = np.floor((cloud.xyz[:, 0] - low[0]) / resolution).astype(np.int64)
    rows = np.floor((cloud.xyz[:, 1] - low[1]) / resolution).astype(np.int64)
    inside = (columns >= 0) & (columns < width) & (rows >= 0) & (rows < height)
    columns, rows = columns[inside], rows[inside]
    heights = cloud.xyz[inside, 2]

    cells = np.full((height, width), -1, dtype=np.int16)
    if columns.size:
        flat = rows * width + columns
        obstacle = flat[heights > low[2] + FLOOR_HEIGHT_M]
        floor = flat[heights <= low[2] + FLOOR_HEIGHT_M]
        # Obstacles win: a cell holding both floor and wall is not somewhere to drive.
        cells.reshape(-1)[floor] = 0
        cells.reshape(-1)[obstacle] = 100
    return {
        "width": width, "height": height, "resolution": float(resolution),
        "origin": [float(low[0]), float(low[1]), 0.0],
        "cells": cells,
    }


def encode_png_gray(cells: np.ndarray) -> bytes:
    """Greyscale PNG: unknown light, free near-white, occupied dark.

    The same reading as the console's canvas - unknown must not look like free
    space - so a thumbnail and the live view agree.
    """
    height, width = cells.shape
    mapped = np.where(cells < 0, 232, np.where(cells == 0, 252, np.clip(200 - cells, 0, 200)))
    scanlines = b"".join(b"\x00" + mapped[row].astype(np.uint8).tobytes() for row in range(height))
    # The eight-byte signature is part of the format, not decoration: without it
    # every decoder rejects the file.
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + _png_chunk(b"IDAT", zlib.compress(scanlines, 6))
            + _png_chunk(b"IEND", b""))


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload)))


def trajectory_geojson(poses: list[tuple[float, float, float]], *,
                       times_unix_ms: list[int] | None = None) -> dict:
    """The driven path, with one coordinate per pose and optional timestamps."""
    coordinates = [[float(x), float(y)] for x, y, _z in poses]
    feature: dict = {
        "type": "Feature",
        "properties": {"pointCount": len(coordinates)},
        "geometry": {"type": "LineString", "coordinates": coordinates},
    }
    if times_unix_ms:
        feature["properties"]["timesUnixMs"] = [int(value) for value in times_unix_ms]
    return {"type": "FeatureCollection", "features": [feature]}


def _write(path: Path, payload: bytes, *, root: Path) -> dict:
    """Write one artifact and describe it relative to the map directory.

    The href has to be relative to the manifest's own directory: an absolute path
    would be machine-specific, and a path outside the directory is the traversal
    the manifest contract refuses.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {"href": path.relative_to(root).as_posix(),
            "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def build_map(
    output_dir: str | Path,
    *,
    map_id: str,
    robot_id: str,
    cloud: PointCloud,
    poses: list[tuple[float, float, float]] | None = None,
    times_unix_ms: list[int] | None = None,
    source: str = "rtabmap",
    mode: str = "mapping",
    lod_levels: int = 5,
    base_voxel_m: float = 0.04,
    resolution: float = 0.05,
    calibration_revision: str | None = None,
    created_at_unix_ms: int | None = None,
) -> dict:
    """Write every artifact and a manifest that verifies against them.

    Returns the manifest. The caller gets a directory that ``verify_artifacts``
    accepts, which is what makes the map safe to hand to a browser.
    """
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)

    levels = build_lod(cloud, levels=lod_levels, base_voxel_m=base_voxel_m)
    # The manifest carries one cloud artifact; the finest level is the canonical
    # entry point and the coarser ones are addressed by name from the client.
    for level, points in enumerate(levels):
        _write(directory / "cloud" / f"lod{level}.bin", encode_lod(points, level=level), root=directory)
    finest = levels[-1]

    grid = occupancy_from_points(finest, resolution=resolution)
    png = _write(directory / "grid" / "occupancy.png", encode_png_gray(grid["cells"]), root=directory)
    np.save(directory / "grid" / "occupancy.npy", grid["cells"])
    _write(directory / "grid" / "occupancy.json", json.dumps({
        "width": grid["width"], "height": grid["height"],
        "resolution": grid["resolution"], "origin": grid["origin"],
    }, ensure_ascii=False, indent=2).encode(), root=directory)

    artifacts = {"cloud": _write(directory / "cloud" / f"lod{lod_levels - 1}.bin",
                                 encode_lod(finest, level=lod_levels - 1), root=directory),
                 "grid": png}
    path = poses or []
    if path:
        trail = trajectory_geojson(path, times_unix_ms=times_unix_ms)
        artifacts["trajectory"] = _write(directory / "trajectory.geojson",
                                         json.dumps(trail, ensure_ascii=False).encode(), root=directory)

    manifest = build_manifest(
        map_id=map_id, robot_id=robot_id, source=source, mode=mode,
        artifacts=artifacts,
        bounds=finest.bounds(),
        point_count=finest.count,
        lod_levels=lod_levels,
        floors=[{"id": "ground", "zMin": finest.bounds()["min"][2] - 0.1,
                 "zMax": finest.bounds()["max"][2] + 0.1}],
        created_at_unix_ms=int(created_at_unix_ms if created_at_unix_ms is not None
                              else time.time() * 1000),
        calibration_revision=calibration_revision,
    )
    return save_manifest(directory, manifest)


def verify_map(directory: str | Path) -> list:
    """Load a built map and check every declared artifact against its hash."""
    from tangying_robot_gateway.map_manifest import load_manifest

    manifest: dict = load_manifest(directory)
    validate_manifest(manifest)
    return verify_artifacts(manifest, directory)
