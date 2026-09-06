"""Two hardware shapes through the real Python service and Go Agent client.

Only sensor producers and actuator handlers are fixtures; the transport,
canonical validation, safety supervisor and Go grounding are production code.
No physical hardware is contacted by these tests.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import grpc
import pytest
from tangying_robot_gateway.plugin_backend import PluginBackend
from tangying_robot_gateway.runtime import Result
from tangying_robot_gateway.service import RobotRuntimeService
from tangying_robot_proto.robot.v1 import robot_pb2_grpc

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def heterogeneous_probe(tmp_path_factory):
    probe = tmp_path_factory.mktemp("heterogeneous-go-probe") / "probe"
    built = subprocess.run(
        ["go", "build", "-o", str(probe), "./tests/contract/heterogeneous_runtime_probe"],
        cwd=ROOT, capture_output=True, text=True, timeout=90, check=False,
    )
    assert built.returncode == 0, built.stderr
    return probe


def profile_for(embodiment):
    arm = embodiment == "arm"
    robot_id = "linear-arm-1" if arm else "mobile-lidar-2"
    return {
        "schemaVersion": "robot.profile.v1", "robotId": robot_id,
        "adapterId": "fixture_linear_arm" if arm else "fixture_mobile_lidar",
        "adapterVersion": "1.0.0", "modelId": "linear-gripper" if arm else "two-wheel-sensor",
        "embodiment": embodiment,
        "joints": [
            {"name": "slide", "kind": "prismatic", "unit": "m", "lower": -0.4, "upper": 0.4},
            {"name": "gripper", "kind": "prismatic", "unit": "m", "lower": 0.0, "upper": 0.05},
        ] if arm else [
            {"name": name, "kind": "continuous", "unit": "rad", "lower": -3.14, "upper": 3.14}
            for name in ["wheel-left", "wheel-right"]
        ],
        "endEffectors": [{"id": "gripper", "kind": "gripper", "jointNames": ["gripper"]}] if arm else [],
        "sensors": [{
            "sourceId": robot_id + "/perception", "sourceType": "rgbd_camera" if arm else "lidar",
            "frameId": "depth-optical" if arm else "lidar-link",
            "transformRevision": "commissioned-transform-1", "maxAgeMs": 2000,
        }],
        "actionLimits": {
            "slide.position": {"min": -0.4, "max": 0.4, "unit": "m"},
            "gripper.gap": {"min": 0.0, "max": 0.05, "unit": "m"},
        } if arm else {
            "wheel-left.velocity": {"min": -1.0, "max": 1.0, "unit": "rad/s"},
            "wheel-right.velocity": {"min": -1.0, "max": 1.0, "unit": "rad/s"},
            "navigation.x": {"min": -2.0, "max": 2.0, "unit": "m"},
            "navigation.y": {"min": -2.0, "max": 2.0, "unit": "m"},
            "navigation.z": {"min": 0.0, "max": 0.0, "unit": "m"},
        },
        "tools": ["observe_scene", "arm.move", "manipulation.pick", "emergency_stop"] if arm else [
            "observe_scene", "navigation.navigate", "emergency_stop",
        ],
    }


class SceneProvider:
    def __init__(self, profile, *, stale):
        self.profile = profile
        self.stale = stale
        self.captures = []
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            sequence = len(self.captures) + 1
            sensor = self.profile["sensors"][0]
            captured = int(time.time() * 1000) - (10_000 if self.stale else 125)
            value = {
                "schemaVersion": "scene.reconstruction.v1", "robotId": self.profile["robotId"],
                "observationId": f"capture-{sequence}", "sourceId": sensor["sourceId"],
                "sourceType": sensor["sourceType"], "sourceFrameId": sensor["frameId"],
                "frameId": "world", "transformRevision": sensor["transformRevision"],
                "observedAtUnixMs": captured, "sequence": sequence, "units": "m",
                "entities": [
                    {"entityId": "red-cup", "category": "cup", "attributes": {"color": "red"},
                     "pose": [0.1, 0.2, 0.3, 1.0, 0.0, 0.0, 0.0], "confidence": 0.98,
                     "relation": "on:workbench"},
                    {"entityId": "workbench", "category": "table", "attributes": {},
                     "pose": [0.0, 0.0, 0.2, 1.0, 0.0, 0.0, 0.0], "confidence": 0.99},
                    {"entityId": "right-bin", "category": "storage_bin", "attributes": {"side": "right"},
                     "pose": [0.5, 0.2, 0.3, 1.0, 0.0, 0.0, 0.0], "confidence": 0.97},
                ],
                "points": [[0.1, 0.2, 0.3], [0.15, 0.2, 0.3]],
            }
            self.captures.append(value)
            return value


def run_probe(probe, profile, *, stale=False):
    provider = SceneProvider(profile, stale=stale)
    movements = []
    stops = []

    def move(command):
        movements.append(command)
        return Result(True, "OK", observation_id=provider.captures[-1]["observationId"], confidence=0.99)

    backend = PluginBackend(
        profile, observation_provider=provider,
        handlers={"arm.move": move} if profile["embodiment"] == "arm" else {},
        physical_ready=lambda: profile["embodiment"] == "arm",
        stop=stops.append,
        state_provider=lambda: {"driverMode": "fixture", "basePose": [0.0, 0.0, 0.0]},
    )
    return exchange_probe(probe, backend), provider, movements, stops


def exchange_probe(probe, backend, *arguments):
    with ThreadPoolExecutor(max_workers=4) as workers:
        server = grpc.server(workers)
        robot_pb2_grpc.add_RobotRuntimeServicer_to_server(RobotRuntimeService(backend), server)
        port = server.add_insecure_port("127.0.0.1:0")
        # Absolute executable + no cwd + close_fds=False permits posix_spawn on
        # macOS, avoiding gRPC C-core at-fork handlers. Python-created unrelated
        # FDs are non-inheritable; the subprocess only needs its stdio pipes.
        process = subprocess.Popen(
            [str(probe), f"127.0.0.1:{port}", *arguments],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            close_fds=False, text=True,
        )
        server.start()
        try:
            stdout, stderr = process.communicate(timeout=40)
            assert process.returncode == 0, stderr
            result = json.loads(stdout)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
            server.stop(0).wait(timeout=5)
    return result


@pytest.mark.parametrize("embodiment", ["arm", "mobile_base"])
def test_two_embodiments_share_real_agent_runtime_and_canonical_perception(heterogeneous_probe, embodiment):
    profile = profile_for(embodiment)
    result, provider, movements, stops = run_probe(heterogeneous_probe, profile)
    assert result["infoError"] == ""
    info = result["info"]
    assert info["RobotID"] == profile["robotId"]
    assert info["Adapter"] == profile["adapterId"]
    assert info["AdapterVersion"] == profile["adapterVersion"]
    assert info["RobotProfile"]["modelId"] == profile["modelId"]
    assert info["RobotProfile"]["embodiment"] == embodiment
    assert info["CatalogRevision"]
    available = {item["Name"]: item["Available"] for item in info["Capabilities"]}
    assert available["observe_scene"] and available["emergency_stop"]
    assert result["observeAvailable"] is True
    assert result["pickAvailable"] is False
    assert result["telemetryError"] == ""
    telemetry = result["telemetry"]
    reconstruction = telemetry["reconstruction"]
    original = next(item for item in provider.captures if item["observationId"] == reconstruction["observationId"])
    assert reconstruction["robotId"] == profile["robotId"]
    assert reconstruction["sourceId"] == profile["sensors"][0]["sourceId"]
    assert reconstruction["sourceType"] == profile["sensors"][0]["sourceType"]
    assert reconstruction["sourceFrameId"] == profile["sensors"][0]["frameId"]
    assert reconstruction["frameId"] == "world" and reconstruction["units"] == "m"
    assert reconstruction["points"] == original["points"]
    assert result["capturedUnixMs"] == original["observedAtUnixMs"]
    assert reconstruction["observedAtUnixMs"] == result["capturedUnixMs"]
    assert telemetry["entities"][0]["pose"] == original["entities"][0]["pose"]
    assert telemetry["entities"][0]["relation"] == "on:workbench"
    assert result["groundError"] == ""
    assert result["ground"]["object"]["id"] == "red-cup"
    assert result["ground"]["destination"]["id"] == "right-bin"
    assert "source mismatch" in result["wrongSourceError"]
    assert result["observeError"] == ""
    assert result["observeResult"]["Success"]
    assert result["observeResult"]["ObservationID"] in {item["observationId"] for item in provider.captures}
    if embodiment == "arm":
        assert available["arm.move"]
        assert result["moveAvailable"] is True
        assert result["unapprovedError"] == ""
        assert result["unapprovedResult"]["Code"] == "APPROVAL_REQUIRED"
        assert result["staleCatalogResult"]["Code"] == "TOOL_CATALOG_STALE"
        assert result["moveError"] == ""
        assert result["moveResult"]["Success"]
        assert len(movements) == 1
        assert movements[0].parameters == {"action_chunk": [{"slide.position": 0.1}]}
        assert movements[0].approval_id == "fixture-approval"
        assert movements[0].catalog_revision == info["CatalogRevision"]
    else:
        assert available["navigation.navigate"] is False
        assert result["moveAvailable"] is False
        assert result["navigateAvailable"] is False
        assert movements == []
    assert stops == []


def test_stale_reconstruction_cannot_ground_or_authorize_fixture_motion(heterogeneous_probe):
    result, _, movements, _ = run_probe(heterogeneous_probe, profile_for("arm"), stale=True)
    assert result["infoError"] == ""
    assert "FailedPrecondition" in result["telemetryError"]
    assert "stale" in result["telemetryError"].lower()
    assert "FailedPrecondition" in result["groundError"]
    assert not result["observeResult"]["Success"]
    assert result["observeResult"]["Code"] == "RECONSTRUCTION_INVALID"
    assert not result["moveResult"]["Success"]
    assert result["moveResult"]["Code"] == "RECONSTRUCTION_INVALID"
    assert movements == []


def test_real_go_seven_step_plan_runs_through_strict_plugin_and_verifies_state(heterogeneous_probe):
    from examples.robots.simulated import arm

    result = exchange_probe(heterogeneous_probe, arm(), "plan")
    assert result["infoError"] == ""
    assert result["groundError"] == ""
    assert result["planShapeError"] == ""
    steps = result["steps"]
    assert [step["skill"] for step in steps] == [
        "observe_scene", "resolve_targets", "plan_grasp", "manipulation.pick",
        "verify_grasp", "manipulation.place", "verify_placement",
    ]
    for step in steps:
        assert step["error"] == ""
        assert step["result"]["Success"], step
    for index, target in [(3, "red-block"), (5, "tray")]:
        params = steps[index]["parameters"]
        assert params["targetRef"] == target
        assert steps[index]["targetRef"] == target
        assert params["policy_execution"]["framework"] == "deterministic"
        assert params["policy_execution"]["artifactSha256"].startswith("deterministic:")
        assert steps[index]["approvalId"] == "approval:heterogeneous-full-plan:physical"
    assert result["finalTelemetryError"] == ""
    state = result["finalTelemetry"]
    assert state["robotState"]["held"] == ""
    block = next(item for item in state["reconstruction"]["entities"] if item["entityId"] == "red-block")
    assert block["relation"] == "inside:tray"
    assert block["pose"] == [0.4, 0.0, 0.1, 1.0, 0.0, 0.0, 0.0]
