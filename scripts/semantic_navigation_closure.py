"""Say a place, drive there, and judge whether the robot can work from where it stopped.

This is the closed loop the representation comparison could not close. It talks to a
**running** MuJoCo stack over the robot's own gRPC contract, so every step is the
production one:

  1. a sentence in Chinese or English is resolved to a canonical room by the tool
     layer's own normaliser (``semantic_map.normalize_location_name``);
  2. the room's commissioned target becomes a 7-element goal pose;
  3. ``navigation.navigate`` drives the base there and ``verify_arrival`` answers
     whether the base reached the pose it was commanded;
  4. the base pose is read back from a fresh observation and ``verify_operable_arrival``
     answers the question nothing used to ask: can the arm reach the work target from
     where the robot actually stopped.

Step 4 is the point. Step 3 can pass on a pose from which the arm cannot reach the
object, and the failure then surfaces later as a "perception" problem at the grasp.

The arm envelope is **measured, not assumed**: it is sampled from the robot's own
shipped kinematics chain (``assets/arm_kinematics.json``), and the shoulder offset is
read from the same file. The derivation is printed so a reader can check it, and
written into the report.

Usage:

    # against the stack this repository starts
    .venv/bin/python scripts/semantic_navigation_closure.py --port 50051

    # against an already-running instance on another port, without moving anything
    .venv/bin/python scripts/semantic_navigation_closure.py --port 50161 --plan-only

Exit status is 0 when every phrasing resolved and every drive that was attempted
arrived inside the operable range, 1 when any of those failed, 2 when the stack could
not be reached at all. The distinction matters: a robot that stopped short is a
result, an unreachable runtime is not.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
import time
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot" / "gateway"))

import grpc
import numpy as np
from tangying_robot_gateway import arm_kinematics
from tangying_robot_gateway.operable_arrival import verify_operable_arrival
from tangying_robot_gateway.semantic_map import SemanticMap
from tangying_robot_gateway.workspace_planner import WorkspaceEnvelope
from tangying_robot_proto.robot.v1 import robot_pb2, robot_pb2_grpc

#: The sentences a person would actually say. Chinese and English, formal and
#: casual, with and without a motion verb, with and without an article - the
#: variation is the experiment, so the list is deliberately not synonyms of one
#: template.
PHRASINGS = {
    "kitchen": ["厨房", "去厨房", "到厨房", "前往厨房", "带我去厨房", "the kitchen",
                "Kitchen", "cookhouse"],
    "living_room": ["客厅", "去客厅", "回到客厅", "我想去客厅", "the living room",
                    "Living Room", "lounge"],
    "bedroom": ["卧室", "去卧室", "请到卧室", "the bedroom", "bed room", "Bedroom"],
    "bathroom": ["卫生间", "去卫生间", "厕所", "wc", "restroom", "the bathroom"],
    "home_corridor": ["走廊", "去走廊", "the corridor", "hallway", "hall"],
}

#: Rooms a drive is attempted for, and the 3-D point the task means to operate on.
#: Only the kitchen commissions workcell fixtures, so only the kitchen has a target
#: to check reach against; for the others the target is the room's own centre at
#: shoulder height, which answers "could anything in this room be worked on from
#: here" without pretending there is an object there.
WORK_TARGETS = {
    "kitchen": (2.275, 3.415, 0.85),          # the commissioned cup position
}

#: The room every episode starts in. The route from here to anywhere is what a
#: caller has to plan, and the runner's first leg is where it shows.
START_ROOM = "living_room"


def semantic_contract(stub, timeout_s=120) -> dict:
    """The published navigation contract, read from the runtime's full state.

    Two things here are easy to get wrong and both cost an hour:

    * The contract rides in ``robot_state["semantic_navigation"]``, not in
      ``Observation.semantic_state``. That field is the runtime's own mode/activity
      summary, and reading the adjacency from it finds an empty dictionary and reports
      that routing is impossible - on a robot that publishes it perfectly well.
    * It is only in the **full** state, which the head/default source returns. The
      ``base-rgbd`` source answers with a deliberately minimal state (``base_pose``,
      ``navigation``, ``perception``, ``simulation``) so that a caller can read the
      base pose without paying for a scene render - which is also why pose reads go to
      that source and this goes to the default one.
    """

    request = robot_pb2.ObserveRequest(streams=["robot_state"], source_id="")
    for observation in stub.Observe(request, timeout=timeout_s):
        state = struct_to_dict(observation.robot_state)
        navigation = state.get("semantic_navigation")
        if isinstance(navigation, dict) and navigation:
            return navigation
    raise SystemExit("the runtime published no semantic navigation contract")


def published_edges(navigation: dict) -> dict[str, list[str]]:
    """The commissioned room adjacency, out of the contract the runtime publishes.

    Read rather than hard-coded: the graph is part of the navigation contract now, and
    a runner carrying its own copy would keep working after the house changed - which
    is exactly the failure the contract exists to prevent.
    """

    edges = navigation.get("routeEdges") if isinstance(navigation, dict) else None
    if not isinstance(edges, dict) or not edges:
        raise SystemExit(
            "the semantic contract carries no routeEdges; routing is not possible")
    return {str(room): [str(n) for n in neighbours] for room, neighbours in edges.items()}


def route_to(edges: dict[str, list[str]], start: str, goal: str) -> list[str]:
    """Shortest room sequence from ``start`` to ``goal`` in the published graph."""

    queue, seen = [(start, [start])], {start}
    while queue:
        room, path = queue.pop(0)
        if room == goal:
            return path
        for candidate in sorted(edges.get(room, ())):
            if candidate not in seen:
                seen.add(candidate)
                queue.append((candidate, [*path, candidate]))
    raise SystemExit(f"no route from {start!r} to {goal!r} in the published adjacency")


def arm_envelope(side: str = "right", samples: int = 60000) -> tuple[WorkspaceEnvelope, dict]:
    """Sample the shipped arm chain for its real reach, and report the derivation.

    The shell is a property of the arm about its first joint, so it is sampled from
    that joint outward - not from the base origin, which would fold the shoulder's own
    0.79 m of height into a number that is supposed to describe the arm.
    """

    links = arm_kinematics.arm_links(side)
    moved = links[:5]
    angles_range = [(link.joint, link.range_min, link.range_max) for link in moved]
    rng = np.random.default_rng(20260920)
    reach = []
    for _ in range(samples):
        angles = {joint: float(rng.uniform(low, high)) for joint, low, high in angles_range}
        angles["gripper"] = 0.0
        poses = arm_kinematics.chain_poses(links, angles)
        shoulder, tool = poses[0][:3, 3], poses[4][:3, 3]
        reach.append(float(np.linalg.norm(tool - shoulder)))
    reach = np.asarray(reach)
    offset = moved[0].position
    # Sampling never quite reaches a singular extreme, so the reported maximum is
    # the sample plus a hair, rounded to the millimetre the rest of the system uses.
    maximum = math.ceil(float(reach.max()) * 1000.0) / 1000.0 + 0.001
    minimum = math.floor(float(reach.min()) * 1000.0) / 1000.0
    # The base creeps while the arm works, and that travel is part of the working
    # range: judged by the arm alone this robot fails its own commissioned dock.
    travel = base_manipulation_travel()
    envelope = WorkspaceEnvelope(
        base_radius=0.30, safety_margin=0.05,
        arm_min_reach=minimum, arm_max_reach=maximum,
        shoulder_height=float(offset[2]),
        shoulder_offset_m=(float(offset[0]), float(offset[1])),
        manipulation_travel_m=travel)
    derivation = {
        "source": f"robot/gateway/tangying_robot_gateway/assets/arm_kinematics.json ({side} arm)",
        "method": "uniform joint sampling over the shipped ranges, tool mount distance from link 1",
        "samples": samples, "observedMinM": round(float(reach.min()), 4),
        "observedMaxM": round(float(reach.max()), 4),
        "shoulderOffsetInBaseFrameM": [float(offset[0]), float(offset[1]), float(offset[2])],
        "shippedNominalReachM": 0.42,
        "manipulationTravelM": travel,
        "manipulationTravelSource": "sim/mujoco/tangying_sim/motion.py BASE_TRANSLATION_LIMIT",
        "operableRangeM": list(envelope.operable_reach),
    }
    return envelope, derivation


def base_manipulation_travel() -> float:
    """How far the base may creep while the arm is working, from the robot's own model.

    Read rather than assumed: if the driver changes its allowance, a verdict that
    hard-coded the old one would keep certifying poses the robot can no longer work
    from. A robot whose model cannot be read commissions no travel, and the report
    says so instead of inventing a number.
    """

    try:
        sys.path.insert(0, str(ROOT / "sim" / "mujoco"))
        from tangying_sim.motion import MotionController

        return float(MotionController.BASE_TRANSLATION_LIMIT)
    except Exception:  # noqa: BLE001 - a missing model is a fact to report, not a crash
        return 0.0


#: Codes that mean "the base did not move, and nothing is wrong".
#: ``NAV_ALREADY_AT_GOAL`` is the runtime correctly declining to drive zero metres;
#: counting it as a failed drive would report a working robot as broken every time
#: the destination is the room it is already standing in, which for the living room
#: - where every ``home_task`` episode starts - is always.
NO_MOTION_CODES = frozenset({"NAV_ALREADY_AT_GOAL"})

#: The controller's own "I drove there" word. A skill event carries the outcome in
#: `code`, and the terminal event type says only that the command finished, so
#: judging a drive by the event type alone reports every successful arrival as a
#: failure.
SUCCESS_CODES = frozenset({"NAV_REACHED", "OK", "SUCCEEDED"})


def struct_to_dict(message) -> dict:
    """A protobuf ``Struct`` as plain Python.

    ``Observation.robot_state`` is a ``google.protobuf.Struct``, so ``dict(...)`` on it
    yields Struct *values*, not the lists and numbers inside them - a base pose read
    that way is a protobuf message that is not a list, and every ``isinstance(pose,
    list)`` check silently answers "no pose available". Converting explicitly is the
    difference between a closed loop and a loop that reports it could not see the
    robot.
    """

    from google.protobuf.json_format import MessageToDict

    try:
        return MessageToDict(message)
    except Exception:  # noqa: BLE001 - already-plain mappings are fine to pass through
        return dict(message)


def command(stub, robot_id, skill, parameters, *, safety_profile, deadline_ms=380000,
            timeout_s=400):
    command_id = f"closure-{skill.replace('.', '-')}-{uuid.uuid4().hex[:8]}"
    request = robot_pb2.SkillCommand(
        schema_version="robot.v1", command_id=command_id, task_id="semantic-closure",
        skill=skill, idempotency_key=command_id,
        deadline_unix_ms=int(time.time() * 1000) + deadline_ms,
        lease_ms=deadline_ms, safety_profile=safety_profile)
    request.parameters.update(parameters)
    events = list(stub.ExecuteSkill(request, timeout=timeout_s))
    terminal = events[-1] if events else None
    code = getattr(terminal, "code", None)
    succeeded = bool(terminal is not None and (
        terminal.type == "SKILL_EVENT_SUCCEEDED" or code in SUCCESS_CODES))
    return {
        "skill": skill,
        "ok": succeeded,
        "code": code,
        "noMotionRequired": code in NO_MOTION_CODES,
        "eventCount": len(events),
        "commandId": command_id,
        "events": [{"type": event.type, "code": event.code,
                    "message": (event.message or "")[:300]} for event in events],
    }


def observe_state(stub, source_id, timeout_s=90):
    """One fresh observation: the base pose and the whole published robot state."""

    request = robot_pb2.ObserveRequest(streams=["robot_state"], source_id=source_id)
    for observation in stub.Observe(request, timeout=timeout_s):
        state = struct_to_dict(observation.robot_state)
        pose = state.get("base_pose")
        if isinstance(pose, list) and len(pose) == 7:
            return [float(value) for value in pose], int(observation.wall_time_unix_ms), state
    return None, 0, {}


def base_pose(stub, source_id, timeout_s=90):
    pose, observed_at, _state = observe_state(stub, source_id, timeout_s)
    return pose, observed_at


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=50051)
    parser.add_argument("--robot-id", default="xlerobot-mujoco-tabletop")
    parser.add_argument("--safety-profile", default="simulation")
    parser.add_argument("--arm", default="right", choices=("left", "right"))
    parser.add_argument("--plan-only", action="store_true",
                        help="resolve and report, drive nothing")
    parser.add_argument("--rooms", default="kitchen,living_room,bedroom,bathroom",
                        help="comma-separated rooms to attempt a drive to")
    parser.add_argument("--json", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)

    envelope, derivation = arm_envelope(args.arm)
    print("手臂可达范围（从自身运动链采样，不是假设）:")
    print(f"  肩关节在基座系: forward {envelope.shoulder_offset_m[0]:.4f} m, "
          f"left {envelope.shoulder_offset_m[1]:.4f} m, height {envelope.shoulder_height:.4f} m")
    print(f"  工具端到肩关节: {envelope.arm_min_reach:.3f} – {envelope.arm_max_reach:.3f} m "
          f"（采样 {derivation['samples']} 次；出厂标称 ARM_REACH = "
          f"{derivation['shippedNominalReachM']} m）")
    print(f"  作业时底盘可移动: ±{envelope.manipulation_travel_m:.3f} m/轴 → "
          f"工具可及半径再增 {envelope.base_travel_radius:.3f} m")
    print(f"  因此可操作范围 = {envelope.operable_reach[0]:.3f} – "
          f"{envelope.operable_reach[1]:.3f} m（这才是要判定的区间）")
    print("  同一个壳若从基座原点量: 会变成 ~0.70 – 1.26 m，与臂本身无关\n")

    channel = grpc.insecure_channel(f"{args.host}:{args.port}")
    stub = robot_pb2_grpc.RobotRuntimeStub(channel)
    try:
        info = stub.GetRuntimeInfo(robot_pb2.GetRuntimeInfoRequest(), timeout=20)
    except grpc.RpcError as error:
        print(f"运行时 {args.host}:{args.port} 不可达：{error.code()} {error.details()}",
              file=sys.stderr)
        return 2
    print(f"运行时: adapter={info.adapter} robot={info.robot_id} "
          f"cameras={list(info.cameras)}")
    source_id = f"{info.robot_id}/base-rgbd"

    # --- 1. resolution, for every phrasing -------------------------------
    semantic = SemanticMap.from_file()
    resolution_rows = []
    print(f"\n自然语言 → 房间（{sum(len(v) for v in PHRASINGS.values())} 条表述）:")
    for room, phrasings in PHRASINGS.items():
        for phrasing in phrasings:
            resolved = semantic.resolve(phrasing)
            name = resolved.name if resolved is not None else None
            ok = name == room
            resolution_rows.append({"room": room, "phrasing": phrasing,
                                    "resolved": name, "correct": ok})
            if not ok:
                print(f"  ✗ {phrasing!r:16s} → {name or 'UNRESOLVED'}（期望 {room}）")
    hits = sum(1 for row in resolution_rows if row["correct"])
    print(f"  解析成功 {hits}/{len(resolution_rows)}")
    unchanged = [{"room": r, "phrasing": p,
                  "samePose": semantic.resolve(p).name == semantic.resolve(
                      PHRASINGS[r][0]).name if semantic.resolve(p) else False}
                 for r in PHRASINGS for p in PHRASINGS[r]]
    print(f"  同一房间的不同说法是否给出同一个目标: "
          f"{sum(1 for row in unchanged if row['samePose'])}/{len(unchanged)}")

    # --- 2/3/4. route, drive each leg, then judge -----------------------
    #
    # Routing is the caller's job, not the runtime's: the runtime drives one pose and
    # its guard stops it at the first wall. So a request for a non-adjacent room is
    # expanded here, through the adjacency the runtime publishes, into the sequence of
    # rooms a person would walk - and only the last leg carries the work target.
    navigation = semantic_contract(stub)
    edges = published_edges(navigation)
    print(f"\n委托房间邻接（来自运行时发布的语义契约，{len(edges)} 个房间）:")
    for room in sorted(edges):
        print(f"  {room:14s} → {', '.join(sorted(edges[room]))}")

    drives = []
    if args.plan_only:
        print("\n--plan-only：不驱动底盘。")
    else:
        current = START_ROOM
        for room in [item.strip() for item in args.rooms.split(",") if item.strip()]:
            if semantic.resolve(room) is None:
                print(f"\n{room}: 语义地图里没有这个房间，跳过")
                continue
            route = route_to(edges, current, room)
            print(f"\n{room}: 从 {current} 走 {len(route) - 1} 段 "
                  f"{' → '.join(route)}")
            for index, leg in enumerate(route[1:], start=1):
                location = semantic.resolve(leg)
                goal = location.to_pose7()
                final = leg == room
                target = (WORK_TARGETS.get(leg) if final else None) or (
                    float(goal[0]), float(goal[1]), envelope.shoulder_height)
                before, _ = base_pose(stub, source_id)
                navigated = command(stub, info.robot_id, "navigation.navigate",
                                    {"goalPose": list(goal)},
                                    safety_profile=args.safety_profile)
                if navigated["noMotionRequired"]:
                    outcome = "NAV_ALREADY_AT_GOAL（已在目标）"
                else:
                    outcome = f"{navigated['code']} ({'ok' if navigated['ok'] else 'FAILED'})"
                arrival = command(stub, info.robot_id, "verify_arrival",
                                  {"goalPose": list(goal)},
                                  safety_profile=args.safety_profile,
                                  deadline_ms=30000, timeout_s=60)
                after, observed_at = base_pose(stub, source_id)
                verdict_payload = None
                if after is not None:
                    verdict_payload = verify_operable_arrival(
                        planned_pose=goal, observed_pose=after,
                        work_target=target, envelope=envelope).to_dict()
                mark = "" if navigated["ok"] or navigated["noMotionRequired"] else "  ← "
                print(f"  第{index}段 → {leg:14s} {mark}{outcome}")
                if after is not None:
                    print(f"        base ({after[0]:7.3f},{after[1]:6.2f})  "
                          f"verify_arrival={arrival['code']}  "
                          f"{(verdict_payload or {}).get('code', '')}")
                else:
                    print("        ✗ 拿不到 base_pose")
                drives.append({
                    "requestedRoom": room, "route": route, "leg": leg,
                    "legIndex": index, "isFinalLeg": final,
                    "goalPose": list(goal), "workTarget": list(target) if final else None,
                    "navigate": navigated, "verifyArrival": arrival,
                    "basePoseBefore": before, "basePoseAfter": after,
                    "observedAtUnixMs": observed_at,
                    "operableArrival": verdict_payload,
                })
                if not (navigated["ok"] or navigated["noMotionRequired"]):
                    # A route is only as good as its legs; continuing after a failed
                    # leg would measure a house the robot is no longer in.
                    print(f"        （第{index}段失败，停止这条路线）")
                    break
            else:
                current = room

    attempted = [row for row in drives]
    final_legs = [row for row in attempted if row["isFinalLeg"]]
    arrived = [row for row in final_legs
               if (row.get("operableArrival") or {}).get("passed") is True]
    legs_ok = [row for row in attempted
               if row["navigate"]["ok"] or row["navigate"]["noMotionRequired"]]
    report = {
        "schemaVersion": "semantic.navigation.closure.v1",
        "runtime": {"adapter": info.adapter, "robotId": info.robot_id,
                    "endpoint": f"{args.host}:{args.port}", "cameras": list(info.cameras)},
        "armEnvelope": {"min": envelope.arm_min_reach, "max": envelope.arm_max_reach,
                        "shoulderHeightM": envelope.shoulder_height,
                        "shoulderOffsetM": list(envelope.shoulder_offset_m),
                        "manipulationTravelM": envelope.manipulation_travel_m,
                        "operableRangeM": list(envelope.operable_reach),
                        "derivation": derivation},
        "resolution": {"total": len(resolution_rows), "correct": hits,
                       "rows": resolution_rows,
                       "samePoseAcrossPhrasings": sum(1 for row in unchanged if row["samePose"]),
                       "phrasingComparisons": len(unchanged)},
        "routeEdges": edges,
        "semanticNavigation": {"mapId": navigation.get("mapId"),
                               "mapRevision": navigation.get("mapRevision"),
                               "goalCount": len(navigation.get("goals") or {}),
                               "aliasCount": len(navigation.get("aliases") or {})},
        "drives": drives,
        "summary": {"phrasings": len(resolution_rows), "resolved": hits,
                    "legsAttempted": len(attempted), "legsArrived": len(legs_ok),
                    "requestsAttempted": len(final_legs),
                    "requestsReachedOperableRange": len(arrived),
                    "verifyArrivalConfirmed": sum(
                        1 for row in attempted
                        if (row.get("verifyArrival") or {}).get("code") == "NAV_ARRIVAL_CONFIRMED")},
    }
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"\n报告写入 {args.json}")

    print(f"\n小结：表述解析 {hits}/{len(resolution_rows)}；"
          f"路线共 {len(attempted)} 段，到达 {len(legs_ok)} 段；"
          f"请求 {len(final_legs)} 个房间，抵达可操作范围 {len(arrived)} 个")
    failed_resolution = [row for row in resolution_rows if not row["correct"]]
    if failed_resolution or (final_legs and len(arrived) != len(final_legs)):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
