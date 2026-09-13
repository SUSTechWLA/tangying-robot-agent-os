import hashlib
import io
import json

import pytest

from scripts import run_home_task_suite as suite


@pytest.mark.parametrize("base", ["https://127.0.0.1", "http://example.com", "http://localhost/task"])
def test_household_suite_requires_an_explicit_local_origin(base):
    with pytest.raises(ValueError):
        suite.local_base(base)


def _api_fixture(monkeypatch, *, corrupt_hash=False, wrong_capture=False, wrong_map=False,
                 missing_map=False, missing_route=False, catalogue=None):
    active_map = {"mapId": "measured-map", "mapRevision": "a"*64, "calibrationRevision": "b"*64}
    if catalogue is None:
        catalogue = [{"id": name, "category": category, "workArea": "kitchen", "attributes": {}}
                     for name, category in (("ceramic-mug", "cup"), ("kitchen-tray", "storage_bin"))]
    raw = b"fixture-camera-png"
    digest = hashlib.sha256(raw).hexdigest()
    records = [{"id": str(index), "stepId": step, "rgbSha256": digest,
                "depthSha256": "incorrect" if corrupt_hash else digest}
               for index, step in enumerate(suite.TRANSFER_STEPS)]
    details = {record["id"]: {
        "stepId": record["stepId"], "captureId": "capture-"+record["id"],
        "snapshot": {"robotState": {"verification": {
            "passed": True, "observed_relation": "inside:kitchen-tray",
            "object_id": "ceramic-mug", "sample_count": 3, "stable_duration_s": .15,
        }}},
    } for record in records}
    for record in records:
        if record["stepId"].startswith("navigate_"):
            details[record["id"]]["snapshot"]["robotState"]["map_route"] = {
                **active_map, "mapId": "wrong-map" if wrong_map else active_map["mapId"]}
    if missing_route:
        # One valid navigation cannot hide a second one with no map evidence.
        details[str(suite.TRANSFER_STEPS.index("navigate_02"))]["snapshot"]["robotState"].pop("map_route")
    task = {"id": "task-1", "state": "SUCCEEDED", "events": [
        {"type": "TOOL_ACTIVITY", "stepId": record["stepId"], "payload": {
            "activityStatus": "CONFIRMED", "evidenceSource": "command_observation",
            "receiptObservationId": "wrong-capture" if wrong_capture else "capture-"+record["id"],
        }} for record in records
    ]}
    calls = []

    def fake_urlopen(request, timeout):
        url = request if isinstance(request, str) else request.full_url
        calls.append(url)
        if url.endswith(("/rgb", "/depth")):
            return io.BytesIO(raw)
        if "/telemetry?" in url:
            data = {"hasLatest": True, "latest": {"robotState": {
                "active_map": {} if missing_map else active_map,
                "semantic_objects": catalogue,
                "perception": {"detector": "rgbd-household-metric-shape-v1", "ground_truth_fallback": False},
                "navigation": {"scene": "home_task"},
            }}}
        elif url.endswith("observations?limit=200"):
            data = {"records": records}
        elif "/observations/" in url:
            data = details[url.rsplit("/", 1)[1]]
        else:
            data = task
        return io.BytesIO(json.dumps(data).encode())

    monkeypatch.setattr(suite, "urlopen", fake_urlopen)
    return calls


def test_household_suite_pins_each_confirmation_and_preserves_original_camera_bytes(tmp_path, monkeypatch):
    calls = _api_fixture(monkeypatch)
    result = suite.run("http://localhost:8897", tmp_path/"run", ["mug-transfer"])
    assert result["passed"]
    assert result["results"][0]["tasks"][0]["verifiedImageCount"] == 24
    assert len(list((tmp_path/"run"/"mug-transfer-1").glob("*.png"))) == 24
    assert sum(url.endswith("/approve") for url in calls) == 1


@pytest.mark.parametrize("fault", ["corrupt_hash", "wrong_capture", "wrong_map", "missing_route"])
def test_household_suite_never_passes_unpinned_or_corrupted_evidence(tmp_path, monkeypatch, fault):
    calls = _api_fixture(monkeypatch, **{fault: True})
    with pytest.raises(AssertionError):
        suite.run("http://localhost:8897", tmp_path/"run", ["mug-transfer"])
    report = json.loads((tmp_path/"run"/"summary.json").read_text())
    assert report["passed"] is False
    assert sum(url.endswith("/approve") for url in calls) == 1


def test_household_suite_requires_a_saved_active_map_before_any_physical_approval(tmp_path, monkeypatch):
    calls = _api_fixture(monkeypatch, missing_map=True)
    with pytest.raises(AssertionError, match="activated saved map"):
        suite.run("http://localhost:8897", tmp_path/"run", ["mug-transfer"])
    assert not any(url.endswith("/approve") for url in calls)


@pytest.mark.parametrize("fault", ["legacy_cups", "wrong_tray", "duplicate_cup", "wrong_area"])
def test_household_suite_rejects_stale_action_catalogue_before_creating_tasks(tmp_path, monkeypatch, fault):
    catalogue = [{"id": "ceramic-mug", "category": "cup", "workArea": "kitchen", "attributes": {}},
                 {"id": "kitchen-tray", "category": "storage_bin", "workArea": "kitchen", "attributes": {}}]
    if fault == "legacy_cups":
        catalogue = [{"id": color+"-cup", "category": "cup", "workArea": "kitchen",
                      "attributes": {"color": color}} for color in ("red", "blue", "green")]+catalogue[1:]
    elif fault == "wrong_tray":
        catalogue[1]["id"] = "kitchen-bin"
    elif fault == "duplicate_cup":
        catalogue.append({**catalogue[0], "id": "another-cup"})
    else:
        catalogue[0]["workArea"] = "bedroom"
    calls = _api_fixture(monkeypatch, catalogue=catalogue)
    with pytest.raises(AssertionError, match="action catalogue"):
        suite.run("http://localhost:8897", tmp_path/"run", ["patrol", "mug-transfer"])
    assert not any(url.endswith(("/v1/tasks", "/approve")) for url in calls)
