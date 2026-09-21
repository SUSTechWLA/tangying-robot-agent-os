"""Run or exactly replay the separately frozen multi-turn context experiment."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchestration.eval.context_eval.archive import frozen_module, verify_archive
from orchestration.eval.context_eval.episodes import run_episodes

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--output", type=Path, required=True)
p.add_argument("--llm-config", type=Path)
p.add_argument("--workers", type=int, default=6)
p.add_argument("--replay", action="store_true")
a = p.parse_args()
if not 1 <= a.workers <= 16:
    p.error("workers must be 1..16")
verify_archive(a.output.resolve(), responses=a.replay)
run = frozen_module(a.output.resolve(), "episodes").run_episodes if a.replay else run_episodes
print(
    json.dumps(
        run(a.output.resolve(), a.llm_config, a.workers, a.replay), ensure_ascii=False, indent=2
    )
)
