"""Measure what the semantic object layer and work-area planning actually change.

Framework modules are now optimised one at a time, so "better" has to be a
measurement instead of an opinion. This benchmark runs the *same* input through
two configurations and reports the difference on four axes:

  runtime        - wall-clock time to obtain a destination or a place to look
  resources      - bytes added to the map, memory held, entries stored
  complex task   - a scripted multi-step household task, end to end
  responsiveness - how long the caller waits for an answer it can act on

Configurations
  baseline   the shipped semantic layer as it was: named places only. A caller
             can navigate to a commissioned waypoint and can look at whatever the
             current camera sees. Nothing tells it where an object was, and the
             waypoint is the only destination that exists.
  upgraded   the same layer plus `map.objects.v1` (recalled through
             `recall_object`) and reachable-pose planning (`plan_work_area` /
             `navigate_to_work_area`).

The task is deliberately the one the baseline cannot finish: the object is not
visible from the commissioned work-area pose, which is exactly the situation that
produced a RECOVERABLE_FAILURE in the household acceptance run. Both
configurations run the same scripted robot, so the difference is the layer, not
the scene.

Usage:
    .venv/bin/python scripts/benchmark_semantic_layer.py --map <map directory> \
        --output artifacts/semantic-benchmark/run-1
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "robot" / "gateway"))
sys.path.insert(0, str(REPO / "tests" / "tool_layer"))

import numpy as np
from fake_adapter import FakeRobotAdapter
from tangying_robot_gateway.map_catalog import MapCatalog
from tangying_robot_gateway.map_pipeline import PointCloud, build_map
from tangying_robot_gateway.object_memory import ObjectMemory
from tangying_robot_gateway.semantic_map import SemanticMap
from tangying_robot_gateway.tools import build_registry
from tangying_robot_gateway.tools.registry import ObservationView
from tangying_robot_gateway.workspace_planner import WorkspaceEnvelope

#: The commissioned workcell pose. The mug sits outside this pose's view, which is
#: what makes the task interesting: arriving is not the same as finding.
WORK_AREA = "kitchen"
CUP = "cup"
TRAY = "storage_bin"


@dataclass
class Scenario:
    """A published map plus the object layer a survey would have left in it."""

    directory: Path
    map_id: str
    revision: str
    calibration_revision: str
    objects: list[dict]
    cloud_points: int
    object_bytes: int

    @property
    def artifacts(self) -> dict:
        return json.loads((self.directory / "manifest.json").read_text())["artifacts"]


def build_scenario(root: Path, *, points: int = 40_000, objects: int = 12,
                   seed: int = 20260914) -> Scenario:
    """One map, built twice: with and without the object layer's bytes."""
    rng = np.random.default_rng(seed)
    xyz = np.zeros((points, 3), dtype=np.float32)
    xyz[:, 0] = rng.uniform(-4.0, 4.0, points)
    xyz[:, 1] = rng.uniform(-2.0, 8.0, points)
    xyz[:, 2] = rng.uniform(0.0, 2.2, points)
    cloud = PointCloud(xyz, rng.integers(0, 255, (points, 3), dtype=np.uint8))
    grid = {"width": 160, "height": 200, "resolution": .05, "origin": [-4.0, -2.0, 0.],
            "cells": np.zeros((200, 160), dtype=np.int16)}
    workspaces = [{"name": "厨房台面", "aliases": ["kitchen counter", "kitchen"],
                   "target": [1.6, 2.4, .9]},
                  {"name": "客厅茶几", "aliases": ["living room"], "target": [0.0, -1.0, .5]}]
    memory = ObjectMemory()
    now = int(time.time() * 1000)
    entities = [type("E", (), {"entity_id": f"obj-{index}", "category": CUP if index % 4 else TRAY,
                               "attributes": {"color": "white" if index % 2 else "blue"},
                               "pose_xyz_quat": [float(rng.uniform(-3, 3)),
                                                 float(rng.uniform(-1, 7)), .85, 1., 0., 0., 0.],
                               "confidence": .9})()
                for index in range(objects)]
    memory.observe(entities, map_from_world=[0., 0., 0.], stamp_unix_ms=now - 1_000,
                   evidence_frame_id="map")
    document = memory.document(now_unix_ms=now, map_id="semantic-benchmark",
                               calibration_revision="c" * 64)

    directory = root / "semantic-benchmark"
    build_map(directory, map_id="semantic-benchmark", robot_id="unit-1", cloud=cloud,
              calibration_revision="c" * 64, lod_levels=3, occupancy_grid=grid,
              semantic_workspaces=workspaces, semantic_objects=document)
    manifest = json.loads((directory / "manifest.json").read_text())
    return Scenario(directory=directory, map_id="semantic-benchmark", revision=manifest["hash"],
                    calibration_revision="c" * 64, objects=document["objects"],
                    cloud_points=points,
                    object_bytes=manifest["artifacts"]["objects"]["bytes"])


