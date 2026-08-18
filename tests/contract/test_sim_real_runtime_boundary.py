from __future__ import annotations

import json
import subprocess
import time
from concurrent import futures
from pathlib import Path

import grpc
from google.protobuf import struct_pb2
from tangying_robot_gateway.backend import BackendResult
from tangying_robot_gateway.service import RobotRuntimeService as PhysicalRuntimeService
from tangying_robot_gateway.xlerobot_backend import XLeRobotDirectBackend
from tangying_robot_proto.robot.v1 import robot_pb2, robot_pb2_grpc
from tangying_sim.server import RobotRuntimeService as SimulationRuntimeService
from tangying_sim.world import TabletopWorld

REPOSITORY_ROOT = Path(__file__).parents[2]


class FakePhysicalTransport:
    """Fake only the hardware transport, not the physical adapter/runtime path."""

    def __init__(self):
        self.sent: list[list[dict[str, float]]] = []
        self.stops: list[str] = []

    def capabilities(self):
        class Capabilities:
            manipulation_ready = True
            blockers: tuple[str, ...] = ()

        return Capabilities()

    def execute_action_chunk(self, actions):
        self.sent.append(actions)

        class Result:
            success = True
            code = "OK"
            message = ""

        return Result()

    def stop(self, reason):
        self.stops.append(reason)


def _command(profile: str, suffix: str) -> robot_pb2.SkillCommand:
    parameters = struct_pb2.Struct()
    parameters.update(
        {
            "objectId": "red-cup",
            "destinationId": "right-bin",
            "action_chunk": [{"left_arm_1.pos": 10.0}],
        }
    )
    return robot_pb2.SkillCommand(
        schema_version="robot.v1",
        command_id=f"cmd-{suffix}",
        task_id="task-contract",
        skill="manipulation.pick",
        target_ref="red-cup",
        parameters=parameters,
        deadline_unix_ms=int(time.time() * 1000) + 30_000,
        lease_ms=5_000,
        idempotency_key=f"task-contract-pick-{suffix}",
        safety_profile=profile,
        approval_id="approval-contract",
    )


def _physical_service(transport: FakePhysicalTransport) -> PhysicalRuntimeService:
    backend = XLeRobotDirectBackend(
        transport,
        entity_provider=lambda: [
            {
                "entity_id": "red-cup",
                "category": "cup",
                "attributes": {"color": "red"},
                "pose_xyz_quat": [0.1, 0.2, 0.3, 1.0, 0.0, 0.0, 0.0],
                "confidence": 0.99,
                "relation": "",
            }
        ],
        verifier=lambda *_: BackendResult(True, confidence=0.99),
    )
    return PhysicalRuntimeService(backend)


def _start_server(servicer):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    robot_pb2_grpc.add_RobotRuntimeServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    return server, f"127.0.0.1:{port}"


def _build_probe(tmp_path: Path) -> Path:
    probe = tmp_path / "runtime-client-probe"
    built = subprocess.run(
        ["go", "build", "-o", str(probe), "./tests/contract/runtime_client_probe"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert built.returncode == 0, built.stderr
    return probe


def _probe(probe: Path, address: str, profile: str, suffix: str) -> dict:
    completed = subprocess.run(
        [str(probe), address, profile, suffix],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_real_simulation_and_physical_adapters_share_the_agent_runtime_contract(tmp_path):
    probe = _build_probe(tmp_path)
    simulation = SimulationRuntimeService(TabletopWorld.seeded(7))
    transport = FakePhysicalTransport()
    physical = _physical_service(transport)
    simulation_server, simulation_address = _start_server(simulation)
    physical_server, physical_address = _start_server(physical)
    try:
        sim_info = simulation.GetRuntimeInfo(robot_pb2.GetRuntimeInfoRequest(), None)
        real_info = physical.GetRuntimeInfo(robot_pb2.GetRuntimeInfoRequest(), None)
        assert (sim_info.adapter, real_info.adapter) == ("mujoco", "xlerobot_direct")
        assert set(sim_info.skills) == set(real_info.skills)

        sim_observation = next(simulation.Observe(robot_pb2.ObserveRequest(), None))
        real_observation = next(physical.Observe(robot_pb2.ObserveRequest(), None))
        assert sim_observation.DESCRIPTOR.full_name == real_observation.DESCRIPTOR.full_name
        assert {item.entity_id for item in sim_observation.entities} >= {"red-cup"}
        assert {item.entity_id for item in real_observation.entities} == {"red-cup"}

        sim_agent_result = _probe(probe, simulation_address, "simulation", "sim-agent")
        real_agent_result = _probe(
            probe,
            physical_address,
            "desktop_standard",
            "real-agent",
        )

        event_simulation = SimulationRuntimeService(TabletopWorld.seeded(7))
        event_physical = _physical_service(transport)
        try:
            sim_events = list(
                event_simulation.execute_for_test(_command("simulation", "sim-events"))
            )
            real_events = list(
                event_physical.execute_for_test(
                    _command("desktop_standard", "real-events")
                )
            )
        finally:
            event_simulation.close()
    finally:
        simulation_server.stop(0).wait(timeout=5)
        physical_server.stop(0).wait(timeout=5)
        simulation.close()

    expected_types = [
        robot_pb2.SKILL_EVENT_ACCEPTED,
        robot_pb2.SKILL_EVENT_RUNNING,
        robot_pb2.SKILL_EVENT_SUCCEEDED,
    ]
    assert [event.type for event in sim_events] == expected_types
    assert [event.type for event in real_events] == expected_types
    for events in (sim_events, real_events):
        assert [event.sequence for event in events] == [1, 2, 3]
        assert all(event.DESCRIPTOR.full_name == "tangying.robot.v1.SkillEvent" for event in events)
        assert events[-1].code == "OK"
    assert sim_agent_result["adapter"] == "mujoco"
    assert real_agent_result["adapter"] == "xlerobot_direct"
    expected_result_fields = {
        "Success",
        "Code",
        "Message",
        "ObservationID",
        "VerificationConfidence",
    }
    assert set(sim_agent_result["result"]) == expected_result_fields
    assert set(real_agent_result["result"]) == expected_result_fields
    assert sim_agent_result["result"]["Success"] is True
    assert real_agent_result["result"]["Success"] is True
    assert sim_agent_result["result"]["Code"] == "OK"
    assert real_agent_result["result"]["Code"] == "OK"
    assert transport.sent == [
        [{"left_arm_1.pos": 10.0}],
        [{"left_arm_1.pos": 10.0}],
    ]
