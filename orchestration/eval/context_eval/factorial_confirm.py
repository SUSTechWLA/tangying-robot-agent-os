"""在读入任何确认结果前锁定新种子和策略；不重新搜索表达。"""

from __future__ import annotations

import json
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .client import Client
from .factorial_analysis import compare, holm, summarize
from .factorial_cases import FACTORS, STAGES, canonical, make_pair, messages, score, signature
from .factorial_runner import BASELINE, MODELS, mean, render, sha, verify, write_json

CONFIRM_SEEDS = [49979687, 49979693, 49979701]


def source_hashes():
    return {
        p.name: sha(p)
        for p in Path(__file__).parent.glob("*.py")
        if p.name
        in {
            "factorial_confirm.py",
            "factorial_cases.py",
            "factorial_analysis.py",
            "factorial_runner.py",
            "client.py",
        }
    }


def prepare(root):
    verify(root)
    folder = root / "confirmation"
    if (folder / "protocol.json").exists():
        return validate(root)
    folder.mkdir(exist_ok=True)
    selected = json.loads((root / "selection.json").read_text())
    test = json.loads((root / "summary.json").read_text())
    cases = [
        c
        for stage in STAGES
        for family in [1, 2, 3, 4]
        for seed in CONFIRM_SEEDS
        for c in make_pair(stage, "confirmation", family, seed)
    ]
    assert len(cases) == 192
    (folder / "cases.jsonl").write_text("".join(canonical(c) + "\n" for c in cases))
    stages = {
        m: {
            s: selected["models"][m]["stages"][s]
            if test["models"][m]["gates"][s]["passed"]
            else BASELINE
            for s in STAGES
        }
        for m in MODELS
    }
    protocol = {
        "version": "factorial-confirmation.v1",
        "cases": len(cases),
        "seeds": CONFIRM_SEEDS,
        "cases_sha256": sha(folder / "cases.jsonl"),
        "selection_sha256": sha(root / "selection.json"),
        "test_summary_sha256": sha(root / "summary.json"),
        "source_sha256": source_hashes(),
        "policy": stages,
        "uniform": {m: selected["models"][m]["uniform"] for m in MODELS},
        "comparisons": ["test-gated locked policy vs baseline", "locked policy vs dev uniform"],
        "inference": "paired family contrasts, Holm across four policy comparisons; new instances of existing families, not new semantic families",
        "gate": "each stage accuracy>=baseline and unsafe<=baseline; otherwise baseline fallback; no further tuning",
    }
    write_json(folder / "protocol.json", protocol)
    return protocol


def validate(root):
    f = root / "confirmation"
    p = json.loads((f / "protocol.json").read_text())
    assert sha(f / "cases.jsonl") == p["cases_sha256"]
    assert sha(root / "selection.json") == p["selection_sha256"]
    assert sha(root / "summary.json") == p["test_summary_sha256"]
    assert source_hashes() == p["source_sha256"]
    return p


def evaluate(root, config=None, replay=False):
    p = prepare(root)
    folder = root / "confirmation"
    cases = list(map(json.loads, (folder / "cases.jsonl").read_text().splitlines()))
    specs = [s for s in FACTORS if s["complete"]]
    rendered = {(c["id"], signature(s)): r for c, s, r in render(root, cases, specs)}
    by_spec = {signature(s): s for s in specs}
    clients = {}
    for model in MODELS:
        clients[model] = Client(root / "models" / model, config, enabled=False)
        saved = json.loads((root / "models" / model / "model-config.json").read_text())
        assert clients[model].url == saved["base_url"]
        clients[model].model = model
        clients[model].enabled = not replay
        assert replay or clients[model].key
    jobs = [(m, c, arm) for m in MODELS for c in cases for arm in ["policy", "baseline", "uniform"]]
    random.Random(4982026).shuffle(jobs)

    def one(job):
        m, c, arm = job
        fmt = (
            p["policy"][m][c["stage"]]
            if arm == "policy"
            else p["uniform"][m]
            if arm == "uniform"
            else BASELINE
        )
        r = rendered[c["id"], fmt]
        response = clients[m].call(messages(c, r["text"]), c["seed"])
        return {
            "case_id": c["id"],
            "pair_id": c["pair_id"],
            "family": c["family"],
            "stage": c["stage"],
            "seed": c["seed"],
            "model": m,
            "format": arm,
            "expression": fmt,
            "request_hash": response["request_hash"],
            "answer": response["answer"],
            "protocol_error": response["protocol_error"],
            "total_tokens": (response.get("usage") or {}).get("total_tokens"),
            "latency_s": response["latency_s"],
            **score(c, by_spec[fmt], response["answer"]),
        }

    rows = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        for f in as_completed([pool.submit(one, j) for j in jobs]):
            rows.append(f.result())
            if len(rows) % 100 == 0:
                print(f"confirmation {len(rows)}/{len(jobs)}", flush=True)
    rows.sort(key=lambda r: (r["model"], r["case_id"], r["format"]))
    (folder / "results.jsonl").write_text("".join(canonical(r) + "\n" for r in rows))
    comparisons, models = [], {}
    for m in MODELS:
        part = [r for r in rows if r["model"] == m]
        for arm in ["baseline", "uniform"]:
            comparisons.append({"model": m, "baseline": arm, **compare(part, "policy", arm)})
        stages = {}
        for s in STAGES:
            b = [r for r in part if r["stage"] == s and r["format"] == "baseline"]
            c = [r for r in part if r["stage"] == s and r["format"] == "policy"]
            passed = mean(c, "unsafe") <= mean(b, "unsafe") and mean(c, "decision_correct") >= mean(
                b, "decision_correct"
            )
            stages[s] = {
                "passed": passed,
                "candidate": summarize(c),
                "baseline": summarize(b),
                "locked": p["policy"][m][s],
                "release": p["policy"][m][s] if passed else BASELINE,
            }
        models[m] = {
            "arms": {
                arm: summarize([r for r in part if r["format"] == arm])
                for arm in ["policy", "baseline", "uniform"]
            },
            "stages": stages,
        }
    summary = {
        "rows": len(rows),
        "models": models,
        "comparisons": holm(comparisons),
        "protocol_sha256": sha(folder / "protocol.json"),
        "results_sha256": sha(folder / "results.jsonl"),
    }
    write_json(folder / "summary.json", summary)
    profile = {
        "version": "factorial-policy.v1",
        "status": "experimental_opt_in",
        "confirmation_sha256": sha(folder / "summary.json"),
        "models": {
            m: {
                s: {
                    k: by_spec[models[m]["stages"][s]["release"]][k]
                    for k in ["syntax", "order", "annotation"]
                }
                for s in STAGES
            }
            for m in MODELS
        },
    }
    write_json(root / "factorial-policy.json", profile)
    return summary
