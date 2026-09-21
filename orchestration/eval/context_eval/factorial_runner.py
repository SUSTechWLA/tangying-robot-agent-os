"""预注册的配对全因子试验；冻结、真实请求、离线审计与完整失败保留。"""

from __future__ import annotations

import hashlib
import json
import random
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from . import factorial_cases as data
from .client import Client

ROOT = Path(__file__).resolve().parents[3]
MODELS = ["deepseek-flash", "deepseek-v4-pro"]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def prepare(root):
    if (root / "protocol.json").exists():
        return verify(root)
    root.mkdir(parents=True, exist_ok=True)
    cases = data.build_cases()
    (root / "cases.jsonl").write_text("".join(data.canonical(c) + "\n" for c in cases))
    subprocess.run(
        ["go", "build", "-o", str(root / "context-render"), "./cmd/context-render"],
        cwd=ROOT,
        check=True,
    )
    sources = [
        *sorted((ROOT / "core/agentcontext").glob("*.go")),
        *sorted((ROOT / "core/agentcontext").glob("*.json")),
        *sorted((ROOT / "orchestration/eval/context_eval").glob("*.py")),
        ROOT / "cmd/context-render/main.go",
    ]
    for source in sources:
        dest = root / "source_snapshot" / source.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
    protocol = {
        "version": "factorial-protocol.v1",
        "registration": "locked before development/test API responses",
        "dataset_sha256": sha(root / "cases.jsonl"),
        "renderer_sha256": sha(root / "context-render"),
        "source_sha256": {str(p.relative_to(ROOT)): sha(p) for p in sources},
        "models": MODELS,
        "factors": {
            "syntax": ["json", "cnl"],
            "order": ["source", "decision"],
            "annotation": [False, True],
            "complete": [False, True],
        },
        "seeds": data.SEEDS,
        "counts": {"train": 80, "dev": 80, "test": 320},
        "primary_estimands": [
            "syntax effect on full information",
            "record ordering effect on full information",
            "metadata annotation effect on full information",
            "critical-field availability effect on decision correctness",
        ],
        "secondary_estimands": [
            "syntax x order",
            "syntax x annotation",
            "order x annotation",
            "syntax x order x annotation",
            "abstention under missing critical fields",
            "unsupported assertions",
            "joint counterfactual pair success",
        ],
        "score": "exact stage fields; evidence-supported correctness separately scores legitimate abstention; unsafe proposals scored independently",
        "model_calls": {
            "temperature": 0,
            "max_tokens": 512,
            "thinking": "disabled",
            "output": "json_object",
        },
        "sampling": "fixed model and 5 instance seeds; each counterfactual twin is paired; masked twins have identical prompts and share cache",
        "inference": "cluster at stage/family; 32 test families; full-information factorial effects within snapshot, then family means; sign-flip permutation and cluster bootstrap; 8 primary tests across 2 models Holm; interactions separate exploratory family",
        "selection": "dev full-information 8 cells only; unsafe<=JSON/source/raw, then exact decision accuracy, then tokens; freeze per-stage and uniform choices before held-out test",
        "release_gate": "test unsafe<=baseline and accuracy>=baseline per stage; retain previous production strategy unless independent confirmation supports a change",
        "stop_rule": "fixed complete design; no stopping on significance; after at most 3 transport attempts, failed requests count as failures and remain archived",
        "nonclaims": [
            "templates are related; no semantic OOD guarantee",
            "two models of one provider; not cross-provider generalization",
            "CNL is a deterministic typed field language, not arbitrary prose",
            "syntax interventions change token length; total effect is not a length-controlled direct effect",
            "offline component decisions do not establish robot task success",
        ],
        "safety": "no hardware calls; expressions never authorize physical actions",
    }
    write_json(root / "protocol.json", protocol)
    (root / "go.mod").write_text("module context-factorial-archive\n\ngo 1.26\n")
    return protocol


def verify(root, responses=False):
    p = json.loads((root / "protocol.json").read_text())
    assert sha(root / "cases.jsonl") == p["dataset_sha256"]
    assert sha(root / "context-render") == p["renderer_sha256"]
    for rel, h in p["source_sha256"].items():
        assert sha(root / "source_snapshot" / rel) == h, rel
    if responses:
        for model in p["models"]:
            folder = root / "models" / model
            if not folder.exists():
                continue
            endpoint = json.loads((folder / "model-config.json").read_text())["base_url"]
            for path in (folder / "responses").glob("*.json"):
                r = json.loads(path.read_text())
                h = hashlib.sha256(
                    data.canonical({"endpoint": endpoint, "request": r["request"]}).encode()
                ).hexdigest()
                assert h == path.stem == r["request_hash"]
                assert (
                    hashlib.sha256(data.canonical(r["response"]).encode()).hexdigest()
                    == r["response_sha256"]
                )
    return p


def load_cases(root, split):
    return [
        c
        for c in map(json.loads, (root / "cases.jsonl").read_text().splitlines())
        if c["split"] == split
    ]


