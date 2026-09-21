"""检查决策上下文的抽象数学契约及冻结生产编码。"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchestration.eval.context_eval.factorial_formal import verify_formal

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--source", type=Path, required=True)
a = p.parse_args()
r = verify_formal(a.source.resolve())
print(json.dumps({k: v for k, v in r.items() if k != "checks"}, ensure_ascii=False, indent=2))
