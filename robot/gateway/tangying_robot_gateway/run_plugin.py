"""Validate or serve a trusted local adapter through the common gRPC runtime."""

from __future__ import annotations

import argparse
import importlib
import json
import signal
from pathlib import Path

from .backend import RobotBackend
from .contracts import RobotProfile, contract_schemas, validate_reconstruction
from .journal import RuntimeJournal
from .local_recovery import exclusive_runtime
from .runtime import ObservationRequest
from .service import RobotRuntimeService, start_server


def load_backend(spec: str) -> RobotBackend:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("factory must be a trusted local module:function")
    factory = getattr(importlib.import_module(module_name), attribute)
    if not callable(factory):
        raise TypeError("adapter factory is not callable")
    backend = factory()
    if not isinstance(backend, RobotBackend):
        raise TypeError("adapter factory must return RobotBackend")
    try:
        info = backend.capabilities()
        if not info.robot_profile:
            raise ValueError("plugin entry point requires a robot.profile.v1 adapter")
        RobotRuntimeService(backend)  # validates advertised identity, tools and immutable profile
    except Exception:
        _close(backend, "ADAPTER_CONTRACT_VALIDATION_FAILED")
        raise
    return backend


def check(args) -> dict:
    if args.profile:
        profile = RobotProfile.model_validate(json.loads(args.profile.read_text()))
        if args.observation:
            validate_reconstruction(json.loads(args.observation.read_text()), profile)
        return {"valid": True, "robotId": profile.robot_id, "modelId": profile.model_id,
                "tools": profile.tools, "observationChecked": args.observation is not None}
    if args.observation:
        raise ValueError("--observation requires --profile")
    backend = load_backend(args.factory)
    try:
        info = backend.capabilities()
        observation = backend.observe(ObservationRequest())
        validate_reconstruction(observation.reconstruction, info.robot_profile)
        return {"valid": True, "robotId": info.robot_id,
                "modelId": info.robot_profile["modelId"], "observationChecked": True,
                "mode": observation.semantic_state.mode,
                "availableTools": [item.name for item in info.capabilities if item.available],
                "blockers": info.blockers}
    finally:
        _close(backend, "ADAPTER_CONFORMANCE_CHECK_FINISHED")


def _close(backend, reason):
    try:
        backend.stop(reason)
    finally:
        disconnect = getattr(backend, "disconnect", None)
        if callable(disconnect):
            disconnect()


def serve(args) -> None:
    # The factory is trusted local application code, never a remotely supplied
    # argument. Ownership is acquired before any adapter lifecycle operation.
    with exclusive_runtime(args.journal):
        journal = RuntimeJournal(args.journal)
        backend = load_backend(args.factory)
        server = None
        previous = {}

        def shutdown(signum, frame):
            raise KeyboardInterrupt

        try:
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.getsignal(signum)
                signal.signal(signum, shutdown)
            server = start_server(
                backend, args.listen, journal=journal, server_key=args.server_key,
                server_cert=args.server_cert, client_ca=args.client_ca,
                allow_insecure=args.allow_insecure,
            )
            print(json.dumps({"listening": args.listen, "robotId": backend.capabilities().robot_id}), flush=True)
            server.wait_for_termination()
        except KeyboardInterrupt:
            pass
        finally:
            try:
                _close(backend, "SERVICE_SHUTDOWN")
            except Exception as exc:
                journal.set_estop(True, f"SERVICE_SHUTDOWN_FAILED: {exc}")
                raise
            finally:
                if server is not None:
                    server.stop(grace=2).wait()
                for signum, handler in previous.items():
                    signal.signal(signum, handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("schema", help="print profile, reconstruction and canonical tool JSON schemas")
    inspect = commands.add_parser("check", help="validate a profile or observe a trusted local adapter")
    source = inspect.add_mutually_exclusive_group(required=True)
    source.add_argument("--profile", type=Path)
    source.add_argument("--factory", help="trusted local module:function; constructs and closes the adapter")
    inspect.add_argument("--observation", type=Path)
    runtime = commands.add_parser("serve", help="serve an already commissioned adapter; never auto-arms it")
    runtime.add_argument("--factory", required=True, help="trusted local module:function")
    runtime.add_argument("--listen", default="127.0.0.1:50051")
    runtime.add_argument("--journal", type=Path, required=True)
    runtime.add_argument("--server-key", type=Path)
    runtime.add_argument("--server-cert", type=Path)
    runtime.add_argument("--client-ca", type=Path)
    runtime.add_argument("--allow-insecure", action="store_true", help="explicit development-only plaintext transport")
    args = parser.parse_args()
    try:
        if args.command == "schema":
            print(json.dumps(contract_schemas(), indent=2, ensure_ascii=False))
        elif args.command == "check":
            print(json.dumps(check(args), ensure_ascii=False))
        else:
            if not args.allow_insecure and not all((args.server_key, args.server_cert, args.client_ca)):
                raise ValueError("mTLS credentials are required unless --allow-insecure is explicit")
            serve(args)
    except (ValueError, TypeError, OSError, ImportError, AttributeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
