"""Bounded planar RGB-D SLAM: measured depth ICP, odometry and pose graph.

No scene assets, object truth or driver type enter this module. The reference
solver assumes a level indoor mobile base. A runtime may register RTAB-Map or
another SLAM provider behind the same mapping service instead.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial import cKDTree

from .map_pipeline import PointCloud, voxel_downsample
from .slam_keyframes import MAX_KEYFRAMES, KeyframePreviews, capture_metadata


def wrap(angle):
    return np.arctan2(np.sin(angle), np.cos(angle))


def pose_se2(pose):
    values = np.asarray(pose, dtype=float)
    if values.shape != (7,) or not np.isfinite(values).all():
        raise ValueError("mapping requires a finite same-capture base pose")
    w, x, y, z = values[3:]
    if abs(np.linalg.norm(values[3:]) - 1) > .001 or abs(x) + abs(y) > .02:
        raise ValueError("planar SLAM requires a normalized level-base quaternion")
    return np.array([values[0], values[1], math.atan2(2*(w*z+x*y), 1-2*(y*y+z*z))])


def transform(points, pose):
    c, s = np.cos(pose[2]), np.sin(pose[2])
    result = points.copy()
    result[:, :2] = points[:, :2] @ np.array([[c, s], [-s, c]]) + pose[:2]
    return result


def relative(a, b):
    c, s = np.cos(a[2]), np.sin(a[2])
    return np.array([c*(b[0]-a[0])+s*(b[1]-a[1]),
                     -s*(b[0]-a[0])+c*(b[1]-a[1]), wrap(b[2]-a[2])])


def compose(a, delta):
    c, s = np.cos(a[2]), np.sin(a[2])
    return np.array([a[0]+c*delta[0]-s*delta[1], a[1]+s*delta[0]+c*delta[1], wrap(a[2]+delta[2])])


def register_depth(local, reference, initial):
    """Trimmed planar point-to-point ICP, returning only gated measurements."""
    if len(local) < 80 or len(reference) < 80:
        return None
    tree = cKDTree(reference)
    estimate = np.array(initial, dtype=float)
    for _ in range(14):
        source = transform(local, estimate)
        distances, indices = tree.query(source, workers=1)
        mask = distances < .18
        if mask.sum() < max(80, len(local)*.30):
            return None
        cutoff = np.quantile(distances[mask], .8)
        mask &= distances <= cutoff
        a, b = source[mask, :2], reference[indices[mask], :2]
        ca, cb = a.mean(axis=0), b.mean(axis=0)
        u, _, vt = np.linalg.svd((a-ca).T @ (b-cb))
        rotation = vt.T @ u.T
        if np.linalg.det(rotation) < 0:
            vt[-1] *= -1
            rotation = vt.T @ u.T
        translation = cb-rotation@ca
        angle = math.atan2(rotation[1, 0], rotation[0, 0])
        estimate[:2] = rotation@estimate[:2]+translation
        estimate[2] = wrap(estimate[2]+angle)
        if np.linalg.norm(translation) < .0003 and abs(angle) < .0003:
            break
    distances, _ = tree.query(transform(local, estimate), workers=1)
    inliers = distances < .10
    ratio = float(inliers.mean())
    rmse = float(np.sqrt(np.mean(distances[inliers]**2))) if inliers.any() else 1.
    correction = relative(initial, estimate)
    if ratio < .40 or rmse > .065 or np.linalg.norm(correction[:2]) > .25 or abs(correction[2]) > .20:
        return None
    return estimate, {"rmseM": rmse, "inlierRatio": ratio}


@dataclass
class Keyframe:
    points: np.ndarray
    colors: np.ndarray
    odometry: np.ndarray
    pose: np.ndarray
    timestamp: int
    observation_id: str
    base_z: float
    metadata: dict


class DenseSLAM:
    MAX_FRAMES = MAX_KEYFRAMES

    def __init__(self):
        self.frames: list[Keyframe] = []
        self.edges = []
        self.registrations = []
        self.loops = []
        self.registration_attempts = []
        self.previews = KeyframePreviews()

    def add(self, observation):
        if len(self.frames) >= self.MAX_FRAMES:
            raise ValueError("本次扫描已达到 400 帧，请保存后开始新地图。")
        sensor = observation.rgbd_frame
        width, height = sensor.width, sensor.height
        if not 1 <= width*height <= 1_000_000 or len(sensor.depth_metres_f32) != width*height*4 or len(sensor.rgb) != width*height*3:
            raise ValueError("invalid metric RGB-D frame sizes")
        if not observation.observation_id or (self.frames and observation.wall_time_unix_ms <= self.frames[-1].timestamp):
            return False
        state = dict(observation.robot_state)
        base = list(state["base_pose"])
        odom = pose_se2(base)
        if self.frames:
            delta = relative(self.frames[-1].odometry, odom)
            if np.linalg.norm(delta[:2]) < .10 and abs(delta[2]) < .16:
                return False
        k = np.array(sensor.intrinsics).reshape(3, 3)
        extrinsic = np.array(sensor.base_from_camera).reshape(4, 4)
        if (not np.isfinite(k).all() or k[0, 0] <= 0 or k[1, 1] <= 0
                or not np.isfinite(extrinsic).all() or not np.allclose(extrinsic[3], [0,0,0,1])
                or not np.allclose(extrinsic[:3,:3].T@extrinsic[:3,:3],np.eye(3),atol=1e-4)
                or np.linalg.det(extrinsic[:3,:3]) < .999):
            raise ValueError("invalid camera calibration")
        depth = np.frombuffer(sensor.depth_metres_f32, dtype="<f4").reshape(height, width)
        rgb = np.frombuffer(sensor.rgb, dtype=np.uint8).reshape(height, width, 3)
        rows, cols = np.mgrid[0:height:2, 0:width:2]
        z = depth[::2, ::2]
        valid = np.isfinite(z) & (z > .15) & (z < 5.)
        if sensor.robot_self_mask:
            if len(sensor.robot_self_mask) != width*height:
                raise ValueError("invalid calibrated robot self-mask")
            valid &= np.frombuffer(sensor.robot_self_mask, dtype=np.uint8).reshape(height,width)[::2,::2] == 0
        optical = np.stack([(cols-k[0,2])*z/k[0,0], (rows-k[1,2])*z/k[1,1], z],axis=-1)[valid]
        local = optical@extrinsic[:3,:3].T+extrinsic[:3,3]
        local[:,2] += base[2]
        cloud = voxel_downsample(PointCloud(local.astype(np.float32), rgb[::2,::2][valid]), .045)
        if cloud.count < 100:
            raise ValueError("可用深度点不足，请调整相机朝向或检查标定。")
        index = len(self.frames)
        estimate = odom.copy() if index == 0 else compose(self.frames[-1].pose, relative(self.frames[-1].odometry, odom))
        if index:
            last = self.frames[-1]
            self.edges.append((index-1,index,relative(last.odometry,odom),np.array([.025,.025,.015]),"odometry"))
            registered = register_depth(cloud.xyz, transform(last.points,last.pose), estimate)
            self.registration_attempts.append({"from": index-1, "to": index, "kind": "adjacent",
                "status": "accepted" if registered else "rejected",
                **(registered[1] if registered else {})})
            if registered:
                estimate, quality = registered
                self.edges.append((index-1,index,relative(last.pose,estimate),np.array([.04,.04,.025]),"depth_icp"))
                self.registrations.append({"from":index-1,"to":index,**quality})
        metadata = capture_metadata(observation, cloud.count)
        metadata["previewStatus"] = self.previews.add(observation, f"kf-{index:04d}")
        frame = Keyframe(cloud.xyz,cloud.rgb,odom,estimate,observation.wall_time_unix_ms,observation.observation_id,float(base[2]), metadata)
        self.frames.append(frame)
        if index >= 10:
            candidates = [(np.linalg.norm(old.pose[:2]-estimate[:2]),i) for i,old in enumerate(self.frames[:index-8])
                          if abs(wrap(old.pose[2]-estimate[2])) < .6]
            if candidates:
                distance, candidate = min(candidates)
                if distance < .45:
                    old = self.frames[candidate]
                    registered = register_depth(frame.points,transform(old.points,old.pose),estimate)
                    loop_accepted = bool(registered and registered[1]["inlierRatio"] > .60)
                    self.registration_attempts.append({"from": candidate, "to": index, "kind": "loop",
                        "status": "accepted" if loop_accepted else "rejected",
                        **(registered[1] if registered else {})})
                    if loop_accepted:
                        pose, quality = registered
                        self.edges.append((candidate,index,relative(old.pose,pose),np.array([.035,.035,.025]),"loop_closure"))
                        self.loops.append({"from":candidate,"to":index,**quality})
                        self.optimize()
        return True

    def optimize(self):
        if len(self.frames) < 2:
            return
        fixed = self.frames[0].pose.copy()
        initial = np.array([frame.pose for frame in self.frames[1:]])
        def residual(values):
            poses = np.vstack([fixed,values.reshape(-1,3)])
            errors = []
            for a,b,measurement,sigma,_ in self.edges:
                error = relative(poses[a],poses[b])-measurement
                error[2] = wrap(error[2])
                errors.extend(error/sigma)
            return np.array(errors)
        # Local edges produce a sparse graph; finite differences remain bounded
        # even for the maximum session size.
        from scipy.sparse import lil_matrix
        sparsity = lil_matrix((len(self.edges)*3, initial.size),dtype=int)
        for i,(a,b,*_) in enumerate(self.edges):
            for node in (a,b):
                if node: sparsity[i*3:i*3+3,(node-1)*3:node*3] = 1
        solution = least_squares(residual,initial.ravel(),jac_sparsity=sparsity.tocsr(),loss="huber",max_nfev=40)
        if not np.isfinite(solution.x).all():
            raise ValueError("pose graph optimization failed")
        for frame,pose in zip(self.frames[1:],solution.x.reshape(-1,3),strict=True):
            frame.pose = pose

    def cloud(self):
        return PointCloud(np.concatenate([transform(f.points,f.pose) for f in self.frames]).astype(np.float32),
                          np.concatenate([f.colors for f in self.frames]))

    def trajectory(self):
        return [[float(f.pose[0]),float(f.pose[1]),f.base_z] for f in self.frames]

    def provenance(self):
        observations = []
        for index, frame in enumerate(self.frames):
            delta = relative(frame.odometry, frame.pose)
            observations.append({"id": frame.observation_id, "frameId": f"kf-{index:04d}",
                "index": index, "stamp": frame.timestamp, "baseZ": frame.base_z,
                "odometry": frame.odometry.tolist(), "optimizedPose": frame.pose.tolist(),
                "correction": {"translationM": float(np.linalg.norm(delta[:2])),
                               "yawRad": float(delta[2]), "localDelta": delta.tolist()},
                **frame.metadata})
        return {"schemaVersion":"slam.session.v1","algorithm":"planar-rgbd-icp-posegraph-v1",
                "frameId": "map", "keyframeMetadataVersion": 1,
                "assumptions":["level indoor base","metric registered RGB-D","same-capture odometry"],
                "keyframeSelection": {"translationM": .10, "rotationRad": .16, "maxFrames": self.MAX_FRAMES},
                "observations": observations, "registrationAttempts": self.registration_attempts,
                "registrations":self.registrations,"loopClosures":self.loops}
