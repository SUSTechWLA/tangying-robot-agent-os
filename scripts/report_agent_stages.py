"""把分环节表达对照追加到既有实验报告。"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchestration.eval.context_eval.stage_report import append_report

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--source", type=Path, required=True)
p.add_argument(
    "--output", type=Path, default=Path("docs/experiments/2026-09-21-agent-context-evaluation.md")
)
a = p.parse_args()
print(append_report(a.source.resolve(), a.output.resolve()))
