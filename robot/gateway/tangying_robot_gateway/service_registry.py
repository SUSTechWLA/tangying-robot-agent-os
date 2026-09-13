"""Runtime-owned, discoverable service calls shared by all driver types."""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from google.protobuf.json_format import MessageToDict
from tangying_robot_proto.robot.v1 import robot_pb2

from .tool_schema import validate_value


class ServiceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RegisteredService:
    name: str
    description: str
    schema: dict
    handler: Callable[[dict], dict]
    mutates_world: bool = False


class ServiceRegistry:
    """Exact schemas and at-most-once admission for deliberate UI/tool commands.

    Read calls are uncached. Mutation receipts are bounded; once the bound is
    reached new mutations fail until restart, rather than evicting identities
    and accidentally replaying old movement requests.
    """
    def __init__(self, robot_id: str):
        self.robot_id = robot_id
        self.services: dict[str, RegisteredService] = {}
        self._lock = threading.Lock()
        self._receipts = OrderedDict()

    def register(self, service: RegisteredService):
        if service.name in self.services:
            raise ValueError(f"duplicate service {service.name}")
        self.services[service.name] = service

    def catalogue(self):
        result = robot_pb2.ServiceCatalog(robot_id=self.robot_id)
        for service in self.services.values():
            item = result.services.add(name=service.name, description=service.description,
                                       available=True, mutates_world=service.mutates_world)
            item.input_schema.update(service.schema)
        return result

    def call(self, request):
        try:
            if request.robot_id != self.robot_id:
                raise ServiceError("ROBOT_ID_MISMATCH", "服务请求与当前机器人不一致，请刷新连接。")
            service = self.services.get(request.name)
            if service is None:
                raise ServiceError("SERVICE_UNAVAILABLE", "机器人没有注册此服务。")
            parameters = MessageToDict(request.parameters)
            if len(json.dumps(parameters)) > 2_000_000:
                raise ServiceError("REQUEST_TOO_LARGE", "服务参数超过大小限制。")
            validate_value(parameters, service.schema)
            if service.mutates_world:
                if not request.request_id or len(request.request_id) > 128:
                    raise ServiceError("REQUEST_ID_REQUIRED", "操作需要唯一 requestId。")
                fingerprint = hashlib.sha256(json.dumps([request.name, parameters], sort_keys=True,
                                                        allow_nan=False).encode()).hexdigest()
                with self._lock:
                    previous = self._receipts.get(request.request_id)
                    if previous:
                        if previous[0] != fingerprint:
                            raise ServiceError("IDEMPOTENCY_CONFLICT", "此操作编号已用于不同参数。")
                        if previous[1] is None:
                            raise ServiceError("REQUEST_IN_PROGRESS", "原操作仍在执行，请读取服务状态。")
                        return copy.deepcopy(previous[1])
                    if len(self._receipts) >= 10000:
                        raise ServiceError("RECEIPT_CAPACITY", "操作记录已满，请在空闲时重启机器人服务。")
                    self._receipts[request.request_id] = (fingerprint, None)
            try:
                payload = service.handler(parameters)
                response = robot_pb2.ServiceResponse(ok=True, code="OK")
                response.result.update(payload)
            except Exception as error:  # noqa: BLE001 - RPC faults become retained failure receipts.
                response = self._failure(error)
            if service.mutates_world:
                with self._lock:
                    self._receipts[request.request_id] = (fingerprint, copy.deepcopy(response))
            return response
        except Exception as error:  # noqa: BLE001 - no provider fault may escape the RPC boundary.
            return self._failure(error)

    @staticmethod
    def _failure(error):
        return robot_pb2.ServiceResponse(ok=False, code=getattr(error, "code", "SERVICE_FAILED"),
                                        message=str(error))


def object_schema(properties=None, required=()):
    return {"type": "object", "properties": properties or {}, "required": list(required),
            "additionalProperties": False}
