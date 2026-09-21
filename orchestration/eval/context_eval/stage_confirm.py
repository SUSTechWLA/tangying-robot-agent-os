"""固定发布策略在新种子上的确认；不据结果继续选择，任务族不变。"""

from __future__ import annotations

import hashlib
import json
import random
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from .archive import frozen_module, verify_archive
from .client import Client
from .dataset import canonical
from .runner import paired

SEEDS = [99991, 999983, 1000033]


def run(source, output, config=None, replay=False):
    verify_archive(source)
    generator = frozen_module(source, "stage_cases")
    renderer = frozen_module(source, "stage_runner")
    profile = json.loads((source / "release-policy.json").read_text())
    selected = json.loads((source / "selection.json").read_text())
    cases = [
        generator.make_case(stage, "confirmation", variant, seed)
        for stage in generator.STAGES
        for variant in range(4, 12)
        for seed in SEEDS
    ]
    output.mkdir(parents=True, exist_ok=True)
    protocol = {
        "version": "stage-confirm.v1",
        "new_seeds": SEEDS,
        "same_test_families": True,
        "no_selection_from_confirmation": True,
        "release_sha256": hashlib.sha256((source / "release-policy.json").read_bytes()).hexdigest(),
        "renderer_sha256": hashlib.sha256((source / "context-render").read_bytes()).hexdigest(),
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "cases_sha256": hashlib.sha256(
            ("".join(canonical(c) + "\n" for c in cases)).encode()
        ).hexdigest(),
        "policies": ["json", "best_uniform", "released"],
        "uniform": selected["uniform"],
        "stages": profile["stages"],
    }
    target = output / "protocol.json"
    if target.exists() and json.loads(target.read_text()) != protocol:
        raise ValueError("确认协议已冻结，不能修改")
    if not target.exists():
        target.write_text(json.dumps(protocol, ensure_ascii=False, indent=2) + "\n")
        shutil.copy2(__file__, output / "source.py")
        shutil.copy2(source / "model-config.json", output / "model-config.json")
        (output / "cases.jsonl").write_text("".join(canonical(c) + "\n" for c in cases))
    client = Client(output, config, enabled=not replay)
    rendered = {
        (c["id"], style): r for c, style, r in renderer.render_cases(source, cases, renderer.STYLES)
    }
    items = [
        (
            c,
            policy,
            "json"
            if policy == "json"
            else selected["uniform"]
            if policy == "best_uniform"
            else profile["stages"][c["stage"]],
        )
        for c in cases
        for policy in protocol["policies"]
    ]
    random.Random(456789).shuffle(items)

    def one(item):
        c, policy, style = item
        r = rendered[(c["id"], style)]
        response = client.call(generator.messages(c, r["content"]), c["seed"])
        return {
            "case_id": c["id"],
            "family": c["family"],
            "seed": c["seed"],
            "stage": c["stage"],
            "format": policy,
            "actual_format": style,
            **generator.score(c, response["answer"]),
            "answer": response["answer"],
            "request_hash": response["request_hash"],
            "prompt_hash": r["sha256"],
            "total_tokens": (response.get("usage") or {}).get("total_tokens"),
            "protocol_error": response["protocol_error"],
        }

    rows = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for future in as_completed([pool.submit(one, item) for item in items]):
            rows.append(future.result())
            if len(rows) % 40 == 0:
                print(f"确认 {len(rows)}/{len(items)}", flush=True)
    rows.sort(key=lambda r: (r["case_id"], r["format"]))
    (output / "results.jsonl").write_text("".join(canonical(r) + "\n" for r in rows))
    stats = {}
    for policy in protocol["policies"]:
        part = [r for r in rows if r["format"] == policy]
        stats[policy] = {
            m: float(np.mean([r[m] for r in part if r[m] is not None]))
            for m in ["ability", "schema_ok", "unsafe", "total_tokens"]
        }
    comparisons = renderer.holm(
        [paired(rows, "released", other) for other in ["json", "best_uniform"]]
    )
    result = {
        "statistics": stats,
        "comparisons": comparisons,
        "rows": len(rows),
        "cases": len(cases),
        "stages": {
            s: {
                p: float(
                    np.mean([r["ability"] for r in rows if r["stage"] == s and r["format"] == p])
                )
                for p in protocol["policies"]
            }
            for s in generator.STAGES
        },
        "limitation": "同任务族的新种子稳定性复测，不是新的语义任务族泛化",
    }
    (output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result
