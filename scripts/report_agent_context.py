"""从已归档的上下文评测生成中文报告与图表。"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchestration.eval.context_eval.report import write_report

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--source", type=Path, required=True)
p.add_argument(
    "--output", type=Path, default=Path("docs/experiments/2026-09-21-agent-context-evaluation.md")
)
a = p.parse_args()
print(write_report(a.source.resolve(), a.output.resolve()))
