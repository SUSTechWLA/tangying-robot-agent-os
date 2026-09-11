"""The exported catalogue must stay in step with the tools the runtime has.

Two failure modes matter: advertising a tool that is not registered (the model
calls something that cannot run), and letting the generated file drift from the
definitions. Both are checked here rather than by review.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tangying_robot_gateway.llm_tools import DEFAULT_OUTPUT, build_catalog, render
from tangying_robot_gateway.semantic_map import SemanticMap
from tangying_robot_gateway.tools import build_registry

from .fake_adapter import FakeRobotAdapter

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def catalog() -> dict:
    return build_catalog()


def test_catalog_is_valid_openai_function_calling(catalog):
    assert catalog["schema_version"] == "llm.tools.v1"
    names = []
    for entry in catalog["tools"]:
        assert entry["type"] == "function"
        function = entry["function"]
        assert set(function) == {"name", "description", "parameters"}
        assert function["name"] == function["name"].lower()
        assert "." not in function["name"]
        parameters = function["parameters"]
        assert parameters["type"] == "object"
        # A no-argument tool legitimately omits "required"; OpenAI accepts that,
        # so the invariant is that anything required is also declared.
        assert set(parameters.get("required", ())) <= set(parameters.get("properties", {}))
        assert function["description"].strip()
        names.append(function["name"])
    assert len(names) == len(set(names)), "duplicate tool names"


def test_every_advertised_tool_is_registered_and_routable(catalog):
    registry = build_registry(FakeRobotAdapter(), SemanticMap.from_file())
    for entry in catalog["tools"]:
        name = entry["function"]["name"]
        assert name in registry, f"{name} is advertised but not registered"
        tool = registry.require(name)
        assert tool.distributed_node, name
        assert tool.timeout_s > 0, name


def test_metadata_covers_the_requested_contract_fields(catalog):
    for name, meta in catalog["metadata"].items():
        assert set(meta) >= {"safety_level", "timeout_s", "distributed_node", "idempotent",
                             "mutates_world", "returns"}
        assert isinstance(meta["mutates_world"], bool)
        assert 0 <= meta["safety_level"] <= 4, name
        assert meta["timeout_s"] > 0, name


def test_descriptions_state_recovery_and_the_result_contract(catalog):
    for entry in catalog["tools"]:
        description = entry["function"]["description"]
        assert "error_code" in description, entry["function"]["name"]
        assert "recoverable" in description, entry["function"]["name"]


def test_coordinate_level_tools_are_withheld_from_the_model(catalog):
    advertised = {entry["function"]["name"] for entry in catalog["tools"]}
    assert "move_arm_to_joints" not in advertised
    assert "navigate_to_pose" not in advertised
    assert set(catalog["excluded_from_llm"]) == {"move_arm_to_joints", "navigate_to_pose"}
    # Semantic-first entry points are present instead.
    assert {"navigate_to", "move_arm_to_pose", "pick_object"} <= advertised


def test_committed_tools_json_is_up_to_date():
    """A stale catalogue would silently advertise an older contract."""

    assert DEFAULT_OUTPUT.exists(), "tools.json is missing; run llm_tools --write"
    assert DEFAULT_OUTPUT.read_text() == render(build_catalog()), (
        "tools.json is stale; run: python -m tangying_robot_gateway.llm_tools --write"
    )


def test_canonical_contracts_still_export_their_own_schemas():
    """The existing schema command keeps working; both surfaces coexist."""

    from tangying_robot_gateway.contracts import contract_schemas

    schemas = contract_schemas()
    assert {"robot.profile.v1", "scene.reconstruction.v1", "tools"} <= set(schemas)
    # The runtime tool names remain the transport vocabulary.
    assert "manipulation.pick" in schemas["tools"]


def test_catalog_generation_needs_no_robot(catalog):
    """Export is offline: it must not require a reachable adapter."""

    assert catalog["tools"], "catalogue should be non-empty without hardware"


def test_llm_tools_check_mode_detects_drift(tmp_path, capsys):
    from tangying_robot_gateway.llm_tools import main

    stale = tmp_path / "tools.json"
    stale.write_text("{}\n")
    assert main(["--check", "--output", str(stale)]) == 1
    assert "stale" in capsys.readouterr().err

    fresh = tmp_path / "fresh.json"
    assert main(["--write", "--output", str(fresh)]) == 0
    assert main(["--check", "--output", str(fresh)]) == 0


def test_tools_json_is_tracked_at_the_repository_root(catalog):
    """The brief asks for tools.json; it must be where a client can find it."""

    assert DEFAULT_OUTPUT == REPO / "tools.json"
    on_disk = json.loads(DEFAULT_OUTPUT.read_text())
    assert len(on_disk["tools"]) == len(catalog["tools"])
