from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[4]
SCRIPT = REPOSITORY_ROOT / "scripts" / "train_semantic_policy.py"


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_train_and_evaluate_cli_emit_one_json_summary_line(tmp_path):
    checkpoint = tmp_path / "semantic-policy.json"

    trained = _run(
        "train",
        "--episodes",
        "300",
        "--seed",
        "7",
        "--output",
        str(checkpoint),
    )
    evaluated = _run(
        "evaluate",
        "--checkpoint",
        str(checkpoint),
        "--episodes",
        "20",
        "--seed",
        "1007",
        "--min-success-rate",
        "0.90",
    )

    assert trained.returncode == 0, trained.stderr
    assert evaluated.returncode == 0, evaluated.stderr
    training_summary = json.loads(trained.stdout)
    evaluation_summary = json.loads(evaluated.stdout)
    assert training_summary["command"] == "train"
    assert training_summary["checkpoint"] == str(checkpoint)
    assert evaluation_summary["command"] == "evaluate"
    assert evaluation_summary["successRate"] >= 0.9
    assert len(trained.stdout.strip().splitlines()) == 1
    assert len(evaluated.stdout.strip().splitlines()) == 1


def test_evaluate_cli_exits_nonzero_when_policy_misses_threshold(tmp_path):
    checkpoint = tmp_path / "semantic-policy.json"
    assert (
        _run("train", "--episodes", "5", "--seed", "3", "--output", str(checkpoint)).returncode == 0
    )
    document = json.loads(checkpoint.read_text())
    document["qTable"] = {state: [0.0] * len(document["actions"]) for state in document["qTable"]}
    checkpoint.write_text(json.dumps(document))

    evaluated = _run(
        "evaluate",
        "--checkpoint",
        str(checkpoint),
        "--episodes",
        "5",
        "--seed",
        "9",
        "--min-success-rate",
        "0.90",
    )

    assert evaluated.returncode == 1
    assert json.loads(evaluated.stdout)["successRate"] == 0.0
