"""Commissioned household shape recognition from measured RGB-D only.

This is a bounded tabletop geometry detector, not a trained general household
recognizer. Class labels use metric shape priors; no pixel colour names, world
object poses, segmentation IDs, held flags or simulator rewards are inputs.
"""

from collections import deque

import numpy as np
from tangying_robot_gateway.contracts import Entity
from tangying_robot_gateway.rgbd import PixelDetection, RgbdPerception, deproject

from .home_scene import (
    HOME_TASK_BIN_SURFACE_HALF_EXTENT,
    HOME_TASK_BIN_WALL_THICKNESS,
    HOME_TASK_WORK_VOLUME,
)
from .household_workcell import HOUSEHOLD_DIMENSIONS


def spatial_components(mask, points):
    """Depth edges split touching image silhouettes into physical components."""
    remaining = mask.copy()
    height, width = mask.shape
    components = []
    for row, col in zip(*np.nonzero(mask), strict=True):
        if not remaining[row, col]:
            continue
        remaining[row, col] = False
        queue, pixels = deque([(int(row), int(col))]), []
        while queue:
            y, x = queue.popleft()
            pixels.append((y, x))
            for yy, xx in ((y-1, x), (y+1, x), (y, x-1), (y, x+1)):
                if (0 <= yy < height and 0 <= xx < width and remaining[yy, xx]
                        and np.linalg.norm(points[yy, xx] - points[y, x]) < .022):
                    remaining[yy, xx] = False
                    queue.append((yy, xx))
        if len(pixels) >= 12:
            result = np.zeros_like(mask)
            yy, xx = np.asarray(pixels).T
            result[yy, xx] = True
            components.append(result)
    return components


def _observed_edge(cloud, axis):
    """A measured edge contains a sufficiently sampled continuous segment.

    Separated end points from opposite sides alone do not constitute a third
    edge: at most 4cm of a segment may be hidden between adjacent samples.
    Disconnected far corners neither invalidate an observed segment nor add
    samples or length to it. Every segment must independently meet both tests.
    """
    if len(cloud) < 5:
        return False
    ordered = np.sort(cloud[:, 1-axis])
    segments = np.split(ordered, np.flatnonzero(np.diff(ordered) > .040)+1)
    return any(len(segment) >= 5 and np.ptp(segment) >= .020 for segment in segments)


def _rim_lines(cloud, axis):
    bins, counts = np.unique(np.round(cloud[:, axis]/.003).astype(int), return_counts=True)
    lines = []
    for coordinate, _count in sorted(zip(bins*.003, counts, strict=True), key=lambda item: item[1], reverse=True):
        samples = cloud[np.abs(cloud[:, axis]-coordinate) < .006]
        if _observed_edge(samples, axis):
            center = float(np.median(samples[:, axis]))
            if all(abs(center-previous) > .010 for previous in lines):
                lines.append(center)
    # This detector is commissioned for one small tray, not arbitrary dense
    # patterns. Excessive candidate edges are ambiguous, never top-k selected.
    return lines if len(lines) <= 16 else []


