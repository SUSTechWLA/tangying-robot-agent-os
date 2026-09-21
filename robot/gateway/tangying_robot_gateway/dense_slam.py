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

# Shared vocabulary comes from ``geometry``: this module no longer imports the grid
# builder at all, so the estimator and the grid pipeline can be replaced
# independently. The pose helpers are re-exported because callers import them here.
from .geometry import (
    PointCloud,
    compose,
    pose_se2,
    relative,
    transform,
    voxel_downsample,
    wrap,
)
from .slam_keyframes import MAX_KEYFRAMES, KeyframePreviews, capture_metadata

#: How much of the motion this step observed a depth registration may correct.
#: Real odometry is off by a few percent of the distance driven, not by a third.
CORRECTION_FRACTION = .25
#: Floors for the same bound, so measurement noise on a stationary step - the
#: robot turning in place - cannot be read as travel the odometry never saw.
IN_PLACE_CORRECTION_M = .02
IN_PLACE_CORRECTION_RAD = .02


def _serialisable(record):
    """A registration record that can be written to the session file.

    The information matrix is a small array and belongs in the record - it is
    what explains the edge's weight - but the session is JSON, so it is stored as
    rounded numbers rather than as an array that only serialises by accident.
    """
    if isinstance(record, np.ndarray):
        return np.round(record, 6).tolist()
    if isinstance(record, dict):
        return {key: _serialisable(value) for key, value in record.items()}
    if isinstance(record, (list, tuple)):
        return [_serialisable(value) for value in record]
    if isinstance(record, np.generic):
        return record.item()
    return record


def surface_normals(points, neighbours=10):
    """Planar normals for a cloud, from the local structure tensor.

    Indoor geometry is almost entirely planes, and a plane is what point-to-point
    ICP handles worst: correspondences slide along it, so the estimator reads a
    regular voxel lattice as a sub-voxel shift and walks the map sideways. The
    normal direction is the one direction a plane does constrain, which is what
    the registration below actually solves for.
    """
    values = np.asarray(points, dtype=float)
    if len(values) < neighbours + 1:
        return None
    tree = cKDTree(values[:, :2])
    _, indices = tree.query(values[:, :2], k=neighbours, workers=1)
    patches = values[indices][:, :, :2]
    centred = patches - patches.mean(axis=1, keepdims=True)
    covariance = np.einsum("nki,nkj->nij", centred, centred) / max(1, neighbours - 1)
    a, b, c = covariance[:, 0, 0], covariance[:, 0, 1], covariance[:, 1, 1]
    spread = np.hypot(a - c, 2 * b)
    major, minor = 0.5 * (a + c + spread), 0.5 * (a + c - spread)
    angle = 0.5 * np.arctan2(2 * b, a - c)          # major axis of the patch
    normal = np.stack([-np.sin(angle), np.cos(angle)], axis=1)
    # A patch is worth a normal when it is a *line* in the plane - a run of
    # samples along one surface - rather than a blob. A wall viewed in plan is a
    # line, so its normal is the minor axis; a corner or a cluttered surface
    # spreads in both directions and constrains nothing, so it is dropped rather
    # than averaged.
    usable = (major > 1e-6) & (major > 3.0 * minor)
    normal[~usable] = 0.0
    return normal


def _point_to_plane_system(source, target, normals):
    """Weighted linear system for one small planar update.

    Residual is ``n . (R p + t - q)``, linearised around the current estimate:
    moving by ``(dx, dy, dtheta)`` changes it by ``n . (d + dtheta * perp(p))``.
    """
    residual = np.einsum("ni,ni->n", normals, source - target)
    perpendicular = np.stack([-source[:, 1], source[:, 0]], axis=1)
    jacobian = np.concatenate([normals, np.einsum("ni,ni->n", normals, perpendicular)[:, None]], axis=1)
    return jacobian, residual


def _robust_weights(residual, scale):
    """Huber weights, so a handful of wrong correspondences cannot steer the fit."""
    magnitude = np.abs(residual)
    weights = np.ones_like(magnitude)
    large = magnitude > scale
    weights[large] = scale / np.maximum(magnitude[large], 1e-9)
    return weights


