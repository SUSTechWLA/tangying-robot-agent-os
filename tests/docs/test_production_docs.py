from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PRODUCTION = REPO / "docs/production"
REQUIRED = {
    "README.md": ("项目负责人", "开发者", "云端运维", "机器人技术员", "安全审核", "系统集成"),
    "architecture.md": ("云端 Fleet", "Local Brain", "Coordinator", "Harness Agent", "WorldModel", "数据流"),
    "quickstart.md": ("从零开始", "RoboCasa", "服务器端", "用户端", "机器人仿真", "机器人实机"),
    "api-reference.md": ("HTTP API", "WebSocket", "gRPC", "错误码", "幂等"),
    "configuration-and-security.md": ("配置清单", "mTLS", "RBAC", "密钥轮换", "备份"),
    "sim-to-real.md": ("工具注册", "观测源", "地图坐标系", "标定", "回滚"),
    "operations-and-failures.md": ("现象", "检查", "恢复", "防止复发", "安全不变量"),
    "testing-and-acceptance.md": ("单元测试", "RoboCasa", "签名证据", "发布检查", "实机验收"),
    "data-contracts.md": ("TaskRevision", "ToolActivity", "ObservationEnvelope", "WorldSnapshot", "fencing"),
    "policy-tools.md": (
        "PolicyManifest",
        "VLA",
        "模仿学习",
        "强化学习",
        "action_chunk",
        "sim2real",
        "Harness Agent",
    ),
}


def _all_production_text() -> str:
    return "\n".join(path.read_text() for path in sorted(PRODUCTION.glob("*.md")))


def test_production_document_set_is_complete():
    for name, phrases in REQUIRED.items():
        path = PRODUCTION / name
        assert path.is_file(), f"missing production document: {name}"
        text = path.read_text()
        assert all(phrase in text for phrase in phrases), name


def test_documented_http_routes_cover_registered_routes():
    registered: set[str] = set()
    pattern = re.compile(r'(?:HandleFunc|Handle)\("(?:GET|POST|PUT|DELETE|PATCH) ([^" ]+)"')
    for source in (REPO / "fleet/server.go", REPO / "console/server.go"):
        registered.update(pattern.findall(source.read_text()))
    documented = set(re.findall(r"`(?:GET|POST|PUT|DELETE|PATCH) ([^` ]+)`", (PRODUCTION / "api-reference.md").read_text()))
    assert registered <= documented, sorted(registered - documented)


def test_internal_markdown_links_and_referenced_make_targets_resolve():
    make_targets = set(re.findall(r"^([A-Za-z0-9_.-]+):", (REPO / "Makefile").read_text(), re.MULTILINE))
    for page in sorted(PRODUCTION.glob("*.md")):
        text = page.read_text()
        for target in re.findall(r"\bmake ([A-Za-z0-9_.-]+)", text):
            assert target in make_targets, f"{page.name}: unknown make target {target}"
        for raw in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
            if "://" in raw or raw.startswith("#"):
                continue
            destination = raw.split("#", 1)[0]
            if destination:
                assert (page.parent / destination).resolve().exists(), f"{page.name}: {raw}"


def test_documented_paths_and_security_examples_are_safe():
    text = _all_production_text()
    for relative in (
        "scripts/fleet-up.sh",
        "scripts/robocasa-fleet.sh",
        "scripts/setup-robocasa.sh",
        "scripts/robot-pi-preflight.sh",
        "deploy/cloud/.env.example",
    ):
        assert relative in text
        assert (REPO / relative).exists()
    assert "BEGIN PRIVATE KEY" not in text
    assert re.search(r"Bearer\s+[A-Za-z0-9_-]{32,}", text) is None
    for name in ("FLEET_OPERATOR_USER", "FLEET_AUTH_SECRET", "FLEET_DEVICE_CREDENTIALS", "MYSQL_PASSWORD"):
        assert name in (PRODUCTION / "configuration-and-security.md").read_text()


def test_policy_tool_runbook_covers_runtime_contract_and_release_gates():
    text = (PRODUCTION / "policy-tools.md").read_text()
    for required in (
        "EDGE_POLICY_MODE",
        "EDGE_POLICY_ENDPOINT",
        "EDGE_ROBOT_MODEL",
        "EDGE_CALIBRATION_REVISION",
        "GET /v1/manifest",
        "POST /v1/infer",
        "OBSERVATION_WAIT",
        "POLICY_RETRY",
        "EXECUTION_RECONCILE",
        "SAFETY_STOP",
        "SIMULATION_GO",
        "SHADOW_GO",
        "PHYSICAL_GO",
    ):
        assert required in text