def planning_context(scenario: Scenario, *, fresh: bool = True):
    return lambda: {"map_id": scenario.map_id, "robot_id": "unit-1",
                    "calibration_revision": scenario.calibration_revision,
                    "map_revision": scenario.revision, "start_xy": [0.0, -1.0],
                    "envelope": WorkspaceEnvelope(.12, .05, .2, .8, .7),
                    "localization_fresh": fresh}


def registries(scenario: Scenario):
    """Both configurations built from the same map, differing only by provider."""
    catalog = MapCatalog(scenario.directory.parent)
    semantic = SemanticMap.from_file()
    baseline = build_registry(FakeRobotAdapter(), semantic,
                              context=None, include_composite=True)
    upgraded = build_registry(FakeRobotAdapter(), semantic, map_catalog=catalog,
                              planning_context=planning_context(scenario),
                              object_recall=lambda name, attributes, max_age_ms: catalog.recall_objects(
                                  scenario.map_id, robot_id="unit-1",
                                  calibration_revision=scenario.calibration_revision,
                                  category=name, attributes=attributes, max_age_ms=max_age_ms))
    return {"baseline": baseline, "upgraded": upgraded}


# ---------------------------------------------------------------- dimensions


def measure_runtime(scenario: Scenario, runs: int = 25) -> dict:
    """How long each configuration takes to answer the two questions a task asks."""
    catalog = MapCatalog(scenario.directory.parent)
    recall = lambda name, attributes, max_age_ms: catalog.recall_objects(
        scenario.map_id, robot_id="unit-1", calibration_revision=scenario.calibration_revision,
        category=name, attributes=attributes, max_age_ms=max_age_ms)

    def timed(call):
        samples = []
        for _ in range(runs):
            started = time.perf_counter()
            call()
            samples.append((time.perf_counter() - started) * 1000)
        return {"medianMs": round(statistics.median(samples), 3),
                "p95Ms": round(sorted(samples)[max(0, int(len(samples) * .95) - 1)], 3)}

    tools = registries(scenario)
    return {
        "baseline": {
            "whereIsTheObject": None,
            "reachableDestination": timed(lambda: tools["baseline"].require("navigate_to").execute(
                location_name="kitchen counter")),
        },
        "upgraded": {
            "whereIsTheObject": timed(lambda: tools["upgraded"].require("recall_object").execute(
                object_name=CUP, max_age_s=3600)),
            "reachableDestination": timed(lambda: tools["upgraded"].require("plan_work_area").execute(
                location_name="kitchen counter")),
        },
        "_samples": runs,
        "_recallRawMs": timed(lambda: recall(CUP, {}, 3_600_000)),
    }


def measure_resources(scenario: Scenario) -> dict:
    """What the layer costs to carry: bytes in the map, entries, peak memory."""
    tracemalloc.start()
    catalog = MapCatalog(scenario.directory.parent)
    catalog.recall_objects(scenario.map_id, robot_id="unit-1",
                           calibration_revision=scenario.calibration_revision, category=CUP)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    artifacts = scenario.artifacts
    layer = artifacts["objects"]
    return {
        "mapArtifactBytesWithoutLayer": sum(entry["bytes"] for role, entry in artifacts.items()
                                            if role != "objects" and isinstance(entry, dict)),
        "mapArtifactBytesWithLayer": sum(entry["bytes"] for entry in artifacts.values()
                                         if isinstance(entry, dict)),
        "layerBytes": layer["bytes"],
        "layerEntries": len(scenario.objects),
        "bytesPerEntry": round(layer["bytes"] / max(1, len(scenario.objects)), 1),
        "peakRecallMemoryKiB": round(peak / 1024, 1),
    }


