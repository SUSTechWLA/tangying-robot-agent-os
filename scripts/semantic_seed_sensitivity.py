"""How much does it matter *where* a person clicks when they name a room?

The representation comparison concluded that "one person, one point per room, and
the map supplies the pose" (``seeded``) beats "a person writes a full pose"
(``static``). That conclusion has an obvious hole in it: if the answer depends on
which point the person happened to click, then the method has not removed the
authoring burden, it has hidden it - and hidden authoring is worse than visible
authoring, because nothing reviews a click.

So this measures it, on a **real SLAM map** rather than on a rasterised truth
raster. The Gazebo comparison had to use synthetic geometry because Gazebo's truth
only exists as a world file; here the grid is a map the robot actually surveyed
(``artifacts/maps/<mapId>``), two thirds of which is still unknown. That matters: a
segmenter that works on clean geometry can still fail on a real occupancy grid full
of unmapped space, and the whole reason to prefer a map-derived pose is that it
follows the map.

Ground truth is the MuJoCo house's own room boxes, read from the compiled model's
body geometry - not from the commissioned waypoints, and not from anything a
representation produced. The waypoints are then *checked against* that truth, and
the one that fails is kept and explained rather than dropped: the commissioned
kitchen pose is the workcell dock, which sits outside the kitchen by design. That
single counter-example is the most interesting row in the output.

What is measured, per room and per sampled click:

* ``correct`` - did the pose the map computed fall inside the room the person
  clicked in? This is the claim ``seeded`` makes, and the whole experiment.
* ``clearanceM`` - the robot's usable half-width at that pose.
* ``movedFromClickM`` - how far the published pose is from the click itself. A
  method that ignores its input would show this constant; one that is at the mercy
  of its input would show a pose that barely moves.

Usage:

    .venv/bin/python scripts/semantic_seed_sensitivity.py --map scan-af0ba496987e
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import statistics
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot" / "gateway"))
sys.path.insert(0, str(ROOT / "sim" / "mujoco"))
sys.path.insert(0, str(ROOT / "scripts"))

from tangying_robot_gateway.exploration import (
    OCCUPIED,
    Grid,
    plan_route,
    traversable_for,
)
from tangying_robot_gateway.map_catalog import MapCatalog
from tangying_robot_gateway.map_manifest import load_manifest
from tangying_robot_gateway.room_segmentation import door_radius_sweep
from tangying_robot_gateway.semantic_map import SemanticMap
from tangying_robot_gateway.semantic_workspaces import build_seeded

MAP_ROOT = ROOT / "artifacts" / "maps"
ROBOT_ID = "xlerobot-mujoco-tabletop"
DEFAULT_MAP = "scan-af0ba496987e"
FOOTPRINT_RADIUS_M = 0.32
MARGIN_M = 0.10
DOOR_RADII = (0.30, 0.45, 0.60, 0.75, 0.90, 1.10)
#: The door radius the comparison uses for the click sampling. 0.60 is the
#: commissioned value; the sweep above shows what the other choices would do.
DOOR_RADIUS_M = 0.60
#: How many points to click per room. Enough for a distribution, small enough that
#: the report can say what every one of them did.
CLICKS_PER_ROOM = 24
CLICK_SEED = 20260920
#: The commissioned living-room pose: where a survey starts from.
START_XY = (0.0, -1.25)


def room_boxes() -> dict[str, tuple[float, float, float, float]]:
    """Each room's floor box from the compiled MuJoCo model: (cx, cy, hx, hy).

    The model is the house. Reading the boxes out of it is reading the truth, and
    nothing here consults the waypoints, the map, or any representation.
    """

    import mujoco
    from tangying_sim.home_scene import HOME_ROOMS, load_home_model

    model = load_home_model()
    boxes: dict[str, tuple[float, float, float, float]] = {}
    for name in HOME_ROOMS:
        body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
        if body < 0 or model.body_geomnum[body] != 1:
            raise SystemExit(f"room {name} is not a single-box body in the model")
        geom = int(model.body_geomadr[body])
        boxes[name] = (float(model.body_pos[body][0]), float(model.body_pos[body][1]),
                       float(model.geom_size[geom][0]), float(model.geom_size[geom][1]))
    return boxes


def room_of(x: float, y: float, boxes) -> str | None:
    """Which room box a point falls in, or None. The boxes do not overlap."""

    hits = [name for name, (cx, cy, hx, hy) in boxes.items()
            if abs(x - cx) <= hx and abs(y - cy) <= hy]
    return hits[0] if len(hits) == 1 else None


def find_package(map_id: str) -> pathlib.Path:
    """A map package is `artifacts/maps/<id>` or `artifacts/maps/<group>/<id>`.

    The catalog root is whatever contains the package, so it has to be found
    rather than assumed: two map groups with the same layout would otherwise pick
    one silently and the report would name the wrong revision.
    """

    hits = [path.parent for path in MAP_ROOT.rglob("manifest.json") if path.parent.name == map_id]
    if len(hits) != 1:
        raise SystemExit(f"expected exactly one package named {map_id!r}, found {len(hits)}")
    return hits[0]


def load_grid(map_id: str):
    package = find_package(map_id)
    catalog = MapCatalog(package.parent)
    manifest = load_manifest(package)
    directory, manifest = catalog.open(
        map_id, robot_id=ROBOT_ID, calibration_revision=manifest["calibrationRevision"])
    return MapCatalog.navigation_grid(directory, manifest), manifest, directory


def cells_from(grid) -> np.ndarray:
    """The surveyed grid, in the convention the segmenter and the router share.

    That convention is the repository's own: ``0`` free, ``>= OCCUPIED`` (65) blocked,
    and **negative means unknown** - not occupied. The distinction is load-bearing.
    ``exploration.clearance_mask`` measures clearance against occupied cells, so a free
    cell next to unmapped space keeps its clearance and stays traversable; that is the
    point of a survey, which is expected to drive into space nobody has measured yet.

    Folding unknown into occupied destroys exactly that, and the damage is not subtle:
    on ``scan-af0ba496987e`` the grid segments to 25.7 m² of connected drivable space
    with the convention and 12.5 m² without, because four of the five rooms stop being
    drivable at all. This function used to do the folding, and the harness then
    reported the segmenter as broken when the harness was.
    """

    return np.asarray(grid["cells"]).astype(np.int8)


def clearance_field(cells: np.ndarray, resolution: float) -> np.ndarray:
    """Distance from each free cell to the nearest **occupied** cell, in metres.

    Measured against occupied cells only, exactly as ``exploration.clearance_mask``
    does, so the clearance column means the same thing as the router's idea of
    clearance. Measuring against "anything that is not free" would fold unmapped space
    into the obstacle set and report a pose in the middle of a surveyed room as having
    no clearance at all - a number that looks alarming and describes the map's
    coverage rather than the room.
    """

    from scipy import ndimage

    return ndimage.distance_transform_edt(cells < OCCUPIED) * resolution


def clearance_at(field, cells, resolution, origin, x, y) -> float:
    column = math.floor((x - origin[0]) / resolution)
    row = math.floor((y - origin[1]) / resolution)
    if not (0 <= row < field.shape[0] and 0 <= column < field.shape[1]):
        return 0.0
    return float(field[row, column])


def reachable_from(grid: Grid, start_xy, pose, traversable) -> bool:
    start_cell = grid.nearest_free_cell(*start_xy)
    goal_cell = grid.nearest_free_cell(float(pose[0]), float(pose[1]))
    if start_cell is None or goal_cell is None:
        return False
    path = plan_route(grid, robot_xy=grid.centre(*start_cell), goal_xy=grid.centre(*goal_cell),
                      radius_m=FOOTPRINT_RADIUS_M, extra_traversable=traversable)
    return path is not None


def free_inside(box, cells, resolution, origin) -> list[tuple[float, float]]:
    """Every cell inside a room box that the grid calls free.

    A person clicking a room in a console clicks somewhere that looks like floor,
    so sampling only free cells is the honest model of the input. It is also why
    the sample count has to be reported: a room with three mapped cells offers
    three places to click, and a hit rate over three samples is not a measurement.
    """

    cx, cy, hx, hy = box
    rows, columns = cells.shape
    found: list[tuple[float, float]] = []
    for row in range(max(0, math.floor((cy - hy - origin[1]) / resolution)),
                     min(rows, math.ceil((cy + hy - origin[1]) / resolution))):
        for column in range(max(0, math.floor((cx - hx - origin[0]) / resolution)),
                            min(columns, math.ceil((cx + hx - origin[0]) / resolution))):
            if cells[row, column] != 0:
                continue
            x = origin[0] + (column + 0.5) * resolution
            y = origin[1] + (row + 0.5) * resolution
            if abs(x - cx) <= hx and abs(y - cy) <= hy:
                found.append((round(x, 4), round(y, 4)))
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", default=DEFAULT_MAP, help="map package under artifacts/maps")
    parser.add_argument("--clicks", type=int, default=CLICKS_PER_ROOM)
    parser.add_argument("--json", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)

    boxes = room_boxes()
    grid_document, manifest, directory = load_grid(args.map)
    resolution = float(grid_document["resolution"])
    origin = tuple(float(value) for value in grid_document["origin"][:2])
    cells = cells_from(grid_document)
    field = clearance_field(cells, resolution)
    grid = Grid(cells=cells, resolution=resolution, origin=origin)
    traversable = traversable_for(grid, FOOTPRINT_RADIUS_M)

    raw = np.asarray(grid_document["cells"])
    values, counts = np.unique(raw, return_counts=True)
    tally = dict(zip(values.tolist(), counts.tolist()))
    total = int(np.prod(cells.shape))
    print(f"地图 {args.map}   栅格 {cells.shape} @ {resolution} m   "
          f"原点 ({origin[0]:.2f}, {origin[1]:.2f})")
    print(f"  自由 {tally.get(0, 0)} 格（{tally.get(0, 0) * resolution ** 2:.1f} m²）· "
          f"未知 {tally.get(-1, 0)} 格（{100 * tally.get(-1, 0) / total:.0f}%）· "
          f"占据 {tally.get(100, 0)} 格")
    print("  真值：MuJoCo 模型里每个房间自己的地板盒（不来自路点、不来自地图）\n")

    static_entries = {}
    semantic = SemanticMap.from_file()
    for name in boxes:
        location = semantic.resolve(name)
        if location is None:
            continue
        pose = location.to_pose7()
        static_entries[name] = {"pose": [pose[0], pose[1], math.atan2(pose[6], pose[3])],
                                "aliases": list(location.aliases)}
    print("委托路点 vs 模型房间盒:")
    waypoint_outliers = []
    for name, entry in static_entries.items():
        landed = room_of(entry["pose"][0], entry["pose"][1], boxes)
        if landed == name:
            print(f"  ✓ {name:14s} ({entry['pose'][0]:6.2f},{entry['pose'][1]:6.2f})")
        else:
            waypoint_outliers.append(name)
            print(f"  ✗ {name:14s} ({entry['pose'][0]:6.2f},{entry['pose'][1]:6.2f})"
                  f" → 落在 {landed or '所有房间之外'}；这是工作台停靠位，不是房间中心")

    # --- how well does the segmenter read the real grid? --------------------
    print("\n门宽半径扫描（真实 SLAM 栅格，真值 5 间房）:")
    print(f"  {'radius':>7}{'区域数':>8}{'命中真实房间':>14}{'面积m2':>10}")
    sweep = []
    for radius, segmentation in zip(
            DOOR_RADII,
            door_radius_sweep(cells, resolution=resolution, origin=origin, radii=DOOR_RADII,
                              footprint_radius_m=FOOTPRINT_RADIUS_M,
                              seed_xy=START_XY)):
        hit = {room_of(room.centre_xy[0], room.centre_xy[1], boxes)
               for room in segmentation.rooms}
        hit.discard(None)
        area = sum(room.area_m2 for room in segmentation.rooms)
        sweep.append({"doorRadiusM": radius, "regions": len(segmentation.rooms),
                      "regionsInRealRooms": len(hit), "totalAreaM2": round(area, 3)})
        print(f"  {radius:>7.2f}{len(segmentation.rooms):>8}{len(hit):>14}{area:>10.1f}")

    # --- does it matter where the person clicked? ---------------------------
    rng = np.random.default_rng(CLICK_SEED)
    candidates = {name: free_inside(box, cells, resolution, origin)
                  for name, box in boxes.items()}
    clicks: dict[str, list[tuple[float, float]]] = {}
    print(f"\n可点击的自由格（真值房间盒 ∩ 栅格自由，每房抽 {args.clicks} 点，"
          f"种子 {CLICK_SEED}）:")
    for name, found in candidates.items():
        if not found:
            clicks[name] = []
            print(f"  {name:14s} 该房间在栅格里没有一个自由格——无处可点")
            continue
        take = min(args.clicks, len(found))
        picks = rng.choice(len(found), size=take, replace=False)
        clicks[name] = [found[int(index)] for index in sorted(picks)]
        note = "" if take == args.clicks else f"（只有 {len(found)} 格可选）"
        print(f"  {name:14s} {len(found):4d} 格可选，抽 {take:3d} 点 {note}")

    results = {}
    for name, points in clicks.items():
        correct, landings, clearances, travels = 0, {}, [], []
        per_click = []
        for point in points:
            built = build_seeded(cells=cells, resolution=resolution, origin=origin,
                                 seed_xy=START_XY, door_radius_m=DOOR_RADIUS_M,
                                 footprint_radius_m=FOOTPRINT_RADIUS_M,
                                 seeds={name: {"point": list(point), "aliases": [name]}})
            entry = built.workspaces[0] if built.workspaces else None
            if entry is None:
                per_click.append({"click": list(point),
                                  "outcome": "no workspace published for this click"})
                continue
            target = entry["target"]
            landed = room_of(target[0], target[1], boxes)
            landings[landed] = landings.get(landed, 0) + 1
            clearance = clearance_at(field, cells, resolution, origin, target[0], target[1])
            travel = math.dist(point, (target[0], target[1]))
            clearances.append(clearance)
            travels.append(travel)
            hit = landed == name
            correct += 1 if hit else 0
            per_click.append({
                "click": list(point), "published": [round(target[0], 3), round(target[1], 3)],
                "landedIn": landed, "correct": hit, "clearanceM": round(clearance, 3),
                "movedFromClickM": round(travel, 3),
                "reachable": reachable_from(grid, START_XY, target, traversable)})
        results[name] = {
            # What the representation says about itself for this seed, so the run
            # records the guard firing rather than only the outcome it produced.
            "degraded": built.degraded, "unavailable": built.unavailable,
            "clickableCells": len(candidates[name]), "clicks": len(points),
            "landedInNamedRoom": correct, "landings": landings,
            "clearanceMinM": round(min(clearances), 3) if clearances else None,
            "clearanceMeanM": round(statistics.fmean(clearances), 3) if clearances else None,
            "meanTravelFromClickM": round(statistics.fmean(travels), 3) if travels else None,
            "reachableFraction": (
                round(sum(1 for item in per_click if item.get("reachable"))
                      / max(1, sum(1 for item in per_click if "reachable" in item)), 4)
                if any("reachable" in item for item in per_click) else None),
            "perClick": per_click,
        }

    print("\nseeded：点在哪，结果会不会变？（真值 = 点击所在的那间房）")
    print(f"  {'房间':14s}{'点击':>5}{'落在正确房间':>13}{'命中率':>8}"
          f"{'最小净空':>9}{'平均净空':>9}{'离点击点':>9}{'可达':>7}")
    for name, row in results.items():
        rate = 100.0 * row["landedInNamedRoom"] / row["clicks"] if row["clicks"] else 0.0
        reach = "" if row["reachableFraction"] is None else f"{100 * row['reachableFraction']:.0f}%"
        print(f"  {name:14s}{row['clicks']:>5}{row['landedInNamedRoom']:>13}{rate:>7.0f}%"
              f"{row['clearanceMinM']!s:>9}{row['clearanceMeanM']!s:>9}"
              f"{row['meanTravelFromClickM']!s:>9}{reach:>7}")
    wrong = {name: row for name, row in results.items()
             if row["clicks"] and row["landedInNamedRoom"] != row["clicks"]}
    if wrong:
        print("\n  不是每一次点击都落在它命名的房间里:")
        for name, row in wrong.items():
            print(f"    {name}: 落在 {row['landings']}")

    # --- the commissioned poses on the same measures -------------------------
    print("\nstatic：同样的三项（委托路点，真值同样是模型房间盒）:")
    printed = []
    for name, entry in static_entries.items():
        x, y, _ = entry["pose"]
        landed = room_of(x, y, boxes)
        clearance = clearance_at(field, cells, resolution, origin, x, y)
        reachable = reachable_from(grid, START_XY, (x, y), traversable)
        printed.append({"name": name, "pose": [round(x, 3), round(y, 3)], "room": landed,
                        "inNamedRoom": landed == name, "clearanceM": round(clearance, 3),
                        "reachable": reachable})
        print(f"  {name:14s} ({x:6.2f},{y:6.2f}) → {landed or '房间外':14s} "
              f"净空 {clearance:.2f} m  可达 {reachable}")

    report = {
        "mapId": args.map, "mapRevision": manifest["hash"],
        "mapPackage": str(directory.relative_to(ROOT)),
        "grid": {"shape": list(cells.shape), "resolution": resolution, "origin": list(origin),
                 "freeCells": tally.get(0, 0), "unknownCells": tally.get(-1, 0),
                 "occupiedCells": tally.get(100, 0), "totalCells": total},
        "truth": {"source": "mujoco model room floor boxes",
                  "boxes": {name: {"centre": [cx, cy], "halfExtent": [hx, hy]}
                            for name, (cx, cy, hx, hy) in boxes.items()},
                  "waypointsOutsideTheirRoom": waypoint_outliers,
                  "waypointOutlierReason": {
                      name: "commissioned workcell dock, outside the room box by design"
                      for name in waypoint_outliers}},
        "footprintRadiusM": FOOTPRINT_RADIUS_M, "marginM": MARGIN_M,
        "doorRadiusM": DOOR_RADIUS_M, "doorRadiusSweep": sweep,
        "clicksPerRoom": args.clicks, "clickSeed": CLICK_SEED,
        "seeded": results, "static": printed,
    }
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"\n报告写入 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
