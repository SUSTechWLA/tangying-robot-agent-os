from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Protocol

from .contracts import Action, InferenceRequest, InferenceResult, PolicyManifest, validate_result


class PolicyProvider(Protocol):
    @property
    def manifest(self) -> PolicyManifest: ...

    def infer(self, request: InferenceRequest) -> InferenceResult: ...


ActionCallable = Callable[[InferenceRequest], list[dict[str, float]]]


class CallableProvider:
    """Bind a VLA, imitation-learning, or RL callable to the stable protocol."""

    def __init__(self, manifest: PolicyManifest, infer: ActionCallable):
        self._manifest = manifest
        self._infer = infer

    @property
    def manifest(self) -> PolicyManifest:
        return self._manifest

    def infer(self, request: InferenceRequest) -> InferenceResult:
        started = time.perf_counter()
        actions = [Action(values=values) for values in self._infer(request)]
        result = InferenceResult(
            schemaVersion="policy.inference.result.v1",
            requestId=request.request_id,
            commandId=request.command_id,
            manifestRevision=request.manifest_revision,
            inferenceId=f"inference-{uuid.uuid4()}",
            observationId=request.observation.observation_id,
            elapsedMs=(time.perf_counter() - started) * 1000,
            actions=actions,
        )
        validate_result(self.manifest, request, result)
        return result
