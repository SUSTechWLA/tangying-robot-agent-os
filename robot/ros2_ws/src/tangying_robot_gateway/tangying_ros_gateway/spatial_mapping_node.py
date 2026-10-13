"""ROS2 acquisition of externally registered SE3 measurements; never commands motion."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

from tangying_robot_gateway.spatial_mapping import (
    RegisteredSpatialSlamProvider,
    _json,
    read_spatial_json,
)

_REQUIRED = {"schemaVersion", "robotId", "mapId", "mapRevision", "worldFrameRevision", "calibrationRevision",
             "resolution", "origin", "shape", "sourceIds", "outputDirectory"}
_DEFAULTS = {"namespace": "", "nodeName": "tangying_spatial_mapper", "frameTopic": "spatial_mapping/registered_frames",
             "saveService": "spatial_mapping/save", "statusService": "spatial_mapping/status",
             "maxFrameBytes": 2 * 1024 * 1024, "maxMessages": 20_000, "maxRejected": 100,
             "maxMeasurementAgeMs": 60_000, "maxFrames": 10_000, "maxPoints": 1_000_000,
             "maxRayCells": 10_000_000}


def mapping_config(document):
    if (not isinstance(document, dict) or not _REQUIRED <= document.keys() or set(document) - _REQUIRED - _DEFAULTS.keys()
            or document["schemaVersion"] != "slam.spatial-mapper.v1"):
        raise ValueError("spatial mapper configuration fields do not match slam.spatial-mapper.v1")
    config = {**_DEFAULTS, **document}
    path = config["outputDirectory"]
    if not isinstance(path, str) or len(path) > 4096 or not Path(path).is_absolute():
        raise ValueError("outputDirectory must be an explicit absolute package path")
    if Path(path).exists() or Path(path).is_symlink():
        raise FileExistsError("stream mapping requires a new immutable output directory")
    sources = config["sourceIds"]
    if (not isinstance(sources, list) or not 1 <= len(sources) <= 32
            or any(not isinstance(v, str) or not v or len(v) > 256 or any(c.isspace() or ord(c) < 32 for c in v) for v in sources)
            or len(set(sources)) != len(sources)):
        raise ValueError("sourceIds must explicitly allow 1..32 unique bounded sensor identities")
    config["sourceIds"] = list(sources)
    for key in ("frameTopic", "saveService", "statusService"):
        value = config[key]
        if (not isinstance(value, str) or len(value) > 256 or not re.fullmatch(r"/?[A-Za-z_][A-Za-z0-9_]*(/[A-Za-z_][A-Za-z0-9_]*)*", value)):
            raise ValueError(f"{key} must be a concrete bounded ROS2 topic/service name")
    if config["saveService"] == config["statusService"]:
        raise ValueError("save and status services must have distinct names")
    if not isinstance(config["nodeName"], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", config["nodeName"]):
        raise ValueError("nodeName invalid")
    if not isinstance(config["namespace"], str) or (config["namespace"] and not re.fullmatch(r"(/[A-Za-z_][A-Za-z0-9_]*)+", config["namespace"])):
        raise ValueError("namespace must be empty or an absolute ROS2 namespace")
    for name, maximum in (("maxFrameBytes", 8 * 1024 * 1024), ("maxMessages", 100_000), ("maxRejected", 10_000)):
        if type(config[name]) is not int or not 1 <= config[name] <= maximum:
            raise ValueError(f"{name} exceeds its integer budget")
    if config["maxRejected"] > config["maxMessages"]:
        raise ValueError("rejection budget cannot exceed total message budget")
    # Centralize all physical bounds, identity and resource checks in the same
    # provider used by the offline importer. Construction dispatches no motion.
    _provider(config)
    return config


def _provider(config):
    return RegisteredSpatialSlamProvider(robot_id=config["robotId"], map_id=config["mapId"],
        map_revision=config["mapRevision"], world_frame_revision=config["worldFrameRevision"],
        calibration_revision=config["calibrationRevision"], resolution=config["resolution"],
        origin=config["origin"], shape=config["shape"], max_measurement_age_ms=config["maxMeasurementAgeMs"],
        max_frames=config["maxFrames"], max_points=config["maxPoints"], max_ray_cells=config["maxRayCells"])


class SpatialMappingSession:
    """Serialized acquisition, bounded rejection, and one immutable save boundary."""
    def __init__(self, config):
        self.config = mapping_config(config)
        self.provider = _provider(self.config)
        self._lock = threading.RLock()
        self._state = "collecting"
        self._messages = self._rejected = 0
        self._last_error = ""
        self._manifest = None

    def status(self):
        with self._lock:
            return {"state": self._state, "messages": self._messages, "acceptedFrames": self.provider.frame_count,
                    "rejectedFrames": self._rejected, "lastError": self._last_error,
                    "packageRevision": self._manifest["revision"] if self._manifest else "",
                    "outputDirectory": self.config["outputDirectory"]}

    def accept(self, payload, *, now_unix_ms=None):
        with self._lock:
            if self._state != "collecting":
                return False
            if self._messages >= self.config["maxMessages"]:
                self._state, self._last_error = "faulted", "message budget exhausted"
                raise ValueError(self._last_error)
            self._messages += 1
            try:
                maximum = self.config["maxFrameBytes"]
                if not isinstance(payload, str) or len(payload) > maximum or len(payload.encode()) > maximum:
                    raise ValueError("registered ROS2 frame exceeds byte budget")
                frame = _json(payload)
                if not isinstance(frame, dict) or frame.get("sourceId") not in self.config["sourceIds"]:
                    raise ValueError("registered frame source is not commissioned for this mapper")
                self.provider.add(frame, now_unix_ms=now_unix_ms)
                return True
            except (ValueError, TypeError, OverflowError) as error:
                self._rejected += 1
                self._last_error = str(error)[:512]
                if self._rejected >= self.config["maxRejected"]:
                    self._state = "faulted"
                raise ValueError(self._last_error) from error

    def save(self):
        with self._lock:
            if self._manifest is not None:
                return dict(self._manifest)
            if self._state == "faulted":
                raise ValueError(f"faulted mapping session cannot publish: {self._last_error}")
            if not self.provider.frame_count:
                raise ValueError("no accepted registered measurements to save")
            # Freeze BEFORE publishing: a retry of a failed disk write uses the
            # identical snapshot; it never silently absorbs later messages.
            self._state = "saving"
            try:
                self._manifest = self.provider.publish(self.config["outputDirectory"])
            except (OSError, ValueError, TypeError) as error:
                self._state, self._last_error = "save_failed", str(error)[:512]
                raise
            self._state = "saved"
            return dict(self._manifest)


def create_node(config):
    from rcl_interfaces.msg import SetParametersResult
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, ReliabilityPolicy
    from std_msgs.msg import String
    from std_srvs.srv import Trigger

    class SpatialMappingNode(Node):
        def __init__(self):
            super().__init__(config["nodeName"], namespace=config["namespace"])
            if self.get_parameter("use_sim_time").value is not False:
                raise ValueError("spatial mapping requires Unix capture timestamps; use_sim_time is unsupported")
            self.add_on_set_parameters_callback(self._validate_parameters)
            self.session = SpatialMappingSession(config)
            self.subscription = self.create_subscription(String, config["frameTopic"], self._frame,
                QoSProfile(depth=8, reliability=ReliabilityPolicy.RELIABLE))
            self.save_service = self.create_service(Trigger, config["saveService"], self._save)
            self.status_service = self.create_service(Trigger, config["statusService"], self._status)
            self.get_logger().info("spatial mapper accepts externally registered SE3 measurements; no motion interfaces")

        def _validate_parameters(self, parameters):
            valid = all(p.name != "use_sim_time" or p.value is False for p in parameters)
            return SetParametersResult(successful=valid, reason="spatial mapper requires Unix capture time" if not valid else "")

        def _frame(self, message):
            try:
                if self.session.accept(message.data):
                    count = self.session.provider.frame_count
                    if count == 1 or count % 100 == 0:
                        self.get_logger().info(f"spatial mapper accepted {count} frames")
            except (ValueError, TypeError, OSError) as error:
                self.get_logger().warning(f"spatial mapper rejected: {str(error)[:512]}")

        def _save(self, request, response):
            del request
            try:
                manifest = self.session.save()
                response.success = True
                response.message = json.dumps({"revision": manifest["revision"], "mapRevision": manifest["mapRevision"],
                                                "outputDirectory": config["outputDirectory"]})
                self.get_logger().info(f"spatial package saved: {manifest['revision']}")
            except (ValueError, TypeError, OSError) as error:
                response.success, response.message = False, str(error)[:512]
                self.get_logger().error(f"spatial package save rejected: {response.message}")
            return response

        def _status(self, request, response):
            del request
            response.success, response.message = True, json.dumps(self.session.status())
            return response

    return SpatialMappingNode()


def main(args=None):
    import rclpy
    from rclpy.utilities import remove_ros_args

    raw = sys.argv if args is None else [sys.argv[0], *args]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=0, help="save and exit after N accepted frames; 0 waits for save service")
    parser.add_argument("--timeout-seconds", type=int, default=120, help="bounded acquisition timeout when --frames is used")
    options = parser.parse_args(remove_ros_args(args=raw)[1:])
    node = None
    initialized = False
    try:
        path = os.environ.get("TANGYING_SPATIAL_MAPPING_CONFIG", "")
        if not path or not Path(path).is_absolute():
            raise ValueError("TANGYING_SPATIAL_MAPPING_CONFIG must name an absolute configuration JSON")
        config = mapping_config(read_spatial_json(path, maximum=128 * 1024))
        if not 0 <= options.frames <= config["maxFrames"] or not 1 <= options.timeout_seconds <= 3600:
            raise ValueError("invalid bounded acquisition frame count or timeout")
        rclpy.init(args=raw[1:])
        initialized = True
        node = create_node(config)
        deadline = time.monotonic() + options.timeout_seconds
        while rclpy.ok():
            rclpy.spin_once(node, timeout_sec=.1)
            status = node.session.status()
            if status["state"] == "faulted":
                raise ValueError(status["lastError"])
            if options.frames and status["acceptedFrames"] >= options.frames:
                manifest = node.session.save()
                print(json.dumps({"revision": manifest["revision"], "acceptedFrames": status["acceptedFrames"],
                                  "evidenceKind": "external_registered_ros2_measurements", "hardwareValidated": False}), flush=True)
                return 0
            if options.frames and time.monotonic() >= deadline:
                raise TimeoutError("bounded ROS2 registered-frame acquisition timed out")
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, TypeError) as error:
        print(f"spatial mapper stopped: {str(error)[:512]}", file=sys.stderr, flush=True)
        return 2
    finally:
        if node is not None:
            node.destroy_node()
        if initialized and rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
