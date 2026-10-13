#!/usr/bin/env python3
"""Run two actual edge-exchange processes with offline queueing and restarts.

Uses explicit loopback HTTP development mode and no robot Runtime. Secrets and
SQLite databases live in a private temporary directory; only redacted evidence
is saved to the requested output directory.
"""

import argparse
import json
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def available_ports(count):
    sockets = []
    try:
        for _ in range(count):
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def request(port, token, route, payload=None):
    raw = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/exchange/{route}", data=raw,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=2) as response:
        return json.load(response)


def await_value(operation, predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = operation()
            if predicate(value):
                return value
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.1)
    raise RuntimeError("timed out waiting for exchange invariant")


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    events = []
    processes = {}
    log_files = []
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="edge-exchange-harness-") as temp:
        directory = Path(temp)
        binary = directory / "edge-exchange"
        subprocess.run(["go", "build", "-o", str(binary), "./cmd/edge-exchange"], cwd=ROOT, check=True)
        receive_a, admin_a, receive_b, admin_b = available_ports(4)
        identities = {
            "a": {"agentId": "edge-a", "robotId": "ugv-a", "domain": "warehouse"},
            "b": {"agentId": "cloud-b", "robotId": "fleet", "domain": "cloud"},
        }
        tokens = {name: secrets.token_hex(32) for name in ("admin-a", "admin-b", "a-to-b", "b-to-a")}
        for name, value in tokens.items():
            path = directory / name
            path.write_text(value)
            path.chmod(0o600)
        for name, remote, local_port, local_admin, remote_port in (
            ("a", "b", receive_a, admin_a, receive_b),
            ("b", "a", receive_b, admin_b, receive_a),
        ):
            config = {
                "identity": identities[name], "database": str(directory / (name + ".db")),
                "capacity": 100, "listen": f"127.0.0.1:{local_port}",
                "adminListen": f"127.0.0.1:{local_admin}",
                "adminTokenFile": str(directory / ("admin-" + name)),
                "allowLoopbackHttp": True,
                "peers": [{"identity": identities[remote], "url": f"http://127.0.0.1:{remote_port}",
                           "incomingTokenFile": str(directory / f"{remote}-to-{name}"),
                           "outgoingTokenFile": str(directory / f"{name}-to-{remote}")}],
            }
            (directory / (name + ".json")).write_text(json.dumps(config))

        def start(name):
            log = (output / (name + ".log")).open("ab")
            log_files.append(log)
            processes[name] = subprocess.Popen([str(binary), "--config", str(directory / (name + ".json"))], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            port = admin_a if name == "a" else admin_b
            await_value(lambda: request(port, tokens["admin-" + name], "status"), lambda value: value["identity"] == identities[name])

        def stop(name):
            process = processes.pop(name)
            process.terminate()
            process.wait(timeout=8)
            if process.returncode != 0:
                raise RuntimeError(f"process {name} failed during shutdown")

        def publish(identifier):
            return request(admin_a, tokens["admin-a"], "publish", {
                "requestId": identifier, "peerId": "cloud-b", "stream": "mission-status",
                "kind": "mission_status", "ttlMs": 60000,
                "payload": {"taskId": "long-mission-1", "status": "awaiting-local-verification"},
            })

        try:
            start("a")
            first = publish("checkpoint-1")
            second = publish("checkpoint-2")
            offline = request(admin_a, tokens["admin-a"], "status")
            assert offline["counts"]["pending"] == 2
            events.append({"case": "receiver-offline", "pending": 2})
            stop("a")
            start("a")
            assert publish("checkpoint-1")["messageId"] == first["messageId"]
            start("b")
            delivered = await_value(lambda: request(admin_a, tokens["admin-a"], "status"), lambda v: v["counts"]["delivered"] == 2)
            inbox = request(admin_b, tokens["admin-b"], "inbox")
            assert [item["sequence"] for item in inbox] == ["2", "1"]
            assert {item["messageId"] for item in inbox} == {first["messageId"], second["messageId"]}
            events.append({"case": "sender-restart-offline-queue-delivery", "counts": delivered["counts"]})
            stop("b")
            start("b")
            assert len(request(admin_b, tokens["admin-b"], "inbox")) == 2
            third = publish("checkpoint-3")
            assert third["sequence"] == "3"
            await_value(lambda: request(admin_a, tokens["admin-a"], "status"), lambda v: v["counts"]["delivered"] == 3)
            events.append({"case": "receiver-restart-and-monotonic-sequence", "sequence": "3"})
            reverse = request(admin_b, tokens["admin-b"], "publish", {
                "requestId": "cloud-data-1", "peerId": "edge-a", "stream": "mission-advice",
                "kind": "evidence", "ttlMs": 60000, "payload": {"taskId": "long-mission-1", "note": "advisory-only"},
            })
            edge_inbox = await_value(lambda: request(admin_a, tokens["admin-a"], "inbox"), lambda v: len(v) == 1)
            assert edge_inbox[0]["messageId"] == reverse["messageId"]
            events.append({"case": "bidirectional-cloud-edge-data", "inbox": 1})
            summary = {"schemaVersion": "edge.exchange.experiment.v1", "passed": True,
                       "startedAtUnix": started, "elapsedSeconds": round(time.time() - started, 3),
                       "transport": "explicit-loopback-http-development", "robotMotion": False,
                       "events": events, "aStatus": request(admin_a, tokens["admin-a"], "status"),
                       "bStatus": request(admin_b, tokens["admin-b"], "status")}
            (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
            print(json.dumps(summary, indent=2))
        finally:
            for process in processes.values():
                process.terminate()
            for process in processes.values():
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            for log in log_files:
                log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output.resolve())
