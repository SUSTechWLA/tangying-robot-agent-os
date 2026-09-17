from __future__ import annotations

import argparse
import importlib
import os
import signal
import socket
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import beacon
from .journal import RuntimeJournal
from .local_recovery import exclusive_runtime, reset_local
from .service import start_server
from .xlerobot_backend import XLeRobotDirectBackend


def load_callable(spec: str | None) -> Callable[..., Any] | None:
    if not spec:
        return None
    module_name, _, attribute = spec.partition(":")
    if not module_name or not attribute:
        raise ValueError(f"provider must look like 'module:function', got {spec!r}")
    module = importlib.import_module(module_name)
    provider = getattr(module, attribute)
    if not callable(provider):
        raise TypeError(f"provider is not callable: {spec!r}")
    return provider


def _float_env(name: str, default: str) -> float | None:
    value = os.getenv(name, default).strip()
    if not value:
        return None
    return float(value)


def _int_env(name: str, default: str) -> int | None:
    value = os.getenv(name, default).strip()
    if not value:
        return None
    return int(value)


def prepare_hardware(backend, journal, *, connect: bool, arm: bool, operator_present: bool, interactive: bool) -> None:
    """Explicit local lifecycle; no import, health check, or restart enables torque."""
    if arm:
        if not connect or not operator_present or not interactive:
            raise ValueError("--arm requires --connect --operator-present and an interactive local terminal")
        if journal.estop_latched:
            raise ValueError(f"cannot arm with persisted safety latch: {journal.estop_reason}; investigate and use local --reset-stop first")
        if backend.entity_provider is None or backend.verifier is None:
            raise ValueError("configure perception and verification providers before arming")
    if connect:
        result = backend.driver.connect()
        if not result.success:
            raise RuntimeError(f"torque-off connection failed: {result.code}: {result.message}")
    if arm:
        result = backend.driver.arm(operator_present=True)
        if not result.success:
            raise RuntimeError(f"arm failed: {result.code}: {result.message}")


def main() -> None:
    parser = argparse.ArgumentParser(description="ROS2-free Tangying Robot Edge")
    parser.add_argument("--connect", action="store_true", help="open robot ports with torque disabled for observation")
    parser.add_argument("--arm", action="store_true", help="explicitly enable arm/head torque for this attended process; never enables wheels")
    parser.add_argument("--operator-present", action="store_true", help="local operator has checked the work area and physical E-stop")
    parser.add_argument("--reset-stop", action="store_true", help="audit and reset the persisted stop locally; never opens robot ports")
    parser.add_argument("--operator", default="", help="operator name recorded for local recovery")
    parser.add_argument("--reset-reason", default="", help="inspection and reconciliation reason recorded for local recovery")
    parser.add_argument("--listen", default=os.getenv("ROBOT_GRPC_LISTEN", "0.0.0.0:50051"))
    parser.add_argument(
        "--entity-provider",
        default=os.getenv("ROBOT_ENTITY_PROVIDER", ""),
        help="module:function returning scene entity dicts",
    )
    parser.add_argument(
        "--verifier-provider",
        default=os.getenv("ROBOT_VERIFIER_PROVIDER", ""),
        help="module:function returning BackendResult for verify skills",
    )
    parser.add_argument(
        "--journal",
        default=os.getenv(
            "ROBOT_RUNTIME_JOURNAL",
            "/var/lib/tangying-robot-agent-os/runtime-journal.json",
        ),
    )
    parser.add_argument(
        "--server-key",
        default=os.getenv(
            "ROBOT_SERVER_KEY",
            "/var/lib/tangying-robot-agent-os/certs/server.key",
        ),
    )
    parser.add_argument(
        "--server-cert",
        default=os.getenv(
            "ROBOT_SERVER_CERT",
            "/var/lib/tangying-robot-agent-os/certs/server.crt",
        ),
    )
    parser.add_argument(
        "--client-ca",
        default=os.getenv(
            "ROBOT_CLIENT_CA",
            "/var/lib/tangying-robot-agent-os/certs/client-ca.crt",
        ),
    )
    parser.add_argument(
        "--allow-insecure",
        action="store_true",
        default=os.getenv("ROBOT_ALLOW_INSECURE", "") == "1",
    )
    parser.add_argument(
        "--max-relative-target",
        type=float,
        default=_float_env("XLEROBOT_MAX_RELATIVE_TARGET", "8.0"),
        help="maximum relative joint target accepted from a policy",
    )
    parser.add_argument(
        "--max-action-chunk-length",
        type=int,
        default=_int_env("XLEROBOT_MAX_ACTION_CHUNK_LENGTH", "64"),
        help="maximum number of actions accepted in one command",
    )
    args = parser.parse_args()
    if args.reset_stop and (args.connect or args.arm):
        parser.error("--reset-stop cannot be combined with --connect or --arm")

    if args.max_relative_target is not None:
        os.environ["XLEROBOT_MAX_RELATIVE_TARGET"] = str(args.max_relative_target)
    if args.max_action_chunk_length is not None:
        os.environ["XLEROBOT_MAX_ACTION_CHUNK_LENGTH"] = str(args.max_action_chunk_length)

    backend = XLeRobotDirectBackend.from_env(
        entity_provider=None if args.reset_stop else load_callable(args.entity_provider),
        verifier=None if args.reset_stop else load_callable(args.verifier_provider),
    )
    # Hold process ownership before reading safety history through all cleanup.
    with exclusive_runtime(Path(args.journal)):
        journal = RuntimeJournal(Path(args.journal))
        if args.reset_stop:
            reset_local(backend, journal, operator_present=args.operator_present,
                        interactive=sys.stdin.isatty(), operator=args.operator, reason=args.reset_reason)
            print("local safety reset recorded; robot remains disconnected and unarmed", flush=True)
            return
        _serve(backend, journal, args)


