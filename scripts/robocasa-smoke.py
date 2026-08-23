#!/usr/bin/env python3
from __future__ import annotations

import json
from importlib.metadata import version

import mujoco
import numpy as np
from robocasa.models.scenes import KitchenArena
from robosuite.models.tasks import ManipulationTask


def main() -> None:
    arena = KitchenArena(
        layout_id=1,
        style_id=1,
        rng=np.random.default_rng(7),
        clutter_mode=0,
    )
    task = ManipulationTask(
        mujoco_arena=arena,
        mujoco_robots=[],
        mujoco_objects=list(arena.fixtures.values()),
    )
    model = mujoco.MjModel.from_xml_string(task.get_xml())
    print(
        json.dumps(
            {
                "robocasa": version("robocasa"),
                "robosuite": version("robosuite"),
                "mujoco": mujoco.__version__,
                "fixtureCount": len(arena.fixtures),
                "nbody": model.nbody,
                "ngeom": model.ngeom,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

