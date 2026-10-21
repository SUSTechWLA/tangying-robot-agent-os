#!/usr/bin/env python3
"""Read-only device binding checks; never imports rclpy or sends a robot command.

This validates local configuration and optionally an operator-supplied ROS graph
snapshot. Neither result commissions hardware or bypasses Runtime admission.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import time
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, ValidationError

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "robot/gateway", ROOT / "python",
               ROOT / "robot/ros2_ws/src/tangying_robot_gateway"):
    sys.path.insert(0, str(source))

from tangying_robot_gateway.contracts import Contract, Identifier, RobotProfile
from tangying_robot_gateway.ros2_profile import profile_revision
from tangying_robot_gateway.spatial_localization import (
    SpatialNavigationDeployment,
    strict_json,
)
from tangying_ros_gateway.profiled_node import ProfiledRosConfig

Text = Annotated[str, Field(min_length=1, max_length=1024)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
RosName = Annotated[str, Field(pattern=r"^/(?:[A-Za-z_][A-Za-z_0-9]*/)*[A-Za-z_][A-Za-z_0-9]*$")]
RosType = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z_0-9]*/(?:msg|srv|action)/[A-Za-z_][A-Za-z_0-9]*$")]


class Device(Contract):
    manufacturer: Text
    model: Identifier
    firmware_revision: Text
    sdk_name: Text
    sdk_revision: Text
    bridge_implementation_revision: Text
    compatibility_reference: Text


class Coordinates(Contract):
    source_id: Identifier
    vendor_convention: Text
    transform_revision: Identifier
    world_frame: Literal["world"]
    world_convention: Literal["right_handed_z_up"]


class Controller(Contract):
    integration_id: Identifier
    vendor_transport: Literal["sdk", "ros2"]
    navigation_dimension: Literal["2d", "terrain25d", "terrain_layered", "volume3d"]
    safe_stop_strategy: Literal["brake", "hold_position", "controlled_land", "vendor_procedure"]
    stop_procedure_id: Identifier
    command_loss_timeout_ms: int = Field(gt=0, le=60000)
    arrival_verification: Literal["independent_localization"]
    command_authority_owner: Literal["local_controller"]
    deduplication_owner: Literal["local_controller"]


class Endpoint(Contract):
    name: RosName
    ros_type: RosType
    role: Literal["action_server", "service_server", "publisher", "subscriber"]


class DeviceBinding(Contract):
    schema_version: Literal["robot.ros2.device-binding.v1"]
    binding_id: Identifier
    robot_id: Identifier
    profile_path: Text
    profile_revision: Digest
    gateway_config_path: Text
    commissioning_id: Identifier
    ros_domain_id: int = Field(ge=0, le=232)
    device: Device
    coordinates: Coordinates
    controller: Controller
    required_vendor_interfaces: list[Endpoint] = Field(max_length=32)


class GraphSnapshot(Contract):
    schema_version: Literal["ros2.interface.inventory.v1"]
    robot_id: Identifier
    namespace: RosName
    ros_domain_id: int = Field(ge=0, le=232)
    observed_at_unix_ms: int = Field(gt=0)
    endpoints: list[Endpoint] = Field(max_length=256)


def _load(path: Path) -> tuple[dict, str]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 256 * 1024:
            raise ValueError("configuration must be a bounded regular file")
        raw = stream.read(256 * 1024 + 1)
    value = strict_json(raw.decode("utf-8"), maximum=256 * 1024)
    if not isinstance(value, dict):
        raise TypeError("configuration must be a JSON object")
    return value, hashlib.sha256(raw).hexdigest()


def _relative(parent: Path, value: str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else parent / path).resolve()


def _name(namespace: str, name: str) -> str:
    absolute = name if name.startswith("/") else namespace + "/" + name
    if not re.fullmatch(r"/(?:[A-Za-z_][A-Za-z_0-9]*/)*[A-Za-z_][A-Za-z_0-9]*", absolute):
        raise ValueError("invalid ROS endpoint name")
    if not absolute.startswith(namespace + "/"):
        raise ValueError("gateway endpoint escapes its robot namespace")
    return absolute


def required_gateway_endpoints(config: ProfiledRosConfig) -> list[Endpoint]:
    """Roles belong to the controller, not the Gateway client."""
    definitions = (
        (config.action_name, "action/ProfiledExecute", "action_server"),
        (config.state_topic, "msg/ProfiledDriverState", "publisher"),
        (config.observation_topic, "msg/ProfiledObservation", "publisher"),
        (config.lease_topic, "msg/CommandLease", "subscriber"),
        (config.stop_service, "srv/EnterSafeStop", "service_server"),
    )
    return [Endpoint(name=_name(config.namespace, name),
                     ros_type="tangying_robot_msgs/" + kind, role=role)
            for name, kind, role in definitions]


def _unique_endpoints(endpoints: list[Endpoint]):
    identities = [(item.name, item.role) for item in endpoints]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate endpoint name/role")
    for item in endpoints:
        category = item.ros_type.split("/")[1]
        expected = {"action_server": "action", "service_server": "srv"}.get(item.role, "msg")
        if category != expected:
            raise ValueError("endpoint type category disagrees with its role")


def _placeholders(value, prefix="") -> list[str]:
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.extend(_placeholders(item, f"{prefix}.{key}" if prefix else key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_placeholders(item, f"{prefix}[{index}]"))
    elif isinstance(value, str) and ("replace-with-" in value or value in {"TODO", "TBD"}):
        found.append(prefix)
    return found


def inspect_binding(path: Path, *, graph_path: Path | None = None,
                    now_unix_ms: int | None = None) -> dict:
    """Validate immutable input bytes, returning no control capability."""
    raw, binding_sha = _load(path)
    binding = DeviceBinding.model_validate(raw)
    profile_path = _relative(path.parent, binding.profile_path)
    gateway_path = _relative(path.parent, binding.gateway_config_path)
    profile_raw, profile_sha = _load(profile_path)
    gateway_raw, gateway_sha = _load(gateway_path)
    profile = RobotProfile.model_validate(profile_raw)
    gateway = ProfiledRosConfig.model_validate(gateway_raw)
    if _relative(gateway_path.parent, gateway.profile_path) != profile_path:
        raise ValueError("binding and gateway refer to different profile files")
    if binding.robot_id != profile.robot_id or binding.device.model != profile.model_id:
        raise ValueError("binding robot/model differs from profile")
    if binding.profile_revision != profile_revision(profile):
        raise ValueError("binding profileRevision does not match the current profile")
    if binding.commissioning_id != gateway.commissioning_id:
        raise ValueError("binding commissioningId differs from gateway")
    # Namespace validation is deliberately stricter than the runtime's generic
    # string field, so several commissioned robots cannot share default names.
    if not re.fullmatch(r"/(?:[A-Za-z_][A-Za-z_0-9]*/)*[A-Za-z_][A-Za-z_0-9]*", gateway.namespace):
        raise ValueError("an explicit non-root robot namespace is required")
    mobility = profile.mobility
    if mobility is None:
        raise ValueError("device binding requires a mobility profile")
    controller = binding.controller
    stop = mobility.safe_stop
    if (controller.safe_stop_strategy != stop.strategy
            or controller.stop_procedure_id != stop.procedure_id
            or controller.command_loss_timeout_ms > stop.command_loss_timeout_ms):
        raise ValueError("controller stop/watchdog declaration disagrees with profile")
    allowed = {"volume3d"} if mobility.domain == "air" else {"2d", "terrain25d", "terrain_layered"}
    if controller.navigation_dimension not in allowed:
        raise ValueError("navigation dimension disagrees with robot mobility")
    sensors = {sensor.source_id: sensor for sensor in profile.sensors}
    coordinates = binding.coordinates
    if (coordinates.source_id not in sensors
            or coordinates.transform_revision != sensors[coordinates.source_id].transform_revision):
        raise ValueError("localization source/calibration differs from profile")
    if controller.vendor_transport == "ros2" and not binding.required_vendor_interfaces:
        raise ValueError("ROS2 vendor transport requires typed vendor endpoints")
    expected = required_gateway_endpoints(gateway) + binding.required_vendor_interfaces
    _unique_endpoints(expected)
    spatial_revision = None
    if gateway.spatial_navigation_path:
        spatial_path = _relative(gateway_path.parent, gateway.spatial_navigation_path)
        deployment = SpatialNavigationDeployment.from_file(spatial_path, profile)
        if deployment.policy["mode"] != controller.navigation_dimension:
            raise ValueError("binding navigation dimension differs from spatial deployment")
        spatial_revision = deployment.revision
    unresolved = (_placeholders(binding.to_wire(), "binding")
                  + _placeholders(profile.to_wire(), "profile")
                  + _placeholders(gateway.to_wire(), "gateway"))
    result = {
        "schemaVersion": "robot.ros2.device-preflight.v1",
        "bindingId": binding.binding_id, "robotId": binding.robot_id,
        "configurationValid": True, "configurationComplete": not unresolved,
        "unresolvedFields": unresolved,
        "sourceHashes": {"binding": binding_sha, "profile": profile_sha, "gateway": gateway_sha},
        "profileRevision": binding.profile_revision, "spatialNavigationRevision": spatial_revision,
        "rosDomainId": binding.ros_domain_id, "namespace": gateway.namespace,
        "expectedEndpoints": [item.to_wire() for item in expected],
        "graphSnapshotStatus": "not_supplied",
        "readOnly": True, "motionAuthorized": False, "hardwareValidated": False,
        "remainingValidation": ["vendor model/firmware/SDK compatibility", "controller implementation",
                                "live driver and sensor freshness", "independent arrival measurement",
                                "physical stop and command-loss response"],
    }
    if graph_path is not None:
        graph_raw, graph_sha = _load(graph_path)
        graph = GraphSnapshot.model_validate(graph_raw)
        now = int(time.time() * 1000) if now_unix_ms is None else now_unix_ms
        if not 0 <= now - graph.observed_at_unix_ms <= 30_000:
            raise ValueError("graph snapshot is stale or future dated")
        if (graph.robot_id != binding.robot_id or graph.namespace != gateway.namespace
                or graph.ros_domain_id != binding.ros_domain_id):
            raise ValueError("graph snapshot robot/namespace/domain differs from binding")
        _unique_endpoints(graph.endpoints)
        actual = {(item.name, item.ros_type, item.role) for item in graph.endpoints}
        missing = [item.to_wire() for item in expected
                   if (item.name, item.ros_type, item.role) not in actual]
        result.update(graphSnapshotStatus="mismatch" if missing else "snapshot_matches",
                      missingEndpoints=missing)
        result["sourceHashes"]["graphSnapshot"] = graph_sha
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binding", type=Path)
    parser.add_argument("--graph", type=Path, help="optional recent offline graph snapshot JSON")
    parser.add_argument("--template", action="store_true",
                        help="accept unresolved template fields, never authorizes hardware")
    args = parser.parse_args(argv)
    try:
        result = inspect_binding(args.binding.resolve(), graph_path=args.graph)
        code = (2 if result["graphSnapshotStatus"] == "mismatch"
                or (not args.template and not result["configurationComplete"]) else 0)
    except (OSError, UnicodeError, ValueError, TypeError, ValidationError) as exc:
        result = {"schemaVersion": "robot.ros2.device-preflight.v1", "configurationValid": False,
                  "error": str(exc), "readOnly": True,
                  "motionAuthorized": False, "hardwareValidated": False}
        code = 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
