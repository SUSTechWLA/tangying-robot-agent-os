"""按环节比较上下文表达并冻结路由策略。"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchestration.eval.context_eval import stage_runner
from orchestration.eval.context_eval.archive import frozen_module

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--output", type=Path, required=True)
p.add_argument(
    "--phase", choices=["prepare", "dev", "test", "all", "replay", "export-training"], default="all"
)
p.add_argument("--llm-config", type=Path)
p.add_argument("--workers", type=int, default=6)
a = p.parse_args()
a.output = a.output.resolve()
if not 1 <= a.workers <= 16:
    p.error("workers must be 1..16")
if a.phase in {"prepare", "dev", "all"}:
    stage_runner.freeze(a.output)
if a.phase in {"dev", "all"}:
    engine = frozen_module(a.output, "stage_runner")
    rows = engine.evaluate(a.output, "dev", a.llm_config, a.workers)
    engine.select(a.output, rows)
if a.phase in {"test", "all", "replay"}:
    engine = frozen_module(a.output, "stage_runner")
    rows = engine.evaluate(a.output, "test", a.llm_config, a.workers, a.phase == "replay")
    result = engine.analyze(a.output, rows)
    print(
        json.dumps(
            {"comparisons": result["comparisons"], "gates": result["gates"]}, ensure_ascii=False
        )
    )
if a.phase in {"all", "export-training"}:
    print("训练条数", frozen_module(a.output, "stage_runner").export_training(a.output))
