"""Generate the committed RoboCasa browser scene bundle."""

from __future__ import annotations

import json
from pathlib import Path

from tangying_robocasa.composer import compose_handoff_scene
from tangying_robocasa.visual_assets import export_visual_bundle


def main() -> int:
    scene = compose_handoff_scene()
    manifest = export_visual_bundle(scene, Path("web/assets/scenes/robocasa-handoff-v1"))
    print(
        json.dumps(
            {"sceneId": manifest.scene_id, "modelHash": manifest.model_hash},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