def measure_complex_task(scenario: Scenario, *, runs: int = 5) -> dict:
    """The household task the baseline cannot finish, scripted identically twice.

    "Fetch the cup and put it in the tray." The detector only reports the cup from
    inside a 0.6 m bubble around its true position, which is how a tabletop shape
    detector behaves: arriving somewhere is not the same as seeing. The baseline
    has exactly one destination - the commissioned work-area pose - and no way to
    learn where the cup was; the upgraded configuration recalls the last sighting
    and drives there. Both runs are the same robot, the same map and the same
    detector; only the layer differs.
    """
    truth = next(entry for entry in scenario.objects if entry["category"] == CUP)
    target = np.array(truth["pose"][:2])
    catalog = MapCatalog(scenario.directory.parent)
    recall = lambda name, attributes, max_age_ms: catalog.recall_objects(
        scenario.map_id, robot_id="unit-1", calibration_revision=scenario.calibration_revision,
        category=name, attributes=attributes, max_age_ms=max_age_ms)
    context = planning_context(scenario)
    outcome = {}
    for name in ("baseline", "upgraded"):
        successes, steps, durations, travelled = 0, [], [], []
        for _ in range(runs):
            adapter = FakeRobotAdapter()
            registry = build_registry(
                adapter, SemanticMap.from_file(),
                map_catalog=catalog if name == "upgraded" else None,
                planning_context=context if name == "upgraded" else None,
                object_recall=recall if name == "upgraded" else None)
            started = time.perf_counter()
            attempt = _run_task(registry, adapter, truth, target, upgraded=name == "upgraded")
            durations.append((time.perf_counter() - started) * 1000)
            successes += 1 if attempt["delivered"] else 0
            steps.append(attempt["steps"])
            travelled.append(attempt["travelledM"])
        outcome[name] = {"successRate": round(successes / runs, 3),
                         "meanSteps": round(statistics.mean(steps), 1),
                         "medianMs": round(statistics.median(durations), 2),
                         "meanTravelledM": round(statistics.mean(travelled), 2),
                         "failures": runs - successes}
    outcome["_runs"] = runs
    outcome["_truthPose"] = [round(value, 3) for value in truth["pose"]]
    outcome["_commissionedPose"] = [1.6, 2.4]
    return outcome


def _run_task(registry, adapter, truth, target, *, upgraded: bool) -> dict:
    """One scripted attempt. Returns delivery, step count and modelled travel."""
    steps = 0
    start = np.array([0.0, -1.0])

    if upgraded:
        recalled = registry.require("recall_object").execute(object_name=CUP, max_age_s=3600)
        steps += 1
        if not recalled.success or not recalled.data.get("found"):
            return {"delivered": False, "steps": steps, "travelledM": 0.0}
        destination = np.array(recalled.data["best"]["pose"][:2])
    else:
        # The baseline knows a room, not an object: its only destination is the
        # commissioned work-area pose, and whatever that pose can see.
        destination = np.array([1.6, 2.4])

    travelled = float(np.linalg.norm(destination - start))
    steps += 1
    within_view = float(np.linalg.norm(destination - target)) <= 0.6
    adapter.observation = ObservationView(
        observation_id="obs-1", source_id="unit-1/head-rgbd", fresh=True,
        robot_state={"base_pose": [destination[0], destination[1], 0.0, 1.0, 0.0, 0.0, 0.0]},
        entities=[{"entity_id": truth["id"], "category": CUP,
                   "attributes": dict(truth["attributes"]), "pose": list(truth["pose"]),
                   "confidence": truth["confidence"]}] if within_view else [])
    found = registry.require("detect_object").execute(object_name="cup")
    steps += 1
    if not (found.success and found.data.get("found")):
        return {"delivered": False, "steps": steps, "travelledM": round(travelled, 2)}
    # Pick, place, verify - both configurations pay these steps, so they are
    # counted rather than modelled away.
    steps += 3
    return {"delivered": True, "steps": steps, "travelledM": round(travelled, 2)}


