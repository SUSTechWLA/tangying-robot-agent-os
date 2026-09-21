"""比较 Agent 上下文表达；冻结、开发选择、留出测试、重放与训练导出。"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from orchestration.eval.context_eval.archive import frozen_module, verify_archive
from orchestration.eval.context_eval.runner import evaluate, export_training, freeze, select


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--llm-config", type=Path)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument(
        "--phase",
        choices=["prepare", "dev", "test", "all", "replay", "export-training"],
        default="all",
    )
    a = p.parse_args()
    a.output = a.output.resolve()
    if not 1 <= a.workers <= 16:
        p.error("--workers 必须为 1..16")
    if a.phase in {"prepare", "dev", "all"}:
        freeze(a.output)
    if a.phase == "prepare":
        return
    if a.phase in {"dev", "all"}:
        rows = evaluate(a.output, "dev", a.llm_config, a.workers)
        select(a.output, rows)
    if a.phase in {"test", "all", "replay"}:
        verify_archive(a.output, responses=a.phase == "replay")
        engine = frozen_module(a.output, "runner")
        rows = engine.evaluate(
            a.output, "test", a.llm_config, a.workers, replay=a.phase == "replay"
        )
        summary = engine.final_statistics(a.output, rows)
        print(
            json.dumps(
                {
                    "selected": summary["selected"],
                    "rows": len(rows),
                    "comparisons": summary["comparisons"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    if a.phase in {"export-training", "all"}:
        print("训练样本：", export_training(a.output, a.output / "training"))


if __name__ == "__main__":
    main()
