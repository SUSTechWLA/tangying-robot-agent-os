"""响应规划零分问题的前瞻结构对照：重新冻结并使用独立新实例确认。"""

from __future__ import annotations

import json
import random
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .client import Client
from .factorial_analysis import compare, holm, summarize
from .factorial_cases import COMMON, QUESTIONS, STAGES, canonical, make_pair, score
from .factorial_runner import MODELS, load_cases, mean, render, sha, verify, write_json

ARMS = ["flat_json", "nested_json", "entity_cnl", "prior_policy"]
NEW_SEEDS = [67867967, 67867979, 67867981]


def prepare(root):
    verify(root)
    folder = root / "structure-study"
    if (folder / "protocol.json").exists():
        return validate(root)
    folder.mkdir(exist_ok=True)
    cases = load_cases(root, "dev") + [
        c
        for s in STAGES
        for f in [1, 2, 3, 4]
        for seed in NEW_SEEDS
        for c in make_pair(s, "structure_confirmation", f, seed)
    ]
    (folder / "cases.jsonl").write_text("".join(canonical(c) + "\n" for c in cases))
    project = Path.cwd()
    subprocess.run(
        ["go", "build", "-o", str(folder / "context-render"), "./cmd/context-render"], check=True
    )
    sources = [
        *sorted((project / "core/agentcontext").glob("*.go")),
        project / "cmd/context-render/main.go",
        *[
            Path(__file__).with_name(n)
            for n in [
                "factorial_structure.py",
                "factorial_cases.py",
                "factorial_analysis.py",
                "factorial_runner.py",
                "client.py",
            ]
        ],
    ]
    source_hashes = {}
    for p in sources:
        rel = str(p.resolve().relative_to(project))
        target = folder / "source_snapshot" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
        source_hashes[rel] = sha(p)
    prior = json.loads((root / "confirmation/protocol.json").read_text())["policy"]
    protocol = {
        "version": "structure-protocol.v1",
        "motivation": "post-test adaptation after observing planning accuracy floor; not part of original factorial preregistration",
        "cases_sha256": sha(folder / "cases.jsonl"),
        "renderer_sha256": sha(folder / "context-render"),
        "sources": source_hashes,
        "new_seeds": NEW_SEEDS,
        "prior_policy": prior,
        "arms": ARMS,
        "dev_cases": 80,
        "confirmation_cases": 192,
        "selection": "dev per-stage unsafe<=flat_json, then exact correctness, then total tokens, then arm name; freeze before new confirmation",
        "gate": "confirmation per-stage accuracy>=flat_json and unsafe<=flat_json; otherwise retain flat_json; experimental only, no minimum absolute competence guarantee",
        "inference": "family paired bootstrap and sign-flip; Holm across 4 models/comparators; structure and serialization package total effects, not isolated hierarchy mechanism",
        "system_prompt": COMMON.replace(
            "输入采用 JSON Pointer 字段表或其受控自然语言等价表达",
            "输入采用完整嵌套 JSON、JSON Pointer 字段表或按字段/实体分组的受控语言等价表达",
        ),
        "independence": "new instance seeds of same semantic families, not semantic OOD; all arms receive the same amended format explanation",
    }
    write_json(folder / "protocol.json", protocol)
    (folder / "go.mod").write_text("module context-structure-archive\n\ngo 1.26\n")
    return protocol


def validate(root):
    folder = root / "structure-study"
    p = json.loads((folder / "protocol.json").read_text())
    assert sha(folder / "cases.jsonl") == p["cases_sha256"]
    assert sha(folder / "context-render") == p["renderer_sha256"]
    for rel, h in p["sources"].items():
        assert sha(folder / "source_snapshot" / rel) == h
        if rel.endswith(".py"):
            assert sha(Path(rel)) == h, "research source changed after structure registration"
    return p


def expression(p, model, stage, arm):
    if arm == "prior_policy":
        syntax, order, anno, _ = p["prior_policy"][model][stage].split("-")
        return {"syntax": syntax, "order": order, "annotation": anno == "derived", "complete": True}
    return {
        "syntax": "json" if arm == "flat_json" else arm,
        "order": "source",
        "annotation": False,
        "complete": True,
    }


