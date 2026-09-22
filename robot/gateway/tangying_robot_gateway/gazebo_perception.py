"""RGB-D detector for the explicitly commissioned coloured Gazebo workcell.

This is not a general object recognizer. IDs denote one uniquely visible colour
fixture; two plausible instances are ambiguous. Simulator poses are unavailable
to this module. Depth, calibrated rays and visible surfaces determine geometry.
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import label
from scipy.optimize import least_squares

from .rgbd import PixelDetection, RgbdPerception, deproject

OBJECT_MODELS = {"red-cup": "red_cup", "blue-bottle": "blue_bottle"}
DESTINATION_MODELS = {"right-bin": "tray_floor", "front-tray": "delivery_tray"}


class GazeboWorkcellPerception:
    def reconstruct(self, frame):
        # All detector state belongs to this capture, including when two camera
        # consumers run concurrently. Nothing survives an empty/occluded frame.
        points, valid = deproject(frame)
        r, g, b = frame.rgb.astype(float).transpose(2, 0, 1)
        definitions = (
            ("red-cup", "cup", "red", (r > 1.8*g) & (r > 1.8*b) & (r > 40)),
            ("blue-bottle", "bottle", "blue", (b > 1.8*r) & (b > 1.8*g) & (b > 40)),
            ("right-bin", "storage_bin", "cyan", (g > 2*r) & (b > 2*r) & (g > .65*b) & (b > .65*g) & (g > 40)),
            ("front-tray", "delivery_tray", "purple", (r > 2*g) & (b > 2*g) & (r > .65*b) & (b > .65*r) & (r > 40)),
        )
        geometry, detections = {}, []
        for identity, category, colour, mask in definitions:
            labels, count = label(mask & valid)
            candidates = []
            for index in range(1, count+1):
                selected = labels == index
                if selected.sum() < 12:
                    continue
                cloud = points[selected]
                lo, hi = np.percentile(cloud, [2, 98], axis=0)
                extent = hi-lo
                pickable = identity in OBJECT_MODELS
                if np.max(extent) > (.20 if pickable else .35) or np.max(extent) < .025:
                    continue
                candidates.append((selected, cloud, lo, hi))
            if len(candidates) != 1:
                continue
            selected, cloud, lo, hi = candidates[0]
            if identity in OBJECT_MODELS:
                # Visible top disc plus known fixture height gives the cylinder
                # centre without the near-surface bias of a whole-mask median.
                top = cloud[cloud[:, 2] >= hi[2]-.008]
                if len(top) < 5:
                    continue
                center = np.median(top, axis=0)
                center[2] = hi[2]-.06
                # The visible cylinder wall is an arc, not a symmetric surface.
                # Fit its commissioned 45 mm radius instead of treating the
                # near-surface median as the object's centre.
                wall = cloud[(cloud[:, 2] < hi[2]-.015) & (cloud[:, 2] > lo[2]+.01)]
                if len(wall) >= 12:
                    fit = least_squares(lambda xy, wall=wall: np.linalg.norm(wall[:, :2]-xy, axis=1)-.045,
                                        center[:2], loss="soft_l1", f_scale=.002)
                    errors = np.abs(np.linalg.norm(wall[:, :2]-fit.x, axis=1)-.045)
                    if np.median(errors) > .004 or np.linalg.norm(fit.x-center[:2]) > .06:
                        continue
                    center[:2] = fit.x
            else:
                center = (lo+hi)/2
                # Bin walls are excluded from the support height estimate.
                center[2] = float(np.percentile(cloud[:, 2], 15))
            geometry[identity] = center
            detections.append(PixelDetection(identity, category, selected, .9,
                {"color": colour, "perceptionMode": "commissioned_rgbd_colour"}))
        result = RgbdPerception(lambda _: detections).reconstruct(frame)
        for entity in result.entities:
            entity.pose = [*geometry[entity.entity_id].tolist(), 1., 0., 0., 0.]
            if entity.entity_id == "right-bin":
                entity.relation = "right_side"
            elif entity.entity_id == "front-tray":
                entity.relation = "front_side"
            else:
                for target in DESTINATION_MODELS:
                    if target not in geometry:
                        continue
                    delta = geometry[entity.entity_id] - geometry[target]
                    if np.max(np.abs(delta[:2])) < .055 and abs(delta[2]-.06) < .025:
                        entity.relation = "inside:"+target
        return result
