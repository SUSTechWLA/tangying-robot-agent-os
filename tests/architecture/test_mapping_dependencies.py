"""Keep replaceable SLAM algorithms out of mapping/semantic consumers."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATEWAY = ROOT / "robot/gateway/tangying_robot_gateway"


def gateway_imports(path):
    """Inspect nested imports as well, including local import optimizations."""
    modules = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            if node.level == 1:
                if node.module:
                    modules.add(node.module.split(".")[0])
                else:
                    modules.update(alias.name.split(".")[0] for alias in node.names)
            elif node.module and node.module.startswith("tangying_robot_gateway."):
                modules.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("tangying_robot_gateway."):
                    modules.add(alias.name.split(".")[1])
    return modules


@pytest.mark.parametrize("name", [
    "robot_workflow", "grid_navigation", "slam_semantics", "semantic_workspaces",
    "semantic_cloud_services", "map_pipeline", "navigation_map", "slam_provider",
])
def test_mapping_consumers_do_not_import_concrete_slam(name):
    imports = gateway_imports(GATEWAY / f"{name}.py")
    assert not imports & {"dense_slam", "dense_slam_provider", "rtabmap_export"}, (
        f"{name} depends on a SLAM implementation; use geometry or slam_provider contracts, "
        "and choose the concrete provider at the driver assembly boundary"
    )


def test_geometry_has_no_gateway_implementation_dependencies():
    assert not gateway_imports(GATEWAY / "geometry.py")
