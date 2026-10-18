#!/usr/bin/env python3
"""Four real loopback exchange processes, three robot identities and scoped advice.

The harness exercises local durable communication and Python planning adapters.
It sends no ROS/controller commands and does not simulate physical robot dynamics.
"""
import argparse
import hashlib
import io
import json
import secrets
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

from run_edge_exchange_harness import available_ports, await_value, request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot/gateway"))
from tangying_robot_gateway.spatial_mission import CoverageLedger, EventJournal
from tangying_robot_gateway.spatial_peer_exchange import (
    PeerAdvisedExplorer,
    PeerExchangeClient,
    coverage_query,
    publish_capture,
)


def freeze_build_sources(output):
    """Freeze compiled non-stdlib dependencies; pin stdlib by Go toolchain version."""
    raw = subprocess.check_output(["go", "list", "-deps", "-json", "./cmd/edge-exchange"], cwd=ROOT).decode()
    decoder, packages, offset = json.JSONDecoder(), [], 0
    while raw[offset:].strip():
        offset += len(raw[offset:]) - len(raw[offset:].lstrip())
        package, offset = decoder.raw_decode(raw, offset)
        packages.append(package)
    files = {}
    for package in packages:
        module = package.get("Module")
        if package.get("Standard") or not module:
            continue
        module_root = Path(module.get("Dir", ROOT / "vendor" / module["Path"]))
        for field in ("GoFiles", "CgoFiles", "EmbedFiles", "SFiles", "HFiles"):
            for name in package.get(field, []):
                path = Path(package["Dir"]) / name
                if path.is_relative_to(ROOT):
                    archive_name = "project/" + str(path.relative_to(ROOT))
                else:
                    archive_name = "modules/" + module["Path"] + "@" + module["Version"] + "/" + str(path.relative_to(module_root))
                files[archive_name] = path
        if not module.get("Main"):
            for name in ("go.mod", "go.sum", "LICENSE", "LICENSE.txt", "COPYING"):
                path = module_root / name
                if path.is_file():
                    archive_name = ("project/" + str(path.relative_to(ROOT)) if path.is_relative_to(ROOT)
                                    else "modules/" + module["Path"] + "@" + module["Version"] + "/" + name)
                    files[archive_name] = path
    for relative in ("go.mod", "go.sum", "vendor/modules.txt", "scripts/run_edge_cooperation_harness.py", "scripts/run_edge_exchange_harness.py",
                     "robot/gateway/tangying_robot_gateway/__init__.py", "robot/gateway/tangying_robot_gateway/spatial_peer_exchange.py",
                     "robot/gateway/tangying_robot_gateway/spatial_mission.py", "robot/gateway/tangying_robot_gateway/spatial_navigation.py"):
        files["project/" + relative] = ROOT / relative
    records = []
    with tarfile.open(output / "frozen-build-source.tar.gz", "w:gz") as archive:
        for name, path in sorted(files.items()):
            content = path.read_bytes()
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(content), 0o644
            archive.addfile(info, io.BytesIO(content))
            records.append({"path": name, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)})
    manifest = {"schemaVersion": "edge.cooperation.source.v1", "files": records,
                "toolchain": subprocess.check_output(["go", "version"], cwd=ROOT, text=True).strip(),
                "pythonVersion": sys.version, "goEnvironment": json.loads(subprocess.check_output(
                    ["go", "env", "-json", "GOOS", "GOARCH", "CGO_ENABLED", "GOVERSION"], cwd=ROOT)),
                "standardLibrary": "pinned by toolchain version; not copied",
                "archiveSha256": hashlib.sha256((output / "frozen-build-source.tar.gz").read_bytes()).hexdigest()}
    (output / "frozen-build-source.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return [(files[record["path"]], record["sha256"]) for record in records]


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    started = time.time()
    frozen_files = freeze_build_sources(output)
    artifacts, processes, logs, events = {}, {}, [], []
    identities = {name: {"agentId": "edge-" + name, "robotId": name, "domain": "lab"}
                  for name in ("ugv", "uav", "legged", "cloud")}
    sources = []
    for directory in ("core/exchangecontract", "edge/exchange", "cmd/edge-exchange"):
        sources.extend(ROOT.glob(directory + "/*.go"))
    sources += [ROOT / path for path in (
        "middleware/sqlite/exchange.go", "middleware/sqlite/exchange_feed.go",
        "robot/gateway/tangying_robot_gateway/spatial_peer_exchange.py",
        "robot/gateway/tangying_robot_gateway/spatial_mission.py",
        "scripts/run_edge_cooperation_harness.py", "scripts/run_edge_exchange_harness.py")]
    source_hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in sources}
    with tempfile.TemporaryDirectory(prefix="edge-cooperation-") as temporary:
        private = Path(temporary)
        binary = private / "edge-exchange"
        subprocess.run(["go", "build", "-o", str(binary), "./cmd/edge-exchange"], cwd=ROOT, check=True)
        ports = available_ports(8)
        receive = dict(zip(identities, ports[::2], strict=True))
        admin = dict(zip(identities, ports[1::2], strict=True))
        token_names = ["admin-" + name for name in identities] + [
            a + "-to-" + b for a in identities for b in identities if a != b]
        tokens = {name: secrets.token_hex(32) for name in token_names}
        for name, token in tokens.items():
            path = private / name
            path.write_text(token)
            path.chmod(0o600)
        for name, identity in identities.items():
            configuration = {"identity": identity, "database": str(private / (name + ".db")),
                             "capacity": 100, "listen": f"127.0.0.1:{receive[name]}",
                             "adminListen": f"127.0.0.1:{admin[name]}",
                             "adminTokenFile": str(private / ("admin-" + name)),
                             "allowLoopbackHttp": True, "peers": [
                                 {"identity": identities[peer], "url": f"http://127.0.0.1:{receive[peer]}",
                                  "incomingTokenFile": str(private / f"{peer}-to-{name}"),
                                  "outgoingTokenFile": str(private / f"{name}-to-{peer}")}
                                 for peer in identities if peer != name]}
            (private / (name + ".json")).write_text(json.dumps(configuration))
        clients = {name: PeerExchangeClient(f"http://127.0.0.1:{admin[name]}", tokens["admin-" + name])
                   for name in identities}
        ledger = CoverageLedger("unknown-room-survey", "registered-world-1", [1, 2, 3], geometry_id="grid-1", max_age_ms=60000)
        query = coverage_query(ledger, mission_revision="contract-r1", basis={
            "worldId": "room", "frameId": "shared-map", "transformRevision": "registered-world-1",
            "mapId": "grid", "mapRevision": "1"})

        def status(name):
            return request(admin[name], tokens["admin-" + name], "status")

        def start(name):
            log = (output / (name + ".log")).open("ab")
            logs.append(log)
            processes[name] = subprocess.Popen([str(binary), "--config", str(private / (name + ".json"))],
                                               cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            await_value(lambda: status(name), lambda value: value["identity"] == identities[name])

        def stop(name):
            process = processes.pop(name)
            process.terminate()
            process.wait(timeout=8)
            if process.returncode:
                raise RuntimeError("exchange process did not stop gracefully")

        def export_capture(name, target, sequence):
            packet = {"sourceId": "depth", "sequence": sequence, "evidenceId": f"{name}-capture-{sequence}",
                      "worldFrameRevision": "registered-world-1", "geometryId": "grid-1",
                      "observedAtMs": int(time.time() * 1000), "classified": {target: 0}}
            peers = [identities[peer]["agentId"] for peer in identities if peer != name]
            receipts = publish_capture(clients[name], query, packet, peers)
            retry = publish_capture(clients[name], query, packet, peers)
            assert [v["messageId"] for v in receipts] == [v["messageId"] for v in retry]
            artifacts[f"{name}-capture-{sequence}"] = {"packet": packet, "receipts": receipts}

        def settled(name):
            return await_value(lambda: status(name), lambda value: value["counts"]["pending"] == 0)

        try:
            for name in identities:
                start(name)
            for target, name in enumerate(("ugv", "uav", "legged"), 1):
                export_capture(name, target, 1)
            for name in ("ugv", "uav", "legged"):
                settled(name)
            views = {name: clients[name].coverage_view(query) for name in identities}
            assert views["cloud"]["proposedCoveredTargetIds"] == ["1", "2", "3"]
            assert all(len(views[name]["proposedCoveredTargetIds"]) == 2 for name in ("ugv", "uav", "legged"))
            assert all(view["completeRead"] and view["advisoryOnly"] and not view["physicalCompletion"] for view in views.values())
            events.append({"case": "three-heterogeneous-identities-to-peer-and-cloud-consumption", "views": views})

            class Explorer:
                def propose(self, _pose, **_arguments):
                    return {"candidates": [
                        {"targetId": "peer-covered", "score": 3., "expectedObservedVoxelIds": [2]},
                        {"targetId": "local-needed", "score": 2.8, "expectedObservedVoxelIds": [1]}]}

            with_advice = EventJournal(output / "planning-advice.jsonl")
            proposal = PeerAdvisedExplorer(Explorer(), clients["ugv"], query,
                                           journal=with_advice).propose([0], observed_targets=ledger.observed_ids)
            with_advice.close()
            assert proposal["candidates"][0]["targetId"] == "local-needed"
            assert ledger.summary()["observedVoxels"] == 0
            events.append({"case": "actual-python-explorer-adapter-rescores-without-completing-local-ledger",
                           "proposal": proposal, "localLedger": ledger.summary()})
            page1 = request(admin["cloud"], tokens["admin-cloud"], "feed?after=0&limit=2")
            page2 = request(admin["cloud"], tokens["admin-cloud"], "feed?after=" + page1["next"] + "&limit=2")
            assert page1["more"] and len(page1["items"]) == 2 and len(page2["items"]) == 1
            cursor = page2["next"]
            stop("cloud")
            for target, name in enumerate(("ugv", "uav", "legged"), 1):
                export_capture(name, target, 2)
            for name in ("ugv", "uav", "legged"):
                await_value(lambda name=name: status(name), lambda value: value["counts"]["pending"] == 1)
            # Sender restart retains the exact queued envelope; peers still progress.
            stop("ugv")
            start("ugv")
            assert status("ugv")["counts"]["pending"] == 1
            start("cloud")
            for name in ("ugv", "uav", "legged"):
                settled(name)
            recovered = request(admin["cloud"], tokens["admin-cloud"], "feed?after=" + cursor + "&limit=10")
            assert len(recovered["items"]) == 3 and recovered["head"] == "6"
            events.append({"case": "offline-cloud-and-sender-restart-preserve-paginated-consumption",
                           "cursorBefore": cursor, "feedAfter": recovered})

            baseline = artifacts["ugv-capture-2"]["receipts"][-1]
            # Each negative report uses a separate stream, avoiding accidental
            # supersession of the valid report under the last-value stream model.
            for case in ("expired", "wrong-frame", "wrong-mission", "stale-capture", "outside-scope"):
                payload = dict(baseline["payload"])
                payload["observedAtMs"] = int(time.time() * 1000)
                basis = dict(query["basis"])
                if case == "wrong-frame":
                    basis["frameId"] = "unregistered-map"
                if case == "wrong-mission":
                    payload["missionRevision"] = "obsolete-contract"
                if case == "stale-capture":
                    payload["observedAtMs"] = int(time.time() * 1000) - 120000
                if case == "outside-scope":
                    payload["observedTargetIds"] = ["999"]
                clients["ugv"].publish({"requestId": case, "peerId": identities["cloud"]["agentId"],
                                        "stream": "negative/" + case, "kind": "observation",
                                        "ttlMs": 1500 if case == "expired" else 60000,
                                        "basis": basis, "payload": payload})
                if case == "expired":
                    settled("ugv")
                    assert any(item["stream"] == "negative/expired" for item in request(
                        admin["cloud"], tokens["admin-cloud"], "inbox"))
            settled("ugv")
            time.sleep(1.6)
            # Refresh legitimate captures so only the deliberately stale data is
            # stale even on a slow build machine with the 5 second local age bound.
            for target, name in enumerate(("ugv", "uav", "legged"), 1):
                export_capture(name, target, 3)
                settled(name)
            view = clients["cloud"].coverage_view(query)
            reasons = {entry["reason"] for entry in view["rejected"]}
            assert {"spatial_basis_mismatch", "task_scope_mismatch", "observation_not_fresh",
                    "target_outside_scope", "envelope_not_fresh"} <= reasons
            assert view["proposedCoveredTargetIds"] == ["1", "2", "3"]
            incomplete = clients["cloud"].coverage_view({**query, "limit": 1})
            assert incomplete["completeRead"] is False and incomplete["proposedCoveredTargetIds"] == []
            events.append({"case": "scope-freshness-and-bounded-read-refusals", "view": view,
                           "incompleteView": incomplete})
            summary = {"schemaVersion": "edge.cooperation.experiment.v1", "passed": True,
                       "startedAtUnix": started, "elapsedSeconds": round(time.time() - started, 3),
                       "transport": "explicit-loopback-http-development", "processCount": 4,
                       "robotIdentities": ["ugv", "uav", "legged"], "hardwareValidation": False,
                       "physicalMotion": False, "wirelessValidation": False, "ddsValidation": False,
                       "events": events, "captures": artifacts,
                       "sourceHashes": source_hashes,
                       "binarySha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
            if any(hashlib.sha256(path.read_bytes()).hexdigest() != digest for path, digest in frozen_files):
                raise RuntimeError("compiled source changed during campaign")
            if source_hashes != {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in sources}:
                raise RuntimeError("source changed during campaign; retain outputs but rerun before claiming evidence")
            (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
            print(json.dumps({key: value for key, value in summary.items()
                              if key not in {"events", "captures", "sourceHashes"}}, indent=2))
        finally:
            for process in processes.values():
                process.terminate()
            for process in processes.values():
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            for log in logs:
                log.close()
            # Partial evidence survives a failed assertion; no silent rerun/overwrite.
            (output / "events.json").write_text(json.dumps(events, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output.resolve())