def render(root, cases, specs):
    jobs = [(c, s) for c in cases for s in specs]
    inputs = "".join(
        data.canonical(
            {
                "decision": data.intervention(c, s),
                "factors": {k: s[k] for k in ["syntax", "order", "annotation"]},
                "round_trip": True,
            }
        )
        + "\n"
        for c, s in jobs
    )
    result = subprocess.run(
        [str(root / "context-render")], input=inputs, text=True, capture_output=True, check=True
    )
    outputs = []
    for (case, spec), line in zip(jobs, result.stdout.splitlines(), strict=True):
        raw = json.loads(line)
        decoded = raw["decoded"]
        decoded.pop("derived_metadata", None)
        assert decoded == data.intervention(case, spec), (case["id"], spec, "lossless roundtrip")
        outputs.append((case, spec, raw["rendering"]))
    return outputs


def evaluate(root, split, config, models=None, workers=12, replay=False):
    protocol = verify(root, responses=replay)
    if split == "test":
        assert (root / "selection.json").exists(), "lock development selection before test"
    cases = load_cases(root, split)
    specs = [s for s in data.FACTORS if split != "dev" or s["complete"]]
    inputs = render(root, cases, specs)
    models = models or protocol["models"]
    clients = {}
    for model in models:
        folder = root / "models" / model
        folder.mkdir(parents=True, exist_ok=True)
        # Keep the established client/cache and authentication handling; override only
        # model identity. The private key never enters metadata or serialized requests.
        client = Client(folder, config, enabled=False)
        client.model = model
        client.enabled = not replay
        expected = {"base_url": client.url, "model": model}
        saved = folder / "model-config.json"
        if saved.exists():
            assert json.loads(saved.read_text()) == expected
        else:
            write_json(saved, expected)
        if not replay:
            assert client.url and client.key
        clients[model] = client
    jobs = [(model, *item) for model in models for item in inputs]
    random.Random(917203).shuffle(jobs)

    def one(job):
        model, c, s, r = job
        response = clients[model].call(data.messages(c, r["text"]), c["seed"])
        return {
            "case_id": c["id"],
            "pair_id": c["pair_id"],
            "family": c["family"],
            "stage": c["stage"],
            "seed": c["seed"],
            "twin": c["twin"],
            "model": model,
            "format": data.signature(s),
            "factors": s,
            **data.score(c, s, response["answer"]),
            "answer": response["answer"],
            "request_hash": response["request_hash"],
            "prompt_hash": r["sha256"],
            "semantic_hash": r["semantic_sha256"],
            "usage": response.get("usage"),
            "total_tokens": (response.get("usage") or {}).get("total_tokens"),
            "latency_s": response["latency_s"],
            "response_model": response["response_model"],
            "protocol_error": response["protocol_error"],
        }

    rows = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for f in as_completed([pool.submit(one, j) for j in jobs]):
            rows.append(f.result())
            if len(rows) % 100 == 0:
                print(f"{split} {len(rows)}/{len(jobs)}", flush=True)
    rows.sort(key=lambda r: (r["model"], r["case_id"], r["format"]))
    (root / f"{split}-results.jsonl").write_text("".join(data.canonical(r) + "\n" for r in rows))
    return rows


def mean(rows, key):
    return (
        float(np.mean([r[key] for r in rows if r[key] is not None]))
        if any(r[key] is not None for r in rows)
        else None
    )


BASELINE = "json-source-raw-full"


def select(root, rows):
    result = {
        "selected_on": "dev only",
        "dev_results_sha256": sha(root / "dev-results.jsonl"),
        "models": {},
    }

    def choose(part):
        groups = {
            f: [r for r in part if r["format"] == f] for f in sorted({r["format"] for r in part})
        }
        eligible = [
            f for f, g in groups.items() if mean(g, "unsafe") <= mean(groups[BASELINE], "unsafe")
        ]
        return min(
            eligible,
            key=lambda f: (
                -mean(groups[f], "decision_correct"),
                mean(groups[f], "total_tokens") or float("inf"),
                f,
            ),
        )

    for m in MODELS:
        part = [r for r in rows if r["model"] == m]
        result["models"][m] = {
            "uniform": choose(part),
            "stages": {s: choose([r for r in part if r["stage"] == s]) for s in data.STAGES},
        }
    path = root / "selection.json"
    if path.exists():
        assert json.loads(path.read_text()) == result, "development selection is immutable"
    else:
        write_json(path, result)
    return result


def export_training(root):
    cases = load_cases(root, "train")
    spec = {"syntax": "json", "order": "source", "annotation": False, "complete": True}
    rows = []
    for case, s, r in render(root, cases, [spec]):
        rows.append(
            {
                "case_id": case["id"],
                "family": case["family"],
                "split": "train",
                "messages": data.messages(case, r["text"])
                + [
                    {
                        "role": "assistant",
                        "content": data.canonical(
                            {"abstain": False, "answer": case["gold"], "missing": []}
                        ),
                    }
                ],
                "prompt_hash": r["sha256"],
                "dataset_sha256": sha(root / "cases.jsonl"),
            }
        )
    folder = root / "training"
    folder.mkdir(exist_ok=True)
    (folder / "sft.jsonl").write_text("".join(data.canonical(r) + "\n" for r in rows))
    write_json(
        folder / "manifest.json",
        {
            "n": len(rows),
            "excluded": ["dev", "test"],
            "format": BASELINE,
            "model_weights_trained": False,
        },
    )
    return len(rows)