def observed_tray_rims(points, valid, horizontal, support_z):
    """Fit the commissioned axis-aligned tray from this frame's rim only.

    Two opposing parallel edges determine one center coordinate; an observed
    orthogonal third edge determines the other. Metric dimensions are class
    priors, while position and height come only from current depth samples.
    Cup pixels above the rim cannot join its footprint. No prior pose is used.
    """
    rim = valid & horizontal & (np.abs(points[:, :, 2]-(support_z+.10)) < .008)
    cloud = points[rim]
    if len(cloud) < 24:
        return []
    half = np.asarray(HOME_TASK_BIN_SURFACE_HALF_EXTENT[:2])+HOME_TASK_BIN_WALL_THICKNESS
    lines = [_rim_lines(cloud, axis) for axis in range(2)]
    candidates = []
    for axis in range(2):
        for first in lines[axis]:
            for second in lines[axis]:
                if second <= first or abs(second-first-2*half[axis]) > .012:
                    continue
                for cross_edge in lines[1-axis]:
                    for sign in (-1, 1):
                        center = np.empty(2)
                        center[axis] = (first+second)/2
                        center[1-axis] = cross_edge+sign*half[1-axis]
                        within = np.all(np.abs(cloud[:, :2]-center) < half+.009, axis=1)
                        local = cloud[within]
                        if len(local) < 24 or np.any(np.ptp(local[:, :2],axis=0) < 2*half*.70):
                            continue
                        on_edge = np.any(np.abs(np.abs(local[:, :2]-center)-half) < .008, axis=1)
                        # A filled horizontal patch is a plate/table surface,
                        # not evidence for a hollow tray perimeter.
                        if on_edge.mean() < .85:
                            continue
                        sides = [_observed_edge(local[np.abs(local[:, a]-(center[a]+side*half[a])) < .008],a)
                                 for a in range(2) for side in (-1,1)]
                        if sum(sides) < 3 or not (all(sides[:2]) or all(sides[2:])):
                            continue
                        measured = np.array([*center, float(np.median(local[:, 2]))-.10])
                        if any(np.linalg.norm(measured-prior[1]) < .020 for prior in candidates):
                            continue
                        mask = np.zeros_like(valid)
                        rows, cols = np.nonzero(rim)
                        mask[rows[within],cols[within]] = True
                        candidates.append((mask,measured))
    return candidates


