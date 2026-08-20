import pytest
from tangying_sim.shared_handoff import (
    SharedHandoffBridge,
    StaleHandoffToken,
    seeded_handoff_worlds,
)


def test_fleet_runtime_services_share_the_same_bridge():
    from tangying_sim.fleet_server import create_fleet_services

    bridge, services = create_fleet_services(seed=19)

    assert set(services) == {"robot-1", "robot-2"}
    assert services["robot-1"].world._shared_handoff is bridge
    assert services["robot-2"].world._shared_handoff is bridge

    assert services["robot-1"].world.pick("red-block").success
    assert services["robot-1"].world.place("handoff-zone").success
    assert services["robot-2"]._resource_grants[bridge.RESOURCE_ID] == (
        "robot-2",
        bridge.fencing_token,
    )
    token = bridge.fencing_token
    assert services["robot-2"].world.pick("red-block").success
    assert bridge.fencing_token == token


def _ids(world):
    return {entity.entity_id for entity in world.entities()}


def test_one_logical_block_moves_from_sender_through_handoff_to_receiver():
    bridge, sender, receiver = seeded_handoff_worlds(seed=7)

    assert bridge.owner == "robot-1"
    assert "red-block" in _ids(sender)
    assert "red-block" not in _ids(receiver)

    assert sender.pick("red-block").success
    assert sender.place("handoff-zone").success
    assert sender.verify_inside("red-block", "handoff-zone").success

    assert bridge.owner == "environment"
    assert bridge.custodian == "robot-2"
    assert "red-block" not in _ids(sender)
    assert "red-block" in _ids(receiver)
    assert receiver.resolve(category="block", color="red").position == pytest.approx(
        bridge.handoff_position, abs=0.02
    )

    assert receiver.pick("red-block").success
    assert bridge.owner == "robot-2"
    assert receiver.place("right-target-zone").success
    assert bridge.owner == "environment"
    assert bridge.completed
    assert receiver.verify_inside("red-block", "right-target-zone").success


def test_receiver_observation_between_pick_and_verify_keeps_attachment_valid():
    """Telemetry may sample the world between two runtime commands."""
    _bridge, sender, receiver = seeded_handoff_worlds(seed=7)
    assert sender.pick("red-block").success
    assert sender.place("handoff-zone").success
    assert receiver.pick("red-block").success

    # entities() synchronizes the shared bridge and mirrors the real Edge
    # telemetry race that previously wrote None into MuJoCo qpos.
    assert "red-block" in _ids(receiver)

    assert receiver.verify_grasp("red-block").success


def test_stale_handoff_token_cannot_change_shared_block_owner():
    bridge = SharedHandoffBridge()
    token = bridge.fencing_token

    next_token = bridge.transfer(
        expected_owner="robot-1",
        new_owner="environment",
        expected_token=token,
        custodian="robot-2",
    )

    assert next_token > token
    with pytest.raises(StaleHandoffToken):
        bridge.transfer(
            expected_owner="environment",
            new_owner="robot-2",
            expected_token=token,
            custodian="robot-2",
        )
    assert bridge.owner == "environment"
    assert bridge.fencing_token == next_token


def test_receiver_cannot_pick_before_verified_handoff():
    _bridge, _sender, receiver = seeded_handoff_worlds(seed=11)

    result = receiver.pick("red-block")

    assert not result.success
    assert result.code == "OBJECT_NOT_AVAILABLE"


def test_receiver_target_zone_matches_natural_language_right_side_selector():
    _bridge, _sender, receiver = seeded_handoff_worlds(seed=13)

    destination = receiver.resolve(category="target_zone", relation="right_side")

    assert destination.entity_id == "right-target-zone"


def test_both_robot_observers_publish_identical_shared_zone_coordinates():
    _bridge, sender, receiver = seeded_handoff_worlds(seed=13)

    sender_target = next(
        entity for entity in sender.entities() if entity.entity_id == "right-target-zone"
    )
    receiver_target = next(
        entity for entity in receiver.entities() if entity.entity_id == "right-target-zone"
    )

    assert sender_target.position == pytest.approx(receiver_target.position, abs=1e-9)
