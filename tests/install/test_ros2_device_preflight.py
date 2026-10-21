"""Offline metadata checks; no ROS discovery, robot driver or motion is started."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import ros2_device_preflight as check

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "examples/robots/bindings"


def write(path, value):
    path.write_text(json.dumps(value))
    return path


@pytest.fixture
def fixture(tmp_path):
    binding = json.loads((TEMPLATES / "agv-nav2.binding.json").read_text())
    profile = json.loads((ROOT / "examples/robots/ugv.commissioning.profile.json").read_text())
    gateway = json.loads((TEMPLATES / "agv-nav2.gateway.json").read_text())
    binding["profilePath"] = gateway["profilePath"] = "profile.json"
    binding["gatewayConfigPath"] = "gateway.json"
    write(tmp_path / "profile.json", profile)
    write(tmp_path / "gateway.json", gateway)
    path = write(tmp_path / "binding.json", binding)
    return path, binding, profile, gateway


@pytest.mark.parametrize("slug", ["dji-uav", "unitree-legged", "agv-nav2"])
def test_templates_are_valid_but_never_ready_for_motion(slug, capsys):
    path = TEMPLATES / f"{slug}.binding.json"
    result = check.inspect_binding(path)
    assert result["configurationValid"] and not result["configurationComplete"]
    assert result["unresolvedFields"] and not result["hardwareValidated"]
    assert not result["motionAuthorized"]
    assert check.main([str(path)]) == 2
    assert check.main([str(path), "--template"]) == 0


@pytest.mark.parametrize("field,value,message", [
    ("robotId", "another-robot", "robot/model"),
    ("profileRevision", "0" * 64, "profileRevision"),
    ("commissioningId", "another-commission", "commissioningId"),
    ("rosDomainId", True, "integer"),
    ("unexpectedKey", True, "Extra inputs"),
])
def test_strict_identity_and_contract_checks(fixture, field, value, message):
    path, binding, _, _ = fixture
    binding[field] = value
    write(path, binding)
    with pytest.raises(ValueError, match=message):
        check.inspect_binding(path)


@pytest.mark.parametrize("section,field,value,message", [
    ("device", "model", "other-model", "robot/model"),
    ("coordinates", "sourceId", "missing-source", "localization"),
    ("coordinates", "transformRevision", "other-calibration", "localization"),
    ("controller", "safeStopStrategy", "hold_position", "stop/watchdog"),
    ("controller", "stopProcedureId", "other-stop", "stop/watchdog"),
    ("controller", "commandLossTimeoutMs", 501, "stop/watchdog"),
    ("controller", "navigationDimension", "volume3d", "dimension"),
    ("controller", "commandAuthorityOwner", "cloud_agent", "local_controller"),
])
def test_semantic_mismatch_rejected(fixture, section, field, value, message):
    path, binding, _, _ = fixture
    binding[section][field] = value
    write(path, binding)
    with pytest.raises(ValueError, match=message):
        check.inspect_binding(path)


@pytest.mark.parametrize("field,value", [("namespace", ""), ("namespace", "/"),
                                         ("actionName", "/another_robot/execute")])
def test_gateway_namespace_is_not_implicitly_shared(fixture, field, value):
    path, _, _, gateway = fixture
    gateway[field] = value
    write(path.parent / "gateway.json", gateway)
    with pytest.raises(ValueError, match="namespace"):
        check.inspect_binding(path)


def test_same_profile_content_at_different_path_does_not_hide_configuration_split(fixture):
    path, _, profile, gateway = fixture
    write(path.parent / "other-profile.json", profile)
    gateway["profilePath"] = "other-profile.json"
    write(path.parent / "gateway.json", gateway)
    with pytest.raises(ValueError, match="different profile files"):
        check.inspect_binding(path)


def test_completed_local_configuration_still_is_not_hardware_validation(fixture):
    path, binding, profile, gateway = fixture
    for key, value in binding["device"].items():
        if value.startswith("replace-with-"):
            binding["device"][key] = "measured-config-revision-1"
    profile["modelId"] = binding["device"]["model"]
    profile["sensors"][0]["transformRevision"] = "calibration-1"
    binding["coordinates"]["transformRevision"] = "calibration-1"
    binding["coordinates"]["vendorConvention"] = "right-handed-z-up-local-map"
    binding["commissioningId"] = gateway["commissioningId"] = "site-commission-1"
    binding["profileRevision"] = check.profile_revision(profile)
    write(path.parent / "profile.json", profile)
    write(path.parent / "gateway.json", gateway)
    write(path, binding)
    result = check.inspect_binding(path)
    assert result["configurationComplete"] and not result["unresolvedFields"]
    assert result["graphSnapshotStatus"] == "not_supplied"
    assert not result["motionAuthorized"] and not result["hardwareValidated"]


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "wrong_kind"])
def test_vendor_endpoint_contract_is_explicit(fixture, mutation):
    path, binding, _, _ = fixture
    endpoints = binding["requiredVendorInterfaces"]
    if mutation == "missing":
        endpoints.clear()
    elif mutation == "duplicate":
        endpoints.append(copy.deepcopy(endpoints[0]))
    else:
        endpoints[0]["rosType"] = "nav2_msgs/msg/NavigateToPose"
    write(path, binding)
    with pytest.raises(ValueError):
        check.inspect_binding(path)


def graph_for(path):
    result = check.inspect_binding(path)
    return {"schemaVersion": "ros2.interface.inventory.v1", "robotId": result["robotId"],
            "namespace": result["namespace"], "rosDomainId": result["rosDomainId"],
            "observedAtUnixMs": 100000, "endpoints": result["expectedEndpoints"]}


def test_fresh_graph_types_and_roles_match_without_granting_control(fixture):
    path, _, _, _ = fixture
    graph_path = write(path.parent / "graph.json", graph_for(path))
    before = {file.name: file.read_bytes() for file in path.parent.iterdir()}
    result = check.inspect_binding(path, graph_path=graph_path, now_unix_ms=100001)
    assert result["graphSnapshotStatus"] == "snapshot_matches"
    assert not result["hardwareValidated"] and not result["motionAuthorized"]
    assert result["missingEndpoints"] == []
    assert before == {file.name: file.read_bytes() for file in path.parent.iterdir()}


@pytest.mark.parametrize("field,value", [("rosDomainId", 89), ("robotId", "wrong-robot"),
                                         ("namespace", "/wrong_robot"),
                                         ("observedAtUnixMs", 1),
                                         ("observedAtUnixMs", 100002)])
def test_unrelated_old_or_future_graph_is_rejected(fixture, field, value):
    path, _, _, _ = fixture
    graph = graph_for(path)
    graph[field] = value
    graph_path = write(path.parent / "graph.json", graph)
    with pytest.raises(ValueError):
        check.inspect_binding(path, graph_path=graph_path, now_unix_ms=100001)


@pytest.mark.parametrize("mutation", ["missing", "wrong_type", "wrong_role"])
def test_discovery_with_missing_or_wrong_endpoint_is_not_compatible(fixture, mutation):
    path, _, _, _ = fixture
    graph = graph_for(path)
    if mutation == "missing":
        graph["endpoints"].pop()
    elif mutation == "wrong_type":
        graph["endpoints"][1]["rosType"] = "std_msgs/msg/String"
    else:
        graph["endpoints"][1]["role"] = "subscriber"
    graph_path = write(path.parent / "graph.json", graph)
    result = check.inspect_binding(path, graph_path=graph_path, now_unix_ms=100001)
    assert result["graphSnapshotStatus"] == "mismatch" and result["missingEndpoints"]


def test_duplicate_json_key_is_not_silently_overwritten(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text('{"robotId":"one","robotId":"two"}')
    with pytest.raises(ValueError, match="duplicate"):
        check.inspect_binding(path)


def test_fifo_input_is_rejected_without_waiting_for_writer(tmp_path):
    path = tmp_path / "fifo"
    os.mkfifo(path)
    with pytest.raises(ValueError, match="regular file"):
        check.inspect_binding(path)


def test_cli_runs_without_ros_install_and_reports_invalid_input(tmp_path):
    path = write(tmp_path / "bad.json", {})
    result = subprocess.run([sys.executable, str(ROOT / "scripts/ros2_device_preflight.py"),
                             str(path)], capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 1
    output = json.loads(result.stdout)
    assert not output["configurationValid"] and output["readOnly"]
    assert not output["motionAuthorized"] and not output["hardwareValidated"]
