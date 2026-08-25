"""Generate the committed RoboCasa browser scene bundle."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

# The RoboCasa Conda environment also contains an installed package. Asset
# export must describe this checkout, not whichever package was installed
# most recently, otherwise Fleet correctly rejects the stale browser model.
REPO = Path(__file__).resolve().parents[1]
for source in reversed(
    (
        REPO / "python",
        REPO / "sim" / "mujoco",
        REPO / "sim" / "robocasa",
    )
):
    sys.path.insert(0, str(source))

from tangying_robocasa.composer import compose_handoff_scene
from tangying_robocasa.visual_assets import export_visual_bundle


def optimize_robot_asset(output_dir: Path) -> str:
    """Create the browser LOD while preserving every articulated node name."""

    robot_path = output_dir / "xlerobot.glb"
    optimized_path = output_dir / ".xlerobot.optimized.glb"
    try:
        subprocess.run(
            [
                "node",
                str(REPO / "web" / "optimize_robot_asset.mjs"),
                str(robot_path),
                str(optimized_path),
            ],
            cwd=REPO,
            check=True,
            timeout=300,
        )
        robot_sha = hashlib.sha256(optimized_path.read_bytes()).hexdigest()
        optimized_path.replace(robot_path)
    finally:
        optimized_path.unlink(missing_ok=True)

    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["contentHashes"]["xlerobot.glb"] = robot_sha
    manifest["robotModels"]["xlerobot"]["asset"] = f"xlerobot.glb?v={robot_sha}"
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return robot_sha


def main() -> int:
    scene = compose_handoff_scene()
    output_dir = Path("web/assets/scenes/robocasa-handoff-v1")
    manifest = export_visual_bundle(scene, output_dir)
    optimize_robot_asset(output_dir)
    print(
        json.dumps(
            {"sceneId": manifest.scene_id, "modelHash": manifest.model_hash},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
