#!/usr/bin/env python3
"""Inject bounded read failures between an agent and a real Gazebo runtime.

Example (create /tmp/gazebo-read-fault.arm only when the task is ready):

    .venv/bin/python scripts/gazebo_read_fault_proxy.py \
        --runtime 127.0.0.1:50161 --listen 127.0.0.1:50162 \
        --output artifacts/read-faults.jsonl \
        --arm-file /tmp/gazebo-read-fault.arm

Point the agent's runtime connection at the proxy. Only the chosen catalogued
read service, called with a ``task-`` request ID, can fail. Failures happen before
forwarding, so no command is executed twice. This does not launch a simulator;
unit tests use an explicitly fake runtime and are not Gazebo acceptance evidence.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import signal
import sys
import threading
import time
from concurrent import futures
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

import grpc
from tangying_robot_proto.robot.v1 import robot_pb2 as pb
from tangying_robot_proto.robot.v1 import robot_pb2_grpc as pbg

# grpc-timeout has at most eight digits; hours are its largest unit.
# https://github.com/grpc/grpc/blob/master/doc/PROTOCOL-HTTP2.md#requests
MAX_WIRE_TIMEOUT_SECONDS = 99_999_999 * 3600


def loopback_endpoint(value: str, *, allow_zero: bool = False) -> str:
    """Accept only numeric loopback hosts (or canonicalize localhost)."""
    try:
        if value.startswith("["):
            host, port_text = value[1:].split("]:", 1)
        else:
            host, port_text = value.rsplit(":", 1)
            if ":" in host:
                raise ValueError("IPv6 addresses need brackets")
        address = ipaddress.ip_address("127.0.0.1" if host == "localhost" else host)
        port = int(port_text)
        if not address.is_loopback or not (0 if allow_zero else 1) <= port <= 65535:
            raise ValueError("not a loopback host and valid port")
    except (ValueError, TypeError) as error:
        raise ValueError(f"endpoint must be a loopback host:port: {value!r}") from error
    host_text = f"[{address}]" if address.version == 6 else str(address)
    return f"{host_text}:{port}"


class FaultJournal:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("a", encoding="utf-8")
        self._lock = threading.Lock()

    def append(self, event: str, **fields) -> None:
        # Never include metadata, parameters, service results, or credentials.
        record = {
            "event": event,
            "time_utc": datetime.now(UTC).isoformat(),
            "monotonic_ns": time.monotonic_ns(),
            **fields,
        }
        with self._lock:
            self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._file.flush()

    def close(self) -> None:
        with self._lock:
            self._file.close()


class ReadFaultProxy(pbg.RobotRuntimeServicer):
    def __init__(
        self, stub, service: str, fail_count: int, journal: FaultJournal, arm_file: Path | None
    ):
        self.stub = stub
        self.service = service
        self.fail_count = fail_count
        self.journal = journal
        self.arm_file = arm_file
        self.injected = 0
        self._fault_lock = threading.Lock()

    @staticmethod
    def _options(context):
        remaining = context.time_remaining()
        # C-core represents an absent deadline with INT64_MAX seconds, exposed
        # here as a finite float near 9.22e18. Forwarding that sentinel as a
        # Python timeout can overflow C-core's int64 nanosecond conversion and
        # expire immediately. It exceeds every encodable grpc-timeout, so preserve
        # its meaning with None while leaving real/expired deadlines intact.
        timeout = (remaining if remaining is not None and math.isfinite(remaining)
                   and remaining <= MAX_WIRE_TIMEOUT_SECONDS else None)
        return {
            "metadata": context.invocation_metadata(),
            "timeout": timeout,
        }

    @staticmethod
    def _attach_cancel(call, context):
        # add_callback returns False if the downstream already went away.
        if not context.add_callback(call.cancel):
            call.cancel()

    @staticmethod
    def _abort(error, context):
        context.set_trailing_metadata(error.trailing_metadata() or ())
        context.abort(error.code(), error.details() or "upstream RPC failed")

    def _unary(self, method, request, context):
        call = method.future(request, **self._options(context))
        self._attach_cancel(call, context)
        try:
            context.send_initial_metadata(call.initial_metadata() or ())
            response = call.result()
            context.set_trailing_metadata(call.trailing_metadata() or ())
            return response
        except grpc.FutureCancelledError:
            context.abort(grpc.StatusCode.CANCELLED, "downstream cancelled")
        except grpc.RpcError as error:
            self._abort(error, context)
        finally:
            call.cancel()

    def _stream(self, method, request, context):
        call = method(request, **self._options(context))
        self._attach_cancel(call, context)
        try:
            context.send_initial_metadata(call.initial_metadata() or ())
            yield from call
            context.set_trailing_metadata(call.trailing_metadata() or ())
        except grpc.RpcError as error:
            self._abort(error, context)
        finally:
            call.cancel()

    def GetRuntimeInfo(self, request, context):
        return self._unary(self.stub.GetRuntimeInfo, request, context)

    def ListServices(self, request, context):
        return self._unary(self.stub.ListServices, request, context)

    def Observe(self, request, context):
        yield from self._stream(self.stub.Observe, request, context)

    def ExecuteSkill(self, request, context):
        yield from self._stream(self.stub.ExecuteSkill, request, context)

    def Cancel(self, request, context):
        return self._unary(self.stub.Cancel, request, context)

    def EmergencyStop(self, request, context):
        return self._unary(self.stub.EmergencyStop, request, context)

    def CallService(self, request, context):
        inject = False
        if request.name == self.service and request.request_id.startswith("task-"):
            with self._fault_lock:
                armed = self.arm_file is None or self.arm_file.is_file()
                if armed and self.injected < self.fail_count and context.is_active():
                    self.journal.append(
                        "fault_injected",
                        service=request.name,
                        request_id=request.request_id,
                        injection_number=self.injected + 1,
                        fail_count=self.fail_count,
                        grpc_status="UNAVAILABLE",
                        forwarded=False,
                    )
                    self.injected += 1
                    inject = True
        if inject:
            context.abort(
                grpc.StatusCode.UNAVAILABLE,
                "injected Gazebo read-service outage; request was not forwarded",
            )
        return self._unary(self.stub.CallService, request, context)


@dataclass
class ProxyServer:
    server: grpc.Server
    channel: grpc.Channel
    journal: FaultJournal
    address: str

    def stop(self):
        self.server.stop(grace=2).wait()
        self.channel.close()
        self.journal.close()


def start_proxy(
    *,
    runtime: str,
    listen: str,
    output: Path,
    service: str = "navigation.status",
    fail_count: int = 1,
    arm_file: Path | None = None,
    startup_timeout: float = 10,
) -> ProxyServer:
    """Validate the real wire contract before opening a listener or fault log."""
    runtime = loopback_endpoint(runtime)
    listen = loopback_endpoint(listen, allow_zero=True)
    if runtime == listen:
        raise ValueError("runtime and listen endpoints must differ")
    if fail_count < 1:
        raise ValueError("fail-count must be positive")
    if startup_timeout <= 0 or not math.isfinite(startup_timeout):
        raise ValueError("startup-timeout must be finite and positive")
    # RGB-D observations may exceed gRPC's default 4 MiB receive limit. Preserve
    # the caller/runtime contract instead of imposing a new limit in the relay.
    message_options = (
        ("grpc.max_receive_message_length", -1),
        ("grpc.max_send_message_length", -1),
    )
    channel = grpc.insecure_channel(runtime, options=message_options)
    journal = None
    server = None
    try:
        stub = pbg.RobotRuntimeStub(channel)
        info = stub.GetRuntimeInfo(pb.GetRuntimeInfoRequest(), timeout=startup_timeout)
        if info.adapter != "gazebo":
            raise ValueError("fault injection requires RuntimeInfo.adapter == gazebo")
        catalog = stub.ListServices(pb.GetRuntimeInfoRequest(), timeout=startup_timeout)
        matches = [entry for entry in catalog.services if entry.name == service]
        if len(matches) != 1:
            raise ValueError(f"service {service!r} must occur exactly once in the runtime catalog")
        if matches[0].mutates_world:
            raise ValueError(f"refusing to inject faults into mutating service {service!r}")
        journal = FaultJournal(Path(output))
        server = grpc.server(
            futures.ThreadPoolExecutor(max_workers=16),
            options=(*message_options, ("grpc.so_reuseport", 0)),
        )
        pbg.add_RobotRuntimeServicer_to_server(
            ReadFaultProxy(stub, service, fail_count, journal, arm_file),
            server,
        )
        port = server.add_insecure_port(listen)
        if not port:
            raise ValueError("could not bind proxy listener")
        address = listen.rsplit(":", 1)[0] + f":{port}"
        journal.append(
            "proxy_ready",
            runtime=runtime,
            listen=address,
            adapter=info.adapter,
            service=service,
            mutates_world=False,
            fail_count=fail_count,
            arm_file_required=arm_file is not None,
        )
        server.start()
        return ProxyServer(server, channel, journal, address)
    except Exception:
        if server is not None:
            server.stop(grace=0).wait()
        channel.close()
        if journal is not None:
            journal.close()
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--runtime", default="127.0.0.1:50161")
    parser.add_argument("--listen", default="127.0.0.1:50162")
    parser.add_argument("--service", default="navigation.status")
    parser.add_argument("--fail-count", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--arm-file", type=Path)
    parser.add_argument("--startup-timeout", type=float, default=10)
    args = parser.parse_args(argv)
    try:
        proxy = start_proxy(**vars(args))
    except (ValueError, OSError, RuntimeError, grpc.RpcError) as error:
        print(f"Gazebo fault proxy refused to start: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "event": "proxy_ready",
                "listen": proxy.address,
                "service": args.service,
                "fail_count": args.fail_count,
                "output": str(args.output),
                "arm_file": str(args.arm_file) if args.arm_file else None,
            }
        ),
        flush=True,
    )
    stopped = threading.Event()
    previous_handlers = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.signal(signum, lambda *_: stopped.set())
    try:
        stopped.wait()
    finally:
        proxy.stop()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
