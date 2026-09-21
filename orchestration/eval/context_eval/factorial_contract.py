"""规划/恢复歧义消融：表达语法 × 显式决策契约，冻结后新种子确认。"""

from __future__ import annotations

import json
import random
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .client import Client
from .factorial_analysis import compare, holm, summarize
from .factorial_cases import COMMON, QUESTIONS, canonical, make_pair, score
from .factorial_runner import MODELS, load_cases, mean, sha, write_json

STAGES = ["planning", "recovery"]
ARMS = ["json", "nested_json", "contract_json", "contract_nested_json"]
SEEDS = [86028121, 86028157, 86028161]


def prepare(root):
    folder = root / "contract-study"
    if (folder / "protocol.json").exists():
        p = json.loads((folder / "protocol.json").read_text())
        assert sha(folder / "cases.jsonl") == p["cases_sha256"]
        assert sha(folder / "context-render") == p["renderer_sha256"]
        for rel, h in p["sources"].items():
            assert sha(folder / "source_snapshot" / rel) == h
            if rel.endswith(".py"):
                assert sha(Path(rel)) == h
        return p
    folder.mkdir(exist_ok=True)
    cases = [c for c in load_cases(root, "dev") if c["stage"] in STAGES] + [
        c
        for s in STAGES
        for f in [1, 2, 3, 4]
        for seed in SEEDS
        for c in make_pair(s, "contract_confirmation", f, seed)
    ]
    (folder / "cases.jsonl").write_text("".join(canonical(c) + "\n" for c in cases))
    subprocess.run(
        ["go", "build", "-o", str(folder / "context-render"), "./cmd/context-render"], check=True
    )
    files = [
        *Path("core/agentcontext").glob("*.go"),
        Path("cmd/context-render/main.go"),
        *[
            Path(__file__).with_name(n)
            for n in [
                "factorial_contract.py",
                "factorial_cases.py",
                "factorial_analysis.py",
                "factorial_runner.py",
                "client.py",
            ]
        ],
    ]
    hashes = {}
    for file in files:
        rel = str(file.resolve().relative_to(Path.cwd()))
        target = folder / "source_snapshot" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, target)
        hashes[rel] = sha(file)
    p = {
        "version": "contract-study.v1",
        "sources": hashes,
        "cases_sha256": sha(folder / "cases.jsonl"),
        "renderer_sha256": sha(folder / "context-render"),
        "seeds": SEEDS,
        "arms": ARMS,
        "stages": STAGES,
        "motivation": "post-test diagnosis of direct-vs-ancestor dependency and output-key/approval semantics ambiguity; this is instruction-contract enrichment, not pure syntax",
        "dev_cases": 20,
        "confirmation_cases": 48,
        "selection": "dev unsafe<=json then exact accuracy then total tokens; no retuning on confirmation",
        "gate": "confirmation accuracy>=json and unsafe<=json or baseline fallback; relative gate does not certify absolute competence",
        "primary": "explicit contract accuracy effect averaged over the two syntaxes; Holm across two models, family clusters (8 total)",
        "system_prompt": COMMON.replace(
            "输入采用 JSON Pointer 字段表或其受控自然语言等价表达",
            "输入采用完整嵌套 JSON、JSON Pointer 字段表或其受控自然语言等价表达",
        ),
    }
    write_json(folder / "protocol.json", p)
    (folder / "go.mod").write_text("module context-contract-archive\n\ngo 1.26\n")
    return p


