from __future__ import annotations

import json

import pytest
from tangying_robocasa.checkpoint import CheckpointStore, ModelHashMismatch
from tangying_robocasa.composer import SceneConfig, compose_handoff_scene
from tangying_robocasa.world import RoboCasaRobotView, RoboCasaSharedWorld

pytestmark = pytest.mark.robocasa


def _world() -> RoboCasaSharedWorld:
    pytest.importorskip("robocasa")
    return RoboCasaSharedWorld.from_scene(compose_handoff_scene(SceneConfig()), seed=7)


def test_checkpoint_restores_released_block_without_reexecuting_tool(tmp_path) -> None:
    world = _world()
    sender = RoboCasaRobotView(world, "robot-1")
    assert sender.pick("red-block").success
    assert sender.place("handoff-zone").success
    qpos = world.data.qpos.copy()
    store = CheckpointStore(tmp_path / "state.json")

    store.save(world)
    restored = _world()
    store.restore(restored)

    assert restored.placement == "handoff-zone"
    assert restored.owner == "environment"
    assert restored.custodian == "robot-2"
    assert restored.fencing_token == 2
    assert restored.held_by == ""
    assert restored.data.time == pytest.approx(world.data.time)
    assert restored.data.qpos.tolist() == pytest.approx(qpos.tolist())


def test_checkpoint_rejects_different_model_hash(tmp_path) -> None:
    world = _world()
    store = CheckpointStore(tmp_path / "state.json")
    store.save(world)
    payload = json.loads(store.path.read_text())
    payload["modelHash"] = "0" * 64
    store.path.write_text(json.dumps(payload))

    with pytest.raises(ModelHashMismatch):
        store.restore(_world())


def test_checkpoint_write_leaves_no_partial_temporary_file(tmp_path) -> None:
    store = CheckpointStore(tmp_path / "state.json")

    store.save(_world())

    assert store.path.exists()
    assert not list(tmp_path.glob(".state.json.*.tmp"))
