"""确认锁定的逐环节表达；--replay 不调用网络。"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchestration.eval.context_eval.factorial_confirm import evaluate

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--source", type=Path, required=True)
p.add_argument("--llm-config", type=Path)
p.add_argument("--replay", action="store_true")
a = p.parse_args()
r = evaluate(a.source.resolve(), a.llm_config, a.replay)
print("Confirmation rows:", r["rows"])