def measure_layer_cost(*, runs: int = 50) -> dict:
    """What building and carrying the layer costs, and how it scales.

    The perception pass itself is the driver's own observation path - the same one
    the console uses for `observe_scene` - so the marginal cost of this upgrade is
    what these three calls take, plus the bytes it adds to the map. The cliff is
    the instance budget: the published layer is bounded at 512 entries.
    """
    now = int(time.time() * 1000)
    scaling = {}
    for count in (12, 128, 512):
        # Spread past the association gate so every entity stays its own instance:
        # the cost being measured is "n objects", not "n sightings of one object".
        entities = [type("E", (), {"entity_id": f"obj-{index}", "category": "cup",
                                   "attributes": {"color": "white"},
                                   "pose_xyz_quat": [float(index) * .5, float(index % 7) * .5,
                                                     .8, 1., 0., 0., 0.],
                                   "confidence": .9})() for index in range(count)]
        memory = ObjectMemory()
        started = time.perf_counter()
        for _ in range(runs):
            memory = ObjectMemory()
            memory.observe(entities, map_from_world=[0., 0., 0.], stamp_unix_ms=now)
        observe_ms = (time.perf_counter() - started) / runs * 1000
        started = time.perf_counter()
        document = memory.document(now_unix_ms=now, map_id="m", calibration_revision="c" * 64)
        document_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        memory.reanchor([0., 0., 0.], [1., 0., 0.])
        reanchor_ms = (time.perf_counter() - started) * 1000
        encoded = len(json.dumps(document, ensure_ascii=False).encode("utf-8"))
        scaling[count] = {"observeMs": round(observe_ms, 3), "documentMs": round(document_ms, 3),
                          "reanchorMs": round(reanchor_ms, 3), "documentBytes": encoded}
    return {"perObservationCost": scaling, "_runs": runs,
            "pollCadenceMs": 1000,
            "note": "每次感知轮询的边际成本是 observe；document 在发布地图时调用一次"}


def measure_responsiveness(scenario: Scenario, *, runs: int = 25) -> dict:
    """Worst-case wait for a destination, including the failure path."""
    tools = registries(scenario)
    without_provider = build_registry(FakeRobotAdapter(), SemanticMap.from_file())

    def timed(call):
        samples = []
        for _ in range(runs):
            started = time.perf_counter()
            call()
            samples.append((time.perf_counter() - started) * 1000)
        return {"medianMs": round(statistics.median(samples), 3), "maxMs": round(max(samples), 3)}

    return {
        "baseline": {
            "planAnswer": timed(lambda: without_provider.require("plan_work_area").execute(
                location_name=WORK_AREA)),
            "recallAnswer": timed(lambda: without_provider.require("recall_object").execute(
                object_name=CUP)),
        },
        "upgraded": {
            "planAnswer": timed(lambda: tools["upgraded"].require("plan_work_area").execute(
                location_name=WORK_AREA)),
            "recallAnswer": timed(lambda: tools["upgraded"].require("recall_object").execute(
                object_name=CUP, max_age_s=3600)),
            "reachableCandidates": len(tools["upgraded"].require("plan_work_area").execute(
                location_name=WORK_AREA).data.get("candidates") or []),
        },
        "_samples": runs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPO / "artifacts/semantic-benchmark/run-1")
    parser.add_argument("--points", type=int, default=40_000)
    parser.add_argument("--objects", type=int, default=12)
    parser.add_argument("--runs", type=int, default=25)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    scenario = build_scenario(args.output, points=args.points, objects=args.objects)
    report = {
        "scenario": {"mapId": scenario.map_id, "revision": scenario.revision,
                     "cloudPoints": scenario.cloud_points, "objects": len(scenario.objects),
                     "workspaces": ["厨房台面", "客厅茶几"],
                     "note": "same map, same scripted robot; only the semantic layer differs"},
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "runtime": measure_runtime(scenario, runs=args.runs),
        "resources": measure_resources(scenario),
        "complexTask": measure_complex_task(scenario, runs=max(3, args.runs // 5)),
        "responsiveness": measure_responsiveness(scenario, runs=args.runs),
        "layerCost": measure_layer_cost(runs=max(10, args.runs * 2)),
    }
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    runtime, task, resources = report["runtime"], report["complexTask"], report["resources"]
    print(f"地图：{scenario.cloud_points:,} 点 + {len(scenario.objects)} 个物体记录"
          f"（物体层 {resources['layerBytes']} B，{resources['bytesPerEntry']} B/条）")
    print(f"运行时间  recall_object 中位 {runtime['upgraded']['whereIsTheObject']['medianMs']} ms"
          f"（baseline 无此答案）· plan_work_area 中位 "
          f"{runtime['upgraded']['reachableDestination']['medianMs']} ms")
    print(f"复杂任务  交付成功率 baseline {task['baseline']['successRate']:.0%}"
          f" → upgraded {task['upgraded']['successRate']:.0%}"
          f" · 平均步骤 {task['baseline']['meanSteps']} → {task['upgraded']['meanSteps']}")
    print(f"响应      baseline 拒绝中位 "
          f"{report['responsiveness']['baseline']['recallAnswer']['medianMs']} ms · upgraded 命中中位 "
          f"{report['responsiveness']['upgraded']['recallAnswer']['medianMs']} ms"
          f" · 工作区候选 {report['responsiveness']['upgraded']['reachableCandidates']} 个")
    print("报告：", args.output / "report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
