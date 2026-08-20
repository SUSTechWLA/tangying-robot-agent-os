"""Atomic, model-bound checkpoints for the shared RoboCasa handoff world."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import mujoco
import numpy as np

from .world import RoboCasaSharedWorld


class CheckpointError(RuntimeError):
    """A checkpoint cannot be safely applied."""


class ModelHashMismatch(CheckpointError):
    """The checkpoint was produced for a different compiled MJCF model."""


class CheckpointStore:
    SCHEMA = "tangying.robocasa.checkpoint.v1"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(self, world: RoboCasaSharedWorld) -> dict[str, Any]:
        with world.lock:
            payload: dict[str, Any] = {
                "schemaVersion": self.SCHEMA,
                "sceneId": world.scene.names[0] if world.scene.names else "robocasa-handoff-v1",
                "modelHash": world.scene.model_hash,
                "episode": world.episode,
                "seed": world.seed,
                "simulationTime": float(world.data.time),
                "qpos": np.asarray(world.data.qpos).tolist(),
                "qvel": np.asarray(world.data.qvel).tolist(),
                "owner": world.owner,
                "custodian": world.custodian,
                "fencingToken": world.fencing_token,
                "completed": world.completed,
                "heldBy": world.held_by,
                "placement": world.placement,
                "pickCounts": dict(world.pick_counts),
                "sourceSequences": dict(world.source_sequences),
                "stepCount": world.step_count,
                "sequence": world.sequence,
            }
        wire = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(wire)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary.exists():
                temporary.unlink()
        return payload

    def restore(self, world: RoboCasaSharedWorld) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CheckpointError(f"cannot read checkpoint {self.path}: {exc}") from exc
        if payload.get("schemaVersion") != self.SCHEMA:
            raise CheckpointError("unsupported RoboCasa checkpoint schema")
        if payload.get("modelHash") != world.scene.model_hash:
            raise ModelHashMismatch(
                f"checkpoint model {payload.get('modelHash')} != runtime model {world.scene.model_hash}"
            )
        if int(payload.get("seed", -1)) != world.seed:
            raise CheckpointError("checkpoint seed does not match runtime episode")
        qpos = np.asarray(payload.get("qpos", []), dtype=float)
        qvel = np.asarray(payload.get("qvel", []), dtype=float)
        if qpos.shape != world.data.qpos.shape or qvel.shape != world.data.qvel.shape:
            raise CheckpointError("checkpoint state vector shape does not match model")

        with world.lock:
            world.data.qpos[:] = qpos
            world.data.qvel[:] = qvel
            world.data.time = float(payload["simulationTime"])
            world.episode = int(payload["episode"])
            world.owner = str(payload["owner"])
            world.custodian = str(payload["custodian"])
            world.fencing_token = int(payload["fencingToken"])
            world.completed = bool(payload["completed"])
            world.held_by = str(payload["heldBy"])
            world.placement = str(payload["placement"])
            world.pick_counts = {
                key: int(value) for key, value in payload["pickCounts"].items()
            }
            world.source_sequences = {
                key: int(value) for key, value in payload["sourceSequences"].items()
            }
            world.step_count = int(payload["stepCount"])
            world.sequence = int(payload["sequence"])
            mujoco.mj_forward(world.model, world.data)
            world._refresh_cache()
        world._notify_grant()
        return payload
