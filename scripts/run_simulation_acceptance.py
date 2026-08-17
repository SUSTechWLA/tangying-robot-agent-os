from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from google.protobuf import struct_pb2
from tangying_robot_proto.robot.v1 import robot_pb2
from tangying_sim.server import RobotGatewayService
from tangying_sim.world import TabletopWorld


def run_episodes(*, episodes: int, base_seed: int) -> dict:
    results = []
    safety_violations = 0
    for index in range(episodes):
        service = RobotGatewayService(TabletopWorld.seeded(base_seed + index))
        commands = [
            make_command(index, "observe", "observe_scene", ""),
            make_command(index, "resolve", "resolve_targets", ""),
            make_command(index, "plan", "plan_grasp", "red-cup"),
            make_command(index, "pick", "manipulation.pick", "red-cup"),
            make_command(index, "verify-grasp", "verify_grasp", "red-cup"),
            make_command(index, "place", "manipulation.place", "right-bin"),
            make_command(
                index,
                "verify-place",
                "verify_placement",
                "right-bin",
                parameters=struct_pb2.Struct(
                    fields={"objectId": struct_pb2.Value(string_value="red-cup")}
                ),
            ),
        ]
        terminal_events = [list(service.execute_for_test(command))[-1] for command in commands]
        success = all(event.type == robot_pb2.SKILL_EVENT_SUCCEEDED for event in terminal_events)
        safety_violations += sum(
            event.type == robot_pb2.SKILL_EVENT_SAFETY_STOPPED for event in terminal_events
        )
        results.append(
            {
                "episode": index + 1,
                "seed": base_seed + index,
                "success": success,
                "terminalCodes": [event.code for event in terminal_events],
            }
        )
    successful = sum(result["success"] for result in results)
    return {
        "schemaVersion": 1,
        "episodes": episodes,
        "successfulEpisodes": successful,
        "successRate": successful / episodes if episodes else 0.0,
        "safetyViolations": safety_violations,
        "results": results,
    }


def make_command(
    episode: int,
    step: str,
    skill: str,
    target: str,
    *,
    parameters: struct_pb2.Struct | None = None,
) -> robot_pb2.SkillCommand:
    return robot_pb2.SkillCommand(
        schema_version="robot.v1",
        command_id=f"episode-{episode}-{step}",
        task_id=f"acceptance-{episode}",
        skill=skill,
        target_ref=target,
        parameters=parameters or struct_pb2.Struct(),
        deadline_unix_ms=int(time.time() * 1000) + 30_000,
        lease_ms=5_000,
        idempotency_key=f"acceptance-{episode}-{step}",
        safety_profile="simulation",
        approval_id=f"acceptance-{episode}-approval",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20260817)
    parser.add_argument("--output", type=Path, default=Path("artifacts/acceptance/simulation.json"))
    args = parser.parse_args()
    report = run_episodes(episodes=args.episodes, base_seed=args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("episodes", "successfulEpisodes", "successRate", "safetyViolations")}))
    if report["successRate"] < 0.9 or report["safetyViolations"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
