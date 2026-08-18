import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_supported_python_runtime():
    assert sys.version_info >= (3, 11)


def test_release_candidate_version_is_consistent():
    assert 'version = "0.1.0rc2"' in (ROOT / "pyproject.toml").read_text()
    for path in (
        ROOT / "sim/mujoco/tangying_sim/server.py",
        ROOT / "robot/ros2_ws/src/tangying_robot_gateway/tangying_ros_gateway/node.py",
    ):
        assert 'software_version="0.1.0-rc.2"' in path.read_text()


def test_ci_covers_fresh_install_plans_and_full_demo():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    for required in (
        "installer-dry-run",
        "macos-14",
        "ubuntu-24.04",
        "robot-pi",
        "bash scripts/demo.sh",
    ):
        assert required in workflow


def test_repository_runtime_is_local_first():
    assert not (ROOT / "cmd/cloud-control-plane").exists()
    assert not (ROOT / "cloud/api").exists()
    assert not (ROOT / "cloud/orchestrator/postgres_store.go").exists()
    assert "github.com/jackc/pgx" not in (ROOT / "go.mod").read_text()
    assert "github.com/redis/" not in (ROOT / "go.mod").read_text()
    assert "kafka" not in (ROOT / "go.mod").read_text().lower()
    assert not list((ROOT / "edge/localstore").glob("*.go"))
    assert (ROOT / "middleware/sqlite/store.go").exists()
    assert (ROOT / "middleware/memory/queue.go").exists()
    task_service = (ROOT / "tasks/service.go").read_text()
    assert "func (s *Service) Claim" not in task_service
    assert "func (s *Service) RenewLease" not in task_service


def test_current_docs_link_governing_design_assets():
    architecture = (ROOT / "docs/architecture.md").read_text()
    assert "superpowers/specs/2026-08-18-local-first-runtime-design.md" in architecture
    assert "superpowers/plans/2026-08-18-local-first-runtime.md" in architecture
    assert "superpowers/specs/2026-08-18-layered-runtime-middleware-design.md" in architecture
    assert "superpowers/plans/2026-08-18-layered-runtime-middleware.md" in architecture
    assert (ROOT / "docs/superpowers/plans/2026-08-18-local-first-runtime.md").exists()
    assert (ROOT / "docs/superpowers/plans/2026-08-18-layered-runtime-middleware.md").exists()
    assert (ROOT / "docs/middleware.md").exists()


def test_removed_pair_rpc_does_not_reappear_in_simulator():
    simulator = (ROOT / "sim/mujoco/tangying_sim/server.py").read_text()
    assert "def Pair(" not in simulator
    assert "PairResponse" not in simulator
