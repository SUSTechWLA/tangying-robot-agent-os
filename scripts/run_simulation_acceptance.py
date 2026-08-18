from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from google.protobuf import struct_pb2
from tangying_robot_proto.robot.v1 import robot_pb2
from tangying_sim.server import RobotRuntimeService
from tangying_sim.world import TabletopWorld


def run_episodes(*, episodes: int, base_seed: int) -> dict:
    results = []
    safety_violations = 0
    for index in range(episodes):
        service = RobotRuntimeService(TabletopWorld.seeded(base_seed + index))
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


OBJECT_MATRIX = [
    (category, color)
    for category in ("cup", "bottle", "block")
    for color in ("red", "blue", "green")
]


def run_object_matrix(*, base_seed: int) -> dict:
    """Exercise every advertised object category/color through the gateway."""
    results = []
    for index, (category, color) in enumerate(OBJECT_MATRIX):
        results.extend(_run_matrix_goal(base_seed + index, category, color, "right_side"))
        results.extend(_run_matrix_goal(base_seed + 1000 + index, category, color, "front_side"))
    successful = sum(result["success"] for result in results)
    return {
        "schemaVersion": 1,
        "goals": len(results),
        "successfulGoals": successful,
        "successRate": successful / len(results) if results else 0.0,
        "results": results,
    }


def _run_matrix_goal(seed: int, category: str, color: str, relation: str) -> list[dict]:
    world = TabletopWorld.seeded(seed)
    service = RobotRuntimeService(world)
    obj = world.resolve(category=category, color=color)
    destination = world.resolve(
        category="delivery_tray" if relation == "front_side" else "storage_bin",
        relation=relation,
    )
    commands = [
        make_command(seed, "observe", "observe_scene", ""),
        make_command(seed, "resolve", "resolve_targets", ""),
        make_command(seed, "plan", "plan_grasp", obj.entity_id),
        make_command(seed, "pick", "manipulation.pick", obj.entity_id),
        make_command(seed, "verify-grasp", "verify_grasp", obj.entity_id),
        make_command(seed, "place", "manipulation.place", destination.entity_id),
        make_command(
            seed,
            "verify-place",
            "verify_placement",
            destination.entity_id,
            parameters=struct_pb2.Struct(
                fields={"objectId": struct_pb2.Value(string_value=obj.entity_id)}
            ),
        ),
    ]
    terminal_events = [list(service.execute_for_test(command))[-1] for command in commands]
    success = all(event.type == robot_pb2.SKILL_EVENT_SUCCEEDED for event in terminal_events)
    return [
        {
            "seed": seed,
            "category": category,
            "color": color,
            "relation": relation,
            "success": success,
            "terminalCodes": [event.code for event in terminal_events],
        }
    ]


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
    object_matrix = run_object_matrix(base_seed=args.seed)
    report["objectMatrix"] = object_matrix
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                **{key: report[key] for key in ("episodes", "successfulEpisodes", "successRate", "safetyViolations")},
                "objectMatrixGoals": object_matrix["goals"],
                "objectMatrixSuccessful": object_matrix["successfulGoals"],
            }
        )
    )
    if (
        report["successRate"] < 0.9
        or report["safetyViolations"]
        or object_matrix["successRate"] < 1.0
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
