"""Cloud observability acceptance over the production Fleet boundaries."""

from __future__ import annotations

from tests.e2e.fleet_harness import FleetHandoffStack

pytest_plugins = ("tests.e2e.fleet_harness",)


def test_cloud_registers_both_robots_and_keeps_catalogs_separate(
    fleet_handoff_stack: FleetHandoffStack,
):
    devices = fleet_handoff_stack.api("/v1/devices")
    assert {device["robotId"] for device in devices if device["online"]} == {
        "robot-1",
        "robot-2",
    }
    for device in devices:
        assert len(device["toolCatalogRevision"]) == 64
        assert len(device["observationCatalogRevision"]) == 64
        assert device["toolCatalogRevision"] != device["observationCatalogRevision"]
        assert any(tool["name"] == "manipulation.pick" for tool in device["toolCatalog"])
        assert {source["sourceId"] for source in device["observationSources"]} == {
            f"{device['robotId']}/proprioception",
            f"{device['robotId']}/scene",
        }


def test_cloud_exposes_live_telemetry_map_frames_and_authoritative_world(
    fleet_handoff_stack: FleetHandoffStack,
):
    stack = fleet_handoff_stack
    for robot_id in ("robot-1", "robot-2"):
        telemetry = stack.api(f"/v1/telemetry?robot_id={robot_id}")
        assert telemetry["hasLatest"]
        assert telemetry["latest"]["pose"]
        assert telemetry["latest"]["entities"]

    global_map = stack.api("/v1/maps/global")
    assert len(global_map["robots"]) == 2
    assert global_map["width"] > 0 and global_map["height"] > 0
    assert global_map["entities"]

    frames = stack.api("/v1/scene/frames")["frames"]
    assert {frame["robotId"] for frame in frames} == {"robot-1", "robot-2"}
    assert all(frame["mediaType"] == "image/png" and frame["bytes"] > 1000 for frame in frames)

    world = stack.api("/v1/world")
    assert world["schemaVersion"] == "world.snapshot.v1"
    assert world["revision"] > 0 and world["eventCursor"]
    assert set(world["robots"]) == {"robot-1", "robot-2"}
    assert "red-block" in world["entities"]
