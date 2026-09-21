"""Agent-facing mapping: reuse what exists, survey only when nothing does.

The behaviour under test is the one a user gets by saying "please explore the
environment and build a global map". Three things have to hold for that sentence
to work: the decision is made from verified evidence, an existing map is reused
instead of silently re-surveyed, and when the request could mean more than one map
the answer is a question rather than a guess.
"""

import numpy as np
import pytest
from tangying_robot_gateway.map_pipeline import PointCloud, build_map
from tangying_robot_gateway.map_selection import (
    AMBIGUOUS,
    EXPLORE,
    REUSE,
    MapRecord,
    select_map,
)
from tangying_robot_gateway.tools.mapping import build_mapping_tools

REVISION = "a" * 64


# --- the decision rule ------------------------------------------------------


def test_no_map_means_survey():
    selection = select_map(available=[])
    assert selection.decision == EXPLORE and selection.should_explore
    assert "探索" in selection.message or "建图" in selection.message
    # The report always carries the evidence, including "none": a caller that
    # cannot tell "no map exists" from "I could not look" starts a survey over a
    # house that is already mapped.
    assert selection.as_report()["availableMaps"] == []


def test_an_existing_map_is_reused_rather_than_surveyed():
    """The user said "explore"; the robot already has a map. Surveying anyway is a
    twenty-minute drive to learn what is already known."""
    selection = select_map(available=[MapRecord("scan-a", "家庭地图", 100)])
    assert selection.decision == REUSE and selection.should_reuse
    assert selection.map_id == "scan-a"
    assert "家庭地图" in selection.message


def test_the_active_map_wins_over_a_newer_one():
    """Continuing where the robot already is beats switching maps underneath it."""
    selection = select_map(available=[MapRecord("scan-new", "新地图", 200),
                                      MapRecord("scan-old", "旧地图", 100)],
                           active_map_id="scan-old")
    assert selection.map_id == "scan-old"


def test_a_named_place_selects_the_map_that_covers_it():
    """An operator says "explore the living room"; the package is called 客厅地图.

    Substring rather than equality, because refusing that match would send the
    robot to survey a room it already has a map of.
    """
    selection = select_map(available=[MapRecord("scan-a", "客厅地图", 100),
                                      MapRecord("scan-b", "厨房地图", 90)],
                           requested="客厅")
    assert selection.decision == REUSE
    assert selection.map_id == "scan-a"


def test_a_place_matching_several_maps_asks_instead_of_guessing():
    """A wrong map is worse than a question: it is a map of the wrong room, and
    every later plan is built on it."""
    selection = select_map(available=[MapRecord("scan-a", "客厅-东", 100),
                                      MapRecord("scan-b", "客厅-西", 90)],
                           requested="客厅")
    assert selection.decision == AMBIGUOUS
    assert {record.map_id for record in selection.candidates} == {"scan-a", "scan-b"}
    assert "scan-a" in selection.message and "scan-b" in selection.message


def test_a_place_with_no_map_surveys_that_place():
    """Substring matching over Chinese names over-matches on shared characters -
    a known limit, noted here so it is not mistaken for correctness. It is
    tolerable because a false match can only ever *narrow* to a reuse the operator
    can see named in the reply, and a false negative starts a survey."""
    selection = select_map(available=[MapRecord("scan-a", "一楼平面图", 100)],
                           requested="车库")
    assert selection.decision == EXPLORE


def test_an_explicitly_named_map_is_used_even_if_a_better_candidate_exists():
    """An instruction outranks a heuristic. Second-guessing it would be ignoring
    what the caller asked for."""
    selection = select_map(available=[MapRecord("scan-a", "客厅", 100),
                                      MapRecord("scan-b", "厨房", 200)],
                           requested_map_id="scan-a")
    assert selection.decision == REUSE and selection.map_id == "scan-a"


def test_an_explicitly_named_map_that_is_gone_is_refused_not_surveyed():
    """"The map you asked for is gone" and "there is no map" are different
    answers, and only one of them is true."""
    selection = select_map(available=[MapRecord("scan-a", "客厅", 100)],
                           requested_map_id="scan-missing")
    assert "scan-missing" in selection.message
    assert selection.decision == EXPLORE


