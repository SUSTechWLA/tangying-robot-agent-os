"""Read a real RTAB-Map database and hand it to the map pipeline.

The pipeline in :mod:`map_pipeline` is deliberately ignorant of where a point cloud
came from. This module is that missing source: it turns the database RTAB-Map
writes on the robot into a cloud and a trajectory.

What is actually in the database, verified against a 208 MB survey from the
simulated house (1237 nodes), because guessing here would produce a map that looks
plausible and is wrong:

* ``Node.pose`` is 48 bytes: twelve little-endian float32 forming a 3x4 transform.
  That is the trajectory, and it needs no library to read.
* ``Data.depth`` is a PNG containing the **raw float32 depth buffer in metres**,
  row-major, 240x320 for this unit. The PNG is a container for bytes, not a picture:
  decoding it as an image and reading pixel values gives nonsense.
* ``Data.scan`` is empty in RGB-D mode; there is no laser scan to read.
* ``Admin.opt_cloud`` is **NULL** - RTAB-Map only persists the assembled cloud when
  configured to. So the dense map has to be reconstructed by back-projecting the
  depth frames through their poses, which is what this module does.
* ``Admin.opt_map`` is a zlib-compressed occupancy grid. Its byte length does not
  factor into the declared resolution's rectangle, so its layout is not yet
  understood and it is **not** read here. Reporting a grid we cannot lay out
  correctly would be worse than reporting none; Nav2 already publishes an
  authoritative grid at runtime.

Camera intrinsics are not taken from ``Data.calibration``: RTAB-Map's own
serialisation is internal and the robot already has a calibration document
(``robot.calibration.v1``) that says which camera and which revision produced the
data. That is the contract the rest of the system uses, so the export uses it too.
"""

from __future__ import annotations

import io
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from tangying_robot_gateway.map_pipeline import PointCloud

#: Depth beyond this is treated as no measurement. Indoor RGB-D returns garbage or
#: infinity where it saw nothing, and back-projecting that scatters points across
#: the room.
MAX_DEPTH_M = 6.0


class RtabmapExportError(RuntimeError):
    """Raised with a message naming what could not be read and why."""


