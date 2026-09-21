"""生成默认关闭且排除已知失败环节的模型绑定研究配置。"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchestration.eval.context_eval.factorial_release import finalize

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--source", type=Path, required=True)
a = p.parse_args()
result = finalize(a.source.resolve())
print(
    "Generated deployment-policy.json; blocked stages:",
    sum(x["blocked"] for m in result["stages"].values() for x in m.values()),
)
