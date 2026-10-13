"""Actual DDS registered-frame ingestion and immutable map publication; no hardware."""
from __future__ import annotations

import argparse
import itertools
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from tangying_robot_gateway.spatial_mapping import load_spatial_map
from tangying_robot_gateway.spatial_navigation import SpatialPathPlanner


def run(output, *, automatic):
    import rclpy
    from rcl_interfaces.msg import Parameter, ParameterType, ParameterValue
    from rcl_interfaces.srv import SetParameters
    from std_msgs.msg import String
    from std_srvs.srv import Trigger

    output.mkdir(parents=True, exist_ok=True)
    namespace = f"/spatial_mapper_fixture_{os.getpid()}_{'automatic' if automatic else 'service'}"
    config = {"schemaVersion": "slam.spatial-mapper.v1", "robotId": "recorded-uav", "mapId": "recorded-room",
              "mapRevision": "registered-solution-1", "worldFrameRevision": "fixture-world-1",
              "calibrationRevision": "fixture-calibration-1", "resolution": 1., "origin": [0., 0., 0.],
              "shape": [11, 11, 7], "sourceIds": ["recorded-lidar"], "outputDirectory": str((output / "package").resolve()),
              "namespace": namespace, "maxMessages": 10, "maxRejected": 3, "maxFrames": 2}
    config_path = output / "config.json"
    config_path.write_text(json.dumps(config))
    environment = {**os.environ, "TANGYING_SPATIAL_MAPPING_CONFIG": str(config_path.resolve())}
    log = (output / "mapper.log").open("w")
    command = [sys.executable, "-m", "tangying_ros_gateway.spatial_mapping_node"]
    if automatic:
        command += ["--frames", "1", "--timeout-seconds", "20"]
    process = subprocess.Popen(command, env=environment, stdout=log, stderr=subprocess.STDOUT)
    rclpy.init(args=[])
    node = rclpy.create_node("registered_frame_fixture", namespace=namespace)
    publisher = node.create_publisher(String, "spatial_mapping/registered_frames", 8)
    status_client = node.create_client(Trigger, "spatial_mapping/status")
    save_client = node.create_client(Trigger, "spatial_mapping/save")
    parameter_client = node.create_client(SetParameters, "tangying_spatial_mapper/set_parameters")
    def call(client, request=None):
        future = client.call_async(Trigger.Request() if request is None else request)
        rclpy.spin_until_future_complete(node, future, timeout_sec=10)
        if not future.done() or future.exception() is not None:
            raise RuntimeError("ROS2 spatial mapping service timed out")
        return future.result()
    try:
        deadline = time.monotonic() + 15
        while not (publisher.get_subscription_count() and status_client.service_is_ready()
                   and parameter_client.service_is_ready()):
            if time.monotonic() >= deadline or process.poll() is not None:
                raise RuntimeError("ROS2 mapper did not expose its acquisition endpoints")
            rclpy.spin_once(node, timeout_sec=.1)
        response = call(parameter_client, SetParameters.Request(parameters=[Parameter(name="use_sim_time",
            value=ParameterValue(type=ParameterType.PARAMETER_BOOL, bool_value=True))]))
        assert not response.results[0].successful, "runtime clock domain must stay Unix"
        assert not call(save_client).success, "empty measurement history cannot publish"
        publisher.publish(String(data='{"sourceId":"uncommissioned"}'))
        deadline = time.monotonic() + 5
        while json.loads(call(status_client).message)["rejectedFrames"] < 1:
            if time.monotonic() >= deadline:
                raise RuntimeError("foreign-source frame was not reported as rejected")
        now = int(time.time() * 1000)
        points = [[x + .5, y + .5, z + .5] for x, y, z in itertools.product(range(11), range(11), range(7))
                  if x in (0, 10) or y in (0, 10) or z in (0, 6)]
        frame = {"schemaVersion": "slam.registered-frame.v1",
                 **{key: config[key] for key in ("robotId", "mapId", "mapRevision", "worldFrameRevision", "calibrationRevision")},
                 "frameId": "world", "sourceId": "recorded-lidar", "sourceType": "fixture",
                 "registrationRevision": "recorded-registration-1", "observationId": "recorded-frame-1",
                 "sequence": 1, "observedAtUnixMs": now, "pose": [5.5, 5.5, 3.5, 1., 0., 0., 0.],
                 "sensorOrigin": [5.5, 5.5, 3.5], "points": points}
        (output / "published-frame.json").write_text(json.dumps(frame))
        publisher.publish(String(data=json.dumps(frame)))
        if automatic:
            assert process.wait(timeout=20) == 0
        else:
            deadline = time.monotonic() + 5
            while json.loads(call(status_client).message)["acceptedFrames"] < 1:
                if time.monotonic() >= deadline:
                    raise RuntimeError("registered DDS frame was not accepted")
            saved = call(save_client)
            assert saved.success, saved.message
            assert call(save_client).message == saved.message, "save acknowledgment must be replayable"
            final = json.loads(call(status_client).message)
            assert final["state"] == "saved" and final["rejectedFrames"] == final["acceptedFrames"] == 1
        package = load_spatial_map(config["outputDirectory"])
        planner = SpatialPathPlanner(package["volume"], {"mode": "volume3d", "bodyHalfExtents": [.1, .1, .1],
            "margin": .01, "maxExpanded": 1000, "maxWaypoints": 128})
        route = planner.plan([2.5, 5.5, 2.5, 1., 0., 0., 0.], [7.5, 5.5, 4.5, 1., 0., 0., 0.])
        (output / "planned-route.json").write_text(json.dumps(route, indent=2))
        result = {"passed": True, "mode": "automatic_frames" if automatic else "trigger_service",
                  "evidenceClass": "actual_ros2_dds_recorded_registered_geometry", "hardwareValidated": False,
                  "emptySaveRejected": True, "foreignSourceRejected": True, "acceptedFrames": 1,
                  "runtimeSimTimeRejected": True,
                  "pointCount": len(points), "freeVoxels": package["volume"]["cells"].count(0),
                  "occupiedVoxels": package["volume"]["cells"].count(100),
                  "mapPackageRevision": package["manifest"]["revision"], "routeStartZ": 2.5, "routeGoalZ": 4.5}
        (output / "summary.json").write_text(json.dumps(result, indent=2))
        return result
    finally:
        node.destroy_node()
        rclpy.shutdown()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = [run(args.output / name, automatic=automatic) for name, automatic in (("service", False), ("automatic", True))]
    summary = {"passed": all(result["passed"] for result in results), "hardwareValidated": False, "cases": results}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
