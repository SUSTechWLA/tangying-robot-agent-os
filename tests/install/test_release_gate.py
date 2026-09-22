"""The aggregate status must fail closed for every non-success dependency."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_pr_heads_have_one_ci_trigger_without_dropping_main_or_release_validation():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    # PyYAML's YAML 1.1 resolver represents the workflow's `on` key as True.
    triggers = workflow.get("on", workflow.get(True))
    assert "pull_request" in triggers and "workflow_dispatch" in triggers
    assert triggers["push"] == {"branches": ["main"], "tags": ["v*"]}


@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled", "skipped", "timed_out"])
def test_release_gate_requires_every_validation_job(outcome):
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    gate = workflow["jobs"]["release-gate"]
    dependencies = set(gate["needs"])
    assert dependencies == set(workflow["jobs"]) - {"release-gate"}
    assert "always()" in gate["if"]
    program = re.search(r"python3 - <<'PYTHON'\n(.*?)\nPYTHON", gate["steps"][0]["run"], re.DOTALL)[1]
    results = {name: {"result": "success"} for name in dependencies}
    results["test"]["result"] = outcome
    result = subprocess.run(
        [sys.executable, "-c", program],
        env={**os.environ, "VALIDATION_RESULTS": json.dumps(results)},
        check=False, capture_output=True, text=True,
    )
    assert (result.returncode == 0) == (outcome == "success"), result.stdout + result.stderr
