from __future__ import annotations

import argparse
import importlib
import os
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .service import start_server
from .xlerobot_backend import XLeRobotDirectBackend


def load_callable(spec: str | None) -> Callable[..., Any] | None:
    if not spec:
        return None
    module_name, _, attribute = spec.partition(":")
    if not module_name or not attribute:
        raise ValueError(f"provider must look like 'module:function', got {spec!r}")
    module = importlib.import_module(module_name)
    return getattr(module, attribute)


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


def main() -> None:
    parser = argparse.ArgumentParser(description="ROS2-free Tangying Robot Edge")
    parser.add_argument("--listen", default=os.getenv("ROBOT_GRPC_LISTEN", "0.0.0.0:50051"))
    parser.add_argument(
        "--entity-provider",
        default=os.getenv("ROBOT_ENTITY_PROVIDER", ""),
        help="module:function returning scene entity dicts",
    )
    parser.add_argument(
        "--policy-provider",
        default=os.getenv("ROBOT_POLICY_PROVIDER", ""),
        help="module:function returning an action_chunk list",
    )
    parser.add_argument(
        "--verifier-provider",
        default=os.getenv("ROBOT_VERIFIER_PROVIDER", ""),
        help="module:function returning BackendResult for verify skills",
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

    if args.max_relative_target is not None:
        os.environ["XLEROBOT_MAX_RELATIVE_TARGET"] = str(args.max_relative_target)
    if args.max_action_chunk_length is not None:
        os.environ["XLEROBOT_MAX_ACTION_CHUNK_LENGTH"] = str(args.max_action_chunk_length)

    backend = XLeRobotDirectBackend.from_env(
        entity_provider=load_callable(args.entity_provider),
        policy=load_callable(args.policy_provider),
        verifier=load_callable(args.verifier_provider),
    )
    capabilities = backend.capabilities()
    if capabilities.blockers:
        print(
            f"xlerobot direct edge readiness: NOT_READY blockers={','.join(capabilities.blockers)}",
            flush=True,
        )
    else:
        print("xlerobot direct edge readiness: READY", flush=True)

    if args.allow_insecure:
        server = start_server(backend, args.listen, allow_insecure=True)
    else:
        server = start_server(
            backend,
            args.listen,
            server_key=Path(args.server_key),
            server_cert=Path(args.server_cert),
            client_ca=Path(args.client_ca),
        )

    stopping = False

    def shutdown(signum: int, _frame: object) -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        print(f"xlerobot direct edge shutting down after signal {signum}", flush=True)
        backend.stop("SERVICE_SHUTDOWN")
        server.stop(grace=2)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print(f"xlerobot direct edge listening on {args.listen}", flush=True)
    server.wait_for_termination()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
