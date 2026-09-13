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

    def __post_init__(self):
        if not np.isfinite([self.fx, self.fy, self.cx, self.cy]).all() or min(self.fx, self.fy) <= 0:
            raise RtabmapExportError("camera intrinsics must be finite with positive focal lengths")

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


def base_from_camera(camera: dict) -> np.ndarray:
    """Calibrated optical -> base transform; articulated mounts need per-node TF."""
    extrinsics = camera.get("extrinsics", {})
    if extrinsics.get("parentLink") != "base_link":
        raise RtabmapExportError("depth export requires base_link extrinsics or per-node mount transforms")
    if any(camera.get("distortion", {}).get("coefficients", ())):
        raise RtabmapExportError("depth export requires rectified images and matching intrinsics")
    try:
        roll, pitch, yaw = extrinsics["rpy"]
        cr, cp, cy = np.cos([roll, pitch, yaw])
        sr, sp, sy = np.sin([roll, pitch, yaw])
        transform = np.eye(4)
        transform[:3, :3] = [[cy*cp, cy*sp*sr-sy*cr, cy*sp*cr+sy*sr],
                             [sy*cp, sy*sp*sr+cy*cr, sy*sp*cr-cy*sr],
                             [-sp, cp*sr, cp*cr]]
        transform[:3, 3] = extrinsics["xyz"]
        return _rigid_transform(transform)
    except (KeyError, TypeError, ValueError) as exc:
        raise RtabmapExportError(f"invalid camera extrinsics: {exc}") from exc


def _rigid_transform(value) -> np.ndarray:
    matrix = np.asarray(value, dtype=float)
    if (matrix.shape != (4, 4) or not np.isfinite(matrix).all()
            or not np.allclose(matrix[3], [0, 0, 0, 1])
            or not np.allclose(matrix[:3, :3].T @ matrix[:3, :3], np.eye(3), atol=1e-5)
            or not np.isclose(np.linalg.det(matrix[:3, :3]), 1, atol=1e-5)):
        raise RtabmapExportError("a finite rigid 4x4 camera transform is required")
    return matrix


def decode_depth(blob: bytes) -> np.ndarray:
    """Depth in metres from RTAB-Map's PNG-wrapped raw float buffer.

    Decode PNG filters and preserve OpenCV channel order for packed float32.
    A 16-bit grayscale PNG instead represents depth in millimetres.
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
    try:
        with Image.open(io.BytesIO(bytes(blob))) as image:
            image.load()
            mode = image.mode
            pixels = np.asarray(image)
    except (OSError, ValueError) as exc:
        raise RtabmapExportError(f"invalid depth PNG: {exc}") from exc
    if mode in ("I;16", "I;16B", "I;16L", "I") and pixels.ndim == 2:
        return (pixels.astype(np.float32) * .001).ravel()
    if mode == "RGBA" and pixels.dtype == np.uint8:
        # RTAB-Map encodes the float buffer as OpenCV BGRA. Pillow returns
        # RGBA, so recover the original byte order before interpreting floats.
        return np.frombuffer(pixels[..., [2, 1, 0, 3]].tobytes(), dtype="<f4")
    raise RtabmapExportError(f"unsupported depth PNG mode {mode}; expected 16UC1 or BGRA float32")


def reconstruct_cloud(
    connection: sqlite3.Connection,
    *,
    intrinsics: CameraIntrinsics,
    width: int = 320,
    height: int = 240,
    stride: int = 2,
    max_frames: int | None = None,
    max_depth_m: float = MAX_DEPTH_M,
    base_from_optical: np.ndarray | None = None,
    optimized_poses: list[tuple[int, np.ndarray]] | None = None,
) -> PointCloud:
    """Back-project depth frames through their poses into one cloud.

    ``stride`` subsamples pixels. Every pixel of every frame is far more detail than
    a viewer needs - and more than the map is worth, since neighbouring frames see
    the same surfaces - so a stride of 2 keeps a quarter of them, which is the
    difference between a workable file and a hundred-million-point one.
    """
    if stride < 1:
        raise ValueError("stride must be at least 1")
    if base_from_optical is None:
        raise RtabmapExportError("depth reconstruction requires calibrated base_from_optical")
    camera_transform = _rigid_transform(base_from_optical)
    if (type(width) is not int or type(height) is not int or width <= 0 or height <= 0
            or width * height > 8192**2 or not np.isfinite(max_depth_m) or max_depth_m <= .05):
        raise RtabmapExportError("invalid depth dimensions or range")
    poses = optimized_poses if optimized_poses is not None else read_poses(connection)
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
        node_transform = np.eye(4)
        node_transform[:3] = pose
        combined = _rigid_transform(node_transform) @ camera_transform
        rotation, translation = combined[:3, :3], combined[:3, 3]
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


def load_optimized_poses(path, connection):
    """RTAB-Map --poses --poses_format 11: stamp xyz qxyzw node_id.

    These must be base_link poses exported with graph optimization enabled.
    Node.pose in SQLite is raw odometry and cannot define a loop-closed map.
    Match node ID and capture time to prevent accidental cross-survey imports.
    """
    stamps = dict(connection.execute("SELECT id, stamp FROM Node"))
    result, seen = [], set()
    for line in Path(path).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        values = [float(value) for value in line.split()]
        if len(values) != 9 or not np.isfinite(values).all() or not values[8].is_integer():
            raise RtabmapExportError("expected RTAB-Map pose format 11")
        stamp, tx, ty, tz, x, y, z, w, raw_id = values
        node_id = int(raw_id)
        if node_id <= 0:  # exported landmarks have negative IDs
            continue
        if node_id in seen or node_id not in stamps or abs(stamps[node_id]-stamp) > .002:
            raise RtabmapExportError("optimized poses do not match this database's node IDs and timestamps")
        if not np.isclose(x*x+y*y+z*z+w*w, 1., atol=1e-4):
            raise RtabmapExportError("optimized quaternion is not normalized")
        transform = np.array([
            [1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w),tx],
            [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w),ty],
            [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y),tz],
            [0,0,0,1]], dtype=float)
        _rigid_transform(transform)
        result.append((node_id,transform[:3]))
        seen.add(node_id)
    if not result:
        raise RtabmapExportError("optimized pose file is empty")
    return result
