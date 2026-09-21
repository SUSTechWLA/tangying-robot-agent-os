"""GVF 的 Gazebo 配对证据实验、确定性回放及中文报告生成器。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from scipy.stats import ttest_rel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot/gateway"))
from grounded_gazebo import ORIGIN, ExperimentBackend, GazeboSession, physical_truth
from grounded_llm import LLMBaseline
from tangying_robot_gateway.grounded import RuntimeVerifier, load_contracts, render_report
from tangying_robot_gateway.grounded.model import (
    ActionContract,
    EvidenceSample,
    StateReport,
    canonical,
)
from tangying_robot_gateway.grounded.store import EvidenceStore
from tangying_robot_gateway.runtime import Command

SEEDS = [1729, 2718, 31415, 16180, 57721]
GROUPS = ["B0", "B1", "B2", "Ours", "A1", "A2", "A3", "A4", "A5"]


def task_set():
    tasks = []
    for target in ["cup", "plate"]:
        for fault in [
            "none",
            "grasp_miss",
            "grasp_slip",
            "wrong_object",
            "occluded",
            "missing_force",
        ]:
            tasks.append({"target": target, "fault": fault, "steps": ["manipulation.pick"]})
    for target in ["cup", "plate"]:
        for fault in ["none", "place_outside", "container_full", "missing_depth"]:
            tasks.append({"target": target, "fault": fault, "steps": ["manipulation.place"]})
    for fault in [
        "none",
        "none",
        "nav_not_reached",
        "nav_not_reached",
        "low_confidence",
        "missing_pose",
    ]:
        tasks.append({"target": "cup", "fault": fault, "steps": ["navigation.navigate"]})
    for fault in ["none", "grasp_miss", "occluded", "grasp_slip"]:
        tasks.append(
            {
                "target": "cup",
                "fault": fault,
                "steps": [
                    "navigation.navigate",
                    "manipulation.pick",
                    "manipulation.place",
                    "navigation.navigate",
                ],
                "route": "living-kitchen-living",
            }
        )
    return [dict(t, task_id=f"task-{i + 1:02d}") for i, t in enumerate(tasks)]


def command(identity, task, kind, target, goal=None):
    args = (
        {"objectId": target, "destinationId": "tray"}
        if kind.startswith("manipulation")
        else {"goalPose": goal}
    )
    return Command(
        schema_version="robot.v1",
        command_id=identity,
        task_id=task,
        capability=kind,
        target_ref=target,
        parameters=args,
        robot_id="gazebo-gvf-fixture",
        idempotency_key=identity,
        safety_profile="desktop_standard",
        approval_id="simulation-experiment",
        deadline_unix_ms=int(time.time() * 1000) + 60_000,
        lease_ms=60_000,
    )


def oracle_trace(paths, target, kind, goal=None):
    previous = None
    truth = []
    for p in paths:
        success, previous = physical_truth(p, target, kind, previous, goal)
        truth.append(success)
    return (
        "UNKNOWN"
        if any(v is None for v in truth[-3:])
        else ("VERIFIED" if all(truth[-3:]) else "FALSIFIED")
    )


def collect(args):
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    if (root / "trials.jsonl").exists():
        raise SystemExit("输出目录已有实验数据；请使用新目录，或 --replay 重放现有证据。")
    seeds = [int(s) for s in args.seeds.split(",")]
    tasks = task_set()[args.offset : args.offset + args.limit]
    if args.demo:
        tasks = [
            {
                "target": "cup",
                "fault": "grasp_miss",
                "steps": ["manipulation.pick"],
                "task_id": "demo-false-success",
            }
        ]
        seeds = [1729]
    config = {
        "schema_version": "grounded-experiment.v1",
        "backend": "Gazebo Harmonic / DART contact physics",
        "seeds": seeds,
        "tasks": tasks,
        "groups": GROUPS,
        "python": platform.python_version(),
        "git_head": subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip(),
        "source_sha256": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                Path(__file__),
                ROOT / "scripts/grounded_gazebo.py",
                ROOT / "scripts/run_grounded_experiments.sh",
                ROOT / "sim/gazebo/grounded_transport.cc",
                ROOT / "robot/ros2_ws/src/tangying_navigation/worlds/tangying_home.sdf",
                ROOT / "robot/gateway/tangying_robot_gateway/assets/grounded-contracts.json",
                *sorted((ROOT / "robot/gateway/tangying_robot_gateway/grounded").glob("*.py")),
            ]
        },
        "missing_groups": {
            "B1": "未配置实际 LLM；不以规则或随机数冒充模型",
            "A5": "未配置实际 LLM；不以规则或随机数冒充模型",
        },
        "design": "同一物理轨迹、同一原始证据上的配对离线比较；恢复候选另外实测；不是九套在线控制器各自运行。",
    }
    config["seed_reset"] = "每个种子启动独立 Gazebo 进程；不使用会丢失接触传感器的 world reset"
    for relative, digest in config["source_sha256"].items():
        source = ROOT / relative
        destination = root / "source_snapshot" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
            raise RuntimeError("采集期间源文件发生变化，请冻结代码后重跑")
    (root / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    store = EvidenceStore(root / "evidence")
    backend = None
    simulator = None
    contracts = load_contracts()
    try:
        with (root / "trials.jsonl").open("w") as output:
            for seed in seeds:
                if backend is not None:
                    backend.transport.close()
                if simulator is not None:
                    simulator.close()
                simulator = GazeboSession(root / "world.sdf", root / f"gazebo-{seed}.log", seed)
                backend = ExperimentBackend(root / "raw" / str(seed))
                for index, task in enumerate(tasks):
                    started = time.perf_counter()
                    backend.reset(seed * 100 + index, task["target"])
                    if task["steps"] == ["manipulation.place"]:
                        backend.execute(
                            command("prepare", task["task_id"], "manipulation.pick", task["target"])
                        )
                    for step, kind in enumerate(task["steps"]):
                        identity = f"{seed}/{task['task_id']}/{step}"
                        fault = (
                            task["fault"]
                            if (len(task["steps"]) == 1 or kind == "manipulation.pick")
                            else "none"
                        )
                        backend.fault = fault
                        if fault == "container_full":
                            backend.transport.call(
                                op="pose",
                                name="full_block",
                                xyz=[ORIGIN[0] + 0.28, ORIGIN[1] - 0.015, 0.56],
                            )
                            backend.transport.step(100)
                        current_pose = (
                            json.loads((backend.last_path / "odom.json").read_text())
                            .get("pose", {})
                            .get("position", {})
                        )
                        goal = [
                            current_pose.get("x", 0)
                            + (1 if index % 2 == 0 else -1)
                            * (
                                0.25
                                + float(np.random.default_rng(seed + index).uniform(-0.02, 0.02))
                            ),
                            current_pose.get("y", 0),
                            0.0,
                            1.0,
                            0.0,
                            0.0,
                            0.0,
                        ]
                        if len(task["steps"]) > 1 and kind == "navigation.navigate":
                            # Commissioned corridor through the existing room doorway. Wheel
                            # odometry is relative to (-4.7,-0.8); the oracle stays separate.
                            waypoints = (
                                [[0.0, 1.5], [5.9, 1.5]] if step == 0 else [[0.0, 1.5], [0.0, 0.0]]
                            )
                            for x, y in waypoints[:-1]:
                                backend.navigate([x, y, 0.0, 1.0, 0.0, 0.0, 0.0])
                            goal = [*waypoints[-1], 0.0, 1.0, 0.0, 0.0, 0.0]
                        cmd = command(identity, task["task_id"], kind, task["target"], goal)
                        action_start = time.perf_counter()
                        tool = backend.execute(cmd)
                        action_s = time.perf_counter() - action_start
                        window = backend.now_ns
                        observe_start = time.perf_counter()
                        samples = backend.collect_grounded_evidence(
                            command=cmd,
                            action_id=identity,
                            start_ns=window,
                            edge_boot_id=f"gazebo-seed-{seed}",
                            store=store,
                            phase="post",
                        )
                        observe_s = time.perf_counter() - observe_start
                        if fault == "missing_pose":
                            samples = [
                                store.record_sample(
                                    **dict(
                                        s.model_dump(exclude={"record_ref", "evidence_refs"}),
                                        evidence_refs=[
                                            r for r in s.evidence_refs if r.kind != "pose"
                                        ],
                                    )
                                )
                                for s in samples
                            ]
                        paths = list(backend.latest_frames)
                        truth = oracle_trace(paths, task["target"], kind, goal)
                        params = {
                            "object": task["target"],
                            "container": "tray",
                            "gripper": "right",
                            "surface": "table",
                            "robot": "gazebo-gvf-fixture",
                            "location": canonical(goal),
                            "goalPose": goal,
                        }
                        common = {
                            "action_id": identity,
                            "edge_boot_id": f"gazebo-seed-{seed}",
                            "start_ns": window,
                            "end_ns": backend.now_ns,
                            "params": params,
                            "task_id": task["task_id"],
                            "episode_id": str(seed),
                            "stage_id": str(step),
                        }
                        r = RuntimeVerifier(store.exists).verify(contracts[kind], samples, **common)
                        store.append(r)
                        trial = {
                            "seed": seed,
                            "task_id": task["task_id"],
                            "step": step,
                            "kind": kind,
                            "fault": fault,
                            "tool_return_status": "SUCCESS" if tool.success else tool.code,
                            "truth": truth,
                            "action_s": action_s,
                            "observation_s": observe_s,
                            "common": common,
                            "samples": [s.model_dump() for s in samples],
                            "raw_paths": [str(Path(p).relative_to(root)) for p in paths],
                            "original_report": r.model_dump(),
                            "recovery": None,
                        }
                        # Measure a bounded recovery candidate, selected only from the report.
                        # UNKNOWN first gets observation, never a hardware retry.
                        if r.verdict != "VERIFIED":
                            recovery_start = time.perf_counter()
                            old_fault = backend.fault
                            backend.fault = "none"
                            backend.transport.call(
                                op="pose", name="occluder", xyz=[ORIGIN[0] + 2, ORIGIN[1], 1.0]
                            )
                            rs = backend.now_ns
                            observed = backend.collect_grounded_evidence(
                                command=cmd,
                                action_id=identity,
                                start_ns=rs,
                                edge_boot_id=f"gazebo-seed-{seed}",
                                store=store,
                                phase="post",
                            )
                            observation_common = dict(common, start_ns=rs, end_ns=backend.now_ns)
                            after_observe = RuntimeVerifier(store.exists).verify(
                                contracts[kind], observed, **observation_common
                            )
                            steps = 1
                            retried = False
                            if after_observe.verdict == "FALSIFIED" and fault != "container_full":
                                if kind == "manipulation.place":
                                    # Re-grasping a dropped object requires a planner we do not
                                    # claim to have. Leave this to the upper layer.
                                    pass
                                else:
                                    backend.execute(cmd)
                                    retried = True
                                    steps += 1
                                    rs = backend.now_ns
                                    observed = backend.collect_grounded_evidence(
                                        command=cmd,
                                        action_id=identity,
                                        start_ns=rs,
                                        edge_boot_id=f"gazebo-seed-{seed}",
                                        store=store,
                                        phase="post",
                                    )
                            rt = oracle_trace(backend.latest_frames, task["target"], kind, goal)
                            trial["recovery"] = {
                                "steps": steps,
                                "hardware_retry": retried,
                                "truth": rt,
                                "after_observe": after_observe.verdict,
                                "latency_s": time.perf_counter() - recovery_start,
                                "samples": [s.model_dump() for s in observed],
                                "raw_paths": [
                                    str(Path(p).relative_to(root)) for p in backend.latest_frames
                                ],
                            }
                            backend.fault = old_fault
                        output.write(canonical(trial) + "\n")
                        output.flush()
                        os.fsync(output.fileno())
                        if args.demo:
                            (root / "demo-report.json").write_text(canonical(r) + "\n")
                            (root / "demo-report.txt").write_text(render_report(r) + "\n")
                            # An actual in-process invocation through the production safety/journal
                            # boundary is independently tested, including the durable replay barrier.
                    print(
                        f"完成 seed={seed} {task['task_id']} {task['fault']} {time.perf_counter() - started:.1f}s",
                        flush=True,
                    )
    finally:
        if backend is not None:
            backend.transport.close()
        if simulator is not None:
            simulator.close()
    return root


def parallel_collect(args):
    root = Path(args.output).resolve()
    if (root / "trials.jsonl").exists():
        raise SystemExit("输出目录已有实验数据")
    seeds = [int(value) for value in args.seeds.split(",")]
    if len(set(seeds)) != len(seeds):
        raise ValueError("随机种子不可重复")
    root.mkdir(parents=True, exist_ok=True)

    def run_seed(seed):
        child = root / "seeds" / str(seed)
        with (root / f"seed-{seed}.log").open("w") as log:
            subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts/run_grounded_experiments.sh"),
                    "--output",
                    str(child),
                    "--seeds",
                    str(seed),
                    "--limit",
                    str(args.limit),
                    "--offset",
                    str(args.offset),
                ],
                check=True,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        print(f"种子 {seed} 已完成：{child}", flush=True)
        return child

    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        children = list(executor.map(run_seed, seeds))
    return merge_children(root, children, args.jobs)


def merge_children(root, children, jobs):
    """Recover aggregation from complete immutable seed outputs, without running physics again."""
    root = Path(root)
    if (root / "trials.jsonl").exists():
        raise ValueError("聚合文件已存在，拒绝覆盖")
    configs = [json.loads((child / "config.json").read_text()) for child in children]
    seeds = [seed for config in configs for seed in config["seeds"]]
    if len(set(seeds)) != len(seeds):
        raise ValueError("种子重复")
    for child, config in zip(children, configs, strict=True):
        rows = [json.loads(line) for line in (child / "trials.jsonl").read_text().splitlines()]
        expected = {
            (seed, task["task_id"], step)
            for seed in config["seeds"]
            for task in config["tasks"]
            for step in range(len(task["steps"]))
        }
        actual = {(r["seed"], r["task_id"], r["step"]) for r in rows}
        if len(rows) != len(expected) or actual != expected:
            raise ValueError(f"子实验不完整：{child}")
    config = configs[0]
    if any(
        c["tasks"] != config["tasks"] or c["source_sha256"] != config["source_sha256"]
        for c in configs
    ):
        raise ValueError("子实验版本或任务集不一致，拒绝合并")
    config["seeds"] = seeds
    config["parallel_jobs"] = jobs
    store = EvidenceStore(root / "evidence")
    with (root / "trials.jsonl").open("x") as output:
        for child in children:
            prefix = child.relative_to(root)
            for blob in (child / "evidence/blobs").iterdir():
                try:
                    os.link(blob, store.root / "blobs" / blob.name)
                except FileExistsError:
                    pass
            for line in (child / "trials.jsonl").read_text().splitlines():
                trial = json.loads(line)
                trial["raw_paths"] = [str(prefix / p) for p in trial["raw_paths"]]
                if trial.get("recovery"):
                    trial["recovery"]["raw_paths"] = [
                        str(prefix / p) for p in trial["recovery"]["raw_paths"]
                    ]
                output.write(canonical(trial) + "\n")
                store.append(StateReport.model_validate(trial["original_report"]))
    (root / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    for name in ["world.sdf", "docker-image.txt"]:
        shutil.copy2(children[0] / name, root / name)
    shutil.copytree(children[0] / "source_snapshot", root / "source_snapshot", dirs_exist_ok=True)
    return root


def ablated(contract, group):
    data = contract.model_dump()
    if group == "A1":

        def strip(e):
            if e["op"] in ["stable", "within", "unchanged"]:
                return strip(e["children"][0])
            return dict(e, children=[strip(c) for c in e["children"]])

        data["postconditions"] = [strip(e) for e in data["postconditions"]]
    if group == "A2":
        data["min_confidence"] = 0.000001
    return ActionContract.model_validate(data)


def evaluate(root, llm_enabled=False, llm_workers=4, llm_config=None):
    root = Path(root)
    config = json.loads((root / "config.json").read_text())
    store = EvidenceStore(root / "evidence")
    trials = [json.loads(line) for line in (root / "trials.jsonl").read_text().splitlines()]
    contracts = load_contracts()
    rows = []
    llm = LLMBaseline(root, enabled=llm_enabled, config_path=llm_config)

    def evaluate_trial(t):
        trial_rows = []
        stream = [EvidenceSample.model_validate(s) for s in t["samples"]]
        for group in GROUPS:
            model_result = None
            report = None
            clock = time.perf_counter()
            failure = "NONE"
            confidence = 1.0
            if group in {"B1", "A5"}:
                model_result = llm.evaluate(t, group, contract=contracts[t["kind"]])
                if model_result is None:
                    continue
                verdict, failure = model_result["verdict"], model_result["failure_type"]
                confidence = None  # No calibrated model confidence is available.
            elif group == "B0":
                verdict = "VERIFIED" if t["tool_return_status"] == "SUCCESS" else "FALSIFIED"
            elif group == "B2":
                v = stream[-1].values
                if t["kind"] == "manipulation.pick":
                    passed = v.get("height_above_surface_m", 0) > 0.02
                elif t["kind"] == "manipulation.place":
                    passed = bool(v.get("inside_container"))
                else:
                    passed = (
                        v.get("position_error_m", math.inf) <= 0.05
                        and v.get("yaw_error_rad", math.inf) <= 0.12
                    )
                verdict = "VERIFIED" if passed else "FALSIFIED"
            else:
                report = RuntimeVerifier(store.exists).verify(
                    ablated(contracts[t["kind"]], group), stream, **t["common"]
                )
                verdict, failure, confidence = (
                    report.verdict,
                    report.failure_type,
                    report.confidence,
                )
                if group == "A2" and verdict == "UNKNOWN":
                    verdict = "FALSIFIED"
                if group == "A3" and failure != "NONE":
                    failure = "UNCLASSIFIED"
                if group != "A4":
                    render_report(report)
            verifier_s = time.perf_counter() - clock
            if report is not None:
                diagnostic_report = report.model_copy(
                    update={"failure_type": failure, "verdict": verdict}
                )
                model_result = llm.evaluate(t, group, report=diagnostic_report)
            model_s = model_result["latency_s"] if model_result else 0.0
            if group in {"B1", "A5"}:
                verifier_s = model_s
            recovery = t.get("recovery") if verdict != "VERIFIED" else None
            # Recovery success is a measured candidate, replayed by one shared policy.
            # A3 cannot select a specialized retry; it can still perform observation.
            use_recovery = recovery is not None and (
                group != "A3" or not recovery["hardware_retry"]
            )
            success = (
                (t["truth"] == "VERIFIED")
                if verdict == "VERIFIED"
                else (use_recovery and recovery["truth"] == "VERIFIED")
            )
            cause = {
                "grasp_miss": "GRASP_MISS",
                "grasp_slip": "GRASP_SLIP",
                "wrong_object": "WRONG_OBJECT",
                "place_outside": "PLACE_UNSTABLE",
                "container_full": "CONTAINER_FULL",
                "nav_not_reached": "NAV_NOT_REACHED",
                "occluded": "PERCEPTION_OCCLUDED",
                "missing_force": "EVIDENCE_INSUFFICIENT",
                "missing_depth": "EVIDENCE_INSUFFICIENT",
                "missing_pose": "EVIDENCE_INSUFFICIENT",
                "low_confidence": "EVIDENCE_INSUFFICIENT",
                "none": "NONE",
            }[t["fault"]]
            trial_rows.append(
                {
                    "seed": t["seed"],
                    "task_id": t["task_id"],
                    "step": t["step"],
                    "group": group,
                    "truth": t["truth"],
                    "verdict": verdict,
                    "failure_type": failure,
                    "confidence": confidence,
                    "correct": verdict == t["truth"],
                    "false_success": verdict == "VERIFIED" and t["truth"] != "VERIFIED",
                    "unknown": verdict == "UNKNOWN",
                    "attribution_correct": failure == cause,
                    "attribution_eligible": t["fault"] != "none",
                    "task_success": bool(success),
                    "physical_task_success": t["truth"] == "VERIFIED",
                    "hardware_retried": bool(use_recovery and recovery["hardware_retry"]),
                    "recovery_eligible": t["truth"] == "FALSIFIED",
                    "recovery_success": bool(use_recovery and recovery["truth"] == "VERIFIED"),
                    "repeated_failure": bool(
                        use_recovery
                        and recovery["hardware_retry"]
                        and recovery["truth"] != "VERIFIED"
                    ),
                    "recovery_steps": recovery["steps"] if use_recovery else 0,
                    "verifier_ms": verifier_s * 1000,
                    "end_to_end_ms": (
                        t["action_s"]
                        + (t["observation_s"] if group != "B0" else 0)
                        + verifier_s
                        + (model_s if group not in {"B1", "A5"} else 0.0)
                        + (recovery["latency_s"] if use_recovery else 0)
                    )
                    * 1000,
                    "human_interventions": int(verdict != "VERIFIED" and not success),
                    "llm_diagnosis_accuracy": (
                        not model_result.get("protocol_error")
                        and model_result["failure_type"] == cause
                    )
                    if model_result
                    else None,
                    "llm_tokens": (
                        (model_result.get("usage") or {}).get("total_tokens") if model_result else 0
                    ),
                    "llm_protocol_error": model_result.get("protocol_error")
                    if model_result
                    else None,
                    "llm_response_sha256": model_result["response_sha256"]
                    if model_result
                    else None,
                    "report_id": t["original_report"]["report_id"],
                }
            )
        return trial_rows

    if llm_enabled:
        with ThreadPoolExecutor(max_workers=llm_workers) as executor:
            for result in executor.map(evaluate_trial, trials):
                rows.extend(result)
    else:
        for trial in trials:
            rows.extend(evaluate_trial(trial))
    (root / "results.jsonl").write_text("".join(canonical(r) + "\n" for r in rows))
    config["missing_groups"] = {
        g: "缺少实际模型响应；未运行" for g in GROUPS if not any(r["group"] == g for r in rows)
    }
    (root / "analysis-config.json").write_text(
        canonical(
            {
                "source_sha256": {
                    str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in [
                        Path(__file__),
                        ROOT / "scripts/grounded_llm.py",
                        *sorted(
                            (ROOT / "robot/gateway/tangying_robot_gateway/grounded").glob("*.py")
                        ),
                    ]
                },
                "missing_groups": config["missing_groups"],
                "llm_enabled": llm_enabled,
            }
        )
        + "\n"
    )
    generate_report(root, config, trials, rows)
    return rows


def generate_report(root, config, trials, rows):
    metrics = [
        "task_success",
        "correct",
        "false_success",
        "unknown",
        "attribution_correct",
        "recovery_success",
        "repeated_failure",
        "recovery_steps",
        "verifier_ms",
        "end_to_end_ms",
        "human_interventions",
        "physical_task_success",
        "llm_diagnosis_accuracy",
        "llm_tokens",
    ]
    stats = {}
    seedstats = {}
    matrices = {}
    for group in GROUPS:
        subset = [r for r in rows if r["group"] == group]
        if not subset:
            continue
        seedstats[group] = {}
        for seed in config["seeds"]:
            part = [r for r in subset if r["seed"] == seed]
            values = {}
            for metric in metrics:
                eligible = part
                if metric == "attribution_correct":
                    eligible = [r for r in part if r["attribution_eligible"]]
                if metric == "recovery_success":
                    eligible = [r for r in part if r["recovery_eligible"]]
                if metric == "llm_diagnosis_accuracy":
                    eligible = [
                        r for r in part if r[metric] is not None and r["attribution_eligible"]
                    ]
                if metric == "llm_tokens":
                    eligible = [r for r in part if r[metric] is not None]
                if metric == "repeated_failure":
                    eligible = [r for r in part if r["hardware_retried"]]
                if metric in {"task_success", "physical_task_success"}:
                    task_ids = sorted({r["task_id"] for r in part})
                    values[metric] = float(
                        np.mean(
                            [
                                all(r[metric] for r in part if r["task_id"] == task)
                                for task in task_ids
                            ]
                        )
                    )
                else:
                    values[metric] = (
                        float(np.mean([r[metric] for r in eligible])) if eligible else None
                    )
            seedstats[group][seed] = values
        stats[group] = {}
        for metric in metrics:
            measured = [v[metric] for v in seedstats[group].values() if v[metric] is not None]
            stats[group][metric] = {
                "mean": float(np.mean(measured)) if measured else None,
                "std": float(np.std(measured, ddof=1)) if len(measured) > 1 else 0.0,
                "measured_seeds": len(measured),
            }
        labels = ["VERIFIED", "FALSIFIED", "UNKNOWN"]
        matrices[group] = [
            [sum(r["truth"] == a and r["verdict"] == b for r in subset) for b in labels]
            for a in labels
        ]
    comparisons = []
    for group in stats:
        if group == "Ours":
            continue
        for metric in [
            "correct",
            "false_success",
            "task_success",
            "llm_diagnosis_accuracy",
            "llm_tokens",
        ]:
            if any(
                seedstats[g][seed][metric] is None
                for g in ["Ours", group]
                for seed in config["seeds"]
            ):
                continue
            a = np.array([seedstats["Ours"][s][metric] for s in config["seeds"]])
            b = np.array([seedstats[group][s][metric] for s in config["seeds"]])
            delta = a - b
            if len(a) < 2 or np.std(delta) == 0:
                p = None
            else:
                p = float(ttest_rel(a, b).pvalue)
            comparisons.append(
                {"group": group, "metric": metric, "mean_difference": float(delta.mean()), "p": p}
            )
    llm_stats = {}
    for group in stats:
        group_rows = [r for r in rows if r["group"] == group]
        diagnosed = [r for r in group_rows if r["llm_diagnosis_accuracy"] is not None]
        llm_stats[group] = {
            "diagnosed_actions": len(diagnosed),
            "protocol_errors": sum(bool(r["llm_protocol_error"]) for r in group_rows),
            "diagnosed_injected_fault_actions": sum(r["attribution_eligible"] for r in diagnosed),
            "diagnosis_accuracy_injected_faults": stats[group]["llm_diagnosis_accuracy"]["mean"],
            "diagnosis_accuracy_all_actions": float(
                np.mean([r["llm_diagnosis_accuracy"] for r in diagnosed])
            )
            if diagnosed
            else None,
            "total_tokens": sum(r["llm_tokens"] for r in group_rows)
            if all(r["llm_tokens"] is not None for r in group_rows)
            else None,
        }
    summary = {
        "llm_statistics": llm_stats,
        "statistics": stats,
        "seed_statistics": seedstats,
        "confusion_matrices": matrices,
        "paired_t_tests": comparisons,
        "missing_groups": config["missing_groups"],
    }
    (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    def fmt(group, metric):
        if group not in stats:
            return "未运行"
        item = stats[group][metric]
        if item["mean"] is None:
            return "未测"
        scale = (
            100
            if metric
            not in [
                "recovery_steps",
                "verifier_ms",
                "end_to_end_ms",
                "human_interventions",
                "llm_tokens",
            ]
            else 1
        )
        return f"{item['mean'] * scale:.2f} ± {item['std'] * scale:.2f}"

    report = [
        "# 物理接地验证框架：Gazebo 接触实验与配对回放报告",
        "",
        "## 1. 实验目的与假设",
        "",
        "检验动作后多传感器与时序合约是否减少工具伪成功，是否以更多 UNKNOWN 和额外延迟为代价；检验移除时序、置信度、失败分类和中文模板的影响。实验不预设必须获得显著提升。",
        "",
        "## 2. 实验设置",
        "",
        f"引擎：{config['backend']}。复用仓库五房间几何与移动底盘，新增有质量、摩擦和关节 PID 的双指实验夹具及顶部 RGB-D 相机。固定种子 {config['seeds']}，{len(config['tasks'])} 个任务模板，{len(trials)} 个已执行动作轨迹。抓放使用实验夹具，尚不是 XLeRobot 的端到端移动抓取。",
        "每个动作都保存 RGB/depth 原始字节、关节反馈、接触力、里程计、独立 oracle、工具回执、时间戳与 SHA-256 引用。验证器只读取 RGB-D 派生量和传感器反馈；oracle 只用于离线计分。注入故障仍返回 SUCCESS。",
        "B0 信任回执；B2 只用末帧几何；Ours 使用 GCL；A1 移除时序；A2 去掉置信度阈值并把 UNKNOWN 强制判为失败；A3 隐藏失败分类；A4 只传 JSON。B1 从未经合约裁决的数值观测自验证；A5 读取相同观测及合约后替代验证器。两者只用于离线计分，不能向生产硬件下指令。原始图像不出边缘，故这里是数值观测版 LLM 对照，不是多模态视觉基线。",
        f"本次缺失实验组：{config['missing_groups'] or '无'}。真实模型结果保存在 llm-cache；没有响应的组留空。",
        "**研究范围：这是同一批物理轨迹的配对离线验证与恢复策略回放。恢复候选确实执行并保存，但各组没有独立在线运行；任务成功率、恢复步骤和端到端成本是该回放策略的估计，不能当作九组自主机器人实测。**",
        "准确率以独立仿真物理真值为分母，UNKNOWN 计为未答对，同时单列弃权率与错误接受率。不会把缺证据自动标为真值 UNKNOWN 来提高得分。失败归因按注入目标计分；注入未生效或发生复合失败仍保留，不筛除不利样本。",
        "先按种子求均值，再报告均值 ± 样本标准差。使用双侧配对 t 检验；五个种子统计功效有限；差值恒定时不伪造 p 值，多重比较未校正，属于探索性统计。",
        "",
        "## 3. 一条命令复现",
        "",
        "```bash",
        "bash scripts/run_grounded_experiments.sh --jobs 3 --llm-config artifacts/local-agent/local.env --output artifacts/grounded-verification/reproduce",
        "```",
        "",
        "完整九组需要有效的私有模型配置（AGENT_BASE_URL、AGENT_MODEL、AGENT_API_KEY）；密钥不会写入报告或传给 Gazebo。移除 --llm-config 可运行无模型子集，但不构成完整九组比较。只重放已保存证据：`.venv/bin/python scripts/grounded_experiment.py --replay --output <运行目录>`。完整配置见 `config.json`，逐动作原始数据见 `trials.jsonl`，逐组结果见 `results.jsonl`，聚合见 `summary.json`。",
        "",
        "## 4. 结果",
        "",
        "| 组 | 任务成功率估计 % | 验证准确率 % | 错误接受率 % | UNKNOWN % | 归因准确率 % | 恢复成功率估计 % |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for g in GROUPS:
        report.append("| " + g + " | " + " | ".join(fmt(g, m) for m in metrics[:6]) + " |")
    report += [
        "",
        "| 组 | 重复失败率估计 % | 平均恢复步骤估计 | 验证器 ms | 端到端 ms 估计 | 每动作人工升级次数估计 | LLM 诊断 % | LLM tokens |",
        "|---|---:|---:|---:|---:|---:|---|---:|",
    ]
    for g in GROUPS:
        report.append(
            "| "
            + g
            + " | "
            + " | ".join(fmt(g, m) for m in metrics[6:11])
            + " | "
            + fmt(g, "llm_diagnosis_accuracy")
            + " | "
            + fmt(g, "llm_tokens")
            + " |"
        )
    report += [
        "",
        f"共同物理轨迹的实际任务成功率：**{fmt('Ours', 'physical_task_success')}%**。各组读取相同轨迹，此实测值对所有组相同；表中的任务成功率是恢复策略回放估计。",
        "重复失败率的分母为实际选用的硬件重试次数；无重试时标为未测。诊断准确率以注入故障动作为分母；tokens 为每动作模型调用的均值 ± 种子间标准差。人工升级列是策略请求估计，实际无人参与手工恢复。遮挡恢复包含实验端移走遮挡板，缺失反馈恢复包含解除故障注入，不是机器人自主修复传感器。",
        "验证器延迟包含本地证据哈希检查、合约解释和报告渲染，不包含传感器采集；B1/A5 的验证器延迟是实测模型请求时间。端到端列按物理执行、观察、裁决、模型诊断和选用恢复候选的耗时相加，是策略回放估计。共享 CPU 主机上的三路 Gazebo 并发及测试负载会影响墙钟耗时。",
        "模型配置与实际响应模型见 llm-config.json 和 llm-cache。非思考模式、temperature=0、JSON 输出；每条响应保存模型提供的 usage。无效 JSON 记录 protocol_error 并按弃权计，不算正确诊断。",
        f"实际模型调用总 tokens：{sum(v['total_tokens'] or 0 for v in llm_stats.values())}；协议异常：{sum(v['protocol_errors'] for v in llm_stats.values())}。",
        "![准确率、错误接受率与弃权率](comparison.svg)",
        "",
        "混淆矩阵：行是真值 VERIFIED/FALSIFIED/UNKNOWN，列是预测 VERIFIED/FALSIFIED/UNKNOWN。",
        "",
    ]
    for g, matrix in matrices.items():
        report.append(f"- {g}：`{matrix}`")
    report += [
        "",
        "## 5. 配对统计与分析",
        "",
        "| 比较 Ours − 对照 | 指标 | 均值差 | 双侧 p |",
        "|---|---|---:|---:|",
    ]
    for c in comparisons:
        p = "不适用（差值无方差或不足两个种子）" if c["p"] is None else f"{c['p']:.6g}"
        report.append(f"| {c['group']} | {c['metric']} | {c['mean_difference']:.4f} | {p} |")
    report += [
        "",
        f"Ours 的错误接受率为 {fmt('Ours', 'false_success')}% ，B0 为 {fmt('B0', 'false_success')}% 。准确率与 UNKNOWN 必须一起读：拒绝下结论可能降低总准确率，但能阻止证据不足的继续动作。不能仅凭一个综合分数宣称全面优于所有基线。",
        f"本批 Ours 准确率 {fmt('Ours', 'correct')}%，B2 {fmt('B2', 'correct')}%，A5 {fmt('A5', 'correct')}%。弃权必须计入代价，不能把 UNKNOWN 从准确率分母删去。",
        "B1/A5 与 Ours 的准确率、错误接受率需分别比较；模型只接收数值观测，结论不能外推到所有多模态模型。A4 与 Ours 的物理结论相同，诊断准确率及 token 差异反映本模型对 JSON 与中文模板的阅读表现。",
        "",
        f"A1 相对 Ours 的总正确判定数变化：{sum(r['correct'] for r in rows if r['group'] == 'A1') - sum(r['correct'] for r in rows if r['group'] == 'Ours')}。如果为零，则这批样本没有证明时序算子的增益；滑落也可能被末帧力与几何直接识别。",
        f"Ours 的 {sum(r['false_success'] for r in rows if r['group'] == 'Ours')} 次错误接受必须保留并追因；相同传感器输入上的形式化检查不能消除底层定位系统偏差。",
        "",
        "## 6. 失败案例与原始证据",
        "",
    ]
    chosen = []
    for t in trials:
        if t["fault"] not in [c["fault"] for c in chosen] and t["fault"] != "none":
            chosen.append(t)
        if len(chosen) >= 8:
            break
    natural_false_accept = next(
        (
            t
            for t in trials
            if t["truth"] == "FALSIFIED" and t["original_report"]["verdict"] == "VERIFIED"
        ),
        None,
    )
    if natural_false_accept is not None:
        chosen.append(natural_false_accept)
    for t in chosen:
        r = t["original_report"]
        refs = [x["uri"] for x in r["evidence_refs"][:2]]
        report += [
            f"### {t['seed']}/{t['task_id']}：{t['fault']}",
            f"工具返回 {t['tool_return_status']}；物理真值 {t['truth']}；验证器 {r['verdict']}/{r['failure_type']}。",
            f"原始目录：`{t['raw_paths'][-1]}`；报告：`{r['report_id']}`；证据：`{refs}`。",
            "",
        ]
    report += [
        "## 7. 消融分析",
        "",
        "- A1：移除时序后的判定变化见上文。本批故障以持续失效为主，不能因实现了时序算子就声称实验已证明其收益。",
        "- A2：UNKNOWN 被强制变成失败后，遮挡与缺传感器会被当作明确失败，不能据此安全重试。",
        "- A3：保留只读观察恢复；没有失败类型时不选择专门硬件重试。差异依赖固定策略，不是分类器直接提高执行器能力。",
        "- A4：物理验证相同；LLM 诊断和 token 消耗的实测差异见表，不能从物理准确率推断模板收益。",
        "- A5：只统计实际模型调用及缓存响应；缺少配置时保持未运行。",
        "",
        "## 8. 局限性",
        "",
        "1. 接触夹具是隔离的实验执行器，不等价于生产 XLeRobot 的真实末端抓取；跨房间步骤与夹具操作可以组成任务，但不是同一本体携物穿门验收。",
        "2. 颜色分割使用已知物体和固定相机标定，不具备开放世界识别。置信度是保守工程评分，尚未做概率校准；0.95 不等于已测得 95% 正确率。",
        "3. 同批轨迹回放不能替代独立在线恢复对照；未实现抓取落到任意位置的重规划，放置失败与满容器交给上层。",
        "4. 模型字段只在实际调用时填写；缺失时不能证明优于 LLM。LLM 诊断与 token 汇总以实际调用为分母，缺 usage 时不猜测。",
        "5. Gazebo 软件渲染和进程调度会使墙钟延迟、接触结果有小幅波动。固定种子、源文件哈希、原始证据回放可重现统计输入，不能承诺跨机器物理仿真逐字节相同。",
        "6. 长距离跨房间的基准控制器使用轮式里程计，可能穿越不了障碍但继续积累轮速位移。独立世界坐标真值能揭示这种错误；生产入口因此只开放有深度保护的有界短程动作，尚未完成跨房间导航认证。",
        "7. LLM 对照接收按采样顺序排列的数值观测、置信度和证据引用，未输入图像字节及完整时钟清单；不能代表多模态模型或独立时钟一致性检查能力。请求的 seed 是否被服务实现由供应商决定；实际响应缓存才是精确回放依据。",
        "8. GCL 是有限轨迹的可执行合约检查器，不是完整 LTL 模型检查器或物理正确性的数学证明。其他硬件动作的未知合约保持拒绝或 UNKNOWN。",
        "",
        "## 9. 结论与下一步",
        "",
        "本次实现了独立于 LLM 的物理证据裁决、持久化报告、确定性中文渲染和未知结果阻断。能用真实 Gazebo 接触与相机证据区分命令成功和物理完成。本报告提供单一模型的数值观测对照；是否改善具体指标应以本表及配对检验为准。真实移动抓取和独立在线恢复收益仍需补充实验，不能由本报告推出。",
        "下一步：扩大模型与故障分布；校准多传感器置信度；为 XLeRobot 采集夹爪反馈并现场标定合约阈值；独立运行每组闭环策略。",
        "",
    ]
    (root / "report.md").write_text("\n".join(report))
    # Standard plotting output; error bars show sample SD across five seeds.
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    for font_path in [
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]:
        if Path(font_path).exists():
            font_manager.fontManager.addfont(font_path)
            plt.rcParams["font.family"] = font_manager.FontProperties(fname=font_path).get_name()
            break
    # Keep SVG labels as text so the viewer can supply its own CJK fallback font.
    plt.rcParams["svg.fonttype"] = "none"

    names = list(stats)
    x = np.arange(len(names))
    fig, ax = plt.subplots(figsize=(12, 4.7), layout="constrained")
    for i, (metric, label, color) in enumerate(
        [
            ("correct", "验证准确率", "#167d72"),
            ("false_success", "错误接受率", "#dc5a46"),
            ("unknown", "弃权率 UNKNOWN", "#8290a5"),
        ]
    ):
        ax.bar(
            x + (i - 1) * 0.25,
            [100 * stats[g][metric]["mean"] for g in names],
            width=0.23,
            yerr=[100 * stats[g][metric]["std"] for g in names],
            capsize=2,
            label=label,
            color=color,
        )
    ax.set(
        xticks=x,
        xticklabels=names,
        ylabel="百分比（均值 ± 种子间标准差）",
        ylim=(0, 110),
        title=f"Gazebo 配对验证：{len(config['tasks'])} 个任务 × {len(config['seeds'])} 个种子",
    )
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(loc="upper right", frameon=False, ncol=3)
    fig.savefig(root / "comparison.svg")
    fig.savefig(root / "comparison.png", dpi=170)
    plt.close(fig)
    print(f"报告已生成：{root / 'report.md'}", flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", required=True)
    p.add_argument("--seeds", default=",".join(map(str, SEEDS)))
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--jobs", type=int, default=1)
    p.add_argument("--llm-workers", type=int, default=4)
    p.add_argument("--llm-config", default=None)
    p.add_argument("--replay", action="store_true")
    p.add_argument(
        "--merge-only",
        action="store_true",
        help="从完整的 seeds 子目录恢复聚合，不重新执行物理动作",
    )
    p.add_argument("--demo", action="store_true")
    p.add_argument(
        "--llm", action="store_true", help="调用已配置模型补齐 LLM 对照；推荐与 --replay 配合"
    )
    args = p.parse_args()
    if not 1 <= args.llm_workers <= 16:
        p.error("--llm-workers 必须为 1..16")
    if not 1 <= args.jobs <= 5:
        p.error("--jobs 必须为 1..5")
    if args.merge_only and args.replay:
        p.error("--merge-only 与 --replay 互斥")
    root = (
        merge_children(
            Path(args.output),
            [Path(args.output) / "seeds" / seed for seed in args.seeds.split(",")],
            args.jobs,
        )
        if args.merge_only
        else Path(args.output)
        if args.replay
        else parallel_collect(args)
        if args.jobs > 1
        else collect(args)
    )
    evaluate(
        root,
        llm_enabled=args.llm or bool(args.llm_config),
        llm_workers=args.llm_workers,
        llm_config=args.llm_config,
    )


if __name__ == "__main__":
    main()
