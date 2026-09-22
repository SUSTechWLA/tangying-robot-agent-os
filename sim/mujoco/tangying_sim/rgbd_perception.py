"""Pixel/depth perception for a commissioned two-object reference workcell.

The catalog recognizes a red cup, a blue bottle, and coloured rectangular trays.
No simulator state is imported. Different objects require a different detector.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
from tangying_robot_gateway.contracts import Entity
from tangying_robot_gateway.rgbd import PixelDetection, RgbdFrame, RgbdPerception, deproject

from .rgbd_workcell import BIN_DIMENSIONS_M, TRAY_DIMENSIONS_M


def colour_clusters(mask, points, *, object_height_m=None):
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
            nearby = np.linalg.norm(center[:2] - other[:2]) < 0.065 and abs(center[2] - other[2]) < 0.07
            # A wrist can split the same tall bottle into a cap and a lower
            # body. Their median heights differ by more than 7 cm. Only join
            # such fragments when their horizontal centres nearly coincide
            # and their entire observed envelope fits the commissioned object.
            occluded_body = False
            if object_height_m is not None and np.linalg.norm(center[:2]-other[:2]) < .035:
                cloud = points[group | selected]
                low, high = np.percentile(cloud, [2, 98], axis=0)
                occluded_body = bool(np.all(high[:2]-low[:2] <= .085)
                                     and high[2]-low[2] <= object_height_m+.01)
            if nearby or occluded_body:
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
        self._support_z = None
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
            ("right-bin", "blue", "storage_bin", "right_side", blue, BIN_DIMENSIONS_M),
            ("left-bin", "orange", "storage_bin", "left_side", orange | red, BIN_DIMENSIONS_M),
            ("front-tray", "gray", "delivery_tray", "front_side", gray, TRAY_DIMENSIONS_M),
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
            for cluster in colour_clusters(mask, points, object_height_m=object_height):
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
                near_gripper = False
                bottom = center[2] - {"cup": 0.06, "bottle": 0.08}[entity.category]
                for arm, position in end_effectors.items():
                    if (
                        np.linalg.norm(center - np.asarray(position)) < 0.10
                    ):
                        near_gripper = True
                        if grippers.get(arm) == "closed" and self._support_z is not None and bottom > self._support_z + 0.025:
                            relation = f"held_by:{frame.robot_id}"
                        break
                if not relation and not near_gripper:
                    for target, (target_center, target_extent, _) in self._geometry.items():
                        if target not in {"left-bin", "right-bin", "front-tray"}:
                            continue
                        if (
                            np.all(
                                np.abs(center[:2] - target_center[:2]) < target_extent / 2 - 0.01
                            )
                            # The observed tray surface is 20 mm above its
                            # calibrated geometric centre. Confirm support,
                            # not merely an object's centre inside a tall box.
                            and abs(bottom - (target_center[2] + 0.02)) < 0.006
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


#: Which advertised object each colour mask is allowed to ground. One entry per
#: colour keeps a detector from inventing an object the catalogue never advertised.
COLOUR_OBJECT_IDS = {
    "blue": "blue-cup", "green": "green-cup",
    "orange": "orange-bowl", "yellow": "yellow-plate",
}


from tangying_sim.home_scene import (
    HOME_TASK_BIN_SURFACE_HALF_EXTENT,
    HOME_TASK_BIN_WALL_HEIGHT,
    HOME_TASK_BIN_WALL_THICKNESS,
    HOME_TASK_OBJECTS,
    HOME_TASK_WORK_VOLUME,
)


@dataclass(frozen=True)
class _BinTrack:
    center: np.ndarray
    source_id: str
    transform_revision: str
    captured_at_unix_ms: int
    sequence: int


class HomeTaskRgbdPerception:
    """RGB-D detector for the commissioned kitchen transfer fixture.

    The detector deliberately knows only the sensor contract (colour masks,
    measured 3-D points and the calibrated task work volume). It never reads
    MuJoCo bodies or placement flags, so a real RGB-D adapter can implement the
    same interface with a learned detector later.
    """

    BIN_TRACK_MAX_AGE_MS = 1_800

    def __init__(self):
        self._geometry = {}
        self._support_z = None
        self._bin_track: _BinTrack | None = None
        self._perception = RgbdPerception(self._detect, max_points=4096)

    def _recent_bin_track(self, frame: RgbdFrame) -> _BinTrack | None:
        track = self._bin_track
        if track is None:
            return None
        age_ms = frame.captured_at_unix_ms - track.captured_at_unix_ms
        if (
            frame.source_id != track.source_id
            or frame.transform_revision != track.transform_revision
            or frame.sequence <= track.sequence
            or not 0 < age_ms <= self.BIN_TRACK_MAX_AGE_MS
        ):
            self._bin_track = None
            return None
        return track

    def _detect(self, frame: RgbdFrame):
        points, valid = deproject(frame)
        previous_support = self._support_z
        self._support_z = None
        # The kitchen work volume is a sensor-space commissioning limit, not a
        # semantic object lookup. It excludes the distant blue floor rugs and
        # household walls while retaining the table and both task fixtures.
        valid &= (
            (points[:, :, 0] > HOME_TASK_WORK_VOLUME["x"][0])
            & (points[:, :, 0] < HOME_TASK_WORK_VOLUME["x"][1])
            & (points[:, :, 1] > HOME_TASK_WORK_VOLUME["y"][0])
            & (points[:, :, 1] < HOME_TASK_WORK_VOLUME["y"][1])
            & (points[:, :, 2] > HOME_TASK_WORK_VOLUME["z"][0])
            & (points[:, :, 2] < HOME_TASK_WORK_VOLUME["z"][1])
        )
        r, g, b = frame.rgb.astype(float).transpose(2, 0, 1)
        red = valid & (r > 1.55 * g) & (r > 1.55 * b) & (r > 45)
        blue = valid & (b > 1.25 * r) & (b > 1.12 * g) & (b > 35)
        green = valid & (g > 1.30 * r) & (g > 1.30 * b) & (g > 40)
        # Orange and yellow have to be told apart by their own masks, not by luck.
        # A saturated yellow satisfies a naive "red-dominant, low blue" orange test,
        # so orange additionally requires green to be well below red, and yellow
        # requires green close to it. Without that split the plate would be grounded
        # as the bowl.
        orange = valid & (r > 1.35 * b) & (g > 0.95 * b) & (g < 0.80 * r) & (r > 80) & ~red
        yellow = valid & (r > 1.40 * b) & (g > 1.40 * b) & (g > 0.72 * r) & (r > 120)
        # Yellow is excluded from orange explicitly: a saturated yellow satisfies
        # a naive orange test, and the plate would then be grounded as the bowl.
        orange = orange & ~yellow

        # Recover the table support plane from its measured brown horizontal
        # surface. This value gates grasp evidence and calibration checks; it
        # is never copied from the MuJoCo table pose.
        dy, dx = np.gradient(points, axis=(0, 1))
        normal = np.cross(dx, dy)
        magnitude = np.linalg.norm(normal, axis=2)
        horizontal = np.abs(normal[:, :, 2]) / np.maximum(magnitude, 1e-12) > 0.98
        wood = valid & horizontal & (r > 1.08 * g) & (g > 1.08 * b) & ~red
        support = points[wood]
        if len(support) < 25:
            # Table edges can make the local normal noisy once the gripper is
            # over the station. A dominant measured brown depth band still
            # identifies the support plane without consulting simulator pose.
            wood_band = points[valid & (r > 1.08 * g) & (g > 1.08 * b) & ~red]
            if len(wood_band) >= 25:
                bands, counts = np.unique(np.round(wood_band[:, 2], 2), return_counts=True)
                candidate = float(bands[np.argmax(counts)])
                if 0.68 <= candidate <= 0.80:
                    self._support_z = candidate
        if len(support) >= 25:
            bins, counts = np.unique(np.round(support[:, 2], 2), return_counts=True)
            self._support_z = float(bins[np.argmax(counts)])
        elif previous_support is not None:
            # A held arm/cup may temporarily occlude the tabletop plane. Keep
            # the last recent calibrated support height until a fresh frame
            # proves a different plane; RGB-D object positions still refresh.
            self._support_z = previous_support

        detections, geometry = [], {}
        red_candidates = []
        red = red & (points[:, :, 2] > (self._support_z or 0.73) + 0.075)
        for cluster in colour_clusters(red, points):
            cloud = points[cluster]
            low, high = np.percentile(cloud, [2, 98], axis=0)
            if 0.025 < high[0] - low[0] < 0.16 and 0.02 < high[1] - low[1] < 0.16:
                top = cloud[cloud[:, 2] > high[2] - 0.008]
                if len(top) >= 8:
                    red_candidates.append((cluster, top, high))
        if red_candidates:
            if len(red_candidates) > 1 and red_candidates[1][0].sum() > red_candidates[0][0].sum() * 0.6:
                # Ambiguous colour instances must be clarified rather than
                # guessed; the gateway will refuse a non-unique grounding.
                red_candidates = []
            else:
                mask, top, high = red_candidates[0]
                center = np.median(top, axis=0)
                center[2] = high[2] - 0.06
                geometry["red-cup"] = (center, np.array([0.09, 0.09, 0.12]), "")
                detections.append(PixelDetection("red-cup", "cup", mask, 0.90, {"color": "red"}))

        # Other coloured task objects use the same measured shape test as the cup:
        # a compact cluster standing above the support plane. Colour is the only
        # clue available to an RGB-D detector here, and the size band is what keeps
        # a small object from being mistaken for the large blue bin.
        bin_minimum_span = np.asarray(HOME_TASK_BIN_SURFACE_HALF_EXTENT[:2]) * 1.3
        for colour, mask in (("blue", blue), ("green", green), ("orange", orange), ("yellow", yellow)):
            item_id = COLOUR_OBJECT_IDS.get(colour)
            if item_id is None:
                continue
            tinted = mask & (points[:, :, 2] > (self._support_z or 0.73) + 0.075)
            candidates = []
            for cluster in colour_clusters(tinted, points):
                cloud = points[cluster]
                low, high = np.percentile(cloud, [2, 98], axis=0)
                # The band admits cups and also wider bowls and plates. It stays
                # below the commissioned storage-bin span, which is what keeps a
                # small object from being grounded as the bin.
                if not (0.025 < high[0] - low[0] < 0.22 and 0.02 < high[1] - low[1] < 0.22):
                    continue
                if colour == "blue" and np.all(high[:2] - low[:2] > bin_minimum_span):
                    continue
                top = cloud[cloud[:, 2] > high[2] - 0.008]
                if len(top) >= 8:
                    candidates.append((cluster, top, high))
            if not candidates:
                continue
            # Same rule as the cup: two similar instances at one colour are
            # ambiguous, and a wrong grounding is worse than none.
            if len(candidates) > 1 and candidates[1][0].sum() > candidates[0][0].sum() * 0.6:
                continue
            mask_selected, top, high = candidates[0]
            center = np.median(top, axis=0)
            center[2] = high[2] - 0.06
            category = next(entry[3] for entry in HOME_TASK_OBJECTS if entry[0] == item_id)
            geometry[item_id] = (center, np.array([0.09, 0.09, 0.12]), "")
            detections.append(PixelDetection(item_id, category, mask_selected, 0.90, {"color": colour}))

        # A blue rim is the only bin-sized blue surface above the kitchen table.
        # Its measured edges recover the recessed release support plane.
        full_bin_candidates = []
        fragment_candidates = []
        recent_track = self._recent_bin_track(frame)
        blue_task = blue & (points[:, :, 2] > (self._support_z or 0.73) + 0.025)
        bin_components = []
        maximum_span = np.asarray([
            *np.asarray(HOME_TASK_BIN_SURFACE_HALF_EXTENT[:2]) * 4,
            HOME_TASK_BIN_WALL_HEIGHT * 4,
        ])
        for cluster in colour_clusters(blue_task, points):
            cloud = points[cluster]
            low, high = np.percentile(cloud, [2, 98], axis=0)
            # A bin component must span most of the commissioned floor in both
            # axes. This sensor-space shape test separates it from the smaller
            # blue cup without assigning either entity from a scene coordinate.
            span = high - low
            geometry_consistent = np.all(span[:2] > 0) and np.all(span < maximum_span)
            if not geometry_consistent:
                continue
            bin_components.append((cluster, cloud, low, high))
            rim_fragment = (
                np.min(span[:2]) <= HOME_TASK_BIN_WALL_THICKNESS * 4
                and np.max(span[:2]) >= HOME_TASK_BIN_WALL_THICKNESS * 2
            )
            if recent_track is not None and rim_fragment and np.any(np.all(
                np.abs(cloud[:, :2] - recent_track.center[:2])
                <= np.asarray(HOME_TASK_BIN_SURFACE_HALF_EXTENT[:2]) * 1.5,
                axis=1,
            )):
                fragment_candidates.append((cluster, cloud))

        # The cup or gripper can split the four sides of one current rim into
        # separate pixel components. Join only components whose measured XY
        # bounds nearly touch, then require the combined current support to span
        # the commissioned bin in both axes. A distant blue object stays separate.
        parents = list(range(len(bin_components)))

        def find(index):
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        def union(first, second):
            first, second = find(first), find(second)
            if first != second:
                parents[second] = first

        join_distance = max(HOME_TASK_BIN_SURFACE_HALF_EXTENT[:2]) * 1.5
        for first, (_, _, low_a, high_a) in enumerate(bin_components):
            for second in range(first + 1, len(bin_components)):
                _, _, low_b, high_b = bin_components[second]
                gap = np.maximum(0.0, np.maximum(low_a[:2] - high_b[:2], low_b[:2] - high_a[:2]))
                if np.linalg.norm(gap) <= join_distance:
                    union(first, second)
        grouped_masks = {}
        for index, (cluster, *_rest) in enumerate(bin_components):
            root = find(index)
            grouped_masks.setdefault(root, np.zeros_like(blue_task))
            grouped_masks[root] |= cluster
        for mask in grouped_masks.values():
            cloud = points[mask]
            low, high = np.percentile(cloud, [2, 98], axis=0)
            span = high - low
            if np.all(span[:2] > bin_minimum_span) and np.all(span < maximum_span):
                full_bin_candidates.append((mask, cloud, low, high))

        if full_bin_candidates:
            # A complete current support always wins over history, including
            # when the bin moved farther than the occlusion association radius.
            mask, _blue_cloud, low, high = max(
                full_bin_candidates, key=lambda item: item[1].shape[0]
            )
            center = np.array([
                high[0] - HOME_TASK_BIN_SURFACE_HALF_EXTENT[0],
                high[1] - HOME_TASK_BIN_SURFACE_HALF_EXTENT[1],
                high[2] - (
                    2 * HOME_TASK_BIN_WALL_HEIGHT
                    - HOME_TASK_BIN_SURFACE_HALF_EXTENT[2]
                ),
            ])
            self._bin_track = _BinTrack(
                center.copy(), frame.source_id, frame.transform_revision,
                frame.captured_at_unix_ms, frame.sequence,
            )
        elif fragment_candidates and recent_track is not None:
            # A clipped rim can preserve the last complete measurement briefly.
            # Fragment frames never refresh the track's age, so repeated partial
            # observations cannot keep an old position alive indefinitely.
            mask = np.zeros_like(blue_task)
            for candidate, _cloud in fragment_candidates:
                mask |= candidate
            center = recent_track.center.copy()
        else:
            # A truly blank sensor frame invalidates continuity immediately.
            # When another measured object remains visible, total rim occlusion
            # may be momentary; retain the old measurement without emitting it
            # or refreshing its finite age, so a later matching fragment can
            # associate within the same short observation sequence.
            if not np.any(red | blue | green | orange | yellow):
                self._bin_track = None
            mask = None

        if mask is not None:
            geometry["kitchen-bin"] = (
                center,
                np.asarray(HOME_TASK_BIN_SURFACE_HALF_EXTENT) * 2,
                "",
            )
            detections.append(PixelDetection("kitchen-bin", "storage_bin", mask, 0.90, {"color": "blue"}))
        if not geometry:
            self._support_z = None
        self._geometry = geometry
        return detections

    def reconstruct(self, frame: RgbdFrame, *, end_effectors=None, grippers=None):
        result = self._perception.reconstruct(frame)
        end_effectors, grippers = end_effectors or {}, grippers or {}
        entities = []
        for entity in result.entities:
            center, _extent, relation = self._geometry[entity.entity_id]
            if entity.entity_id == "red-cup":
                near_gripper = False
                for arm, position in end_effectors.items():
                    if np.linalg.norm(center - np.asarray(position)) < 0.11:
                        near_gripper = True
                        if grippers.get(arm) == "closed" and self._support_z is not None and center[2] > self._support_z + 0.025:
                            relation = f"held_by:{frame.robot_id}"
                        break
                target = self._geometry.get("kitchen-bin")
                if not relation and not near_gripper and target is not None:
                    target_center, target_extent, _ = target
                    bottom = center[2] - 0.06
                    if (np.all(np.abs(center[:2] - target_center[:2]) < target_extent[:2] / 2 - 0.02)
                            and abs(bottom - target_center[2]) < 0.035):
                        relation = "inside:kitchen-bin"
            entities.append(Entity(
                entity_id=entity.entity_id, category=entity.category,
                attributes=entity.attributes,
                pose=[*center.tolist(), 1.0, 0.0, 0.0, 0.0],
                confidence=entity.confidence, relation=relation,
            ))
        result.entities = entities
        return result