def _announcement_identity(backend, args, *, cert_directory: Path) -> beacon.RobotIdentity:
    """What this robot tells the network about itself.

    Read once at startup rather than per announcement: the identity, the adapter
    and the address a peer should use do not change while the process runs, and
    recomputing them every five seconds would only add a way for them to disagree
    with themselves.
    """
    robot_id, source = beacon.default_robot_id()
    if source != "configured":
        # Which path produced the identity is the difference between "someone
        # chose this" and "this is a fallback", and a robot that will not appear
        # in the agent's list is diagnosed from this line.
        print(f"robot identity from {source}: {robot_id}", flush=True)
    capabilities = backend.capabilities()
    tools = getattr(capabilities, "tools", None)
    return beacon.RobotIdentity(
        robot_id=robot_id,
        hostname=socket.gethostname(),
        address=beacon.resolve_address(
            args.listen, override=os.getenv("ROBOT_ANNOUNCE_ADDRESS") or None
        ),
        adapter=beacon.adapter_environment(),
        capability_count=len(tools) if tools is not None else 0,
        # A robot is only open to pairing inside a window someone deliberately
        # opened. An unpaired robot announces that it is unpaired — which is
        # useful, because the agent can then say "this robot needs pairing"
        # instead of showing nothing — but announcing "open" by default would
        # invite the first thing on the network to try.
        pairing_state=beacon.pairing_state(
            cert_directory,
            open_for_pairing=os.getenv("ROBOT_PAIRING_WINDOW", "") == "1",
        ),
    )


def _start_announcing(backend, args, *, cert_directory: Path):
    """Start announcing, or say why not and carry on.

    Announcing is a convenience, never a dependency. An agent can always be
    pointed at this robot's address by hand, so a robot whose discovery could not
    start must still serve — refusing to run because a broadcast socket could not
    be opened would turn an inconvenience into an outage. This is the same rule
    the agent side follows about its own observers.
    """
    try:
        identity = _announcement_identity(backend, args, cert_directory=cert_directory)
        announcer = beacon.Announcer(identity_provider=lambda: identity)
        announcer.start()
        print(
            f"announcing {identity.robot_id} at {identity.address} "
            f"(pairing={identity.pairing_state})",
            flush=True,
        )
        return announcer
    except Exception as exc:  # noqa: BLE001 - discovery must never take the server down
        print(f"robot announcement not started: {exc}", flush=True)
        return None


def _serve(backend, journal, args) -> None:
    server = None
    announcer = None
    stopping = False
    previous_handlers = {}

    def shutdown(signum: int, _frame: object) -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        # Unwind into normal cleanup; never perform reentrant serial I/O from
        # a signal handler while a bus transaction or arm operation is active.
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
        raise SystemExit(128 + signum)

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, shutdown)
        prepare_hardware(backend, journal, connect=args.connect, arm=args.arm,
                         operator_present=args.operator_present, interactive=sys.stdin.isatty())
        capabilities = backend.capabilities()
        if capabilities.blockers:
            print(
                f"xlerobot direct edge readiness: NOT_READY blockers={','.join(capabilities.blockers)}",
                flush=True,
            )
        else:
            print("xlerobot direct edge capabilities: AVAILABLE; physical commissioning is not certified", flush=True)
        if args.allow_insecure:
            server = start_server(backend, args.listen, journal=journal, allow_insecure=True)
        else:
            server = start_server(
                backend, args.listen, journal=journal,
                server_key=Path(args.server_key), server_cert=Path(args.server_cert),
                client_ca=Path(args.client_ca),
            )
        print(f"xlerobot direct edge listening on {args.listen}", flush=True)
        # Announcing starts only once the server is accepting connections. A robot
        # that announced before it could be connected to would send an agent to an
        # address that refuses it, which looks like a network fault and is not one.
        announcer = _start_announcing(
            backend, args, cert_directory=Path(getattr(args, "server_cert", "")).parent
        )
        server.wait_for_termination()
    finally:
        stopping = True
        if announcer is not None:
            # Stopped before the server so a robot on its way down stops
            # advertising itself first: the alternative is an agent connecting to
            # a server that is shutting down.
            announcer.stop()
        errors = []
        try:
            backend.stop("SERVICE_SHUTDOWN")
        except Exception as exc:  # noqa: BLE001 - still shut down server and both buses
            errors.append(f"stop: {exc}")
        if server is not None:
            try:
                server.stop(grace=2)
            except Exception as exc:  # noqa: BLE001 - disconnect even if server shutdown fails
                errors.append(f"server: {exc}")
        try:
            backend.driver.disconnect()
        except Exception as exc:  # noqa: BLE001 - retain physical cleanup failure
            errors.append(f"disconnect: {exc}")
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
        if errors:
            reason = "SERVICE_SHUTDOWN_FAILED: " + "; ".join(errors)
            try:
                journal.set_estop(True, reason)
            except Exception as exc:  # noqa: BLE001 - retain cleanup and persistence failures
                reason += f"; journal: {exc}"
            raise RuntimeError(reason)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
