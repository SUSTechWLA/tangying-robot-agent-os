"""Read-only adapters: preserve the scope of archived evidence; never run a robot."""

from __future__ import annotations

import json
from pathlib import Path

from .engine import catalog, digest, file_hash, validate, value


def jsonl(path):
    with Path(path).open() as stream:
        return [json.loads(line) for line in stream if line.strip()]


def source(path):
    return {"path": str(Path(path).resolve()), "sha256": file_hash(path)}


def dimensions(**overrides):
    return {**dict.fromkeys(catalog()["slice_axes"], "none"), **overrides}


def _bool(row, key):
    result = row[key]
    if type(result) is not bool:
        raise ValueError(f"archive {key} must be a boolean")
    return int(result)


def import_context(root, model, representation):
    root = Path(root)
    protocol_path, cases_path, results_path = [
        root / p for p in ["protocol.json", "cases.jsonl", "test-results.jsonl"]
    ]
    protocol = json.loads(protocol_path.read_text())
    if file_hash(cases_path) != protocol["dataset_sha256"]:
        raise ValueError("context dataset differs from its registered hash")
    cases = [r for r in jsonl(cases_path) if r["split"] == "test"]
    expected = {r["id"]: r for r in cases}
    if len(expected) != len(cases) or len(cases) != protocol["counts"]["test"]:
        raise ValueError("duplicate or missing registered test cases")
    selected = [
        r for r in jsonl(results_path) if r["model"] == model and r["format"] == representation
    ]
    ids = [r["case_id"] for r in selected]
    if len(ids) != len(set(ids)) or set(ids) != set(expected):
        raise ValueError("archive must contain exactly one result for every planned test case")
    units = []
    for row in sorted(selected, key=lambda r: r["case_id"]):
        case = expected[row["case_id"]]
        if (row["family"], row["stage"]) != (case["family"], case["stage"]):
            raise ValueError("context result identity disagrees with registered case")
        failed = row["protocol_error"] is not None
        metrics = {
            dst: _bool(row, src)
            for dst, src in {
                "decision_exact": "decision_correct",
                "supported_correct": "supported_correct",
                "schema_valid": "schema_ok",
                "unsafe_proposal": "unsafe",
                "unsupported_assertion": "unsupported_assertion",
            }.items()
        }
        metrics.update(latency_ms=row["latency_s"] * 1000, tokens=row["total_tokens"])
        units.append(
            {
                "unit_id": row["case_id"],
                "case_id": row["case_id"],
                "family_id": row["family"],
                "cluster_id": row["family"],
                "repeat_id": 0,
                "draw_id": row["request_hash"],
                "fresh_draw": False,
                "execution_status": "error" if failed else "completed",
                "dimensions": dimensions(
                    stage=row["stage"],
                    module="agentcontext." + row["stage"],
                    fault="synthetic_context",
                    backend="llm_archive",
                    tier="decision",
                    truth="synthetic_oracle",
                    risk="proposal_only",
                ),
                "measurements": metrics,
                "not_applicable": [],
                "evidence_refs": [
                    f"{results_path.resolve()}#case={row['case_id']}&model={model}&format={representation}",
                    "request-sha256:" + row["request_hash"],
                    "prompt-sha256:" + row["prompt_hash"],
                ],
                "failure": {"type": "protocol_error", "detail": row["protocol_error"]}
                if failed
                else None,
            }
        )
    return validate(
        {
            "schema_version": "system-eval-run.v1",
            "run_id": f"context/{model}/{representation}",
            "benchmark": {
                "id": "context-factorial-test",
                "version": protocol["version"],
                "dataset_sha256": protocol["dataset_sha256"],
                "grader_sha256": digest(
                    {
                        "archive_score_versions": sorted({r["score_version"] for r in selected}),
                        "registered_case_source": protocol["source_sha256"][
                            "orchestration/eval/context_eval/factorial_cases.py"
                        ],
                        "adapter": file_hash(__file__),
                    }
                ),
                "split": "historical",
                "primary_metric": "decision_exact",
                "risk_metric": "unsafe_proposal",
            },
            "components": {
                "model": model,
                "representation": representation,
                "renderer": protocol["renderer_sha256"],
            },
            "controls": {
                "environment_id": "factorial-v1/run-v3-archived-interface",
                "budget": protocol["model_calls"],
                "semantic_contract_id": "factorial-score.v1",
                "source_mode": "archive_replay",
            },
            "provenance": {
                "sources": [source(p) for p in [protocol_path, cases_path, results_path]],
                "claim_scope": "历史上下文单轮决策；本次未重新调用模型，不测量 Agent 模块执行或机器人任务成功。",
                "original_split": "test",
                "analysis_registration": "retrospective",
                "adapter_sha256": file_hash(__file__),
                "limitations": [
                    "导入归档分数，不重新验证原始 API 响应或原评分器；来源哈希用于追踪。",
                    "按 stage/family 聚类；同族反事实与种子不当作独立样本。",
                    "复用已有 token/latency 记录，不计作本次调用成本，不提供独立重复可靠性结论。",
                ],
            },
            "planned_units": [u["unit_id"] for u in units],
            "units": units,
        }
    )


