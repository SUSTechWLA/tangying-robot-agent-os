"""Two gRPC Robot Runtimes backed by one RoboCasa shared world."""

from __future__ import annotations

import argparse
import os
import signal
import threading
from concurrent import futures
from pathlib import Path

import grpc
from tangying_robot_proto.robot.v1 import robot_pb2_grpc
from tangying_sim.server import RobotRuntimeService

from .checkpoint import CheckpointStore
from .composer import SceneConfig, compose_handoff_scene
from .world import RoboCasaRobotView, RoboCasaSharedWorld


def create_fleet_services(*, seed: int = 7, human_speed: float = 0.0):
    scene = compose_handoff_scene(SceneConfig(seed=seed))
    world = RoboCasaSharedWorld.from_scene(scene, seed=seed, human_speed=human_speed)
    # Higher-resolution god-view frames: the overview camera renders the
    # whole kitchen from the physical engine, and the console shows it as
    # the main live picture (not just placeholder boxes).
    # 分辨率由环境变量控制；默认保持 320x240 与既有行为一致（macOS Metal
    # 渲染在此环境间歇不稳定，高分辨率在用户环境稳定后可调）。
    render_width = int(os.environ.get("ROBOCASA_RENDER_WIDTH", "320"))
    render_height = int(os.environ.get("ROBOCASA_RENDER_HEIGHT", "240"))
    services = {
        robot_id: RobotRuntimeService(
            RoboCasaRobotView(world, robot_id),
            robot_id=robot_id,
            adapter="robocasa",
            cameras=("overview", f"{robot_id}-evidence"),
            allow_monotonic_grant_adoption=True,
            render_width=render_width,
            render_height=render_height,
        )
        for robot_id in ("robot-1", "robot-2")
    }

    def update_runtime_grants(owner: str, token: int) -> None:
        for service in services.values():
            service.register_resource(world.RESOURCE_ID, owner=owner, token=token)

    initial_owner, initial_token = world.command_grant()
    update_runtime_grants(initial_owner, initial_token)
    world.add_grant_listener(update_runtime_grants)
    return world, services


def serve_fleet(
    sender_address: str,
    receiver_address: str,
    *,
    seed: int = 7,
    human_speed: float = 0.0,
    checkpoint_path: str | None = None,
) -> None:
    world, services = create_fleet_services(seed=seed, human_speed=human_speed)
    checkpoint = CheckpointStore(Path(checkpoint_path)) if checkpoint_path else None
    if checkpoint is not None and checkpoint.path.exists():
        checkpoint.restore(world)
    servers: list[grpc.Server] = []
    stop_requested = threading.Event()
    previous_handlers: dict[signal.Signals, object] = {}

    def request_stop(_signum, _frame) -> None:
        stop_requested.set()

    if threading.current_thread() is threading.main_thread():
        for handled in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[handled] = signal.getsignal(handled)
            signal.signal(handled, request_stop)
    for robot_id, address in (
        ("robot-1", sender_address),
        ("robot-2", receiver_address),
    ):
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
        robot_pb2_grpc.add_RobotRuntimeServicer_to_server(services[robot_id], server)
        if server.add_insecure_port(address) == 0:
            raise RuntimeError(f"could not bind {robot_id} RoboCasa runtime to {address}")
        server.start()
        servers.append(server)
    checkpoint_saved = False
    try:
        while not stop_requested.is_set():
            servers[0].wait_for_termination(timeout=0.5)
    except KeyboardInterrupt:
        stop_requested.set()
    finally:
        for server in servers:
            server.stop(grace=1)
        if checkpoint is not None and not checkpoint_saved:
            checkpoint.save(world)
            checkpoint_saved = True
        for service in services.values():
            service.close()
        for handled, previous in previous_handlers.items():
            signal.signal(handled, previous)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="two XLeRobot runtimes sharing one RoboCasa kitchen"
    )
    parser.add_argument("--sender-listen", default="127.0.0.1:51051")
    parser.add_argument("--receiver-listen", default="127.0.0.1:51052")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--human-speed", type=float, default=0.04)
    parser.add_argument("--checkpoint", default="")
    args = parser.parse_args()
    serve_fleet(
        args.sender_listen,
        args.receiver_listen,
        seed=args.seed,
        human_speed=args.human_speed,
        checkpoint_path=args.checkpoint or None,
    )


if __name__ == "__main__":
    main()