def register_depth(local, reference, initial, *, normals=None, max_correspondence_m=.20,
                   min_overlap_fraction=.30, max_correction_m=.25, max_correction_rad=.20,
                   report=None):
    """Robust planar point-to-plane ICP, returning only gated measurements.

    Three things separate this from a plain ICP and each of them was measured
    against a simulator where the true trajectory is known:

    * the residual is along the surface normal, so correspondence noise along a
      wall no longer turns into a sideways step;
    * correspondences are trimmed and then Huber-weighted, so a few outliers
      (a reflection, a moving edge) cannot drag the fit;
    * a registration whose normals do not span the plane is **rejected rather
      than returned**. Two parallel corridor walls cannot say where along the
      corridor the robot is, and accepting that measurement is what quietly
      rotates a map.
    """
    def refuse(reason, **detail):
        # A refusal is evidence too: "this view was too flat to localize against"
        # is exactly what an operator needs when a survey drifts.
        if report is not None:
            report.update({"status": reason, **detail})

    if len(local) < 80 or len(reference) < 80:
        return refuse("too_few_points", localPoints=len(local), referencePoints=len(reference))
    if normals is None:
        normals = surface_normals(reference)
    if normals is None:
        return refuse("no_surface_model")
    # Correspondences are planar: the pose being solved for has three degrees of
    # freedom, so the tree is built on the plane rather than in space.
    tree = cKDTree(reference[:, :2])
    estimate = np.array(initial, dtype=float)
    for _ in range(18):
        source = transform(local, estimate)[:, :2]
        distances, indices = tree.query(source, workers=1)
        mask = distances < max_correspondence_m
        if mask.sum() < max(80, len(local) * min_overlap_fraction):
            return refuse("no_overlap", correspondences=int(mask.sum()),
                          required=int(max(80, len(local) * min_overlap_fraction)))
        cutoff = np.quantile(distances[mask], .75)
        mask &= distances <= cutoff
        picked = normals[indices[mask]]
        usable = np.linalg.norm(picked, axis=1) > .5
        if usable.sum() < max(60, len(local) * min_overlap_fraction * .6):
            return refuse("too_flat_or_few_normals", normals=int(usable.sum()))
        target = reference[indices[mask]][usable][:, :2]
        normals_used = picked[usable]
        source_used = source[mask][usable]
        jacobian, residual = _point_to_plane_system(source_used, target, normals_used)
        scale = max(1.4826 * np.median(np.abs(residual)), 1e-4)
        weights = _robust_weights(residual, scale)
        weighted = jacobian * weights[:, None]
        try:
            step, *_ = np.linalg.lstsq(weighted, -residual * weights, rcond=None)
        except np.linalg.LinAlgError:
            return refuse("singular_system")
        if not np.isfinite(step).all():
            return refuse("nonfinite_update")
        # Bound each iteration so a bad correspondence set cannot teleport the pose.
        step = np.clip(step, [-.05, -.05, -.05], [.05, .05, .05])
        estimate[:2] += step[:2]
        estimate[2] = wrap(estimate[2] + step[2])
        if np.linalg.norm(step) < 2e-4:
            break

    source = transform(local, estimate)[:, :2]
    distances, indices = tree.query(source, workers=1)
    inliers = distances < .10
    ratio = float(inliers.mean())
    if not inliers.any():
        return refuse("no_inliers")
    picked = normals[indices[inliers]]
    usable = np.linalg.norm(picked, axis=1) > .5
    if usable.sum() < 60:
        return refuse("too_flat_at_final", normals=int(usable.sum()))
    _, residual = _point_to_plane_system(source[inliers][usable],
                                         reference[indices[inliers]][usable][:, :2],
                                         picked[usable])
    rmse = float(np.sqrt(np.mean(residual ** 2)))
    information, conditioning = registration_information(source[inliers][usable], picked[usable])
    correction = relative(initial, estimate)
    quality = {"rmseM": rmse, "inlierRatio": ratio, "conditioning": conditioning,
               "correspondences": int(usable.sum()),
               "correctionM": float(np.linalg.norm(correction[:2])),
               "correctionRad": float(abs(correction[2])),
               "information": information}
    # The gates are the honest part of an ICP: a measurement that is not
    # constrained, not accurate or not close is not evidence about the pose.
    if ratio < .40:
        return refuse("low_overlap", **quality)
    if rmse > .05:
        return refuse("large_residual", **quality)
    if conditioning < 1e-4:
        # Everything below this is weighted away in the pose graph rather than
        # thrown away: a measurement that pins one direction is still a
        # measurement of that direction.
        return refuse("underconstrained", **quality)
    if np.linalg.norm(correction[:2]) > max_correction_m or abs(correction[2]) > max_correction_rad:
        return refuse("correction_too_large", **quality)
    if report is not None:
        report.update({"status": "accepted", **quality})
    return estimate, quality


