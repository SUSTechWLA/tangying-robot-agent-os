import hashlib
import io
import json

import pytest

from scripts import run_home_task_suite as suite

# One mug-transfer as the deterministic planner writes it: a step id and the tool
# that step invokes.
#
# The suite asserts on the tool, not on the name, so this fixture could use any
# names at all — it keeps the real ones because a fixture that looks like the
# thing it stands for is easier to correct when the thing changes. The model-planned
# version of the same task names these steps observe-start / navigate-kitchen / …,
# and the suite has to pass for both.
FIXTURE_STEPS = [
    ("observe", "observe_scene"),
    ("pre_position", "navigation.pre_position"),
    ("navigate_01", "navigation.navigate"),
    ("verify_arrival_01", "verify_arrival"),
    ("observe_after_navigation", "observe_scene"),
    ("resolve", "resolve_targets"),
    ("plan_grasp", "plan_grasp"),
    ("pick", "manipulation.pick"),
    ("verify_grasp", "verify_grasp"),
    ("place", "manipulation.place"),
    ("verify_place", "verify_placement"),
    ("navigate_02", "navigation.navigate"),
    ("verify_arrival_02", "verify_arrival"),
]


@pytest.mark.parametrize("base", ["https://127.0.0.1", "http://example.com", "http://localhost/task"])
def test_household_suite_requires_an_explicit_local_origin(base):
    with pytest.raises(ValueError):
        suite.local_base(base)


def _api_fixture(monkeypatch, *, corrupt_hash=False, wrong_capture=False, wrong_map=False,
                 missing_map=False, missing_route=False, catalogue=None, steps=None):
    steps = FIXTURE_STEPS if steps is None else steps
    active_map = {"mapId": "measured-map", "mapRevision": "a"*64, "calibrationRevision": "b"*64}
    if catalogue is None:
        catalogue = [{"id": name, "category": category, "workArea": "kitchen", "attributes": {}}
                     for name, category in (("ceramic-mug", "cup"), ("kitchen-tray", "storage_bin"))]
    raw = b"fixture-camera-png"
    digest = hashlib.sha256(raw).hexdigest()
    records = [{"id": str(index), "stepId": step, "rgbSha256": digest,
                "depthSha256": "incorrect" if corrupt_hash else digest}
               for index, (step, _) in enumerate(steps)]
    details = {record["id"]: {
        "stepId": record["stepId"], "captureId": "capture-"+record["id"],
        "snapshot": {"robotState": {"verification": {
            "passed": True, "observed_relation": "inside:kitchen-tray",
            "object_id": "ceramic-mug", "sample_count": 3, "stable_duration_s": .15,
        }}},
    } for record in records}
    tools = {step: tool for step, tool in steps}
    for record in records:
        if tools[record["stepId"]] == "navigation.navigate":
            details[record["id"]]["snapshot"]["robotState"]["map_route"] = {
                **active_map, "mapId": "wrong-map" if wrong_map else active_map["mapId"]}
    if missing_route:
        # One valid navigation cannot hide a second one with no map evidence: the
        # suite counts the legs the robot actually drove, not the steps it named.
        index = str([step for step, _ in steps].index("navigate_02"))
        details[index]["snapshot"]["robotState"].pop("map_route")
    task = {"id": "task-1", "state": "SUCCEEDED", "events": [
        {"type": "TOOL_ACTIVITY", "stepId": record["stepId"], "payload": {
            "activityStatus": "CONFIRMED", "toolName": tools[record["stepId"]],
            "evidenceSource": "command_observation",
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
    # Two images per capturing step, one colour and one depth. The count moved
    # from 24 to 26 when every mobile plan gained its preamble: `observe` runs
    # before the base moves, and it captures. The number is pinned here on
    # purpose — it is what makes a plan that quietly stops taking pictures fail.
    assert result["results"][0]["tasks"][0]["verifiedImageCount"] == 26
    assert len(list((tmp_path/"run"/"mug-transfer-1").glob("*.png"))) == 26
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


# The reason this suite was rewritten.
#
# It used to compare `step_ids` against a list of names the deterministic planner
# produces. On a stack with a model configured the same request comes back with
# completely different names — the run that exposed this produced
# ["observe-start", "navigate-bedroom", "verify-bedroom", ...] for a patrol — and a
# task that was doing exactly the right thing failed with "unexpected confirmed
# steps".
#
# docs/architecture/orchestration-post-training.md states the rule the suite had
# been breaking: assert the outcome, not the implementation, and it records that
# the evaluator itself was taught that by a real failure.
MODEL_PLANNED_STEPS = [
    ("observe-start", "observe_scene"),
    ("settle-before-driving", "navigation.pre_position"),
    ("navigate-kitchen", "navigation.navigate"),
    ("verify-kitchen", "verify_arrival"),
    ("look-around-kitchen", "observe_scene"),
    ("find-the-mug", "resolve_targets"),
    ("work-out-the-grasp", "plan_grasp"),
    ("grab-it", "manipulation.pick"),
    ("check-the-grip", "verify_grasp"),
    ("drop-it-in-the-tray", "manipulation.place"),
    ("confirm-the-drop", "verify_placement"),
    ("head-back", "navigation.navigate"),
    ("confirm-home", "verify_arrival"),
]


def test_a_model_planned_task_passes_whatever_its_steps_are_called(tmp_path, monkeypatch):
    """Same task, a planner that names everything differently, same verdict."""
    _api_fixture(monkeypatch, steps=MODEL_PLANNED_STEPS)
    result = suite.run("http://localhost:8897", tmp_path/"run", ["mug-transfer"])
    assert result["passed"]
    assert result["results"][0]["tasks"][0]["confirmedTools"].count("navigation.navigate") == 2


def test_the_suite_still_fails_a_task_that_skipped_a_required_tool(tmp_path, monkeypatch):
    """Planner-agnostic is not the same as permissive.

    The point of asserting on tools is that a plan cannot buy a pass by renaming
    its steps — or by leaving one out. A transfer that never verified the
    placement has not established the thing the suite exists to establish.
    """
    without_verification = [(step, tool) for step, tool in MODEL_PLANNED_STEPS
                            if tool != "verify_placement"]
    _api_fixture(monkeypatch, steps=without_verification)
    with pytest.raises(AssertionError, match="verify_placement"):
        suite.run("http://localhost:8897", tmp_path/"run", ["mug-transfer"])