def import_gvf(path):
    path = Path(path)
    rows, units = jsonl(path), []
    for index, row in enumerate(rows, start=1):
        report = row["original_report"]
        truth, verdict = row["truth"], report["verdict"]
        if truth not in {"VERIFIED", "FALSIFIED", "UNKNOWN"} or verdict not in {
            "VERIFIED",
            "FALSIFIED",
            "UNKNOWN",
        }:
            raise ValueError("unknown GVF truth/verdict")
        unit_id = f"{row['seed']}/{row['task_id']}/{row['step']}/{row['kind']}/{row['fault']}"
        refs = [r["uri"] for r in report["evidence_refs"]]
        units.append(
            {
                "unit_id": unit_id,
                "case_id": unit_id,
                "family_id": row["kind"] + "/" + row["fault"],
                "cluster_id": f"seed/{row['seed']}",
                "repeat_id": 0,
                "draw_id": None,
                "fresh_draw": False,
                "execution_status": "completed",
                "dimensions": dimensions(
                    stage="verification",
                    module="grounded.verifier",
                    tool=row["kind"],
                    tool_family=catalog()["canonical_tool_families"].get(row["kind"], "unknown"),
                    fault=row["fault"],
                    backend="gazebo_archive",
                    tier="component",
                    truth=truth,
                    risk="false_verification",
                ),
                "measurements": {
                    "verification_correct": int(verdict == truth),
                    "false_verified": int(verdict == "VERIFIED") if truth != "VERIFIED" else None,
                    "unknown_preserved": int(verdict == "UNKNOWN") if truth == "UNKNOWN" else None,
                    "evidence_refs_present": int(bool(refs)),
                    "action_duration_ms": row["action_s"] * 1000,
                },
                "not_applicable": (["false_verified"] if truth == "VERIFIED" else [])
                + (["unknown_preserved"] if truth != "UNKNOWN" else []),
                "evidence_refs": [f"{path.resolve()}#line={index}", *refs],
                "failure": None,
            }
        )
    return validate(
        {
            "schema_version": "system-eval-run.v1",
            "run_id": "gvf/archived-original-verifier",
            "benchmark": {
                "id": "gvf-recorded-verifier",
                "version": "gvf-trials-import.v1",
                "dataset_sha256": file_hash(path),
                "grader_sha256": digest(
                    {
                        "adapter": file_hash(__file__),
                        "rule": "verdict == recorded independent truth",
                    }
                ),
                "split": "historical",
                "primary_metric": "verification_correct",
                "risk_metric": "false_verified",
            },
            "components": {
                "verifier": ",".join(
                    sorted({r["original_report"]["verifier_version"] for r in rows})
                )
            },
            "controls": {
                "environment_id": "gazebo-gvf-recorded-fixture",
                "budget": {"status": "not_recorded_in_trial_schema"},
                "semantic_contract_id": "state-report.v1",
                "source_mode": "archive_replay",
            },
            "provenance": {
                "sources": [source(path)],
                "adapter_sha256": file_hash(__file__),
                "claim_scope": "历史仿真记录上的验证器判定；非新仿真、实机验收或完整任务成功率。",
                "limitations": [
                    "沿用归档独立真值；未重新执行物理 oracle 或核验每个证据文件的字节。",
                    "证据引用存在不等于证据有效；按 seed 聚类，不把 210 个动作当 210 个独立场景。",
                    "本适配器导入整个已存在文件；原始未记录或丢失的试验无法恢复。",
                    "dataset hash 包含归档判定，当前适配器只供分项统计，后续成对验证器对比需独立冻结输入案例集。",
                ],
            },
            "planned_units": [u["unit_id"] for u in units],
            "units": units,
        }
    )


def coverage(runs, tools_path):
    for run in runs:
        validate(run)
    tool_document = json.loads(Path(tools_path).read_text())
    declared = sorted(t["function"]["name"] for t in tool_document["tools"])
    mapped = [t for family in catalog()["tool_families"].values() for t in family]
    if len(mapped) != len(set(mapped)) or set(mapped) != set(declared):
        raise ValueError("tool catalog drift: classify new/removed tools before reporting coverage")
    rows = [
        u
        for r in runs
        for u in r["units"]
        if value(u, r["benchmark"]["primary_metric"]) is not None
    ]
    return {
        "version": "system-coverage.v1",
        "run_sha256": [digest(r) for r in runs],
        "tool_catalog_sha256": file_hash(tools_path),
        "capabilities": {
            c["id"]: {
                "label": c["label"],
                "observed_tiers": sorted(
                    {u["dimensions"]["tier"] for u in rows if u["dimensions"]["stage"] == c["id"]}
                ),
                "scope": "阶段标签的已记录证据，不代表生产模块或完整能力通过",
            }
            for c in catalog()["capabilities"]
        },
        "exact_tools": {
            t: sorted({u["dimensions"]["tier"] for u in rows if u["dimensions"]["tool"] == t})
            for t in declared
        },
        "other_tool_identifiers": sorted(
            {
                u["dimensions"]["tool"]
                for u in rows
                if u["dimensions"]["tool"] not in declared and u["dimensions"]["tool"] != "none"
            }
        ),
        "limitations": [
            "只覆盖本次导入的证据；未导入不等于仓库完全没有测试。",
            "canonical action 与公开 tool 名称不自动等同；失败记录也是已测证据，覆盖不等于通过。",
            "本版本报告存在性覆盖；案例多样性、严重度权重及任务组合覆盖需要后续套件注册。",
        ],
    }
