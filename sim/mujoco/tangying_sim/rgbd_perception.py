"""Pixel/depth perception for a commissioned two-object reference workcell.

The catalog recognizes a red cup, a blue bottle, and coloured rectangular trays.
No simulator state is imported. Different objects require a different detector.
"""

from __future__ import annotations

from collections import deque

import numpy as np
from tangying_robot_gateway.contracts import Entity
from tangying_robot_gateway.rgbd import PixelDetection, RgbdFrame, RgbdPerception, deproject


def colour_clusters(mask, points):
    remaining = mask.copy()
    h, w = mask.shape
    clusters = []
    for v, u in zip(*np.nonzero(mask), strict=True):
        if not remaining[v, u]:
            continue
        remaining[v, u] = False
        queue, pixels = deque([(int(v), int(u))]), []
        while queue:
            y, x = queue.popleft()
            pixels.append((y, x))
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < h and 0 <= nx < w and remaining[ny, nx]:
                    remaining[ny, nx] = False
                    queue.append((ny, nx))
        if len(pixels) >= 15:
            selected = np.zeros_like(mask)
            y, x = np.asarray(pixels).T
            selected[y, x] = True
            clusters.append(selected)
    merged = []
    for selected in sorted(clusters, key=lambda m: int(m.sum()), reverse=True):
        center = np.median(points[selected], axis=0)
        for group in merged:
            other = np.median(points[group], axis=0)
            if np.linalg.norm(center[:2] - other[:2]) < 0.065 and abs(center[2] - other[2]) < 0.07:
                group |= selected
                break
        else:
            merged.append(selected)
    return sorted(merged, key=lambda m: int(m.sum()), reverse=True)


class TabletopRgbdPerception:
    def __init__(self):
        self._geometry = {}
        self._support_z = None
        self._perception = RgbdPerception(self._detect, max_points=4096)

    def _detect(self, frame: RgbdFrame):
        points, valid = deproject(frame)
        # Calibrated robot work volume, not object locations.
        valid &= (
            (np.abs(points[:, :, 0]) < 0.65)
            & (points[:, :, 1] > 0.20)
            & (points[:, :, 1] < 1.1)
            & (points[:, :, 2] > 0.6)
            & (points[:, :, 2] < 1.2)
        )
        r, g, b = frame.rgb.astype(float).transpose(2, 0, 1)
        red = (r > 1.6 * g) & (r > 1.6 * b) & (r > 35)
        blue = (b > 1.35 * r) & (b > 1.15 * g) & (b > 45)
        orange = (r > 1.15 * g) & (g > 1.8 * b) & (r > 60)
        gray = (
            (np.maximum.reduce((r, g, b)) - np.minimum.reduce((r, g, b)) < 12)
            & (r > 45)
            & (r < 210)
        )
        dy, dx = np.gradient(points, axis=(0, 1))
        normal = np.cross(dx, dy)
        magnitude = np.linalg.norm(normal, axis=2)
        horizontal = np.abs(normal[:, :, 2]) / np.maximum(magnitude, 1e-12) > 0.98
        wood = valid & horizontal & (r > 1.1 * g) & (g > 1.1 * b) & ~red
        support = points[wood]
        if len(support) < 25:
            # Do not carry a previous scene's support plane through sensor loss.
            self._geometry = {}
            return []
        bins, counts = np.unique(np.round(support[:, 2], 2), return_counts=True)
        self._support_z = float(bins[np.argmax(counts)])
        plate = (
            valid
            & horizontal
            & (points[:, :, 2] > self._support_z + 0.025)
            & (points[:, :, 2] < self._support_z + 0.065)
        )
        detections, geometry = [], {}
        for name, color, category, relation, color_mask, dimensions in (
            ("right-bin", "blue", "storage_bin", "right_side", blue, (0.15, 0.18)),
            ("left-bin", "orange", "storage_bin", "left_side", orange | red, (0.15, 0.18)),
            ("front-tray", "gray", "delivery_tray", "front_side", gray, (0.36, 0.13)),
        ):
            mask = plate & color_mask
            cloud = points[mask]
            if len(cloud) < 25:
                continue
            low, high = np.percentile(cloud, [1, 99], axis=0)
            if high[0] - low[0] < dimensions[0] * 0.3 or high[1] - low[1] < dimensions[1] * 0.3:
                continue
            # Visible outer/far edges + commissioned tray dimensions recover
            # the centre even when the robot occludes an inner edge.
            x = low[0] + dimensions[0] / 2 if name == "left-bin" else high[0] - dimensions[0] / 2
            y = high[1] - dimensions[1] / 2
            center = np.array([x, y, float(np.median(cloud[:, 2])) - 0.02])
            geometry[name] = (center, np.array(dimensions), relation)
            detections.append(PixelDetection(name, category, mask, 0.9, {"color": color}))
        for name, color, category, color_mask, object_height in (
            ("red-cup", "red", "cup", red, 0.12),
            ("blue-bottle", "blue", "bottle", blue, 0.16),
        ):
            mask = valid & color_mask & (points[:, :, 2] > self._support_z + 0.075)
            candidates = []
            for cluster in colour_clusters(mask, points):
                cloud = points[cluster]
                low, high = np.percentile(cloud, [2, 98], axis=0)
                if not 0.025 < high[0] - low[0] < 0.105 or not 0.02 < high[1] - low[1] < 0.105:
                    continue
                top = cloud[cloud[:, 2] > high[2] - 0.006]
                if len(top) >= 8:
                    candidates.append((cluster, top, high))
            if not candidates:
                continue
            if len(candidates) > 1 and candidates[1][0].sum() > candidates[0][0].sum() * 0.6:
                continue  # Multiple plausible instances require clarification.
            mask, top, high = candidates[0]
            center = np.median(top, axis=0)
            center[2] = high[2] - object_height / 2
            geometry[name] = (center, np.array([0.07, 0.07]), "")
            detections.append(PixelDetection(name, category, mask, 0.9, {"color": color}))
        self._geometry = geometry
        return detections

    def reconstruct(self, frame: RgbdFrame, *, end_effectors=None, grippers=None):
        result = self._perception.reconstruct(frame)
        end_effectors, grippers = end_effectors or {}, grippers or {}
        entities = []
        for entity in result.entities:
            center, _extent, relation = self._geometry[entity.entity_id]
            if entity.category in {"cup", "bottle"}:
                near_closed_gripper = False
                bottom = center[2] - {"cup": 0.06, "bottle": 0.08}[entity.category]
                for arm, position in end_effectors.items():
                    if (
                        grippers.get(arm) == "closed"
                        and np.linalg.norm(center - np.asarray(position)) < 0.085
                    ):
                        near_closed_gripper = True
                        if self._support_z is not None and bottom > self._support_z + 0.025:
                            relation = f"held_by:{frame.robot_id}"
                        break
                if not relation and not near_closed_gripper:
                    for target, (target_center, target_extent, _) in self._geometry.items():
                        if target not in {"left-bin", "right-bin", "front-tray"}:
                            continue
                        if (
                            np.all(
                                np.abs(center[:2] - target_center[:2]) < target_extent / 2 - 0.01
                            )
                            and -0.03 < center[2] - target_center[2] < 0.10
                        ):
                            relation = f"inside:{target}"
                            break
            entities.append(
                Entity(
                    entity_id=entity.entity_id,
                    category=entity.category,
                    attributes=entity.attributes,
                    pose=[*center.tolist(), 1.0, 0.0, 0.0, 0.0],
                    confidence=entity.confidence,
                    relation=relation,
                )
            )
        result.entities = entities
        return result
