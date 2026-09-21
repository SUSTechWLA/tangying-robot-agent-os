"""新的层级结构对照；开发选择冻结后再运行新种子确认。"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchestration.eval.context_eval.factorial_structure import evaluate

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--source", type=Path, required=True)
p.add_argument("--llm-config", type=Path)
p.add_argument("--replay", action="store_true")
a = p.parse_args()
for split in ["dev", "structure_confirmation"]:
    r = evaluate(a.source.resolve(), split, a.llm_config, a.replay)
    print("Completed", split, flush=True)
