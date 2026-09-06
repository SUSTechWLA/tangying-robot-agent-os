"""Exercise the public MCP transport against an isolated Fleet HTTP fixture."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import ssl
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = ROOT / "robot" / "mcp"
TOKEN = "mcp-test-secret-not-for-output"
TOOLS = {
    "list_robots", "get_robot_capabilities", "observe_world", "list_tasks",
    "create_task", "get_task", "cancel_task", "emergency_stop",
}


def bridge_module(monkeypatch):
    monkeypatch.syspath_prepend(str(PACKAGE_ROOT))
    assert importlib.util.find_spec("tangying_mcp.server") is not None
    return importlib.import_module("tangying_mcp.server")


@pytest.fixture
def fleet():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.dispatch()

        def do_POST(self):
            self.dispatch()

        def log_message(self, *args):
            pass

        def dispatch(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = json.loads(raw) if raw else None
            self.server.calls.append((self.command, self.path, self.headers, body))
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                status, payload = 401, {"message": "invalid token"}
            elif self.path in self.server.overrides:
                status, payload, delay = self.server.overrides[self.path]
                time.sleep(delay)
            elif self.path == "/v1/devices":
                status, payload = 200, [{"robotId": "arm-1", "adapter": "custom_arm"}]
            elif self.path == "/v1/devices/arm-1":
                status, payload = 200, {
                    "robotId": "arm-1", "adapter": "custom_arm", "online": True,
                    "capabilities": [{"name": "observe", "available": True}],
                    "toolCatalogRevision": "sha256:catalog",
                    "toolCatalog": [{"name": "robot.observe", "available": True}],
                    "observationCatalogRevision": "sha256:sensors",
                    "observationSources": [{"sourceId": "lidar", "sourceType": "lidar"}],
                }
            elif self.path == "/v1/world":
                status, payload = 200, {
                    "worldVersion": 3, "frameId": "world", "robots": [{"robotId": "arm-1"}],
                    "entities": [{"id": "cup", "frameId": "world", "position": [1, 2, 3]}],
                }
            elif self.path == "/v1/tasks" and self.command == "POST":
                status, payload = 201, {
                    "id": "task-123", "approved": False, "state": "READY", **body,
                }
            elif self.path == "/v1/tasks":
                status, payload = 200, [{"id": "task-123", "approved": False, "state": "READY"}]
            elif self.path == "/v1/tasks/task-123/cancel":
                status, payload = 200, {"id": "task-123", "state": "CANCELLED"}
            elif self.path == "/v1/tasks/task-123":
                status, payload = 200, {"id": "task-123", "approved": False, "state": "READY"}
            elif self.path == "/v1/devices/arm-1/estop":
                status, payload = 200, {"status": "pushed", "robotId": "arm-1"}
            else:
                status, payload = 404, {"message": "not found"}
            encoded = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if status == 302:
                self.send_header("Location", self.server.redirect_url)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            try:
                self.wfile.write(encoded)
            except BrokenPipeError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.calls, server.overrides = [], {}
    server.url = f"http://127.0.0.1:{server.server_port}"
    server.redirect_url = server.url + "/stolen-token"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


@asynccontextmanager
async def mcp_client(fleet, tmp_path, **config):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = {
        "PYTHONPATH": str(PACKAGE_ROOT),
        "TANGYING_MCP_FLEET_URL": fleet.url,
        "TANGYING_MCP_TOKEN": TOKEN,
        **config,
    }
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "tangying_mcp"], env=env,
    )
    with (tmp_path / "stderr.log").open("w") as stderr:
        async with stdio_client(params, errlog=stderr) as streams:
            async with ClientSession(*streams) as session:
                initialized = await session.initialize()
                assert initialized.serverInfo.name == "躺营 Robot Agent"
                yield session
    assert TOKEN not in (tmp_path / "stderr.log").read_text()


@pytest.mark.asyncio
async def test_stdio_contract_and_approval_boundary(monkeypatch, fleet, tmp_path):
    bridge_module(monkeypatch)
    async with mcp_client(fleet, tmp_path) as session:
        catalog = (await session.list_tools()).tools
        assert {tool.name for tool in catalog} == TOOLS
        assert all(tool.inputSchema["additionalProperties"] is False for tool in catalog)
        assert all(tool.outputSchema["properties"]["schema_version"] for tool in catalog)
        args = {
            "list_robots": {}, "get_robot_capabilities": {"robot_id": "arm-1"},
            "observe_world": {}, "list_tasks": {},
            "create_task": {"request": "把杯子放到托盘", "adapter": "custom_arm"},
            "get_task": {"task_id": "task-123"},
            "cancel_task": {"task_id": "task-123"},
            "emergency_stop": {"robot_id": "arm-1", "reason": "human requested stop"},
        }
        results = {}
        for name, arguments in args.items():
            result = await session.call_tool(name, arguments)
            assert not result.isError, result
            assert result.structuredContent["schema_version"] == "tangying.mcp/v1"
            assert result.structuredContent["ok"] is True
            results[name] = result.structuredContent
        assert results["create_task"]["approval_required"] is True
        assert results["create_task"]["data"]["approved"] is False
        assert results["get_robot_capabilities"]["data"]["observationSources"][0]["sourceId"] == "lidar"
        assert results["observe_world"]["data"]["frameId"] == "world"
        assert results["emergency_stop"]["data"]["status"] == "pushed"
    assert all(call[2]["Authorization"] == f"Bearer {TOKEN}" for call in fleet.calls)
    assert not any("approve" in call[1] for call in fleet.calls)
    creation = next(call for call in fleet.calls if call[:2] == ("POST", "/v1/tasks"))
    assert creation[3] == {"request": "把杯子放到托盘", "adapter": "custom_arm"}


@pytest.mark.asyncio
async def test_invalid_arguments_cannot_bypass_tools(monkeypatch, fleet, tmp_path):
    bridge_module(monkeypatch)
    async with mcp_client(fleet, tmp_path) as session:
        for name, args in [
            ("get_task", {"task_id": "../devices/arm-1/estop"}),
            ("get_task", {"task_id": 123}),
            ("create_task", {"request": "", "adapter": "custom"}),
            ("create_task", {"request": "x", "adapter": "custom", "approved": True}),
            ("create_task", {"request": "x", "adapter": "custom", "auto_approve": True}),
            ("observe_world", {"url": "https://other.invalid"}),
        ]:
            result = await session.call_tool(name, args)
            assert result.isError
            assert result.structuredContent["error"]["code"] == "INVALID_ARGUMENTS"
        unknown = await session.call_tool("approve_task", {"task_id": "task-123"})
        assert unknown.isError
    assert fleet.calls == []


@pytest.mark.asyncio
async def test_http_errors_redirects_and_body_do_not_leak_token(monkeypatch, fleet, tmp_path):
    bridge_module(monkeypatch)
    async with mcp_client(fleet, tmp_path) as session:
        for status in [401, 403, 404, 429, 500, 302]:
            fleet.overrides["/v1/world"] = status, {"message": TOKEN}, 0
            result = await session.call_tool("observe_world", {})
            assert result.isError
            assert TOKEN not in result.model_dump_json()
        fleet.overrides["/v1/world"] = 200, {"message": TOKEN, "access_token": "private"}, 0
        safe = await session.call_tool("observe_world", {})
        assert TOKEN not in safe.model_dump_json()
        assert "private" not in safe.model_dump_json()
    assert all(call[1] == "/v1/world" for call in fleet.calls)


@pytest.mark.asyncio
async def test_draft_response_and_timeout_fail_closed(monkeypatch, fleet, tmp_path):
    bridge_module(monkeypatch)
    async with mcp_client(fleet, tmp_path, TANGYING_MCP_TIMEOUT_SECONDS="0.1") as session:
        fleet.overrides["/v1/tasks"] = 201, {
            "id": "task-123", "approved": True, "state": "EXECUTING",
        }, 0
        unsafe = await session.call_tool("create_task", {"request": "x", "adapter": "custom"})
        assert unsafe.isError
        assert unsafe.structuredContent["error"]["code"] == "UNSAFE_TASK_RESPONSE"
        fleet.overrides["/v1/tasks"] = 201, {"id": "task-123"}, 0.3
        unknown = await session.call_tool("create_task", {"request": "x", "adapter": "custom"})
        assert unknown.isError
        assert unknown.structuredContent["error"]["code"] == "OUTCOME_UNKNOWN"
        assert unknown.structuredContent["error"]["retryable"] is False
    assert len(fleet.calls) == 2  # Never retry a potentially persisted mutation.


@pytest.mark.asyncio
async def test_rate_limit_does_not_block_emergency_stop(monkeypatch, fleet, tmp_path):
    bridge_module(monkeypatch)
    async with mcp_client(fleet, tmp_path, TANGYING_MCP_RATE_LIMIT="1") as session:
        assert not (await session.call_tool("list_robots", {})).isError
        limited = await session.call_tool("observe_world", {})
        assert limited.isError
        assert limited.structuredContent["error"]["code"] == "RATE_LIMITED"
        stop = await session.call_tool("emergency_stop", {"robot_id": "arm-1", "reason": "stop"})
        assert not stop.isError
    assert [call[1] for call in fleet.calls] == ["/v1/devices", "/v1/devices/arm-1/estop"]


@pytest.mark.parametrize("name,value", [
    ("TANGYING_MCP_FLEET_URL", "http://example.com"),
    ("TANGYING_MCP_FLEET_URL", "https://user:secret@example.com"),
    ("TANGYING_MCP_FLEET_URL", "https://example.com/?token=secret"),
    ("TANGYING_MCP_FLEET_URL", "https://example.com/v1"),
    ("TANGYING_MCP_FLEET_URL", "file:///tmp/fleet"),
    ("TANGYING_MCP_TOKEN", ""),
    ("TANGYING_MCP_TOKEN", "line\nbreak"),
    ("TANGYING_MCP_TIMEOUT_SECONDS", "nan"),
    ("TANGYING_MCP_TIMEOUT_SECONDS", "0"),
    ("TANGYING_MCP_RATE_LIMIT", "-1"),
])
def test_config_rejects_insecure_or_invalid_settings(monkeypatch, name, value):
    module = bridge_module(monkeypatch)
    env = {"TANGYING_MCP_FLEET_URL": "http://127.0.0.1:18080", "TANGYING_MCP_TOKEN": TOKEN}
    env[name] = value
    with pytest.raises(ValueError) as error:
        module.Config.from_env(env)
    assert "secret" not in str(error.value)


def test_config_accepts_remote_tls_and_ipv6_loopback(monkeypatch):
    module = bridge_module(monkeypatch)
    for url in ["https://fleet.example.com", "http://[::1]:18080", "http://localhost:18080/"]:
        config = module.Config.from_env({"TANGYING_MCP_FLEET_URL": url, "TANGYING_MCP_TOKEN": TOKEN})
        assert config.fleet_url == url.rstrip("/")


@pytest.fixture
def tls_fleet(fleet, tmp_path):
    """Real TLS listener with a private test CA and a localhost-only SAN."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = datetime.datetime.now(datetime.UTC)

    def issue(name, *, issuer=None, san=None):
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        builder = (
            x509.CertificateBuilder().subject_name(subject)
            .issuer_name(issuer[1].subject if issuer else subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=issuer is None, path_length=None), critical=True)
        )
        if san:
            builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False)
        cert = builder.sign(issuer[0] if issuer else key, hashes.SHA256())
        return key, cert

    ca = issue("MCP fixture CA")
    wrong_ca = issue("MCP unrelated CA")
    key, certificate = issue("localhost", issuer=ca, san="localhost")
    server_cert, server_key = tmp_path / "server.pem", tmp_path / "server.key"
    server_cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    server_key.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    server_key.chmod(0o600)
    ca_file, wrong_ca_file = tmp_path / "ca.pem", tmp_path / "wrong-ca.pem"
    ca_file.write_bytes(ca[1].public_bytes(serialization.Encoding.PEM))
    wrong_ca_file.write_bytes(wrong_ca[1].public_bytes(serialization.Encoding.PEM))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(server_cert, server_key)
    server = ThreadingHTTPServer(("127.0.0.1", 0), fleet.RequestHandlerClass)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.calls, server.overrides = [], {}
    server.url = f"https://localhost:{server.server_port}"
    server.ca_file, server.wrong_ca_file = ca_file, wrong_ca_file
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.asyncio
async def test_real_https_requires_explicit_ca_and_matching_hostname(monkeypatch, tls_fleet, tmp_path):
    bridge_module(monkeypatch)
    for configuration, succeeds in [
        ({}, False),
        ({"SSL_CERT_FILE": str(tls_fleet.ca_file)}, False),
        ({"TANGYING_MCP_CA_FILE": str(tls_fleet.ca_file)}, True),
        ({"TANGYING_MCP_CA_FILE": str(tls_fleet.wrong_ca_file)}, False),
        ({"TANGYING_MCP_CA_FILE": str(tls_fleet.ca_file),
          "TANGYING_MCP_FLEET_URL": f"https://127.0.0.1:{tls_fleet.server_port}"}, False),
    ]:
        async with mcp_client(tls_fleet, tmp_path, **configuration) as session:
            result = await session.call_tool("list_robots", {})
            assert result.isError is not succeeds, result
            if succeeds:
                assert result.structuredContent["data"][0]["robotId"] == "arm-1"
            else:
                assert result.structuredContent["error"]["code"] == "FLEET_UNAVAILABLE"
            assert TOKEN not in result.model_dump_json()
    # Failed handshakes never send credentials or an HTTP request to this server.
    assert len(tls_fleet.calls) == 1
    assert tls_fleet.calls[0][2]["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize("ca_kind", ["missing", "invalid", "directory"])
def test_invalid_ca_fails_startup_without_exposing_path_or_token(monkeypatch, tmp_path, ca_kind):
    bridge_module(monkeypatch)
    ca_file = tmp_path / "confidential-local-ca.pem"
    if ca_kind == "invalid":
        ca_file.write_text("not a certificate\n" + TOKEN)
    elif ca_kind == "directory":
        ca_file.mkdir()
    process = subprocess.run(
        [sys.executable, "-m", "tangying_mcp"], input="", text=True, capture_output=True,
        env={**os.environ, "PYTHONPATH": str(PACKAGE_ROOT), "TANGYING_MCP_TOKEN": TOKEN,
             "TANGYING_MCP_FLEET_URL": "https://localhost:18080",
             "TANGYING_MCP_CA_FILE": str(ca_file)}, timeout=10, check=False,
    )
    assert process.returncode == 2
    assert process.stdout == ""
    assert "TANGYING_MCP_CA_FILE" in process.stderr
    assert TOKEN not in process.stderr
    assert "confidential-local-ca" not in process.stderr
    assert "Traceback" not in process.stderr


@pytest.mark.asyncio
async def test_response_limits_and_read_timeout(monkeypatch, fleet, tmp_path):
    module = bridge_module(monkeypatch)
    async with mcp_client(fleet, tmp_path, TANGYING_MCP_TIMEOUT_SECONDS="0.15") as session:
        for payload, expected in [
            ({"big": "x" * (module.MAX_RESPONSE_BYTES + 1)}, "RESPONSE_TOO_LARGE"),
            (42, "INVALID_RESPONSE"),
            ({"position": [float("nan"), 0, 0]}, "INVALID_RESPONSE"),
        ]:
            fleet.overrides["/v1/world"] = 200, payload, 0
            result = await session.call_tool("observe_world", {})
            assert result.isError
            assert result.structuredContent["error"]["code"] == expected
        fleet.overrides["/v1/world"] = 200, {}, 0.3
        timeout = await session.call_tool("observe_world", {})
        assert timeout.structuredContent["error"]["code"] == "FLEET_UNAVAILABLE"
        assert timeout.structuredContent["error"]["retryable"] is True


@pytest.mark.asyncio
async def test_failed_mutation_is_never_automatically_retried(monkeypatch, fleet, tmp_path):
    bridge_module(monkeypatch)
    fleet.overrides["/v1/tasks/task-123/cancel"] = 500, {"message": TOKEN}, 0
    async with mcp_client(fleet, tmp_path) as session:
        result = await session.call_tool("cancel_task", {"task_id": "task-123"})
        assert result.isError
        assert result.structuredContent["error"]["code"] == "OUTCOME_UNKNOWN"
        assert result.structuredContent["error"]["retryable"] is False
        assert TOKEN not in result.model_dump_json()
    assert len(fleet.calls) == 1
