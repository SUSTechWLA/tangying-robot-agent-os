"""The setup package must never turn incomplete or stale records into a release pass."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from xlerobot_adapter.calibration import MOTOR_IDS

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("sim2real", ROOT / "scripts/sim2real.py")
site = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site)


def save(path, value):
    path.write_bytes(site.wire(value))


@pytest.fixture
def kit(tmp_path):
    kit = tmp_path / "robot-1"
    site.initialize(kit, "robot-1")
    profile = site.read_json(kit / "site.json")
    profile["hardware"].update(leftSerial="left-usb-1", rightSerial="right-usb-2")
    profile.update(softwareRevision="release-reviewed-sha", taskScope="supervised tabletop cup placement",
                   calibrationRevision="cal-1", transformRevision="tf-1", fleetUrl="https://fleet.test")
    save(kit / "site.json", profile)
    save(kit / profile["files"]["calibration"], {name: {"id": motor_id, "drive_mode": 0, "homing_offset": 0,
         "range_min": 0, "range_max": 4095} for name, motor_id in MOTOR_IDS.items()})
    save(kit / profile["files"]["transforms"], {"robotId": "robot-1", "transformRevision": "tf-1", "transforms": [
        {"parent": "world", "child": "camera", "matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]}]})
    save(kit / profile["files"]["perceptionValidation"], {"robotId": "robot-1", "calibrationRevision": "cal-1",
        "transformRevision": "tf-1", "passed": True, "procedure": "fixture integration only",
        "calibrationSha256": site.digest((kit / profile["files"]["calibration"]).read_bytes()),
        "transformsSha256": site.digest((kit / profile["files"]["transforms"]).read_bytes())})
    (kit / profile["files"]["policyArtifact"]).write_bytes(b"test model fixture only")
    save(kit / profile["files"]["policyManifest"], {
        "schemaVersion": "policy.manifest.v1", "policyId": "fixture", "version": "1", "framework": "imitation",
        "artifactSha256": site.digest(b"test model fixture only"), "capabilities": ["manipulation.pick", "manipulation.place"],
        "robotModels": ["xlerobot-dual-arm"], "adapters": ["xlerobot_direct"], "observationSchema": "policy.observation.v1",
        "requiredObservationSources": ["scene", "proprioception"], "maxObservationAgeMs": 500,
        "actionSchema": "xlerobot.named-joints.v1", "maxActionChunkLength": 4,
        "actionBounds": {"left_arm_shoulder_pan.pos": {"minimum": -5, "maximum": 5}},
        "calibrationRevision": "cal-1", "transformRevision": "tf-1"})
    with (kit / "robot-pi.env").open("a") as f:
        f.write("ROBOT_ENTITY_PROVIDER=fixture:scene\nROBOT_VERIFIER_PROVIDER=fixture:verify\n")
    edge = (kit / "edge.env").read_text().replace("https://fleet.example.invalid", "https://fleet.test")
    edge = edge.replace("EDGE_TRANSFORM_REVISION=\n", "EDGE_TRANSFORM_REVISION=tf-1\n").replace("EDGE_CALIBRATION_REVISION=\n", "EDGE_CALIBRATION_REVISION=cal-1\n")
    (kit / "edge.env").write_text(edge.replace("EDGE_DEVICE_TOKEN=\n", "EDGE_DEVICE_TOKEN=private-test-token\n"))
    assert site.inspect(kit)["checksPassed"], site.inspect(kit)
    return kit


def add_record(kit, kind="simulation", **kw):
    log = kit / "sample.log"
    log.write_text("a test log fixture, never a real hardware result")
    return site.record(kit, kind=kind, operator="test operator", result="passed", notes="fixture only", evidence=[log], **kw)


def test_init_is_private_incomplete_and_refuses_overwrite(tmp_path):
    kit = tmp_path / "new"
    site.initialize(kit, "robot-1")
    assert kit.stat().st_mode & 0o777 == 0o700
    assert (kit / "site.json").stat().st_mode & 0o777 == 0o600
    result = site.inspect(kit)
    assert not result["checksPassed"]
    assert {"SERIAL_IDENTITY", "FLEET_URL"} <= {x["code"] for x in result["blockers"]}
    before = (kit / "site.json").read_bytes()
    with pytest.raises(FileExistsError):
        site.initialize(kit, "robot-2")
    assert (kit / "site.json").read_bytes() == before


def test_check_does_not_import_site_providers_or_report_secrets(kit):
    result = site.inspect(kit)
    assert result["checksPassed"]  # fixture:scene module deliberately does not exist.
    assert "private-test-token" not in json.dumps(result)
    assert "physicalReady" not in result


@pytest.mark.parametrize("mutation,code", [("robot", "CONFIG_MISMATCH"), ("manifest", "POLICY_HASH"), ("calibration", "CALIBRATION_INVALID"), ("escape", "FILE_POLICYARTIFACT")])
def test_mismatched_delivery_is_blocked(kit, mutation, code):
    if mutation == "robot":
        path = kit / "edge.env"
        path.write_text(path.read_text().replace("EDGE_ROBOT_ID=robot-1", "EDGE_ROBOT_ID=robot-2"))
    elif mutation == "manifest":
        (kit / "artifacts/policy.bin").write_bytes(b"changed model")
    elif mutation == "calibration":
        save(kit / "artifacts/tangying-xlerobot.json", {"test": {}})
    else:
        profile = site.read_json(kit / "site.json")
        profile["files"]["policyArtifact"] = "../outside.bin"
        (kit.parent / "outside.bin").write_bytes(b"outside")
        save(kit / "site.json", profile)
    assert code in {x["code"] for x in site.inspect(kit)["blockers"]}


def test_evidence_binds_configuration_and_attachment_bytes(kit):
    path = add_record(kit)
    item = site.read_json(path)
    assert site.pilot_report(kit)["evidenceCounts"]["simulation"] == 1
    (kit / item["attachments"][0]["path"]).write_bytes(b"tampered")
    assert "EVIDENCE_INVALID" in {x["code"] for x in site.pilot_report(kit)["blockers"]}
    profile = site.read_json(kit / "site.json")
    profile["softwareRevision"] = "new-release"
    save(kit / "site.json", profile)
    report = site.pilot_report(kit)
    assert report["staleRecords"] == 1
    assert report["evidenceCounts"]["simulation"] == 0


def test_full_pilot_record_set_counts_unique_tasks_and_requires_soak(kit):
    for kind in site.KINDS:
        if kind not in ("trial", "soak"):
            add_record(kit, kind)
    for number in range(30):
        add_record(kit, "trial", task_id=f"task-{number}")
    with pytest.raises(ValueError, match="3600"):
        add_record(kit, "soak", duration_seconds=3599)
    assert not site.pilot_report(kit)["checksPassed"]
    add_record(kit, "soak", duration_seconds=3600)
    add_record(kit, "trial", task_id="task-0")
    result = site.pilot_report(kit)
    assert result["checksPassed"]
    assert result["evidenceCounts"]["trial"] == 30
    assert result["scope"] == "offline_commissioning_records"


def test_incomplete_kit_cannot_collect_passing_records(tmp_path):
    kit = tmp_path / "robot"
    site.initialize(kit, "robot")
    with pytest.raises(ValueError, match="integration"):
        add_record(kit)


@pytest.mark.parametrize("field,value,code", [
    ("requiredObservationSources", ["camera.front"], "POLICY_SOURCES"),
    ("maxActionChunkLength", 65, "POLICY_CHUNK_LIMIT"),
    ("actionBounds", {"x.vel": {"minimum": 0, "maximum": 1}}, "POLICY_ACTION_BOUNDS"),
])
def test_manifest_must_match_current_edge_observation_and_action_contract(kit, field, value, code):
    path = kit / "artifacts/policy-manifest.json"
    manifest = site.read_json(path)
    manifest[field] = value
    save(path, manifest)
    assert code in {item["code"] for item in site.inspect(kit)["blockers"]}


@pytest.mark.parametrize("mutation", ["calibration", "transforms", "missing_hash", "wrong_hash"])
def test_perception_validation_requires_exact_current_input_hashes(kit, mutation):
    if mutation == "calibration":
        path = kit / "artifacts/tangying-xlerobot.json"
        calibration = site.read_json(path)
        calibration["left_arm_shoulder_pan"]["homing_offset"] = 100
        save(path, calibration)
    elif mutation == "transforms":
        path = kit / "artifacts/transforms.json"
        transforms = site.read_json(path)
        transforms["transforms"][0]["matrix"][3] = 0.25
        save(path, transforms)
    else:
        path = kit / "artifacts/perception-validation.json"
        validation = site.read_json(path)
        if mutation == "missing_hash":
            validation.pop("calibrationSha256")
        else:
            validation["transformsSha256"] = "0" * 64
        save(path, validation)
    result = site.inspect(kit)
    assert not result["checksPassed"]
    assert "PERCEPTION_INPUT_HASH" in {item["code"] for item in result["blockers"]}


def test_failed_soak_can_be_recorded_before_minimum_duration_and_blocks_report(kit):
    path = site.record(kit, kind="soak", operator="test operator", result="failed",
                       notes="fixture failed after 60 seconds", evidence=[kit / "site.json"], duration_seconds=60)
    item = site.read_json(path)
    assert item["result"] == "failed" and item["durationSeconds"] == 60
    report = site.pilot_report(kit)
    assert not report["checksPassed"]
    assert "EVIDENCE_INVALID" in {entry["code"] for entry in report["blockers"]}
    assert report["evidenceCounts"]["soak"] == 0


@pytest.mark.parametrize("duration", [-1, True, 1.5])
def test_failed_soak_rejects_invalid_duration(kit, duration):
    with pytest.raises(ValueError):
        site.record(kit, kind="soak", operator="test operator", result="failed",
                    notes="fixture", evidence=[kit / "site.json"], duration_seconds=duration)


@pytest.mark.parametrize("name,key,value", [
    ("robot-pi.env", "ROBOT_ALLOW_INSECURE", "1"),
    ("edge.env", "EDGE_RUNTIME_INSECURE", "1"),
    ("edge.env", "EDGE_RUNTIME_SERVER_NAME", "other-robot.local"),
    ("edge.env", "EDGE_CALIBRATION_REVISION", "old-calibration"),
    ("edge.env", "EDGE_TRANSFORM_REVISION", "old-transform"),
])
def test_runtime_security_and_revisions_must_match_profile(kit, name, key, value):
    path = kit / name
    values = site.env_values(path)
    values[key] = value
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    report = site.inspect(kit)
    assert not report["checksPassed"]
    assert "CONFIG_MISMATCH" in {entry["code"] for entry in report["blockers"]}
