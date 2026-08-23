import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_supported_python_runtime():
    assert sys.version_info >= (3, 11)


def test_release_candidate_version_is_consistent():
    assert 'version = "0.2.0rc1"' in (ROOT / "pyproject.toml").read_text()
    assert "## v0.2.0-rc.1 - 2026-08-23" in (ROOT / "CHANGELOG.md").read_text()
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


def test_fork_sensitive_runtime_contract_runs_in_an_isolated_pytest_process():
    makefile = (ROOT / "Makefile").read_text()
    isolated = (
        ".venv/bin/pytest -q tests/contract/test_sim_real_runtime_boundary.py"
    )
    remaining = (
        ".venv/bin/pytest -q "
        "--ignore=tests/contract/test_sim_real_runtime_boundary.py"
    )

    assert isolated in makefile
    assert remaining in makefile
    assert makefile.index(isolated) < makefile.index(remaining)


def test_robocasa_fault_matrix_uses_the_active_python_environment():
    fault_matrix = (ROOT / "tests/e2e/test_robocasa_faults.py").read_text()

    assert '"conda", "run"' not in fault_matrix
    assert "sys.executable" in fault_matrix


def test_robocasa_process_harness_probes_capability_without_conda_discovery():
    harness = (ROOT / "tests/e2e/robocasa_harness.py").read_text()

    assert 'shutil.which("conda")' not in harness
    assert "ROBOCASA_PYTHON" in harness
    assert "import robocasa" in harness


def test_cloud_primary_keeps_offline_local_brain_decoupled():
    assert not (ROOT / "cmd/cloud-control-plane").exists()
    assert not (ROOT / "cloud/api").exists()
    assert not (ROOT / "cloud/orchestrator/postgres_store.go").exists()
    assert "github.com/jackc/pgx" not in (ROOT / "go.mod").read_text()
    assert "kafka" not in (ROOT / "go.mod").read_text().lower()
    assert not list((ROOT / "edge/localstore").glob("*.go"))
    assert (ROOT / "middleware/sqlite/store.go").exists()
    assert (ROOT / "middleware/memory/queue.go").exists()
    assert (ROOT / "cmd/fleet-control-plane/main.go").exists()
    assert (ROOT / "core/worldmodel/types.go").exists()
    task_service = (ROOT / "tasks/service.go").read_text()
    assert "func (s *Service) Claim" not in task_service
    assert "func (s *Service) RenewLease" not in task_service


def test_fleet_cloud_is_primary_but_local_brain_has_no_cloud_store_dependency():
    go_mod = (ROOT / "go.mod").read_text()
    assert "github.com/redis/" in go_mod  # fleet/redis adapters only
    assert "github.com/go-sql-driver/mysql" in go_mod  # fleet/mysql adapter only
    local_agent = (ROOT / "cmd/local-agent/main.go").read_text()
    assert "fleet/redis" not in local_agent
    assert "fleet/mysql" not in local_agent
    edge_worker = (ROOT / "cmd/edge-worker/main.go").read_text()
    assert "fleet/mysql" not in edge_worker
    assert "middleware/sqlite" not in edge_worker
    assert (ROOT / "fleet/redis/queue.go").exists()
    assert (ROOT / "deploy/cloud/docker-compose.yml").exists()


def test_current_docs_link_governing_design_assets():
    architecture = (ROOT / "docs/architecture.md").read_text()
    assert "superpowers/specs/2026-08-18-local-first-runtime-design.md" in architecture
    assert "superpowers/plans/2026-08-18-local-first-runtime.md" in architecture
    assert "superpowers/specs/2026-08-18-layered-runtime-middleware-design.md" in architecture
    assert "superpowers/plans/2026-08-18-layered-runtime-middleware.md" in architecture
    assert "superpowers/specs/2026-08-20-distributed-agentos-world-harness-design.md" in architecture
    assert (ROOT / "docs/superpowers/plans/2026-08-18-local-first-runtime.md").exists()
    assert (ROOT / "docs/superpowers/plans/2026-08-18-layered-runtime-middleware.md").exists()
    assert (ROOT / "docs/middleware.md").exists()


def test_readme_leads_with_cloud_product_and_keeps_offline_local_brain():
    readme = (ROOT / "README.md").read_text()
    assert "联网即用" in readme[:2500]
    assert "无网络" in readme[:2500]
    assert "./scripts/fleet-sim.sh handoff" in readme[:5000]


def test_cloud_env_exposes_world_and_coordination_controls():
    environment = (ROOT / "deploy/cloud/.env.example").read_text()
    for key in (
        "FLEET_WORLD_ID",
        "FLEET_WORLD_FRESHNESS",
        "FLEET_WORLD_DELTA_RETENTION",
        "FLEET_LEADER_LEASE",
        "FLEET_RESOURCE_LEASE",
    ):
        assert key in environment


def test_removed_pair_rpc_does_not_reappear_in_simulator():
    simulator = (ROOT / "sim/mujoco/tangying_sim/server.py").read_text()
    assert "def Pair(" not in simulator
    assert "PairResponse" not in simulator
