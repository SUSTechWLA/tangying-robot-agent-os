"""冻结并确认规划/恢复决策契约消融；--replay 离线复算。"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchestration.eval.context_eval.factorial_contract import evaluate

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--source", type=Path, required=True)
p.add_argument("--llm-config", type=Path)
p.add_argument("--replay", action="store_true")
a = p.parse_args()
for split in ["dev", "contract_confirmation"]:
    evaluate(a.source.resolve(), split, a.llm_config, a.replay)
    print("Completed", split, flush=True)
