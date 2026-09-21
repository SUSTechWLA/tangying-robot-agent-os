"""Exercise the production ROS servicer's Observe method over real gRPC.

Extract just this method so the transport regression also runs on hosts without
ROS. Camera acquisition is irrelevant here; the single worker must become usable
by control RPCs immediately after the client cancels its camera stream.
"""
import ast
import threading
import time
from concurrent import futures
from pathlib import Path
from types import SimpleNamespace

import grpc
from tangying_robot_proto.robot.v1 import robot_pb2 as pb
from tangying_robot_proto.robot.v1 import robot_pb2_grpc as rpc


def test_cancelled_observation_releases_worker_for_control_rpc():
    root = Path(__file__).resolve().parents[3]
    source = root / 'robot/ros2_ws/src/tangying_navigation/tangying_navigation/gazebo_runtime_node.py'
    tree = ast.parse(source.read_text())
    method = next(member for cls in tree.body if isinstance(cls, ast.ClassDef)
                  for member in cls.body if isinstance(member, ast.FunctionDef) and member.name == 'Observe')
    namespace = {'threading': threading, 'time': time}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)  # noqa: S102 - checked-in method, no external input

    class Servicer(rpc.RobotRuntimeServicer):
        Observe = namespace['Observe']

        def GetRuntimeInfo(self, request, context):
            return pb.RuntimeInfo(robot_id='probe', adapter='gazebo')

    servicer = Servicer()
    servicer._node = SimpleNamespace(runtime=SimpleNamespace(_samples={'base-rgbd': object()}))
    servicer._skills = SimpleNamespace(Observe=lambda request, context: iter([pb.Observation(observation_id='one')]))
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=1))
    rpc.add_RobotRuntimeServicer_to_server(servicer, server)
    port = server.add_insecure_port('127.0.0.1:0')
    server.start()
    try:
        with grpc.insecure_channel(f'127.0.0.1:{port}') as channel:
            stub = rpc.RobotRuntimeStub(channel)
            stream = stub.Observe(pb.ObserveRequest(max_rate_hz=1), timeout=3)
            assert next(stream).observation_id == 'one'
            stream.cancel()
            # Previously the only worker slept for one second and this failed.
            result = stub.GetRuntimeInfo(pb.GetRuntimeInfoRequest(), timeout=.5)
            assert result.adapter == 'gazebo'
    finally:
        server.stop(0).wait()
