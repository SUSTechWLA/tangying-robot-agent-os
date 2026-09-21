#!/usr/bin/env python3
"""Offline system evaluation CLI. Does not contact models, simulators or robots."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from orchestration.eval.system_eval.adapters import (
    coverage,
    import_context,
    import_gvf,
)
from orchestration.eval.system_eval.engine import (
    PACKAGE,
    compare,
    digest,
    file_hash,
    load,
    reliability,
    scorecard,
    write,
)
from orchestration.eval.system_eval.report import (
    comparison_markdown,
    coverage_markdown,
    score_markdown,
)


def emit(path, result, renderer=None):
    write(path, result)
    if renderer:
        Path(path).with_suffix(".md").write_text(renderer(result))


def demo(output):
    output = Path(output)
    source = ROOT / "artifacts/agent-context-eval/factorial-v1/run-v3"
    baseline = import_context(source, "deepseek-v4-pro", "json-source-raw-full")
    candidate = import_context(source, "deepseek-v4-pro", "json-source-derived-full")
    gvf = import_gvf(ROOT / "artifacts/grounded-verification/validated-run/trials.jsonl")
    for label, run in [("baseline", baseline), ("candidate", candidate), ("gvf", gvf)]:
        write(output / f"{label}.run.json", run, immutable=True)
        emit(
            output / f"{label}.score.json",
            scorecard(run, "tool" if label == "gvf" else "stage"),
            score_markdown,
        )
    profile = json.loads((PACKAGE / "research-gate.example.json").read_text())
    comparison = compare(baseline, candidate, ["representation"], profile)
    emit(output / "comparison.json", comparison, comparison_markdown)
    report = coverage([baseline, candidate, gvf], ROOT / "tools.json")
    emit(output / "coverage.json", report, coverage_markdown)
    write(output / "reliability.json", reliability(candidate, 3))
    manifest = {
        "mode": "offline_archive_import",
        "new_model_calls": 0,
        "new_robot_actions": 0,
        "sources_scope": "既有历史证据的统一整理；不是新系统能力实验",
        "comparison_status": comparison["status"],
        "runs": {
            k: {"units": len(r["units"]), "sha256": digest(r)}
            for k, r in [("baseline", baseline), ("candidate", candidate), ("gvf", gvf)]
        },
        "evaluation_code_sha256": {
            str(p.relative_to(ROOT)): file_hash(p) for p in sorted(PACKAGE.glob("*")) if p.is_file()
        },
        "cli_sha256": file_hash(__file__),
    }
    write(output / "manifest.json", manifest)
    (output / "README.md").write_text(
        "\n".join(
            [
                "# Agent 系统 Eval 离线接入演示",
                "",
                "这是既有实验记录的统一导入，新增模型调用和机器人动作均为 0。",
                "",
                f"上下文基线与候选各 {len(baseline['units'])} 个单轮判定；GVF {len(gvf['units'])} 个动作验证记录。两类 benchmark 不合并为系统总成功率。",
                "",
                "- [基线分项](baseline.score.md)",
                "- [候选分项](candidate.score.md)",
                "- [验证器分项](gvf.score.md)",
                "- [实现配对比较](comparison.md)",
                "- [覆盖与缺口](coverage.md)",
                "- [原始来源与代码指纹](manifest.json)",
                "",
                f"示例门禁结果 **{comparison['status']}**；历史选择与过少独立簇不能支持新晋级结论。",
                "",
                "所有 run 中 fresh_draw=false，因此 all-3 重复可靠性为未测量；不能重复播放缓存来增加样本数。",
                "",
                "目录中的 run 使用不可覆盖写入；若适配器或源数据改变，请指定一个新输出目录。",
                "",
            ]
        )
    )
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("import-context")
    p.add_argument("--source", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--format", required=True)
    p.add_argument("--output", required=True)
    p = sub.add_parser("import-gvf")
    p.add_argument("--source", required=True)
    p.add_argument("--output", required=True)
    p = sub.add_parser("score")
    p.add_argument("--run", required=True)
    p.add_argument("--by", default="stage")
    p.add_argument("--output", required=True)
    p = sub.add_parser("compare")
    p.add_argument("--baseline", required=True)
    p.add_argument("--candidate", required=True)
    p.add_argument("--changed-component", action="append", required=True)
    p.add_argument("--profile", required=True)
    p.add_argument("--output", required=True)
    p = sub.add_parser("coverage")
    p.add_argument("--runs", nargs="+", required=True)
    p.add_argument("--tools", default=str(ROOT / "tools.json"))
    p.add_argument("--output", required=True)
    p = sub.add_parser("reliability")
    p.add_argument("--run", required=True)
    p.add_argument("--k", type=int, required=True)
    p.add_argument("--output", required=True)
    p = sub.add_parser("demo")
    p.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "import-context":
        write(args.output, import_context(args.source, args.model, args.format), immutable=True)
    elif args.command == "import-gvf":
        write(args.output, import_gvf(args.source), immutable=True)
    elif args.command == "score":
        emit(args.output, scorecard(load(args.run), args.by), score_markdown)
    elif args.command == "compare":
        result = compare(
            load(args.baseline),
            load(args.candidate),
            args.changed_component,
            json.loads(Path(args.profile).read_text()),
        )
        emit(args.output, result, comparison_markdown)
        print(json.dumps({"output": args.output, "status": result["status"]}))
        return 0 if result["status"] == "PASS" else 2 if result["status"] == "FAIL" else 3
    elif args.command == "coverage":
        emit(args.output, coverage([load(p) for p in args.runs], args.tools), coverage_markdown)
    elif args.command == "reliability":
        emit(args.output, reliability(load(args.run), args.k))
    else:
        result = demo(args.output)
        print(
            json.dumps(
                {
                    "output": args.output,
                    "status": result["comparison_status"],
                    "new_model_calls": 0,
                },
                ensure_ascii=False,
            )
        )
        return 0  # A completed demonstration may correctly report a failing candidate.
    print(json.dumps({"output": args.output}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
