"""复算冻结的第三轮统计，并追加详细报告，保留早期章节。"""

import argparse
import importlib
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchestration.eval.context_eval.factorial_runner import verify

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--source", type=Path, required=True)
p.add_argument("--analyze-only", action="store_true")
p.add_argument(
    "--output", type=Path, default=Path("docs/experiments/2026-09-21-agent-context-evaluation.md")
)
a = p.parse_args()
root = a.source.resolve()
verify(root)
folder = root / "source_snapshot/orchestration/eval/context_eval"
name = "_factorial_report_archive"
spec = importlib.util.spec_from_file_location(
    name, folder / "__init__.py", submodule_search_locations=[str(folder)]
)
module = importlib.util.module_from_spec(spec)
sys.modules[name] = module
spec.loader.exec_module(module)
analysis = importlib.import_module(name + ".factorial_analysis").analyze(root)
print("Analyzed rows:", analysis["rows"])
if not a.analyze_only:
    from orchestration.eval.context_eval.factorial_report import append_report

    print(append_report(root, a.output.resolve()))