def registration_information(points, normals):
    """What a registration actually measured, as a 3x3 information matrix.

    The point-to-plane Jacobian says how strongly a small `(dx, dy, dtheta)`
    would change the residuals. Its normalised Gram matrix is therefore the
    honest weight of the measurement: directions with a near-zero eigenvalue
    (along a single wall, down a bare corridor) were not observed at all, and a
    pose graph that treats them as if they were is how a map acquires a rotation
    nobody asked for.

    Returns ``(information, conditioning)`` with the information normalised so
    its eigenvalues are comparable across registrations.
    """
    if len(points) < 3:
        return None, 0.0
    perpendicular = np.stack([-points[:, 1], points[:, 0]], axis=1)
    jacobian = np.concatenate(
        [normals, np.einsum("ni,ni->n", normals, perpendicular)[:, None]], axis=1)
    information = jacobian.T @ jacobian / len(points)
    trace = float(np.trace(information))
    if not np.isfinite(trace) or trace <= 0:
        return None, 0.0
    normalised = information / trace * 3.0
    eigenvalues = np.linalg.eigvalsh(normalised)
    return normalised, float(max(eigenvalues[0], 0.0))


def information_weight(information, yaw):
    """The square root of an information matrix, rotated into a pose's frame.

    ``relative(a, b)`` expresses its translation in ``a``'s frame, so the
    weight has to be rotated with it; the heading component is already relative.
    """
    if information is None:
        return None
    eigenvalues, vectors = np.linalg.eigh(information)
    root = vectors @ np.diag(np.sqrt(np.clip(eigenvalues, 0.0, None))) @ vectors.T
    c, s = math.cos(-yaw), math.sin(-yaw)
    rotation = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    return rotation @ root @ rotation.T


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
    #: How many recent keyframes the registration reference is built from.
    #: Registering against the previous keyframe alone makes every measurement
    #: relative to one noisy surface; against a short history it is relative to
    #: the surfaces of a few metres of room, which is both better conditioned and
    #: far less biased.
    LOCAL_MAP_KEYFRAMES = 8
    LOCAL_MAP_VOXEL_M = .04
    #: How far apart two keyframes must be before matching them is a loop.
    #: Too small and every pair of neighbours is a "loop"; a real revisit in a
    #: house comes back several metres later.
    LOOP_MIN_GAP = 25
    LOOP_RADIUS_M = .60
    #: Loops are matched with a wider correspondence basin than adjacent frames.
    #: Their whole purpose is to close a gap that drift has already opened, and a
    #: revisit that has drifted half a metre outside a 20 cm basin is exactly the
    #: revisit that most needs closing.
    LOOP_CORRESPONDENCE_M = .50
    #: Revisit candidates tried per keyframe, and heading guesses per candidate.
    #: Together with the adjacent fit they bound the attempts a session records
    #: at 1 + candidates * guesses = 9 per keyframe, which is the budget the
    #: console's keyframe panel validates against before it will open a map.
    MAX_LOOP_CANDIDATES = 4
    LOOP_GUESSES = 2

    #: How far the base must move, or turn, before a capture becomes a keyframe.
    #: Exposed as attributes so a survey can trade evidence density for range
    #: without touching the registration path, which runs on every capture.
    keyframe_translation_m = .14
    keyframe_rotation_rad = .22

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
            # Coverage over density: 0.14 m keeps a whole dwelling inside the
            # session budget while every accepted capture still contributes its
            # points to the cloud. At the old 0.10 m a 50 m survey needed 500
            # keyframes and stopped at the 400-frame cap before reaching the last
            # rooms, which is what "the robot never mapped the bathroom" looked
            # like from the map's side.
            if np.linalg.norm(delta[:2]) < self.keyframe_translation_m and abs(delta[2]) < self.keyframe_rotation_rad:
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
            motion = relative(last.odometry, odom)
            self.edges.append((index-1,index,motion,
                               np.diag([1/.025,1/.025,1/.015]),"odometry"))
            reference, reference_normals = self._registration_reference()
            attempt = {"from": index-1, "to": index, "kind": "adjacent",
                       "referenceKeyframes": min(index, self.LOCAL_MAP_KEYFRAMES)}
            registered = register_depth(cloud.xyz, reference, estimate,
                                        normals=reference_normals, report=attempt,
                                        max_correction_m=self.correction_allowance_m(motion),
                                        max_correction_rad=self.correction_allowance_rad(motion))
            self.registration_attempts.append(_serialisable(attempt))
            if registered:
                estimate, quality = registered
                # The sigma follows the measurement: a surface fit with a large
                # residual pulls the graph less than a tight one, which is what
                # stops one marginal registration from bending a whole room.
                self.edges.append((index-1,index,relative(last.pose,estimate),
                                   self._edge_weight(quality,last.pose[2]),"depth_icp"))
                self.registrations.append(_serialisable({"from":index-1,"to":index,**quality}))
        metadata = capture_metadata(observation, cloud.count)
        metadata["previewStatus"] = self.previews.add(observation, f"kf-{index:04d}")
        frame = Keyframe(cloud.xyz,cloud.rgb,odom,estimate,observation.wall_time_unix_ms,observation.observation_id,float(base[2]), metadata)
        self.frames.append(frame)
        if index >= self.LOOP_MIN_GAP + 2:
            self._close_loop(frame, index, estimate)
        return True

    @staticmethod
    def correction_allowance_m(motion):
        """How far a depth registration may move a keyframe this step, in metres.

        A registration corrects drift, and drift is a small fraction of the
        distance driven - a real odometer is off by a few percent, not by a
        third. So the allowance is a fraction of the motion this step actually
        observed, with a floor for measurement noise. That floor is what stops
        the degenerate case: while the robot turns in place the odometry says it
        translated not at all, the view changes completely, correspondences slide
        along the surfaces that stay in frame, and the fit reports several
        centimetres of travel. Two such edges - each weighted above the odometer
        they contradicted - pulled 124 keyframes of a real survey 0.14 m off,
        growing to 0.38 m by the end of the leg, in a simulator whose odometry is
        exact. Nothing local could tell that from drift; the motion can.
        """
        return max(IN_PLACE_CORRECTION_M, CORRECTION_FRACTION * float(np.linalg.norm(motion[:2])))

    @staticmethod
    def correction_allowance_rad(motion):
        """The same bound for heading, where a stationary turn is the honest case."""
        return max(IN_PLACE_CORRECTION_RAD, CORRECTION_FRACTION * abs(float(motion[2])))

    @staticmethod
    def _edge_weight(quality, yaw):
        """Scale an ICP edge by what it measured, and by how well it fitted.

        A registration with a large residual and a nearly flat normal set gets a
        small weight in the directions it did not observe, so it can sharpen a
        room without being able to rotate it.
        """
        information = quality.get("information")
        if information is None:
            sigma = max(quality.get("rmseM", .02), .01)
            return np.diag([1.0 / sigma] * 3)
        weight = information_weight(np.asarray(information, dtype=float), yaw)
        # The residual is how well the surfaces matched, not how well the pose is
        # known: a fit over a thousand correspondences locates the pose far more
        # precisely than its worst residual. Scaling the residual down is what
        # lets an accurate measurement outvote a drifting odometer instead of
        # being averaged with it.
        sigma = max(.35 * quality.get("rmseM", .02), .004)
        return weight / sigma

    def _registration_reference(self):
        """Points and normals of the recent keyframes, in the map frame."""
        recent = self.frames[-self.LOCAL_MAP_KEYFRAMES:]
        points = np.concatenate([transform(f.points, f.pose) for f in recent])
        cloud = voxel_downsample(PointCloud(points.astype(np.float32)), self.LOCAL_MAP_VOXEL_M)
        return cloud.xyz, surface_normals(cloud.xyz)

    def _loop_candidates(self, estimate, index):
        """Every earlier keyframe the robot has plausibly come back to.

        All of them, not just the nearest: coming back into a room usually puts
        several keyframes within reach, and the nearest one is not necessarily
        the one whose surfaces are visible from here.

        Position only. Heading is deliberately not filtered: the most valuable
        closure in a house is driving back down a corridor, and that puts the
        robot in the same place facing the *opposite* way. Rejecting those by
        heading is what leaves a corridor's along-axis drift uncorrected, since
        two parallel walls never constrain it.
        """
        found = []
        for i, old in enumerate(self.frames[:index - self.LOOP_MIN_GAP]):
            distance = float(np.linalg.norm(old.pose[:2] - estimate[:2]))
            if distance <= self.LOOP_RADIUS_M:
                found.append((distance, i))
        found.sort()
        return [i for _distance, i in found[:self.MAX_LOOP_CANDIDATES]]

    def _close_loop(self, frame, index, estimate):
        """Match against a submap around each revisit candidate, and keep the best."""
        for candidate in self._loop_candidates(estimate, index):
            reference = self.frames[max(0, candidate - 3):candidate + 4]
            points = np.concatenate([transform(f.points, f.pose) for f in reference])
            cloud = voxel_downsample(PointCloud(points.astype(np.float32)), self.LOCAL_MAP_VOXEL_M)
            normals = surface_normals(cloud.xyz)
            # A revisit made from the other end of a corridor is the same place
            # approached backwards, so the initial guess is turned to match the
            # candidate's own heading before the fit is attempted.
            guesses = [np.array(estimate, dtype=float)]
            turned = wrap(self.frames[candidate].pose[2] + math.pi)
            if abs(wrap(turned - estimate[2])) > .35:
                guesses.append(np.array([estimate[0], estimate[1], turned]))
            best = None
            for number, guess in enumerate(guesses):
                attempt = {"from": candidate, "to": index, "kind": "loop",
                           "reversed": number > 0}
                registered = register_depth(frame.points, cloud.xyz, guess,
                                            normals=normals, report=attempt,
                                            max_correspondence_m=self.LOOP_CORRESPONDENCE_M,
                                            # A loop exists to close error that has
                                            # already accumulated, so it is the one
                                            # measurement allowed to move a pose a
                                            # long way - bounded by the search
                                            # radius the candidate was found in.
                                            max_correction_m=self.LOOP_RADIUS_M * 2,
                                            max_correction_rad=.75,
                                            # A loop matches a wide current view
                                            # against a small submap, so the test
                                            # is an absolute floor on how many of
                                            # the view's surfaces land on it, not
                                            # a fraction of a five-metre view.
                                            min_overlap_fraction=.02)
                accepted = bool(registered and registered[1]["inlierRatio"] > .55
                                and registered[1]["conditioning"] > .03
                                and registered[1]["rmseM"] < .03)
                if registered and not accepted:
                    attempt["status"] = "weak_loop"
                self.registration_attempts.append(_serialisable(attempt))
                if accepted and (best is None or registered[1]["inlierRatio"] > best[1][1]["inlierRatio"]):
                    best = registered
            if best is None:
                continue
            pose, quality = best
            self.edges.append((candidate, index, relative(self.frames[candidate].pose, pose),
                               self._edge_weight(quality, self.frames[candidate].pose[2]),
                               "loop_closure"))
            self.loops.append(_serialisable({"from": candidate, "to": index, **quality}))
            self.optimize()
            return

    def optimize(self):
        if len(self.frames) < 2:
            return
        fixed = self.frames[0].pose.copy()
        initial = np.array([frame.pose for frame in self.frames[1:]])
        def residual(values):
            poses = np.vstack([fixed,values.reshape(-1,3)])
            errors = []
            for a,b,measurement,weight,_ in self.edges:
                error = relative(poses[a],poses[b])-measurement
                error[2] = wrap(error[2])
                # A weight matrix, not a sigma: an ICP that measured two
                # directions of three contributes two directions of evidence.
                errors.extend(weight @ error)
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

    def cloud_sources(self):
        """Which keyframe each point of :meth:`cloud` came from, in the same order.

        A consumer deciding whether a cell is really occupied has to know whether
        one viewpoint or several put points there; the index answers that, and it
        is cheap because the cloud is already a concatenation of the frames.
        """
        return np.repeat(np.arange(len(self.frames),dtype=np.int64),
                         [len(f.points) for f in self.frames])

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
        return _serialisable({"schemaVersion":"slam.session.v1","algorithm":"planar-rgbd-icp-posegraph-v1",
                "frameId": "map", "keyframeMetadataVersion": 1,
                "assumptions":["level indoor base","metric registered RGB-D","same-capture odometry"],
                "keyframeSelection": {"translationM": self.keyframe_translation_m,
                                  "rotationRad": self.keyframe_rotation_rad, "maxFrames": self.MAX_FRAMES},
                "observations": observations, "registrationAttempts": self.registration_attempts,
                "registrations":self.registrations,"loopClosures":self.loops})
