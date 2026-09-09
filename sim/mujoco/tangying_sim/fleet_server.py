from __future__ import annotations

import argparse
from concurrent import futures

import grpc
from tangying_robot_proto.robot.v1 import robot_pb2_grpc

from .server import RobotRuntimeService
from .shared_handoff import seeded_handoff_worlds


def create_fleet_services(*, seed: int = 7, human_speed: float = 0.0):
    bridge, sender, receiver = seeded_handoff_worlds(
        seed=seed, human_speed=human_speed
    )
    # These legacy overview cameras render dense CAD meshes in software on
    # headless Fleet hosts. Keep all illumination and the key light's shadow,
    # while avoiding two extra mesh projection passes for fill-light shadows.
    # This visual quality budget belongs only to these independent Fleet
    # models; shared XML and robot-mounted RGB-D rendering are unchanged.
    for world in (sender, receiver):
        key_light = world.model.light("task_key").id
        world.model.light_castshadow[:] = False
        world.model.light_castshadow[key_light] = True
    services = {
        bridge.sender_id: RobotRuntimeService(sender, robot_id=bridge.sender_id),
        bridge.receiver_id: RobotRuntimeService(receiver, robot_id=bridge.receiver_id),
    }
    for service in services.values():
        service.register_resource(
            bridge.RESOURCE_ID,
            owner=bridge.owner,
            token=bridge.fencing_token,
        )

    def update_runtime_grants(owner: str, token: int) -> None:
        for service in services.values():
            service.register_resource(bridge.RESOURCE_ID, owner=owner, token=token)

    bridge.add_grant_listener(update_runtime_grants)
    return bridge, services


def serve_fleet(
    sender_address: str,
    receiver_address: str,
    *,
    seed: int = 7,
    human_speed: float = 0.0,
) -> None:
    _bridge, services = create_fleet_services(seed=seed, human_speed=human_speed)
    servers: list[grpc.Server] = []
    for robot_id, address in (
        ("robot-1", sender_address),
        ("robot-2", receiver_address),
    ):
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
        robot_pb2_grpc.add_RobotRuntimeServicer_to_server(services[robot_id], server)
        if server.add_insecure_port(address) == 0:
            raise RuntimeError(f"could not bind {robot_id} runtime to {address}")
        server.start()
        servers.append(server)
    try:
        servers[0].wait_for_termination()
    finally:
        for server in servers:
            server.stop(grace=1)
        for service in services.values():
            service.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="two MuJoCo robot runtimes sharing one logical handoff block"
    )
    parser.add_argument("--sender-listen", default="127.0.0.1:50051")
    parser.add_argument("--receiver-listen", default="127.0.0.1:50052")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--human-speed", type=float, default=0.0)
    args = parser.parse_args()
    serve_fleet(
        args.sender_listen,
        args.receiver_listen,
        seed=args.seed,
        human_speed=args.human_speed,
    )


if __name__ == "__main__":
    main()