def evaluate(root, split, config=None, replay=False):
    p = prepare(root)
    folder = root / "contract-study"
    cases = load_cases(folder, split)
    if split != "dev":
        assert (folder / "selection.json").exists()
    jobs = [(c, arm) for c in cases for arm in ARMS]
    inputs = "".join(
        canonical(
            {
                "decision": c["packet"],
                "factors": {"syntax": arm, "order": "source", "annotation": False},
                "round_trip": True,
            }
        )
        + "\n"
        for c, arm in jobs
    )
    proc = subprocess.run(
        [str(folder / "context-render")], input=inputs, text=True, capture_output=True, check=True
    )
    rendered = []
    for (c, arm), line in zip(jobs, proc.stdout.splitlines(), strict=True):
        out = json.loads(line)
        packet = out["decoded"]
        if arm.startswith("contract_"):
            contract = packet["goal"].pop("decision_contract")
            assert contract["stage"] == c["stage"] and c["packet"]["goal"][
                "current_step_id"
            ] not in canonical(contract)
        assert packet == c["packet"]
        rendered.append((c, arm, out["rendering"]))
    clients = {}
    for m in MODELS:
        cl = Client(root / "models" / m, config, enabled=False)
        assert (
            cl.url
            == json.loads((root / "models" / m / "model-config.json").read_text())["base_url"]
        )
        cl.model, cl.enabled = m, not replay
        assert replay or cl.key
        clients[m] = cl
    jobs = [(m, *r) for m in MODELS for r in rendered]
    random.Random(8602026).shuffle(jobs)

    def one(job):
        m, c, arm, r = job
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
            "stage": c["stage"],
            "family": c["family"],
            "format": arm,
            "answer": response["answer"],
            "request_hash": response["request_hash"],
            "prompt_hash": r["sha256"],
            "semantic_hash": r["semantic_sha256"],
            "total_tokens": (response.get("usage") or {}).get("total_tokens"),
            "latency_s": response["latency_s"],
            "protocol_error": response["protocol_error"],
            **score(c, {"complete": True}, response["answer"]),
        }

    rows = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        for f in as_completed([pool.submit(one, j) for j in jobs]):
            rows.append(f.result())
            if len(rows) % 100 == 0:
                print(f"contract {split} {len(rows)}/{len(jobs)}", flush=True)
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
                    arm for arm in ARMS if mean(arms[arm], "unsafe") <= mean(arms["json"], "unsafe")
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
    from .factorial_analysis import estimate

    selected = json.loads((folder / "selection.json").read_text())["models"]
    models = {}
    effects = []
    for m in MODELS:
        part = [r for r in rows if r["model"] == m]
        fam = {}
        for r in part:
            fam.setdefault(r["family"], []).append(
                (1 if r["format"].startswith("contract_") else -1) * r["decision_correct"]
            )
        effects.append({"model": m, **estimate([2 * sum(v) / len(v) for v in fam.values()])})
        stages = {}
        for s in STAGES:
            a = {
                arm: summarize([r for r in part if r["stage"] == s and r["format"] == arm])
                for arm in ARMS
            }
            c = a[selected[m][s]]
            b = a["json"]
            passed = c["decision_correct"] >= b["decision_correct"] and c["unsafe"] <= b["unsafe"]
            stages[s] = {
                "arms": a,
                "selected": selected[m][s],
                "passed": passed,
                "release": selected[m][s] if passed else "json",
            }
        chosen = [
            {**r, "format": "selected"} for r in part if r["format"] == selected[m][r["stage"]]
        ]
        models[m] = {
            "stages": stages,
            "selected": summarize(chosen),
            "comparison_exploratory": compare(part + chosen, "selected", "json"),
        }
    result = {
        "rows": len(rows),
        "models": models,
        "contract_effects": holm(effects),
        "results_sha256": sha(folder / f"{split}-results.jsonl"),
        "selection_sha256": sha(folder / "selection.json"),
    }
    write_json(folder / "summary.json", result)
    profile = json.loads((root / "structure-policy.json").read_text())
    profile["confirmation_sha256"] = sha(folder / "summary.json")
    for m in MODELS:
        for s in STAGES:
            profile["models"][m][s] = {
                "syntax": models[m]["stages"][s]["release"],
                "order": "source",
                "annotation": False,
            }
    write_json(root / "recommended-policy.json", profile)
    return result
