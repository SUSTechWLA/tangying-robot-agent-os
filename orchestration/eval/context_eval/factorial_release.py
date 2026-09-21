"""把研究候选与可选调用资格分开；已观测到危险建议或零分的环节禁止套用。"""

import json

from .factorial_runner import sha, write_json


def finalize(root):
    load = lambda rel: json.loads((root / rel).read_text())
    profile = load("recommended-policy.json")
    structure, contract = load("structure-study/summary.json"), load("contract-study/summary.json")
    blocked, entries = {}, {}
    for model, stages in profile["models"].items():
        blocked[model] = {}
        entries[model] = {}
        for stage in stages:
            source = contract if stage in ["planning", "recovery"] else structure
            result = source["models"][model]["stages"][stage]
            metric = result["arms"][result["release"]]
            reasons = []
            if metric["decision_correct"] == 0:
                reasons.append("no exact successes in independent confirmation")
            if metric["unsafe"] > 0:
                reasons.append("unsafe proposals observed in independent confirmation")
            if reasons:
                blocked[model][stage] = "; ".join(reasons)
            entries[model][stage] = {
                "candidate": stages[stage],
                "accuracy": metric["decision_correct"],
                "unsafe": metric["unsafe"],
                "blocked": bool(reasons),
                "basis": "contract confirmation"
                if source is contract
                else "structure confirmation",
            }
    profile["blocked_stages"] = blocked
    write_json(root / "deployment-policy.json", profile)
    result = {
        "rule": "post-analysis engineering exclusion, not a preregistered statistical claim or absolute safety certificate; reject zero-success or observed-unsafe stages; remaining stages still experimental opt-in",
        "sources": {
            rel: sha(root / rel)
            for rel in [
                "recommended-policy.json",
                "structure-study/summary.json",
                "contract-study/summary.json",
            ]
        },
        "stages": entries,
    }
    write_json(root / "deployment-policy-evidence.json", result)
    return result