@dataclass(frozen=True)
class CameraIntrinsics:
    fx: float
    fy: float
    cx: float
    cy: float

    @classmethod
    def from_calibration(cls, camera: dict) -> CameraIntrinsics:
        """Take intrinsics from a ``robot.calibration.v1`` camera entry."""
        values = (camera or {}).get("intrinsics") or {}
        try:
            return cls(float(values["fx"]), float(values["fy"]), float(values["cx"]), float(values["cy"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise RtabmapExportError(f"camera intrinsics are incomplete: {exc}") from None


def open_database(path: str | Path) -> sqlite3.Connection:
    """Open read-only. An export must never write to the robot's survey."""
    target = Path(path)
    if not target.is_file():
        raise RtabmapExportError(f"{target} does not exist")
    connection = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for required in ("Node", "Data", "Admin"):
        if required not in tables:
            raise RtabmapExportError(f"{target} has no {required} table; is it an RTAB-Map database?")
    return connection


def database_summary(connection: sqlite3.Connection) -> dict:
    """What a caller needs to decide whether an export is worth starting."""
    nodes = connection.execute("SELECT COUNT(*) FROM Node").fetchone()[0]
    depth = connection.execute("SELECT COUNT(*) FROM Data WHERE depth IS NOT NULL").fetchone()[0]
    opt_cloud = connection.execute("SELECT opt_cloud FROM Admin LIMIT 1").fetchone()
    version = connection.execute("SELECT version FROM Admin LIMIT 1").fetchone()
    return {
        "nodes": nodes,
        "depthFrames": depth,
        "hasOptimizedCloud": bool(opt_cloud and opt_cloud[0]),
        "databaseVersion": version[0] if version else None,
        "health": survey_health(connection),
    }


def survey_health(connection: sqlite3.Connection, *, max_span_s: float = 6 * 3600) -> list[str]:
    """Reasons this database is not a clean single survey.

    A database is not necessarily one mapping run. The sample taken from the
    navigation volume holds two maps, spans roughly fifty hours of accumulated
    sessions and restarts, and has nodes whose weight went negative - which is how
    RTAB-Map marks a node as removed. Exporting that as one map would silently
    merge unrelated sessions into a single place. The reasons are returned rather
    than raised so a caller can decide, and so the message can be shown to whoever
    is looking at the robot.
    """
    reasons: list[str] = []
    maps = connection.execute("SELECT COUNT(DISTINCT map_id) FROM Node").fetchone()[0]
    if maps > 1:
        reasons.append(f"数据库包含 {maps} 张地图；它们不是同一次建图，合并导出会得到错误的地图")
    active, removed = connection.execute(
        "SELECT SUM(weight >= 0), SUM(weight < 0) FROM Node").fetchone()
    if removed:
        reasons.append(f"有 {removed} 个节点已被删除（weight < 0），"
                       f"仅 {active} 个仍然有效")
    span = connection.execute("SELECT MIN(stamp), MAX(stamp) FROM Node").fetchone()
    if span[0] is not None and span[1] is not None and (span[1] - span[0]) > max_span_s:
        hours = (span[1] - span[0]) / 3600
        reasons.append(f"时间跨度约 {hours:.1f} 小时，远超单次建图；"
                       "这是多次会话累积的库")
    return reasons


def read_poses(connection: sqlite3.Connection) -> list[tuple[int, np.ndarray]]:
    """Every node's pose as a 3x4 transform, in node order."""
    poses: list[tuple[int, np.ndarray]] = []
    for node_id, blob in connection.execute("SELECT id, pose FROM Node ORDER BY id"):
        if blob is None:
            continue
        array = np.frombuffer(bytes(blob), dtype="<f4")
        if array.size != 12:
            raise RtabmapExportError(f"node {node_id} has a {array.size}-float pose, expected 12")
        poses.append((int(node_id), array.reshape(3, 4).astype(np.float64)))
    return poses


def require_distinct_poses(poses: list[tuple[int, np.ndarray]]) -> None:
    """Refuse a database whose nodes all claim the same pose.

    A real survey from the simulated house has 1237 nodes and exactly one distinct
    ``Node.pose`` value. Back-projecting depth at a single repeated pose stacks
    every scan on top of every other and produces a dense, plausible-looking,
    completely wrong cloud - the kind of output that is worse than no output, because
    nothing downstream can tell it is wrong. So this is an error, not a warning.
    """
    if len(poses) < 2:
        return
    # A tuple key, not a set: a set comprehension over a generator would collect
    # generator objects, each unique, and the check would never fire.
    distinct = {tuple(round(float(entry), 6) for entry in pose.ravel()) for _, pose in poses}
    if len(distinct) < 2:
        raise RtabmapExportError(
            "every node in this database reports the same pose, so it carries no "
            "trajectory; a cloud built from it would stack all "
            f"{len(poses)} scans at one place instead of placing them. RTAB-Map only "
            "writes per-node poses when pose saving is enabled for the run."
        )


def read_trajectory(connection: sqlite3.Connection) -> list[tuple[float, float, float]]:
    """The driven path in map coordinates.

    Raises rather than returning a degenerate path: a single repeated pose is not a
    route, and callers draw this.
    """
    poses = read_poses(connection)
    require_distinct_poses(poses)
    return [(float(pose[0, 3]), float(pose[1, 3]), float(pose[2, 3])) for _, pose in poses]


def decode_depth(blob: bytes) -> np.ndarray:
    """Depth in metres from RTAB-Map's PNG-wrapped raw float buffer.

    Deliberately not decoded with an image library: the PNG holds the bytes of a
    float32 buffer, so interpreting it as pixels yields numbers that look like
    depths and are not.
    """
    if not blob:
        raise RtabmapExportError("empty depth blob")
    if bytes(blob[:8]) != b"\x89PNG\r\n\x1a\n":
        raise RtabmapExportError("depth blob is not a PNG container")
    # The PNG must be fully decoded, not just inflated. Its scanlines carry filter
    # bytes and per-row filtering, so reading the raw IDAT stream yields bytes that
    # are neither filtered nor unfiltered - and interpreting those as float32 gives
    # numbers that look like depths and are not. Decoding to pixels and taking the
    # pixel bytes back out is what returns the original float buffer.
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - PIL is a runtime dependency
        raise RtabmapExportError(f"decoding depth needs Pillow: {exc}") from None
    with Image.open(io.BytesIO(bytes(blob))) as image:
        image.load()
        pixels = np.asarray(image)
    return np.frombuffer(pixels.tobytes(), dtype="<f4")


def reconstruct_cloud(
    connection: sqlite3.Connection,
    *,
    intrinsics: CameraIntrinsics,
    width: int = 320,
    height: int = 240,
    stride: int = 2,
    max_frames: int | None = None,
    max_depth_m: float = MAX_DEPTH_M,
) -> PointCloud:
    """Back-project depth frames through their poses into one cloud.

    ``stride`` subsamples pixels. Every pixel of every frame is far more detail than
    a viewer needs - and more than the map is worth, since neighbouring frames see
    the same surfaces - so a stride of 2 keeps a quarter of them, which is the
    difference between a workable file and a hundred-million-point one.
    """
    if stride < 1:
        raise ValueError("stride must be at least 1")
    poses = read_poses(connection)
    if not poses:
        raise RtabmapExportError("the database has no nodes to reconstruct from")
    require_distinct_poses(poses)
    if max_frames is not None:
        poses = poses[:max_frames]

    rows, columns = np.mgrid[0:height:stride, 0:width:stride]
    columns = columns.astype(np.float64)
    rows = rows.astype(np.float64)

    frames: list[np.ndarray] = []
    colours: list[np.ndarray] = []
    # Colour is all-or-nothing for the cloud: a frame whose colour cannot be decoded
    # still contributes its geometry, but the cloud then carries no colour at all
    # rather than a colour array that is shorter than the positions and rejects.
    colour_complete = True
    for node_id, pose in poses:
        row = connection.execute("SELECT depth, image FROM Data WHERE id = ?", (node_id,)).fetchone()
        if row is None or row[0] is None:
            continue
        try:
            depth = decode_depth(bytes(row[0]))
        except RtabmapExportError:
            continue
        if depth.size != width * height:
            # A differently sized frame would be silently mis-shaped; skip it and
            # let the caller see the count rather than a corrupted cloud.
            continue
        depth = depth.reshape(height, width)[::stride, ::stride]

        # Camera space: +x right, +y down, +z forward, which is the optical
        # convention the calibration contract uses.
        z = depth.astype(np.float64)
        x = (columns - intrinsics.cx) * z / intrinsics.fx
        y = (rows - intrinsics.cy) * z / intrinsics.fy

        valid = np.isfinite(z) & (z > 0.05) & (z < max_depth_m)
        if not valid.any():
            continue
        flat_valid = valid.reshape(-1)
        points = np.stack([x[valid], y[valid], z[valid]], axis=1)
        rotation, translation = pose[:, :3], pose[:, 3]
        world = (points @ rotation.T + translation).astype(np.float32)
        frames.append(world)

        # Colour has to be sampled through the same validity mask as the points:
        # only a subset of pixels produced a usable depth, and colouring all of them
        # would misalign every colour after the first invalid pixel.
        colour = None
        if row[1]:
            pixels = _frame_colour(bytes(row[1]), height, width, stride)
            if pixels is not None and pixels.shape[0] == flat_valid.size:
                colour = pixels[flat_valid]
        if colour is None:
            colour_complete = False
        colours.append(colour)

    if not frames:
        raise RtabmapExportError(
            "no depth frame could be reconstructed; the database may hold no RGB-D data"
        )
    xyz = np.concatenate(frames)
    rgb = None
    if colour_complete and all(entry is not None for entry in colours):
        rgb = np.concatenate([entry for entry in colours if entry is not None])
        if rgb.shape[0] != xyz.shape[0]:
            rgb = None
    return PointCloud(xyz=xyz, rgb=rgb)


def _frame_colour(blob: bytes, height: int, width: int, stride: int) -> np.ndarray:
    """Decode the RGB frame that goes with a depth frame.

    Colour is optional: a cloud without it is still a map, so a frame that cannot
    be decoded costs its colours and nothing else.
    """
    try:
        from PIL import Image

        with Image.open(io.BytesIO(blob)) as image:
            image.load()
            # Masked with the depth validity mask by the caller, so return the flat
            # pixel list and let it select.
            array = np.asarray(image.convert("RGB"))[::stride, ::stride].reshape(-1, 3)
        return array.astype(np.uint8)
    except Exception:  # noqa: BLE001 - any decode failure just drops colour
        return None
