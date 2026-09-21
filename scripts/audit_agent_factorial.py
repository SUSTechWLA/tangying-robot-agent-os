"""离线审计真实请求、归档响应、当前生产渲染一致性及既有仿真证据的字段覆盖。"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchestration.eval.context_eval.factorial_cases import FACTORS, canonical
from orchestration.eval.context_eval.factorial_runner import (
    load_cases,
    render,
    sha,
    verify,
    write_json,
)

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--source", type=Path, required=True)
a = p.parse_args()
root = a.source.resolve()
protocol = verify(root, responses=True)
folder = root / "audit"
folder.mkdir(exist_ok=True)
binary = folder / "context-render"
subprocess.run(["go", "build", "-o", str(binary), "./cmd/context-render"], check=True)
original = render(root, load_cases(root, "test"), FACTORS)
current = render(folder, load_cases(root, "test"), FACTORS)
assert len(original) == len(current) == 5120
for (c, s, one), (_, _, two) in zip(original, current, strict=True):
    assert one["text"] == two["text"] and one["semantic_sha256"] == two["semantic_sha256"], (
        c["id"],
        s,
    )

# Count only requests referenced by this study, not old invalid cases in the shared cache.
rows = []
for rel in [
    "dev-results.jsonl",
    "test-results.jsonl",
    "confirmation/results.jsonl",
    "structure-study/dev-results.jsonl",
    "structure-study/structure_confirmation-results.jsonl",
    "contract-study/dev-results.jsonl",
    "contract-study/contract_confirmation-results.jsonl",
]:
    if (root / rel).exists():
        rows.extend(map(json.loads, (root / rel).read_text().splitlines()))
requests = {(r["model"], r["request_hash"]) for r in rows}
by_request = {}
for row in rows:
    by_request.setdefault((row["model"], row["request_hash"]), []).append(row)
tokens = 0
failures, transport_retries, identities = 0, 0, set()
for model, h in sorted(requests):
    saved = json.loads((root / "models" / model / "responses" / (h + ".json")).read_text())
    raw = saved["response"]
    parsed = None
    if raw:
        try:
            parsed = json.loads(raw["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, ValueError):
            pass
    assert parsed == saved["answer"], h
    for r in by_request[model, h]:
        assert r["answer"] == parsed, h
    tokens += (saved.get("usage") or {}).get("total_tokens", 0)
    failures += bool(saved["protocol_error"])
    transport_retries += bool(saved.get("transport_errors"))
    identities.add((model, saved.get("response_model")))

source = Path("artifacts/grounded-verification/validated-run/trials.jsonl")
trials = list(map(json.loads, source.read_text().splitlines()))
packets, coverage = [], {}
fields = [
    "report_id",
    "episode_id",
    "edge_boot_id",
    "edge_monotonic_ts_ns",
    "evidence_refs",
    "robot_id",
    "plan_revision",
    "attempt_id",
    "observed_ms",
    "valid_until_ms",
    "arrival_ts",
    "verifier_version",
]
for field in fields:
    coverage[field] = sum(t["original_report"].get(field) not in [None, "", []] for t in trials)
for i, t in enumerate(trials):
    report = t["original_report"]
    scope = {
        "task_id": report.get("task_id"),
        "robot_id": report.get("robot_id"),
        "plan_revision": report.get("plan_revision"),
        "episode_id": report.get("episode_id"),
    }
    packet = {
        "schema_version": "decision-context.v1",
        "stage": "verification",
        "scope": scope,
        "snapshot": {
            "as_of_ms": None,
            "clock_domain": "unknown",
            "ledger_sequence": None,
            "history_complete": False,
            "omitted_events": 0,
        },
        "goal": {"request": "只读归档报告的无损载荷审计"},
        "steps": [],
        "attempts": [],
        "tools": [],
        "constraints": ["历史仿真报告不能被视为当前物理状态。"],
        "missing": ["/snapshot/as_of_ms"],
        "records": [
            {
                "id": report.get("report_id", f"archive-{i}"),
                "kind": "verification",
                "scope": scope,
                "step_id": None,
                "attempt_id": report.get("attempt_id"),
                "source": "archived_gazebo_report",
                "source_version": report.get("verifier_version"),
                "observed_ms": report.get("observed_ms"),
                "valid_until_ms": report.get("valid_until_ms"),
                "clock_domain": "unknown",
                "payload": report,
                "evidence_ids": [e["uri"] for e in report.get("evidence_refs", [])],
                "supersedes": [],
            }
        ],
    }
    packets.append({"id": f"archive-{i}", "packet": packet})
full_specs = [s for s in FACTORS if s["complete"]] + [
    {"syntax": syntax, "order": "source", "annotation": False, "complete": True}
    for syntax in ["nested_json", "entity_cnl"]
]
views = render(folder, packets, full_specs)
assert len(views) == 2100
structure_root = root / "structure-study"
structure_cases = load_cases(structure_root, "dev") + load_cases(
    structure_root, "structure_confirmation"
)
structure_original = render(structure_root, structure_cases, full_specs)
structure_current = render(folder, structure_cases, full_specs)
for (_, _, before), (_, _, after) in zip(structure_original, structure_current, strict=True):
    assert before["text"] == after["text"] and before["semantic_sha256"] == after["semantic_sha256"]
contract_root = root / "contract-study"
contract_cases = load_cases(contract_root, "dev") + load_cases(
    contract_root, "contract_confirmation"
)
contract_jobs = [
    (c, syntax)
    for c in contract_cases
    for syntax in ["json", "nested_json", "contract_json", "contract_nested_json"]
]
contract_inputs = "".join(
    canonical(
        {
            "decision": c["packet"],
            "factors": {"syntax": s, "order": "source", "annotation": False},
            "round_trip": True,
        }
    )
    + "\n"
    for c, s in contract_jobs
)
contract_outputs = []
for executable in [contract_root / "context-render", binary]:
    proc = subprocess.run(
        [str(executable)], input=contract_inputs, text=True, capture_output=True, check=True
    )
    contract_outputs.append(list(map(json.loads, proc.stdout.splitlines())))
for (case, syntax), frozen, live in zip(contract_jobs, *contract_outputs, strict=True):
    assert frozen["rendering"]["text"] == live["rendering"]["text"]
    assert frozen["rendering"]["semantic_sha256"] == live["rendering"]["semantic_sha256"]
    packet = live["decoded"]
    if syntax.startswith("contract_"):
        packet["goal"].pop("decision_contract")
    assert packet == case["packet"]
result = {
    "version": "factorial-audit.v1",
    "production_frozen_text_equalities": len(original),
    "structure_frozen_text_equalities": len(structure_original),
    "contract_frozen_text_equalities": len(contract_jobs),
    "scoring_rows": len(rows),
    "distinct_referenced_requests": len(requests),
    "total_tokens_distinct_requests": tokens,
    "protocol_failures_distinct": failures,
    "requests_with_transport_retries": transport_retries,
    "model_identities": sorted(identities, key=str),
    "recorded_simulation": {
        "source": str(source),
        "sha256": sha(source),
        "trials": len(trials),
        "lossless_payload_roundtrips": len(views),
        "nonempty_top_level_field_counts": coverage,
        "interpretation": "旧 Gazebo 证据载荷迁移审计，不是新物理试验；嵌套 action_params.robot 不等于经认证的 robot_id；单调时钟不能伪造 UTC",
    },
    "current_renderer_sha256": sha(binary),
    "frozen_renderer_sha256": protocol["renderer_sha256"],
}
write_json(folder / "summary.json", result)
print(json.dumps(result, ensure_ascii=False, indent=2))
