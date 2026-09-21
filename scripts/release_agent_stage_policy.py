"""将已完成评测的开发策略写为内置候选路由；未过门禁的环节保留 JSON。"""

import argparse
import hashlib
import json
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--source", type=Path, required=True)
p.add_argument("--output", type=Path, default=Path("core/agentcontext/stage-policy.json"))
a = p.parse_args()
source = a.source.resolve()
selection = json.loads((source / "selection.json").read_text())
summary = json.loads((source / "summary.json").read_text())
if (
    hashlib.sha256((source / "test-results.jsonl").read_bytes()).hexdigest()
    != summary["test_results_sha256"]
):
    raise SystemExit("测试结果与摘要不匹配")
if summary["selection"]["stages"] != selection["stages"]:
    raise SystemExit("候选路由不等于锁定的开发选择")
selection_hash = hashlib.sha256((source / "selection.json").read_bytes()).hexdigest()
stages = {
    stage: chosen if summary["gates"][stage]["passed"] else "json"
    for stage, chosen in selection["stages"].items()
}
profile = {
    "version": "stage-policy.v1-" + selection_hash[:12],
    "model": selection["model"],
    "stages": stages,
    "development_candidates": selection["stages"],
    "gate_passed": {s: g["passed"] for s, g in summary["gates"].items()},
    "selection_sha256": selection_hash,
    "test_results_sha256": summary["test_results_sha256"],
    "fallback": "json",
    "scope": "输入表达；不授权物理动作；开启前核对端点与任务分布",
    "automatic_deployment": False,
}
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n")
(source / "release-policy.json").write_text(
    json.dumps(profile, ensure_ascii=False, indent=2) + "\n"
)
print(
    json.dumps(
        {"version": profile["version"], "released": stages, "gates": profile["gate_passed"]},
        ensure_ascii=False,
    )
)
