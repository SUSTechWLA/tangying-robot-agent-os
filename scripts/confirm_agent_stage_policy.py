"""对固定发布表达策略运行新种子确认，不再选择候选。"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchestration.eval.context_eval.stage_confirm import run

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--source", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
p.add_argument("--llm-config", type=Path)
p.add_argument("--replay", action="store_true")
a = p.parse_args()
print(
    json.dumps(
        run(a.source.resolve(), a.output.resolve(), a.llm_config, a.replay), ensure_ascii=False
    )
)