class HouseholdRgbdPerception:
    """Shape priors calibrated for one ceramic mug, tray and decorative set."""

    def __init__(self):
        self._geometry = {}
        self._support_z = None
        self._perception = RgbdPerception(self._detect, max_points=4096)
        self._robot_mask = None

    def _detect(self, frame):
        points, valid = deproject(frame)
        if self._robot_mask is not None:
            valid &= ~np.asarray(self._robot_mask, dtype=bool)
        for axis, index in (("x", 0), ("y", 1), ("z", 2)):
            low, high = HOME_TASK_WORK_VOLUME[axis]
            valid &= (points[:, :, index] > low) & (points[:, :, index] < high)
        dy, dx = np.gradient(points, axis=(0, 1))
        normals = np.cross(dx, dy)
        horizontal = np.abs(normals[:, :, 2]) / np.maximum(
            np.linalg.norm(normals, axis=2), 1e-12
        ) > .98
        # Estimate the dominant horizontal support in the commissioned height
        # band. This band is sensor calibration, not the table's simulator pose.
        support = points[valid & horizontal & (points[:, :, 2] > .68)
                         & (points[:, :, 2] < .78)]
        self._support_z = None
        if len(support) >= 25:
            bands, counts = np.unique(np.round(support[:, 2], 2), return_counts=True)
            self._support_z = float(bands[np.argmax(counts)])
        if self._support_z is None:
            self._geometry = {}
            return []
        foreground = valid & (points[:, :, 2] > self._support_z + .015)
        candidates = {"ceramic-mug": [], "dinnerware": [], "ceramic-vase": []}
        for component in spatial_components(foreground, points):
            cloud = points[component]
            low, high = np.percentile(cloud, [2, 98], axis=0)
            span = high - low
            top = cloud[cloud[:, 2] > high[2] - .008]
            if len(top) < 6:
                continue
            above_support = high[2] - self._support_z
            if (.025 < span[0] < .13 and .025 < span[1] < .13
                  and .19 < above_support < .28 and span[2] > .16):
                candidates["ceramic-vase"].append((component, top, low, high))
            elif (.13 < max(span[:2]) < .28 and .04 < min(span[:2]) < .25
                  and .02 < above_support < .085):
                candidates["dinnerware"].append((component, top, low, high))
        # The cup protrudes above the tray rim after a placement. Segment its
        # upper body separately so touching cup/tray pixels cannot collapse into
        # one object. Robot first-return pixels are excluded using only robot CAD
        # and capture-time encoders supplied by the caller.
        cup_band = foreground & (points[:, :, 2] > self._support_z + .108)
        for component in spatial_components(cup_band, points):
            cloud = points[component]
            low, high = np.percentile(cloud, [2, 98], axis=0)
            span = high-low
            top = cloud[cloud[:, 2] > high[2]-.008]
            # A vase's bulb may be separated from its narrow neck by a depth
            # edge. A measured continuation above that bulb rules it out as a
            # mug; otherwise the two objects would create false ambiguity.
            continuation = (foreground
                            & np.all(np.abs(points[:, :, :2]-(low[:2]+high[:2])/2) < .07, axis=2)
                            & (points[:, :, 2] > high[2]+.025))
            if (.025 < span[0] < .135 and .02 < span[1] < .115
                    and high[2]-self._support_z < .42 and span[2] < .17
                    and len(top) >= 6 and continuation.sum() < 6):
                candidates["ceramic-mug"].append((component, top, low, high))
        detections, geometry = [], {}
        categories = {"ceramic-mug": "cup", "kitchen-tray": "storage_bin",
                      "dinnerware": "plate", "ceramic-vase": "vase"}
        names = {"ceramic-mug": "陶瓷杯", "kitchen-tray": "收纳盘",
                 "dinnerware": "餐具", "ceramic-vase": "花瓶"}
        for entity_id, options in candidates.items():
            options.sort(key=lambda item: int(item[0].sum()), reverse=True)
            if not options or (len(options) > 1 and options[1][0].sum() > options[0][0].sum()*.6):
                continue
            mask, top, _low, high = options[0]
            dimensions = np.asarray(HOUSEHOLD_DIMENSIONS[entity_id])
            center = np.median(top, axis=0)
            center[2] = high[2] - dimensions[2]/2
            geometry[entity_id] = (center, dimensions)
            detections.append(PixelDetection(entity_id, categories[entity_id], mask, .90,
                                              {"name": names[entity_id], "recognition": "rgbd_metric_shape"}))
        trays = observed_tray_rims(points, valid, horizontal, self._support_z)
        if len(trays) == 1:
            mask, center = trays[0]
            geometry["kitchen-tray"] = (center, np.asarray(HOME_TASK_BIN_SURFACE_HALF_EXTENT)*2)
            detections.append(PixelDetection("kitchen-tray", "storage_bin", mask, .90,
                                              {"name": "收纳盘", "recognition": "rgbd_metric_rim"}))
        self._geometry = geometry
        return detections

    def reconstruct(self, frame, *, end_effectors=None, grippers=None, robot_mask=None):
        self._robot_mask = robot_mask
        result = self._perception.reconstruct(frame)
        end_effectors, grippers = end_effectors or {}, grippers or {}
        entities = []
        for entity in result.entities:
            center, dimensions = self._geometry[entity.entity_id]
            relation = ""
            if entity.category == "cup":
                near_gripper = False
                for arm, position in end_effectors.items():
                    if np.linalg.norm(center - np.asarray(position)) < .11:
                        near_gripper = True
                        if (grippers.get(arm) == "closed"
                                and center[2]-dimensions[2]/2 > self._support_z + .025):
                            relation = f"held_by:{frame.robot_id}"
                        break
                target = self._geometry.get("kitchen-tray")
                if not relation and not near_gripper and target is not None:
                    tray_center, tray_dimensions = target
                    if (np.all(np.abs(center[:2]-tray_center[:2]) < tray_dimensions[:2]/2-.02)
                            and abs(center[2]-dimensions[2]/2-tray_center[2]) < .035):
                        relation = "inside:kitchen-tray"
            entities.append(Entity(entity_id=entity.entity_id, category=entity.category,
                                   attributes=entity.attributes, pose=[*center.tolist(), 1, 0, 0, 0],
                                   confidence=entity.confidence, relation=relation))
        result.entities = entities
        return result
