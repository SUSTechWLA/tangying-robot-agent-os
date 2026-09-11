"""Is the map good enough to run a task on, and where should the robot go next?

First-time mapping fails in a specific way: the operator drives around, the map
looks plausible in a viewer, and then a natural-language task refuses to plan
because some corridor was never observed. The fix is not a better viewer, it is a
number the operator can act on plus the next place to drive to.

Everything here is computed from data the runtime already produces - an occupancy
grid, a pose trajectory, and per-capture quality flags - so it works the same for
a simulated robot and a real one. The simulation supplies the geometry when a real
unit has not been measured yet.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

#: Grid cell values, matching the convention Nav2 and RTAB-Map publish.
UNKNOWN = -1
FREE = 0

#: Thresholds a first run must clear before a task is allowed to plan across rooms.
MIN_COVERAGE_RATIO = 0.85
MIN_LOOP_CLOSURES = 1
MIN_VALID_DEPTH_RATIO = 0.35
MAX_POSE_GAP_M = 1.0


@dataclass(frozen=True)
class CameraSpec:
    """A camera's pose and field of view, in the contract's optical convention."""

    name: str
    position: tuple[float, float, float]
    forward: tuple[float, float, float]
    fovy_deg: float
    aspect: float = 4 / 3
    # Exact axes when the caller knows them (a simulator model does). Without
    # them the basis is reconstructed from `forward`, which is close enough for a
    # measured robot camera but cannot reproduce a specific mount exactly.
    right: tuple[float, float, float] | None = None
    up: tuple[float, float, float] | None = None

    def half_angles(self) -> tuple[float, float]:
        vertical = math.radians(self.fovy_deg) / 2
        return vertical, math.atan(math.tan(vertical) * self.aspect)


def _unit(vector: Sequence[float]) -> tuple[float, float, float]:
    length = math.sqrt(sum(component * component for component in vector))
    if length == 0:
        raise ValueError("a camera direction cannot be zero")
    return tuple(component / length for component in vector)  # type: ignore[return-value]


def _basis(spec: CameraSpec) -> tuple[tuple[float, float, float], ...]:
    """Right/up/forward basis, taken as given when the caller supplies it."""
    forward = _unit(spec.forward)
    if spec.right is not None and spec.up is not None:
        return _unit(spec.right), _unit(spec.up), forward
    up_hint = (0.0, 0.0, 1.0)
    if abs(sum(f * u for f, u in zip(forward, up_hint, strict=True))) > 0.99:
        up_hint = (0.0, 1.0, 0.0)
    right = _unit((
        forward[1] * up_hint[2] - forward[2] * up_hint[1],
        forward[2] * up_hint[0] - forward[0] * up_hint[2],
        forward[0] * up_hint[1] - forward[1] * up_hint[0],
    ))
    up = (
        right[1] * forward[2] - right[2] * forward[1],
        right[2] * forward[0] - right[0] * forward[2],
        right[0] * forward[1] - right[1] * forward[0],
    )
    return right, up, forward


def point_in_view(spec: CameraSpec, point: Sequence[float], *, min_depth: float = 0.05) -> bool:
    right, up, forward = _basis(spec)
    offset = tuple(point[index] - spec.position[index] for index in range(3))
    depth = sum(offset[index] * forward[index] for index in range(3))
    if depth <= min_depth:
        return False
    tan_v, tan_h = spec.half_angles()
    across = sum(offset[index] * right[index] for index in range(3))
    vertical = sum(offset[index] * up[index] for index in range(3))
    return abs(across) <= tan_h * depth and abs(vertical) <= tan_v * depth


def shared_view_volume(cameras: Sequence[CameraSpec], *,
                       bounds: tuple[tuple[float, float], ...] = ((-1.0, 1.0), (-1.0, 1.5), (0.02, 1.2)),
                       steps: tuple[int, int, int] = (40, 50, 24)) -> dict:
    """Fraction of a sampled volume both cameras can see, plus where it is.

    Extrinsic calibration between two RGB-D cameras needs a target that both can
    image at once. If this is empty on a real unit, the extrinsics must come from
    a known chassis motion instead - so the number decides which procedure the
    operator is sent to.
    """
    if len(cameras) < 2:
        raise ValueError("at least two cameras are needed to talk about a shared view")
    axes = [tuple(low + (high - low) * index / (count - 1) for index in range(count))
            for (low, high), count in zip(bounds, steps, strict=True)]
    total = shared = 0
    minimum = [math.inf] * 3
    maximum = [-math.inf] * 3
    for x in axes[0]:
        for y in axes[1]:
            for z in axes[2]:
                point = (x, y, z)
                total += 1
                if all(point_in_view(camera, point) for camera in cameras):
                    shared += 1
                    for index, value in enumerate(point):
                        minimum[index] = min(minimum[index], value)
                        maximum[index] = max(maximum[index], value)
    return {
        "sampled": total,
        "shared": shared,
        "ratio": shared / total if total else 0.0,
        "bounds": None if shared == 0 else [
            [round(minimum[index], 3), round(maximum[index], 3)] for index in range(3)
        ],
        "has_shared_view": shared > 0,
    }


@dataclass
class CoverageInputs:
    """Everything the report needs, none of it hardware-specific."""

    grid: Sequence[Sequence[int]]
    resolution_m: float
    origin: tuple[float, float]
    trajectory: Sequence[tuple[float, float]] = ()
    room_of: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)
    loop_closures: int = 0
    valid_depth_ratio: float = 1.0
    motion_too_fast: bool = False
    map_revision: str = ""

    def free_cells(self) -> int:
        return sum(1 for row in self.grid for value in row if value >= FREE)

    def unknown_cells(self) -> int:
        return sum(1 for row in self.grid for value in row if value == UNKNOWN)


def _frontier_clusters(inputs: CoverageInputs) -> list[tuple[float, float, int]]:
    """Unknown cells touching known free space, grouped into drive-to targets.

    Returned in world coordinates, largest cluster first, because the largest gap
    is the one most likely to block a route.
    """
    grid = inputs.grid
    rows = len(grid)
    columns = len(grid[0]) if rows else 0
    seen: set[tuple[int, int]] = set()
    clusters: list[tuple[float, float, int]] = []
    for row in range(rows):
        for column in range(columns):
            if grid[row][column] != UNKNOWN or (row, column) in seen:
                continue
            # Breadth-first over connected unknown cells that touch free space.
            stack = [(row, column)]
            seen.add((row, column))
            members: list[tuple[int, int]] = []
            touches_free = False
            while stack:
                current_row, current_column = stack.pop()
                members.append((current_row, current_column))
                for delta_row, delta_column in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    neighbour_row, neighbour_column = current_row + delta_row, current_column + delta_column
                    if not (0 <= neighbour_row < rows and 0 <= neighbour_column < columns):
                        continue
                    value = grid[neighbour_row][neighbour_column]
                    if value == FREE:
                        touches_free = True
                    elif value == UNKNOWN and (neighbour_row, neighbour_column) not in seen:
                        seen.add((neighbour_row, neighbour_column))
                        stack.append((neighbour_row, neighbour_column))
            if not touches_free:
                continue  # an enclosed pocket cannot be reached by driving
            centre_row = sum(member[0] for member in members) / len(members)
            centre_column = sum(member[1] for member in members) / len(members)
            clusters.append((
                inputs.origin[0] + (centre_column + 0.5) * inputs.resolution_m,
                inputs.origin[1] + (centre_row + 0.5) * inputs.resolution_m,
                len(members),
            ))
    clusters.sort(key=lambda cluster: cluster[2], reverse=True)
    return clusters


def _coverage_by_room(inputs: CoverageInputs) -> dict[str, dict[str, float]]:
    """Known-free ratio inside each named room rectangle (x0, y0, x1, y1)."""
    rooms: dict[str, dict[str, float]] = {}
    for name, (x0, y0, x1, y1) in inputs.room_of.items():
        known = total = 0
        for row, cells in enumerate(inputs.grid):
            y = inputs.origin[1] + (row + 0.5) * inputs.resolution_m
            if not y0 <= y <= y1:
                continue
            for column, value in enumerate(cells):
                x = inputs.origin[0] + (column + 0.5) * inputs.resolution_m
                if not x0 <= x <= x1:
                    continue
                total += 1
                if value >= FREE:
                    known += 1
        rooms[name] = {
            "ratio": (known / total) if total else 0.0,
            "knownCells": known,
            "cells": total,
        }
    return rooms


def _largest_pose_gap(inputs: CoverageInputs) -> float:
    """Longest straight-line step between consecutive poses.

    A large step means the robot moved through space without observing it
    properly, which is exactly where a route will later fail to plan.
    """
    largest = 0.0
    for (x0, y0), (x1, y1) in zip(inputs.trajectory, inputs.trajectory[1:], strict=False):
        largest = max(largest, math.hypot(x1 - x0, y1 - y0))
    return largest


def coverage_report(inputs: CoverageInputs) -> dict:
    """Metrics plus the instructions an operator can follow."""
    free = inputs.free_cells()
    unknown = inputs.unknown_cells()
    total = free + unknown
    overall = free / total if total else 0.0
    rooms = _coverage_by_room(inputs)
    frontiers = _frontier_clusters(inputs)
    gap = _largest_pose_gap(inputs)

    problems: list[str] = []
    next_targets: list[dict] = []
    if overall < MIN_COVERAGE_RATIO:
        problems.append(
            f"整体只覆盖了 {overall:.0%}，目标是 {MIN_COVERAGE_RATIO:.0%}；"
            "请把机器人开到还没走过的区域"
        )
    for name, room in sorted(rooms.items(), key=lambda item: item[1]["ratio"]):
        if room["ratio"] < MIN_COVERAGE_RATIO:
            problems.append(f"{name} 只覆盖了 {room['ratio']:.0%}，请进去走一圈")
    if inputs.loop_closures < MIN_LOOP_CLOSURES:
        problems.append("还没有识别到回环；请让机器人绕一圈回到起点，让地图闭合")
    if inputs.valid_depth_ratio < MIN_VALID_DEPTH_RATIO:
        problems.append(
            f"深度有效像素只有 {inputs.valid_depth_ratio:.0%}；"
            "请放慢速度，避开玻璃、镜面和过近的墙面重拍"
        )
    if inputs.motion_too_fast:
        problems.append(f"有一步移动超过 {MAX_POSE_GAP_M:g} 米，太快了；请减速重走这一段")
    if gap > MAX_POSE_GAP_M:
        problems.append(f"轨迹里最长一步 {gap:.2f} 米，超过 {MAX_POSE_GAP_M:g} 米；这一段需要重拍")
    for x, y, size in frontiers[:3]:
        next_targets.append({
            "x": round(x, 2), "y": round(y, 2), "unknownCells": size,
            "instruction": f"开到地图上坐标 ({x:.1f}, {y:.1f}) 附近，慢速通过",
        })

    ready = not problems
    return {
        "mapRevision": inputs.map_revision,
        "ready": ready,
        "coverageRatio": round(overall, 4),
        "freeCells": free,
        "unknownCells": unknown,
        "rooms": {name: {key: round(value, 4) if isinstance(value, float) else value
                         for key, value in room.items()} for name, room in rooms.items()},
        "frontierCount": len(frontiers),
        "loopClosures": inputs.loop_closures,
        "validDepthRatio": round(inputs.valid_depth_ratio, 4),
        "largestPoseGapM": round(gap, 3),
        "problems": problems,
        "nextTargets": next_targets,
        "summary": ("地图已覆盖五个房间、回环已闭合，可以开始执行任务了。"
                    if ready else
                    f"还不能开始任务：{'；'.join(problems[:2])}。"),
    }


def guidance_text(report: dict) -> str:
    """One sentence for the operator, no jargon and no bare percentages."""
    if report["ready"]:
        return report["summary"]
    target = report["nextTargets"][0] if report["nextTargets"] else None
    if target is None:
        return report["summary"]
    return f"{report['problems'][0]}。{target['instruction']}。"


def simulation_camera_specs(model, *, names: Sequence[str] = ("head_depth", "base_depth")) -> list[CameraSpec]:
    """Camera poses from a MuJoCo model, so the geometry check needs no hardware.

    Imported lazily so this module stays usable without MuJoCo installed.
    """
    import mujoco
    import numpy as np

    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    specs: list[CameraSpec] = []
    for name in names:
        camera_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, name)
        if camera_id < 0:
            continue
        rotation = np.array(data.cam_xmat[camera_id]).reshape(3, 3)
        specs.append(CameraSpec(
            name=name,
            position=tuple(float(value) for value in data.cam_xpos[camera_id]),
            # MuJoCo camera axes: +X right, +Y up, and the view runs along -Z.
            forward=tuple(float(value) for value in -rotation[:, 2]),
            right=tuple(float(value) for value in rotation[:, 0]),
            up=tuple(float(value) for value in rotation[:, 1]),
            fovy_deg=float(model.cam_fovy[camera_id]),
        ))
    return specs
