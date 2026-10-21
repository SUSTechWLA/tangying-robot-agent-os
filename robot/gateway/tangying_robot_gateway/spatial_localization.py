"""Deployment-bound localization for both planar and volumetric ROS2 navigation.

This module validates reported measurements. It does not estimate pose or certify
that a physical sensor, map, controller or terrain survey is accurate.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import stat
import time
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from .contracts import (
    Contract,
    Identifier,
    RobotProfile,
    SourceType,
    validate_pose,
    validate_tool_parameters,
)

MAX_DEPLOYMENT_BYTES = 64 * 1024
MAX_VOLUME_BYTES = 8 * 1024 * 1024
MAX_PLAN_BYTES = 512 * 1024
ARRIVAL_POSITION_TOLERANCE_M = 0.05
ARRIVAL_ORIENTATION_TOLERANCE_RAD = 0.10


def strict_json(payload: str, *, maximum: int):
    if not isinstance(payload, str) or len(payload.encode()) > maximum:
        raise ValueError("spatial JSON exceeds its byte budget")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON field: {key}")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"non-finite JSON number: {value}")

    return json.loads(payload, object_pairs_hook=pairs, parse_constant=constant)


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _read_json(path: Path, maximum: int):
    # Inspect the opened descriptor, not a pre-open stat: a path may be replaced
    # between checks. NONBLOCK ensures a replacement FIFO cannot stall startup.
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
            raise ValueError("spatial deployment file must be a bounded regular file")
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("spatial deployment file exceeds its byte budget")
    return strict_json(raw.decode("utf-8"), maximum=maximum)


class LocalizationConfig(Contract):
    source_id: Identifier
    source_type: SourceType
    source_frame_id: Identifier
    max_age_ms: int = Field(ge=1, le=5000)
    max_position_variance_m2: float = Field(gt=0)
    max_orientation_variance_rad2: float = Field(gt=0)


class LocalizationQuality(Contract):
    state: Literal["localized", "degraded", "lost"]
    position_variance_m2: list[float] = Field(min_length=3, max_length=3)
    orientation_variance_rad2: list[float] = Field(min_length=3, max_length=3)

    @field_validator("position_variance_m2", "orientation_variance_rad2")
    @classmethod
    def _nonnegative(cls, values):
        if any(value < 0 for value in values):
            raise ValueError("localization variance cannot be negative")
        return values


class SpatialLocalization(Contract):
    schema_version: Literal["spatial.localization.v1"]
    robot_id: Identifier
    source_id: Identifier
    source_type: SourceType
    source_frame_id: Identifier
    frame_id: Literal["world"]
    map_id: Identifier
    map_revision: Identifier
    world_frame_revision: Identifier
    transform_revision: Identifier
    observation_id: Identifier
    observed_at_unix_ms: int = Field(gt=0)
    sequence: int = Field(gt=0, le=9_007_199_254_740_991)
    pose_model: Literal["se2", "se3"]
    pose: list[float] = Field(min_length=7, max_length=7)
    linear_unit: Literal["m"]
    angular_unit: Literal["rad"]
    quality: LocalizationQuality

    _valid_pose = field_validator("pose")(validate_pose)


class SpatialNavigationDeployment:
    """Immutable content pins; changing a local file requires process restart/admission."""

    def __init__(self, volume: dict, policy: dict, localization: dict, profile):
        from .spatial_navigation import SpatialPathPlanner, validate_policy, validate_volume

        profile = RobotProfile.model_validate(profile)
        volume = validate_volume(volume)
        policy = validate_policy(policy)
        local = LocalizationConfig.model_validate(localization)
        mobility = profile.mobility
        if mobility is None or volume["robotId"] != profile.robot_id:
            raise ValueError("spatial deployment requires matching mobile robot identity")
        if policy["mode"] == "volume3d" and (mobility.domain != "air" or mobility.pose_model != "se3"):
            raise ValueError("volume3d navigation requires an air/se3 robot")
        if policy["mode"] in {"terrain25d", "terrain_layered"} and mobility.domain != "ground":
            raise ValueError("terrain navigation requires a ground robot")
        if mobility.navigation_preparation != "controller":
            raise ValueError("spatial navigation requires controller-owned preparation")
        if volume["calibrationRevision"] not in {s.transform_revision for s in profile.sensors}:
            raise ValueError("spatial volume calibration not in commissioned profile")
        self._volume, self._policy, self._localization = volume, policy, local
        self._profile = profile
        self._planner = SpatialPathPlanner(volume, policy)
        self.revision = hashlib.sha256(canonical_json({
            "schemaVersion": "spatial.deployment.v1", "volume": volume, "policy": policy,
            "localization": local.to_wire(), "robotProfile": profile.to_wire(),
        }).encode()).hexdigest()

    @property
    def volume(self):
        return copy.deepcopy(self._volume)

    @property
    def policy(self):
        return copy.deepcopy(self._policy)

    @property
    def localization_config(self):
        return self._localization.to_wire()

    @classmethod
    def from_file(cls, path, profile):
        path = Path(path).expanduser().absolute()
        config = _read_json(path, MAX_DEPLOYMENT_BYTES)
        if not isinstance(config, dict) or set(config) != {"schemaVersion", "volumePath", "policy", "localization"}:
            raise ValueError("spatial deployment requires exact schema, volumePath, policy and localization")
        if config["schemaVersion"] != "spatial.deployment.v1" or not isinstance(config["volumePath"], str) or not config["volumePath"].strip():
            raise ValueError("spatial deployment schema or volumePath invalid")
        volume_path = Path(config["volumePath"])
        if not volume_path.is_absolute():
            volume_path = path.parent / volume_path
        volume_path = volume_path.absolute()
        volume = _read_json(volume_path, MAX_VOLUME_BYTES)
        # Packages produced by our mapping provider additionally prove their
        # measured derivation. Standalone volumes remain an explicit external
        # commissioned-map contract; their full bytes are pinned in revision.
        if volume_path.name == "volume.json" and (volume_path.parent / "manifest.json").exists():
            from .spatial_mapping import load_spatial_map
            package = load_spatial_map(volume_path.parent)
            if package["volume"] != volume:
                raise ValueError("spatial volume changed while validating its package")
        return cls(volume, config["policy"], config["localization"], profile)

    def localization(self, value, observed, *, received_monotonic: float | None = None):
        item = SpatialLocalization.model_validate(value)
        local, volume, mobility = self._localization, self._volume, self._profile.mobility
        for actual, expected in (
            (item.robot_id, self._profile.robot_id), (item.source_id, local.source_id),
            (item.source_type, local.source_type), (item.source_frame_id, local.source_frame_id),
            (item.map_id, volume["mapId"]), (item.map_revision, volume["mapRevision"]),
            (item.world_frame_revision, volume["worldFrameRevision"]),
            (item.transform_revision, volume["calibrationRevision"]),
            (item.transform_revision, observed.transform_revision),
            (item.observation_id, observed.observation_id), (item.observed_at_unix_ms, observed.observed_at_unix_ms),
            (item.sequence, observed.sequence), (item.pose_model, mobility.pose_model),
        ):
            if actual != expected:
                raise ValueError("spatial localization identity/frame/map/capture binding mismatch")
        age = int(time.time() * 1000) - item.observed_at_unix_ms
        if not 0 <= age <= local.max_age_ms:
            raise ValueError("spatial localization is stale or future dated")
        if received_monotonic is not None and not 0 <= (time.monotonic() - received_monotonic) * 1000 <= local.max_age_ms:
            raise ValueError("spatial localization exceeded monotonic lifetime")
        if (item.quality.state != "localized"
                or any(v > local.max_position_variance_m2 for v in item.quality.position_variance_m2)
                or any(v > local.max_orientation_variance_rad2 for v in item.quality.orientation_variance_rad2)):
            raise ValueError("spatial localization quality is insufficient")
        if item.pose_model == "se2" and (abs(item.pose[4]) > 1e-6 or abs(item.pose[5]) > 1e-6):
            raise ValueError("se2 localization cannot silently discard roll/pitch")
        for index, axis in enumerate(("x", "y", "z")):
            limit = self._profile.action_limits.get("navigation." + axis)
            if limit is None or not limit.min <= item.pose[index] <= limit.max:
                raise ValueError("spatial localization exceeds commissioned workspace")
        return item.to_wire()

    def plan(self, localization, goal_pose, command_id):
        plan = self._planner.plan(localization["pose"], goal_pose)
        for pose in plan["waypoints"]:
            validate_tool_parameters("navigation.navigate", {"goalPose": pose}, self._profile)
        plan.update(spatialNavigationRevision=self.revision, commandId=command_id,
                    localizationObservationId=localization["observationId"],
                    localizationSequence=localization["sequence"],
                    localizationObservedAtUnixMs=localization["observedAtUnixMs"])
        plan["planId"] = hashlib.sha256(canonical_json(plan).encode()).hexdigest()
        wire = canonical_json(plan)
        if len(wire.encode()) > MAX_PLAN_BYTES:
            raise ValueError("spatial navigation plan exceeds the wire budget")
        return plan, wire

    def verify_plan(self, plan, start_pose, goal_pose, command_id=None):
        """Controller-side fixture helper: replan exact endpoints and reject mutations.

        Production controllers still own dynamic collision checking and tracking;
        this helper only verifies this bounded static planner contract.
        """
        if not isinstance(plan, dict):
            raise TypeError("spatial navigation plan must be an object")
        unsigned = {key: value for key, value in plan.items() if key != "planId"}
        if plan.get("planId") != hashlib.sha256(canonical_json(unsigned).encode()).hexdigest():
            raise ValueError("spatial navigation plan hash mismatch")
        if (plan.get("spatialNavigationRevision") != self.revision
                or (command_id is not None and plan.get("commandId") != command_id)):
            raise ValueError("spatial navigation plan deployment/command mismatch")
        expected = self._planner.plan(start_pose, goal_pose)
        extras = {"planId", "spatialNavigationRevision", "commandId", "localizationObservationId",
                  "localizationSequence", "localizationObservedAtUnixMs"}
        if {key: value for key, value in plan.items() if key not in extras} != expected:
            raise ValueError("spatial navigation plan differs from validated route")
        for pose in plan["waypoints"]:
            validate_tool_parameters("navigation.navigate", {"goalPose": pose}, self._profile)
        return True


def pose_reached(actual, goal, *, position_tolerance=ARRIVAL_POSITION_TOLERANCE_M,
                 orientation_tolerance=ARRIVAL_ORIENTATION_TOLERANCE_RAD):
    validate_pose(actual)
    validate_pose(goal)
    # Input validation permits small normalization error. The angular metric
    # must remove it from both operands or an over-length quaternion can hide
    # an orientation error outside the arrival tolerance.
    actual_norm = math.sqrt(sum(value * value for value in actual[3:]))
    goal_norm = math.sqrt(sum(value * value for value in goal[3:]))
    dot = abs(sum((a / actual_norm) * (b / goal_norm) for a, b in zip(actual[3:], goal[3:])))
    angle = 2 * math.acos(min(1.0, dot))
    return math.dist(actual[:3], goal[:3]) <= position_tolerance and angle <= orientation_tolerance
