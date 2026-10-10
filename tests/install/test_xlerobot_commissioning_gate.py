"""Synthetic commissioning records exercise gates, never prove real robot results."""

from __future__ import annotations

import json
import subprocess

import pytest

from scripts import xlerobot_production_check as gate
from tests.install.test_sim2real import add_record, save, site
from tests.install.test_sim2real import kit as _offline_kit

kit = _offline_kit


@pytest.fixture
def bound_kit(kit, monkeypatch):
    config = kit / "robot-pi.env"
    values = site.env_values(config)
    values.update(
        ROBOT_COMMISSIONING_KIT=str(kit),
        XLEROBOT_CALIBRATION=str(kit / "artifacts"),
        XLEROBOT_CALIBRATION_ROOT=str(kit / "artifacts"),
    )
    config.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    monkeypatch.setattr(
        gate,
        "installed_source_identity",
        lambda: {"revision": "release-reviewed-sha", "dirty": False},
    )
    for kind in site.KINDS:
        if kind not in ("trial", "soak"):
            add_record(kit, kind)
    for i in range(30):
        add_record(kit, "trial", task_id=f"synthetic-test-{i}")
    add_record(kit, "soak", duration_seconds=3600)
    return kit


def check(kit, config=None):
    config = config or kit / "robot-pi.env"
    return gate.commissioning_checks(gate.read_env(config), config)


def test_bound_offline_fixture_does_not_claim_live_or_physical_readiness(bound_kit):
    blockers, summary = check(bound_kit)
    assert blockers == []
    assert summary["evidence_counts"]["trial"] == 30
    assert summary["live_verified"] is False and summary["physical_ready"] is False


@pytest.mark.parametrize("source", ["gazebo", "mujoco", "robocasa", "mock", "simulation"])
def test_known_simulator_json_cannot_support_a_physical_record(bound_kit, source):
    log = bound_kit / "explicit-simulation.json"
    log.write_text(json.dumps({"runtime": {"adapter": source}, "success": True}))
    site.record(
        bound_kit,
        kind="trial",
        operator="test fixture",
        result="passed",
        notes="synthetic negative test",
        task_id="synthetic-not-hardware",
        evidence=[log],
    )
    blockers, summary = check(bound_kit)
    assert any("simulated/mock data" in item for item in blockers)
    assert summary["evidence_counts"]["trial"] == 30  # The extra simulator record is excluded.


def test_declared_simulation_kind_remains_separate_from_physical_trials(bound_kit):
    log = bound_kit / "simulation.json"
    log.write_text('{"adapter":"gazebo"}')
    site.record(
        bound_kit,
        kind="simulation",
        operator="test fixture",
        result="passed",
        notes="simulation record only",
        evidence=[log],
    )
    blockers, summary = check(bound_kit)
    assert blockers == []
    assert summary["evidence_counts"]["trial"] == 30
    assert summary["evidence_counts"]["simulation"] == 2


@pytest.mark.parametrize("task_id", ["synthetic-test-0", " synthetic-test-0 "])
def test_duplicate_trial_blocks_even_when_thirty_other_records_exist(bound_kit, task_id):
    add_record(bound_kit, "trial", task_id=task_id)
    blockers, summary = check(bound_kit)
    assert any("duplicates a previously recorded task ID" in item for item in blockers)
    assert summary["evidence_counts"]["trial"] == 30


def test_simulation_flag_on_trial_record_blocks_and_excludes_it(bound_kit):
    path = add_record(bound_kit, "trial", task_id="explicit-synthetic-record")
    record = site.read_json(path)
    record["simulation"] = True
    save(path, record)
    blockers, summary = check(bound_kit)
    assert any("declares simulated/mock evidence" in item for item in blockers)
    assert summary["evidence_counts"]["trial"] == 30


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("active calibration", "deployed calibration bytes"),
        ("installed version", "Git revision"),
        ("dirty source", "modified"),
        ("active config", "configuration"),
    ],
)
def test_deployed_drift_cannot_reuse_commissioning_records(
    bound_kit, monkeypatch, mutation, expected
):
    config = bound_kit / "robot-pi.env"
    if mutation == "active calibration":
        active = bound_kit.parent / "active-calibration"
        active.mkdir()
        (active / "tangying-xlerobot.json").write_text('{"changed":true}')
        config = bound_kit.parent / "deployed.env"
        config.write_text(
            (bound_kit / "robot-pi.env")
            .read_text()
            .replace(str(bound_kit / "artifacts"), str(active))
        )
    elif mutation == "installed version":
        monkeypatch.setattr(
            gate,
            "installed_source_identity",
            lambda: {"revision": "another-version", "dirty": False},
        )
    elif mutation == "dirty source":
        monkeypatch.setattr(
            gate,
            "installed_source_identity",
            lambda: {"revision": "release-reviewed-sha", "dirty": True},
        )
    else:
        config = bound_kit.parent / "deployed.env"
        config.write_text(
            (bound_kit / "robot-pi.env").read_text() + "ROBOT_ENTITY_PROVIDER=different:scene\n"
        )
    blockers, _ = check(bound_kit, config)
    assert any(expected in item for item in blockers), blockers


def test_changed_kit_software_revision_invalidates_old_trial_records(bound_kit):
    profile = site.read_json(bound_kit / "site.json")
    profile["softwareRevision"] = "a-new-reviewed-version"
    save(bound_kit / "site.json", profile)
    blockers, summary = check(bound_kit)
    assert blockers
    assert summary["evidence_counts"]["trial"] == 0
    assert summary["stale_records"] >= 30


@pytest.mark.parametrize(
    "changed_path",
    [
        "robot/ros2_ws/src/tangying_robot_gateway/tangying_ros_gateway/node.py",
        "robot/ros2_ws/src/xlerobot_adapter/config/xlerobot.yaml",
        "cmd/local-agent/main.go",
    ],
)
def test_installed_identity_detects_real_gateway_and_config_drift(
    tmp_path, monkeypatch, changed_path
):
    # This isolated Git repository is only a fixture, never the project checkout.
    source = tmp_path / changed_path
    source.parent.mkdir(parents=True)
    source.write_text("reviewed fixture source\n")

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
        ).stdout.strip()

    git("init", "-q")
    git("add", ".")
    git(
        "-c",
        "user.name=Offline test fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "Synthetic fixture only",
    )
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    revision = git("rev-parse", "HEAD")
    assert gate.installed_source_identity() == {"revision": revision, "dirty": False}
    source.write_text("changed fixture source\n")
    assert gate.installed_source_identity() == {"revision": revision, "dirty": True}
