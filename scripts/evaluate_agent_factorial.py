"""严格拆分上下文表达：冻结后使用同一生产渲染器和真实模型进行配对评测。"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchestration.eval.context_eval import factorial_runner as current

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--output", type=Path, required=True)
p.add_argument(
    "--phase", choices=["prepare", "dev", "test", "replay", "export-training"], default="prepare"
)
p.add_argument("--llm-config", type=Path)
p.add_argument("--workers", type=int, default=12)
a = p.parse_args()
a.output = a.output.resolve()
if not 1 <= a.workers <= 16:
    p.error("workers must be 1..16")
if a.phase == "prepare":
    current.prepare(a.output)
    print("Protocol and source frozen")
    raise SystemExit
# Isolated package loads the precise sources present at registration time.
current.verify(a.output)
folder = a.output / "source_snapshot/orchestration/eval/context_eval"
name = "_factorial_archive"
spec = importlib.util.spec_from_file_location(
    name, folder / "__init__.py", submodule_search_locations=[str(folder)]
)
module = importlib.util.module_from_spec(spec)
sys.modules[name] = module
spec.loader.exec_module(module)
import importlib

engine = importlib.import_module(name + ".factorial_runner")
if a.phase in ["dev", "test", "replay"]:
    phase = "test" if a.phase == "replay" else a.phase
    rows = engine.evaluate(
        a.output, phase, a.llm_config, workers=a.workers, replay=a.phase == "replay"
    )
    if phase == "dev":
        print(json.dumps(engine.select(a.output, rows), ensure_ascii=False))
else:
    print("Training samples", engine.export_training(a.output))
