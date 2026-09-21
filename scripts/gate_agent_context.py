"""Compare a candidate checkpoint with a frozen context evaluation baseline."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchestration.eval.context_eval.gate import compare_runs

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--baseline", type=Path, required=True)
p.add_argument("--candidate", type=Path, required=True)
p.add_argument("--baseline-format", default="json")
p.add_argument("--candidate-format", default="json")
p.add_argument("--output", type=Path)
p.add_argument("--min-ability-delta", type=float, default=0.0)
p.add_argument("--max-token-ratio", type=float, default=2.5)
a = p.parse_args()
r = compare_runs(
    a.baseline,
    a.candidate,
    a.baseline_format,
    a.candidate_format,
    min_ability_delta=a.min_ability_delta,
    max_token_ratio=a.max_token_ratio,
)
data = json.dumps(r, ensure_ascii=False, indent=2) + "\n"
if a.output:
    a.output.write_text(data)
print(data)
sys.exit(0 if r["passed"] else 1)