def evaluate(root, split, config=None, replay=False):
    p = prepare(root)
    folder = root / "structure-study"
    if split != "dev":
        assert (folder / "selection.json").exists()
    cases = load_cases(folder, split)
    clients = {}
    jobs = []
    for m in MODELS:
        client = Client(root / "models" / m, config, enabled=False)
        assert (
            client.url
            == json.loads((root / "models" / m / "model-config.json").read_text())["base_url"]
        )
        client.model, client.enabled = m, not replay
        assert replay or client.key
        clients[m] = client
        for stage in STAGES:
            part = [c for c in cases if c["stage"] == stage]
            specs = [expression(p, m, stage, arm) for arm in ARMS]
            rendered = render(folder, part, specs)
            for i, (c, spec, r) in enumerate(rendered):
                jobs.append((m, c, ARMS[i % len(ARMS)], spec, r))
    random.Random(6792026).shuffle(jobs)

    def one(job):
        m, c, arm, spec, r = job
        response = clients[m].call(
            [
                {"role": "system", "content": p["system_prompt"] + QUESTIONS[c["stage"]]},
                {"role": "user", "content": r["text"]},
            ],
            c["seed"],
        )
        return {
            "model": m,
            "case_id": c["id"],
            "pair_id": c["pair_id"],
            "family": c["family"],
            "stage": c["stage"],
            "seed": c["seed"],
            "format": arm,
            "factors": spec,
            "prompt_hash": r["sha256"],
            "semantic_hash": r["semantic_sha256"],
            "request_hash": response["request_hash"],
            "answer": response["answer"],
            "protocol_error": response["protocol_error"],
            "total_tokens": (response.get("usage") or {}).get("total_tokens"),
            "latency_s": response["latency_s"],
            **score(c, spec, response["answer"]),
        }

    rows = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        for f in as_completed([pool.submit(one, j) for j in jobs]):
            rows.append(f.result())
            if len(rows) % 100 == 0:
                print(f"structure {split} {len(rows)}/{len(jobs)}", flush=True)
    rows.sort(key=lambda r: (r["model"], r["case_id"], r["format"]))
    (folder / f"{split}-results.jsonl").write_text("".join(canonical(r) + "\n" for r in rows))
    if split == "dev":
        selected = {}
        for m in MODELS:
            selected[m] = {}
            for s in STAGES:
                arms = {
                    arm: [
                        r
                        for r in rows
                        if r["model"] == m and r["stage"] == s and r["format"] == arm
                    ]
                    for arm in ARMS
                }
                allowed = [
                    arm
                    for arm in ARMS
                    if mean(arms[arm], "unsafe") <= mean(arms["flat_json"], "unsafe")
                ]
                selected[m][s] = min(
                    allowed,
                    key=lambda a: (
                        -mean(arms[a], "decision_correct"),
                        mean(arms[a], "total_tokens"),
                        a,
                    ),
                )
        result = {"models": selected, "dev_results_sha256": sha(folder / "dev-results.jsonl")}
        if (folder / "selection.json").exists():
            assert json.loads((folder / "selection.json").read_text()) == result
        else:
            write_json(folder / "selection.json", result)
        return result
    selected = json.loads((folder / "selection.json").read_text())["models"]
    models, comparisons = {}, []
    for m in MODELS:
        part = [r for r in rows if r["model"] == m]
        chosen = [
            {**r, "format": "selected"} for r in part if r["format"] == selected[m][r["stage"]]
        ]
        for arm in ["flat_json", "prior_policy"]:
            comparisons.append(
                {"model": m, "baseline": arm, **compare(part + chosen, "selected", arm)}
            )
        stages = {}
        for s in STAGES:
            byarm = {
                arm: summarize([r for r in part if r["stage"] == s and r["format"] == arm])
                for arm in ARMS
            }
            candidate = byarm[selected[m][s]]
            baseline = byarm["flat_json"]
            passed = (
                candidate["unsafe"] <= baseline["unsafe"]
                and candidate["decision_correct"] >= baseline["decision_correct"]
            )
            stages[s] = {
                "arms": byarm,
                "selected": selected[m][s],
                "passed": passed,
                "release": selected[m][s] if passed else "flat_json",
            }
        models[m] = {
            "stages": stages,
            "selected": summarize(chosen),
            "arms": {arm: summarize([r for r in part if r["format"] == arm]) for arm in ARMS},
        }
    result = {
        "rows": len(rows),
        "models": models,
        "comparisons": holm(comparisons),
        "selection_sha256": sha(folder / "selection.json"),
        "results_sha256": sha(folder / f"{split}-results.jsonl"),
    }
    write_json(folder / "summary.json", result)
    profile = {
        "version": "factorial-policy.v1",
        "status": "experimental_opt_in",
        "confirmation_sha256": sha(folder / "summary.json"),
        "models": {
            m: {
                s: {
                    k: v
                    for k, v in expression(p, m, s, models[m]["stages"][s]["release"]).items()
                    if k != "complete"
                }
                for s in STAGES
            }
            for m in MODELS
        },
    }
    write_json(root / "structure-policy.json", profile)
    return result
