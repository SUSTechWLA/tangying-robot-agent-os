"""Exploratory context-field ablations; never overwrite primary test results."""

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchestration.eval.context_eval.runner import evaluate, paired, summarize

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--source", type=Path, required=True)
p.add_argument("--llm-config", type=Path)
p.add_argument("--replay", action="store_true")
a = p.parse_args()
source = a.source.resolve()
root = source / "ablations"
root.mkdir(exist_ok=True)
for name in [
    "cases.jsonl",
    "dataset-manifest.json",
    "selection.json",
    "model-config.json",
    "context-render",
]:
    target = root / name
    if not target.exists():
        shutil.copy2(source / name, target)
# Reuse exact requests when deleting an already empty history makes no change.
if not (root / "responses").exists():
    (root / "responses").symlink_to(source / "responses", target_is_directory=True)
(root / "protocol.json").write_text(
    json.dumps(
        {
            "version": "context-ablation.v1",
            "exploratory": True,
            "not_used_for_selection": True,
            "styles": ["no_history", "no_provenance"],
            "base_format": "nl_decision",
            "no_provenance": "remove record scopes, times and supersedes; statements may still explicitly restate them",
            "no_history": "remove attempts only; verification statements may still restate failures",
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
rows = evaluate(
    root, "test", a.llm_config, 6, replay=a.replay, styles=["no_history", "no_provenance"]
)
base = [
    json.loads(x)
    for x in (source / "test-results.jsonl").read_text().splitlines()
    if json.loads(x)["format"] == "nl_decision"
]
result = {
    "statistics": summarize(base + rows),
    "comparisons": [paired(base + rows, "nl_decision", s) for s in ["no_history", "no_provenance"]],
}
(root / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(result["comparisons"], ensure_ascii=False))
