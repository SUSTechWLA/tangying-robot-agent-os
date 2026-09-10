"""The mutates_world contract must reach every consumer that gates on it.

The Agent refuses to record a physical write as done without fresh
post-command evidence, and the runtime's own capability declaration is what
tells it which tools are writes. If that flag were lost in transport, a new
adapter tool could silently skip post-condition verification.
"""

from __future__ import annotations

import pytest
from tangying_robot_gateway.contracts import MUTATES_WORLD_TOOLS, PHYSICAL_TOOLS
from tangying_sim.rgbd_runtime import RgbdRuntimeService, RgbdTabletopWorld


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.delenv("TANGYING_NAVIGATION_URL", raising=False)
    service = RgbdRuntimeService(RgbdTabletopWorld.seeded(7))
    yield service
    service.close()


@pytest.fixture
def mobile_runtime(monkeypatch):
    """A mobile deployment advertises navigation, which is also a write tool."""

    monkeypatch.setenv("TANGYING_NAVIGATION_URL", "http://127.0.0.1:8899")
    monkeypatch.setenv("TANGYING_NAVIGATION_TOKEN", "local-test-token")
    service = RgbdRuntimeService(RgbdTabletopWorld.seeded(7))
    yield service
    service.close()


def test_capabilities_declare_world_mutation(runtime):
    info = runtime.GetRuntimeInfo(None, None)
    declared = {item.name: item for item in info.capabilities}

    # Every write tool the Agent must verify is declared, and every read tool is
    # not, so the closure gate follows the contract rather than a name list.
    for name in ("manipulation.pick", "manipulation.place", "recover_to_safe_pose"):
        assert declared[name].mutates_world is True, name
    for name in ("observe_scene", "resolve_targets", "plan_grasp", "verify_grasp", "verify_placement", "verify_arrival"):
        assert declared[name].mutates_world is False, name

    # Emergency stop is physical but not a scene mutation: its effect is the
    # latched stop state, which the runtime reports directly.
    assert declared["emergency_stop"].safety_level == "physical_motion"
    assert declared["emergency_stop"].mutates_world is False

    # The fixed workcell does not advertise a base tool at all.
    assert "navigation.navigate" not in declared


def test_mobile_deployment_declares_navigation_as_a_world_mutation(mobile_runtime):
    info = mobile_runtime.GetRuntimeInfo(None, None)
    declared = {item.name: item for item in info.capabilities}
    assert declared["navigation.navigate"].mutates_world is True
    assert declared["navigation.navigate"].safety_level == "physical_motion"


def test_every_mutating_tool_is_also_physical(runtime):
    info = runtime.GetRuntimeInfo(None, None)
    for item in info.capabilities:
        if item.mutates_world:
            assert item.safety_level == "physical_motion", item.name
            assert item.name in PHYSICAL_TOOLS, item.name


def test_python_and_go_tool_classification_agree(runtime):
    """The running adapter must agree with the canonical Python contract."""

    info = runtime.GetRuntimeInfo(None, None)
    declared = {item.name for item in info.capabilities if item.mutates_world}
    assert declared == set(MUTATES_WORLD_TOOLS) & {item.name for item in info.capabilities}
