"""Optional fixed room cameras, published through the ordinary sensor contract.

These views help a human follow the robot. Mapping and manipulation continue to
use the robot's calibrated head/base cameras. No scene entities are synthesized.
"""
from __future__ import annotations

import copy
import hashlib
import threading
import time

import mujoco
import numpy as np
from tangying_robot_gateway.rgbd import RgbdFrame, validate_frame

from .rendering import SceneRenderer
from .rgbd_navigation import NavigationCapture

CAMERAS = {"room-rgbd": "room_overview", "workspace-rgbd": "workspace_overview",
           "home-rgbd": "home_panorama"}


def add_room_cameras(spec):
    """Commission fixed cameras with a physical mount frame in the home model."""
    for name, position, target, fovy in (
        ("room_overview", [0, 3, 12], [0, 3, 0], 52),
        ("workspace_overview", [4.2, 1.65, 2.95], [2.45, 3.8, .5], 62),
        ("home_panorama", [10, -9, 15], [0, 3, .6], 34),
    ):
        mount = spec.worldbody.add_body(name=f"{name}_mount", pos=position)
        backwards = np.asarray(position, dtype=float) - target
        backwards /= np.linalg.norm(backwards)
        if name == "room_overview":
            # The home is longer north/south; orient that span horizontally to
            # fill the landscape preview without clipping any of its rooms.
            right, up = np.array([0., 1., 0.]), np.array([-1., 0., 0.])
        else:
            # This scene is Z-up. Keeping its vertical axis upright avoids a
            # tilted worktop in the operator's view.
            right = np.cross([0, 0, 1], backwards)
            right /= np.linalg.norm(right)
            up = np.cross(backwards, right)
        mount.add_camera(name=name, xyaxes=[*right, *up], fovy=fovy)


class RoomCameras:
    def __init__(self, world, robot_id):
        self.world = world
        self.robot_id = robot_id
        self._lock = threading.Lock()
        self._sequence = int(time.time() * 1000) * 1000
        self._renderers = {}
        self.sensors = []
        for source, name in CAMERAS.items():
            index = mujoco.mj_name2id(world.model, mujoco.mjtObj.mjOBJ_CAMERA, name)
            if index < 0:
                continue
            mount = world.model.cam_bodyid[index]
            revision = hashlib.sha256(
                world.model.body_pos[mount].tobytes() + world.model.cam_pos[index].tobytes()
                + world.model.cam_quat[index].tobytes() + world.model.cam_fovy[index].tobytes()
            ).hexdigest()
            self.sensors.append({"sourceId": f"{robot_id}/{source}", "sourceType": "rgbd_camera",
                "frameId": f"{name}_optical", "transformRevision": revision, "maxAgeMs": 2000})

    def has(self, source):
        return any(sensor["sourceId"] == source for sensor in self.sensors)

    def capture(self, source):
        sensor = next((sensor for sensor in self.sensors if sensor["sourceId"] == source), None)
        if sensor is None:
            raise ValueError("unknown room camera source")
        with self._lock:
            if self.world.lock.acquire(blocking=False):
                try:
                    self.world._publish_sensor_snapshot()
                finally:
                    self.world.lock.release()
            data, state, stamp = self.world.sensor_snapshot
            state = copy.deepcopy(state)
            if source not in self._renderers:
                self._renderers[source] = SceneRenderer(
                    width=640, height=480, camera=CAMERAS[source.rsplit("/", 1)[-1]])
            pixels = self._renderers[source].render_rgbd(self.world.model, data)
            now = int(time.time() * 1000)
            if any(type(value) is not int or value > now for value in (stamp, pixels.captured_at_unix_ms)):
                raise ValueError("room camera capture is invalid or future dated")
            self._sequence += 1
            frame = RgbdFrame(self.robot_id, source, sensor["frameId"], sensor["transformRevision"],
                min(stamp, pixels.captured_at_unix_ms), self._sequence, pixels.rgb, pixels.depth_m,
                pixels.intrinsics, pixels.world_from_camera)
            validate_frame(frame)
            return NavigationCapture(frame, state["base_pose"], state["_self_filter_joint_positions"],
                state["_self_filter_observed_at_unix_ms"])

    def close(self):
        with self._lock:
            for renderer in self._renderers.values():
                renderer.close()
            self._renderers.clear()
