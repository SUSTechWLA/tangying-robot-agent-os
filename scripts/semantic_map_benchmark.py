"""Compare semantic-map representations for natural-language navigation.

The question this answers is not "which representation is best" in the abstract -
it is **what does each one cost, and what does it get wrong**. Four ways of
producing "where should the robot stand when it is in the kitchen" are built on the
same surveyed map and scored on the same axes:

    static      a person writes the full pose
    seeded      a person writes a point in the room; the map decides the pose
    segmented   nobody writes anything; rooms are numbered regions
    landmark    rooms are named by the furniture a detector saw in them

Axes, split into the two halves of the trade:

*Efficiency* - how many numbers a human must supply, build wall-clock, and the
bytes the layer adds to the map package.

*Precision* - whether the produced pose is (a) in the room it claims, (b) standing
on floor the robot can actually occupy with its own clearance, (c) reachable by a
planned route from where the survey started, and (d) resolvable from the ways a
person actually says the room's name.

Ground truth is not a hand-typed coordinate. It is the world file's own wall
topology - the two partitions at x = 0 and y = 2 divide the house into the four
rooms - validated against the furniture the world file names (a bed is in the
bedroom, a sink is in the bathroom). A truth that came from the same source as the
answer would make the comparison a tautology.

Run::

    .venv/bin/python scripts/semantic_map_benchmark.py --truth gazebo
    .venv/bin/python scripts/semantic_map_benchmark.py --truth gazebo --json out.json
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot" / "gateway"))
sys.path.insert(0, str(ROOT / "scripts"))

import exploration_survey_benchmark as bench
from tangying_robot_gateway.exploration import Grid, plan_route, traversable_for
from tangying_robot_gateway.semantic_workspaces import (
    REPRESENTATIONS,
    Representation,
    build_workspaces,
)

#: The door radius is the segmentation's only free parameter and it has no ground
#: truth behind it, so the report carries the curve rather than one number that
#: happened to look right. The step is coarse on purpose: the question is where the
#: *plateau* is, and a fine sweep would suggest a precision the method does not have.
DOOR_RADII_SWEEP = (0.30, 0.45, 0.60, 0.75, 0.90, 1.10)


#: The four rooms, as the world file's partitions define them: x = 0 and y = 2.
#: The furniture check below fails if this stops matching the world.
def room_of(x: float, y: float) -> str:
    return ("living_room" if x < 0 else "kitchen") if y < 2 else (
        "bedroom" if x < 0 else "bathroom")


#: Where the world file puts one unmistakable piece of furniture per room.
FURNITURE_TRUTH = {
    "living_room": (-4.6, -1.9),    # living_sofa
    "kitchen": (3.6, -0.8),         # kitchen_island
    "bedroom": (-4.0, 5.3),         # bed
    "bathroom": (3.6, 5.8),         # bathroom_sink
}

#: A point a person could put a finger on, on a floor plan, to mean each room.
#: Deliberately *only a point*: that is the whole claim of the `seeded`
#: representation, and giving it a pose instead would be measuring a different
#: representation.
HUMAN_SEEDS = {
    "living_room": (-2.0, -2.0),
    "kitchen": (1.5, -2.0),
    "bedroom": (-3.0, 3.0),
    "bathroom": (2.0, 3.0),
}


#: Full authored poses for the `static` representation, in the house's own frame.
#: These are what a person writes today - note that each needs a *position and a
#: heading*, not a point, and that neither is checked against the map by anything.
STATIC_ANCHORS = {
    "living_room": {"pose": (-2.0, -2.0, math.pi / 2), "aliases": ["客厅", "living room", "lounge"]},
    "kitchen": {"pose": (1.5, -2.0, 0.0), "aliases": ["厨房", "cookhouse"]},
    "bedroom": {"pose": (-3.0, 3.0, math.pi / 2), "aliases": ["卧室", "bed room"]},
    "bathroom": {"pose": (2.0, 3.0, 0.0),
                 "aliases": ["卫生间", "浴室", "厕所", "restroom", "wc"]},
}

#: The same aliases for both named representations. Without this the comparison
#: would measure "which words did the author bother to publish" instead of "where
#: does the pose come from"; the alias table is a separate axis with its own
#: result, and the report keeps it separate.
COMMON_ALIASES = {room: spec["aliases"] for room, spec in STATIC_ANCHORS.items()}

#: How people actually ask, per room, in both languages, with the variations that
#: have historically broken a layer: article, case, spacing, synonyms.
PHRASES = {
    "living_room": ["客厅", "去客厅", "living room", "the living room", "Living Room", "lounge"],
    "kitchen": ["厨房", "去厨房", "kitchen", "the kitchen", "Kitchen", "cookhouse"],
    "bedroom": ["卧室", "去卧室", "bedroom", "the bedroom", "Bedroom", "bed room"],
    "bathroom": ["卫生间", "厕所", "bathroom", "the bathroom", "restroom", "wc"],
}


def real_objects() -> list[dict]:
    """Every object any surveyed map in this repo has actually recorded.

    Pooled across maps because the question is not "what did one survey see" but
    "what vocabulary does the perception stack emit at all" - and the answer to
    that is what decides whether a room can be named by its furniture.
    """
    seen, out = set(), []
    for path in sorted((ROOT / "artifacts" / "maps").rglob("objects.json")):
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        for item in payload.get("objects", []):
            key = (item.get("category"), item.get("id"), tuple(item.get("pose", [])[:2]))
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out


def load_truth(truth: str):
    # The four-room ground truth below is *this world's* wall topology (x = 0 and
    # y = 2). Grading the furnished MuJoCo home against it would score every
    # representation against a partition that house does not have, and a benchmark
    # that silently answers the wrong question is worse than one that refuses.
    # Adding MuJoCo means writing its ground truth, not passing a different flag.
    if truth != "gazebo":
        raise SystemExit(
            f"--truth {truth} has no room ground truth in this harness; the rule "
            f"here is the Gazebo house's partitions")
    cache = ROOT / "artifacts" / "benchmark" / "survey-truth-gazebo.npz"
    if not cache.is_file():
        raise SystemExit(f"no cached truth at {cache}; build it with\n"
                         f"  python scripts/exploration_survey_benchmark.py --truth {truth}")
    saved = np.load(cache)
    occupied = saved["occupied"]
    origin = tuple(float(v) for v in saved["origin"])
    start = tuple(float(v) for v in saved["start"]) if "start" in saved else (0.0, 0.0)
    resolution = bench.RESOLUTION
    height, width = occupied.shape
    xs = origin[0] + np.arange(width) * resolution
    ys = origin[1] + np.arange(height) * resolution
    # A surveyed grid, not a clearance-filtered one: free where the floor is clear,
    # occupied at obstacles, unknown outside the outer walls. Passing a
    # clearance-filtered grid would apply the envelope twice and shrink the house.
    inside = ((xs[None, :] >= -6.0) & (xs[None, :] <= 6.0)
              & (ys[:, None] >= -3.0) & (ys[:, None] <= 7.0))
    cells = np.where(occupied, 100, np.where(inside, 0, -1)).astype(np.int16)
    return cells, resolution, origin, start


def reachable_poses(cells, resolution, origin, start, poses) -> list[bool]:
    """Would a planned route exist from the survey's start to each pose?

    Uses the same planner and the same traversability rule the survey uses, so
    "reachable" here means the same thing it means at run time rather than
    "geometrically unobstructed".
    """
    grid = Grid(cells=cells, resolution=resolution, origin=origin)
    traversable = traversable_for(grid, 0.32)
    start_cell = grid.nearest_free_cell(*start)
    out = []
    for pose in poses:
        goal = grid.nearest_free_cell(float(pose[0]), float(pose[1]))
        if start_cell is None or goal is None:
            out.append(False)
            continue
        path = plan_route(grid, robot_xy=grid.centre(*start_cell),
                          goal_xy=grid.centre(*goal), radius_m=0.32,
                          extra_traversable=traversable)
        out.append(path is not None)
    return out


def clearance_at(cells, resolution, origin, pose) -> float:
    """Distance from a pose to the nearest obstacle, in metres."""
    from scipy import ndimage

    free = cells == 0
    distance = ndimage.distance_transform_edt(free) * resolution
    column = math.floor((pose[0] - origin[0]) / resolution)
    row = math.floor((pose[1] - origin[1]) / resolution)
    if not (0 <= row < distance.shape[0] and 0 <= column < distance.shape[1]):
        return 0.0
    return float(distance[row, column])


def score(representation: Representation, cells, resolution, origin, start) -> dict:
    """Everything measurable about one representation's answers."""
    poses = [item["target"] for item in representation.workspaces]
    named = representation.kind in {"static", "seeded", "landmark"}
    in_room = sum(1 for item in representation.workspaces
                  if room_of(item["target"][0], item["target"][1]) == item["name"])
    # Room coverage is the metric that works for every kind: how many of the four
    # real rooms have *some* workspace standing in them. A representation with no
    # names can still be asked "did you find the kitchen" - as "is there a pose in
    # the kitchen" - and the answer is what a robot needs in order to be sent there.
    rooms_covered = {room_of(item["target"][0], item["target"][1])
                     for item in representation.workspaces}
    room_coverage = len(rooms_covered & set(FURNITURE_TRUTH)) / len(FURNITURE_TRUTH)
    reachable = reachable_poses(cells, resolution, origin, start, poses) if poses else []
    clearances = [clearance_at(cells, resolution, origin, pose) for pose in poses]

    per_phrase, hits, total = {}, 0, 0
    for room, phrases in PHRASES.items():
        for phrase in phrases:
            total += 1
            # `resolve` uses the same normalisation the tool layer uses, so a
            # representation is not credited for a name it does not publish.
            found = representation.resolve(phrase)
            ok = found is not None and found.get("name") == room
            hits += 1 if ok else 0
            per_phrase[f"{room}/{phrase}"] = "ok" if ok else (
                "wrong-room" if found is not None else "unresolved")
    named = len(representation.workspaces) or 1
    return {
        "kind": representation.kind,
        "workspaceCount": len(representation.workspaces),
        "humanInputs": representation.human_inputs,
        "buildSeconds": round(representation.build_seconds, 6),
        "payloadBytes": representation.payload_bytes,
        "inRoom": in_room if named else None,
        "inRoomFraction": round(in_room / named, 4) if named else None,
        "roomCoverage": round(room_coverage, 4),
        "reachableFraction": round(sum(reachable) / named, 4) if poses else 0.0,
        "meanClearanceM": round(float(np.mean(clearances)), 4) if clearances else 0.0,
        "minClearanceM": round(float(np.min(clearances)), 4) if clearances else 0.0,
        "resolutionRecall": round(hits / total, 4) if total else 0.0,
        "phrasesResolved": f"{hits}/{total}",
        "notes": representation.notes,
        "unavailable": representation.unavailable,
        "perPhrase": per_phrase,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--truth", choices=("gazebo",), default="gazebo",
                        help="only gazebo: the room ground truth is this world's "
                             "partition rule, and grading another house against it "
                             "would produce a confident wrong answer")
    parser.add_argument("--footprint-radius", type=float, default=0.32)
    parser.add_argument("--door-radius", type=float, default=0.60)
    parser.add_argument("--json", type=pathlib.Path, default=None)
    args = parser.parse_args()

    cells, resolution, origin, start = load_truth(args.truth)

    print(f"真值来源 {args.truth}   栅格 {cells.shape} @ {resolution} m   "
          f"起点 {start}")
    print(f"自由 {int((cells == 0).sum()) * resolution ** 2:.1f} m²   "
          f"障碍 {int((cells >= 65).sum()) * resolution ** 2:.1f} m²")
    objects = real_objects()
    categories = sorted({str(item.get("category") or "") for item in objects if item.get("category")})
    print(f"真实物体层 {len(objects)} 个实例，类别 {categories}")

    # The ground truth is only ground truth if the world file agrees with it.
    print("\n房间真值（由世界文件的隔墙定义，并用家具名验证）:")
    bad = []
    for room, point in FURNITURE_TRUTH.items():
        got = room_of(*point)
        print(f"  {room:12s} 家具 {point} -> {got:12s} {'✓' if got == room else '✗ 与真值不符'}")
        bad += [] if got == room else [room]
    if bad:
        raise SystemExit(f"ground truth disagrees with the world file for {bad}")

    # Each builder takes only what it needs: a representation that was handed a
    # grid it does not use would be measuring the harness, not the representation.
    grid_common = {"cells": cells, "resolution": resolution, "origin": origin,
                   "seed_xy": start}
    builders = {
        "static": {"anchors": STATIC_ANCHORS, "anchor_pose": (0.0, 0.0, 0.0)},
        "seeded": {**grid_common, "footprint_radius_m": args.footprint_radius,
                   "door_radius_m": args.door_radius,
                   "seeds": {name: {"point": point, "aliases": COMMON_ALIASES.get(name, [])}
                             for name, point in HUMAN_SEEDS.items()}},
        "segmented": {**grid_common, "footprint_radius_m": args.footprint_radius,
                      "door_radius_m": args.door_radius},
        # Fed the *real* object layer from a surveyed map, so the result measures
        # what the perception stack actually produces rather than an empty list the
        # harness chose.
        "landmark": {"objects": real_objects(), "anchor_pose": (0.0, 0.0, 0.0)},
    }

    rows = []
    for kind in REPRESENTATIONS:
        started = time.perf_counter()
        representation = build_workspaces(kind, **builders[kind])
        row = score(representation, cells, resolution, origin, start)
        row["wallSeconds"] = round(time.perf_counter() - started, 4)
        rows.append(row)

    header = (f"\n{'表达方式':<11}{'人工输入':>8}{'条目':>5}{'构建ms':>9}{'字节':>7}"
              f"{'房间覆盖':>9}{'在房间内':>9}{'可达':>7}{'最小净空':>9}{'NL解析':>8}")
    print(header)
    print("-" * (len(header) - 1))
    for row in rows:
        inroom = "—" if row["inRoomFraction"] is None else f"{row['inRoomFraction']:.0%}"
        print(f"{row['kind']:<11}{row['humanInputs']:>8}{row['workspaceCount']:>5}"
              f"{row['buildSeconds'] * 1000:>9.1f}{row['payloadBytes']:>7}"
              f"{row['roomCoverage']:>9.0%}{inroom:>9}"
              f"{row['reachableFraction']:>7.0%}"
              f"{row['minClearanceM']:>9.2f}{row['resolutionRecall']:>8.0%}")

    print("\n各表达方式解析失败的表述:")
    for row in rows:
        missed = {k: v for k, v in row["perPhrase"].items() if v != "ok"}
        if row["unavailable"]:
            print(f"  {row['kind']}: 不可用 — {row['unavailable']}")
        elif missed:
            print(f"  {row['kind']}: " + ", ".join(f"{k}({v})" for k, v in sorted(missed.items())))
        else:
            print(f"  {row['kind']}: 全部解析成功")

    # The door radius is the segmentation's only free parameter and it has no
    # ground truth behind it, so the honest report carries the curve. A number that
    # happened to look right at one radius is not a result.
    sweep = []
    if "seeded" in REPRESENTATIONS:
        from tangying_robot_gateway.room_segmentation import door_radius_sweep

        for radius, segmentation in zip(
                DOOR_RADII_SWEEP,
                door_radius_sweep(cells, resolution=resolution, origin=origin,
                                  radii=DOOR_RADII_SWEEP,
                                  footprint_radius_m=args.footprint_radius,
                                  seed_xy=start)):
            covered = {room_of(r.centre_xy[0], r.centre_xy[1]) for r in segmentation.rooms}
            sweep.append({"doorRadiusM": radius, "rooms": len(segmentation.rooms),
                          "roomsInHouse": len(covered & set(FURNITURE_TRUTH)),
                          "totalAreaM2": round(sum(r.area_m2 for r in segmentation.rooms), 3)})
    print("\n门宽半径敏感性（分割唯一的自由参数，没有真值背书）:")
    print(f"  {'radius':>7}{'区域数':>8}{'落在真实房间':>14}{'总面积m2':>11}")
    for row in sweep:
        print(f"  {row['doorRadiusM']:>7.2f}{row['rooms']:>8}{row['roomsInHouse']:>14}"
              f"{row['totalAreaM2']:>11.1f}")

    report = {"truth": args.truth, "resolution": resolution, "origin": list(origin),
              "start": list(start), "doorRadiusM": args.door_radius,
              "footprintRadiusM": args.footprint_radius,
              "groundTruth": {"rooms": FURNITURE_TRUTH, "rule": "x<0 ? living|bedroom : kitchen|bathroom"},
              "phrases": PHRASES, "representations": rows,
              "doorRadiusSensitivity": sweep}
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"\n报告写入 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