# --- the workflow that answers it -------------------------------------------


def package(root, map_id, *, revision=REVISION, robot_id="unit-1", name="家庭地图",
            created_at_unix_ms=None):
    """A real, verified, *activatable* map package.

    The session document carries the fields activation checks, not just a name: a
    package that lists but cannot be loaded would leave the reuse branch - the
    whole point of the feature - untested.
    """
    cloud = PointCloud(np.array([[0, 0, 0], [2, 2, 1]], dtype=np.float32),
                       np.zeros((2, 3), dtype=np.uint8))
    grid = {"width": 20, "height": 20, "resolution": .1, "origin": [0, 0, 0],
            "cells": np.zeros((20, 20), dtype=int)}
    metadata = {
        "name": name,
        "schemaVersion": "slam.session.v1",
        "navigationEvidenceVersion": 2,
        "robotId": robot_id,
        "mapId": map_id,
        "calibrationRevision": revision,
        # Must agree with the workflow's own world frame, or activation is refused
        # as a localisation mismatch - correct, but not what is under test here.
        "worldFrameRevision": "stable-driver-frame",
        "mapFromWorld": [0.0, 0.0, 0.0],
    }
    if created_at_unix_ms is not None:
        metadata["createdAtUnixMs"] = created_at_unix_ms
    return build_map(root / map_id, map_id=map_id, robot_id=robot_id, cloud=cloud,
                     calibration_revision=revision, lod_levels=1, occupancy_grid=grid,
                     slam_metadata=metadata)


def workflow_for(tmp_path, *, calibration_revision=REVISION, starts=None):
    from tangying_robot_gateway.robot_workflow import RobotWorkflow
    from tangying_robot_gateway.service_registry import ServiceError
    owner = [None]

    def reserve():
        if owner[0] is not None:
            raise ServiceError("ROBOT_BUSY", "busy")
        owner[0] = object()
        return owner[0]

    def release(token):
        if owner[0] is token:
            owner[0] = None

    workflow = RobotWorkflow(
        robot_id="unit-1", root=tmp_path,
        calibration_get=lambda: {"revision": calibration_revision},
        calibration_run=lambda: {"revision": calibration_revision},
        calibration_save=lambda *args: {"revision": calibration_revision},
        capture=lambda: None, move=lambda *args, **kwargs: {"ok": True},
        reserve=reserve, release=release, survey_goals=list,
        semantic_workspaces=lambda anchor: [], world_frame_revision="stable-driver-frame")
    if starts is not None:
        workflow.start = starts
    return workflow


def test_the_inventory_lists_verified_maps_and_nothing_else(tmp_path):
    """A package built against another calibration describes another world, and
    offering it is how an agent gets talked into activating it."""
    package(tmp_path, "scan-good", name="客厅地图")
    package(tmp_path, "scan-other-robot", robot_id="unit-2")
    package(tmp_path, "scan-other-calibration", revision="b" * 64)
    workflow = workflow_for(tmp_path)
    inventory = workflow.map_inventory()
    assert [item["mapId"] for item in inventory["availableMaps"]] == ["scan-good"]
    assert inventory["availableMaps"][0]["name"] == "客厅地图"
    assert inventory["activeMapId"] == ""


def test_the_inventory_sees_maps_under_a_group_directory(tmp_path):
    """The reference scene ships its surveys under a group; a plain survey does not.

    Both are the same kind of package. Walking only the root's own children left every
    grouped map invisible to the reuse-or-survey decision, so an agent asked to go
    somewhere in the reference house would start a fresh survey while the map it
    wanted was already on disk.
    """
    package(tmp_path, "scan-flat")
    group = tmp_path / "furnished-home"
    group.mkdir()
    package(group, "scan-grouped", name="分组地图")
    # A group directory that holds no package is not a map and has no name to find.
    (group / "empty-group").mkdir()

    inventory = workflow_for(tmp_path).map_inventory()
    available = {item["mapId"]: item["name"] for item in inventory["availableMaps"]}
    assert set(available) == {"scan-flat", "scan-grouped"}, available
    assert available["scan-grouped"] == "分组地图"


