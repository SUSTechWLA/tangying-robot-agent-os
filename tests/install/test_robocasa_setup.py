from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_visual_extra_is_isolated_and_pinned():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert project["project"]["optional-dependencies"]["visual"] == [
        "trimesh==4.12.2",
        "pygltflib==1.16.5",
    ]
    assert "trimesh==4.12.2" not in project["project"]["dependencies"]
    assert "pygltflib==1.16.5" not in project["project"]["dependencies"]


def test_setup_script_uses_isolated_conda_and_official_clones():
    script = (ROOT / "scripts/setup-robocasa.sh").read_text()

    assert 'ROBOCASA_ENV_NAME="${ROBOCASA_ENV_NAME:-tangying-robocasa}"' in script
    assert "https://github.com/ARISE-Initiative/robosuite.git" in script
    assert '"$ROBOCASA_ROOT"' in script
    assert "python -m pip install -e" in script
    assert "download_kitchen_assets" in script
    assert "setup_macros" in script
    assert "export PYTHONNOUSERSITE=1" in script
    assert "--no-capture-output" in script
    assert "robosuite/macros_private.py" in script
    assert 'ROBOCASA_ASSET_PROFILE="${ROBOCASA_ASSET_PROFILE:-minimal}"' in script
    assert 'download_assets "$TEXTURE_MARKER" tex' in script
    assert 'download_assets "$FIXTURE_MARKER" fixtures_lw' in script
    assert 'download_assets "$OBJECT_LW_MARKER" objs_lw' in script
    assert 'python -m pip install -e "$PROJECT_ROOT[visual]"' in script
    assert "import trimesh, pygltflib" in script


def test_setup_script_is_idempotent_and_does_not_reclone_user_checkouts():
    script = (ROOT / "scripts/setup-robocasa.sh").read_text()

    assert 'if [ ! -d "$ROBOSUITE_ROOT/.git" ]' in script
    assert 'if [ ! -d "$ROBOCASA_ROOT/.git" ]' in script
    assert "--depth 1" in script
    assert "--filter=blob:none" in script
    assert 'git -C "$ROBOSUITE_ROOT" rev-parse --verify HEAD' in script
    assert "conda env list --json" in script
    assert ".tangying-robocasa-assets-minimal-complete" in script


def test_smoke_script_loads_fixed_kitchen_arena_and_compiles_xml():
    script = (ROOT / "scripts/robocasa-smoke.py").read_text()

    assert "KitchenArena(" in script
    assert "layout_id=1" in script
    assert "style_id=1" in script
    assert "clutter_mode=0" in script
    assert "mujoco.MjModel.from_xml_string" in script
    assert '"fixtureCount"' in script


def test_makefile_exposes_robocasa_install_and_smoke_targets():
    makefile = (ROOT / "Makefile").read_text()

    assert "robocasa-install:" in makefile
    assert "robocasa-install-full:" in makefile
    assert "robocasa-smoke:" in makefile
    assert "scripts/setup-robocasa.sh" in makefile
    assert "scripts/robocasa-smoke.py" in makefile
