"""Source-bound read-only planning artifacts and honest verification semantics."""
import json

import pytest

from scripts import plan_spatial_coverage as cli


@pytest.fixture
def source_request(tmp_path):
    path = tmp_path / "request.json"
    path.write_bytes((cli.ROOT / "examples/coverage/mixed-air-ground.json").read_bytes())
    return path


def test_coverage_artifacts_preserve_inputs_and_replay_exact_source(source_request, tmp_path):
    directory = tmp_path / "run"
    result = cli.create_plan(source_request, directory)
    assert result["status"] == "predicted_complete"
    assert result["observedCoverage"] is None and result["physicalValidation"] is False
    assert (directory / "request.json").read_bytes() == source_request.read_bytes()
    replay = cli.verify_plan(directory)
    assert replay["verified"] and replay["predictionOnly"] and not replay["physicalValidation"]
    with pytest.raises(FileExistsError):
        cli.create_plan(source_request, directory)


def test_verification_detects_altered_data_and_manifest_paths(source_request, tmp_path):
    directory = tmp_path / "run"
    cli.create_plan(source_request, directory)
    plan = directory / "plan.json"
    original = plan.read_bytes()
    plan.write_bytes(original + b" ")
    with pytest.raises(ValueError, match="bytes changed"):
        cli.verify_plan(directory)
    plan.write_bytes(original)
    manifest = directory / "manifest.json"
    data = json.loads(manifest.read_text())
    data["files"]["../../private"] = data["files"].pop("plan.json")
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="manifest contract"):
        cli.verify_plan(directory)


def test_verification_refuses_current_implementation_drift(source_request, tmp_path, monkeypatch):
    directory = tmp_path / "run"
    cli.create_plan(source_request, directory)
    monkeypatch.setattr(cli, "sha", lambda _data: "changed-source")
    with pytest.raises(ValueError):
        cli.verify_plan(directory)


def test_request_bytes_are_frozen_once_even_when_original_file_changes(source_request, tmp_path, monkeypatch):
    import tangying_robot_gateway.spatial_coverage as coverage

    original_bytes = source_request.read_bytes()
    original_plan = coverage.plan_coverage_request

    def mutate_after_parse(request):
        result = original_plan(request)
        source_request.write_text('{"modified": true}')
        return result

    monkeypatch.setattr(coverage, "plan_coverage_request", mutate_after_parse)
    directory = tmp_path / "run"
    cli.create_plan(source_request, directory)
    assert (directory / "request.json").read_bytes() == original_bytes
    monkeypatch.setattr(coverage, "plan_coverage_request", original_plan)
    assert cli.verify_plan(directory)["verified"]


def test_source_mutation_during_planning_never_publishes_output(source_request, tmp_path, monkeypatch):
    import tangying_robot_gateway.spatial_coverage as coverage

    root = tmp_path / "source-root"
    for name in cli.SOURCES:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((cli.ROOT / name).read_bytes())
    monkeypatch.setattr(cli, "ROOT", root)
    original_plan = coverage.plan_coverage_request

    def mutate_source(request):
        result = original_plan(request)
        (root / cli.SOURCES[0]).write_text("changed")
        return result

    monkeypatch.setattr(coverage, "plan_coverage_request", mutate_source)
    directory = tmp_path / "run"
    with pytest.raises(RuntimeError, match="source changed"):
        cli.create_plan(source_request, directory)
    assert not directory.exists()


def test_invalid_request_does_not_leave_a_successful_evidence_directory(tmp_path):
    request = tmp_path / "bad.json"
    request.write_text('{"schemaVersion":"wrong"}')
    directory = tmp_path / "run"
    with pytest.raises(ValueError):
        cli.create_plan(request, directory)
    assert not directory.exists()


def test_read_only_command_line_never_executes_a_driver(source_request, tmp_path, capsys):
    directory = tmp_path / "cli-run"
    assert cli.main(["--request", str(source_request), "--output", str(directory)]) == 0
    assert json.loads(capsys.readouterr().out)["observedCoverage"] is None
    assert cli.main(["--verify", str(directory)]) == 0
    assert json.loads(capsys.readouterr().out)["verified"] is True