def test_a_corrupt_package_is_skipped_rather_than_failing_the_listing(tmp_path):
    """One bad directory is a fact about that directory. Refusing to list anything
    because of it hides the maps that are fine - exactly when the list is needed."""
    package(tmp_path, "scan-good")
    broken = tmp_path / "scan-broken"
    broken.mkdir()
    (broken / "manifest.json").write_text("{not json")
    workflow = workflow_for(tmp_path)
    assert [item["mapId"] for item in workflow.map_inventory()["availableMaps"]] == ["scan-good"]


def test_no_map_starts_an_exploration_survey(tmp_path):
    calls = []
    workflow = workflow_for(tmp_path, starts=lambda parameters: calls.append(parameters) or {"state": "moving"})
    report = workflow.ensure_map({})
    assert report["decision"] == EXPLORE and report["started"] is True
    assert calls and calls[0]["mode"] == "explore", "建图必须走自动探索模式"


def test_an_existing_map_is_reused_and_no_survey_starts(tmp_path):
    """The whole point of the request: the user asked for a map, not for a drive."""
    package(tmp_path, "scan-a", name="家庭地图")
    calls = []
    workflow = workflow_for(tmp_path, starts=lambda parameters: calls.append(parameters))
    report = workflow.ensure_map({})
    assert report["decision"] == REUSE and report["started"] is False
    assert calls == [], "an existing map must not be re-surveyed"
    assert report["mapId"] == "scan-a"


def test_an_ambiguous_place_is_answered_with_the_choice(tmp_path):
    package(tmp_path, "scan-east", name="客厅-东")
    package(tmp_path, "scan-west", name="客厅-西")
    calls = []
    workflow = workflow_for(tmp_path, starts=lambda parameters: calls.append(parameters))
    report = workflow.ensure_map({"environment": "客厅"})
    assert report["decision"] == AMBIGUOUS
    assert report["started"] is False
    assert {item["mapId"] for item in report["matches"]} == {"scan-east", "scan-west"}
    assert calls == [], "an ambiguous request must not start driving"


# --- the tool the model calls ------------------------------------------------


def test_build_map_tool_reports_the_decision_it_did_not_make():
    """The tool asks; the deployment answers. A tool that decided for itself would
    be a second authority on which map the robot is using."""
    seen = []

    def ensure(arguments):
        seen.append(arguments)
        return {"decision": "explore", "message": "开始探索", "mapId": "",
                "availableMaps": [], "matches": [], "started": True}

    tool = next(item for item in build_mapping_tools(None, None, ensure)
                if item.name == "build_map")
    result = tool.execute(environment="客厅", max_travel_m=40)
    assert result.success
    assert result.data["surveyStarted"] is True
    assert seen[0]["environment"] == "客厅" and seen[0]["maxTravelM"] == 40


def test_build_map_tool_is_not_a_retryable_failure_without_a_deployment():
    """A commissioning gap is not a transient fault: telling a model it may retry
    turns one missing provider into a retry loop."""
    tool = next(item for item in build_mapping_tools(None, None, None)
                if item.name == "build_map")
    result = tool.execute()
    assert not result.success
    assert result.recoverable is False


def test_build_map_tool_surfaces_a_choice_rather_than_a_failure():
    """An ambiguous answer is the tool working: it must not be reported as an
    error the model should retry or apologise for."""
    tool = next(item for item in build_mapping_tools(
        None, None, lambda arguments: {"decision": "ambiguous", "message": "选一张",
                                       "started": False, "matches": [], "availableMaps": []})
        if item.name == "build_map")
    result = tool.execute(environment="客厅")
    assert result.success and result.data["requiresChoice"] is True


def test_build_map_tool_refuses_a_lying_provider():
    tool = next(item for item in build_mapping_tools(None, None, lambda arguments: "explore")
                if item.name == "build_map")
    result = tool.execute()
    assert not result.success and result.recoverable is False


@pytest.mark.parametrize("name", ["build_map", "plan_work_area"])
def test_the_mapping_surface_is_visible_to_the_model(name):
    """A capability the model cannot see is a capability the operator has to spell
    out as a sequence of gateway calls - which is what this replaces."""
    tools = build_mapping_tools(None, None, lambda arguments: {})
    assert name in {tool.name for tool in tools}
    tool = next(item for item in tools if item.name == name)
    assert tool.llm_visibility == "primary"
