"""Tool discovery, routing, retry and health for distributed execution.

The executor is the only component that decides *how* a tool call is run. It
adds the distributed concerns the runtime deliberately does not own:

* **discovery** — a tool resolves to the node that registered it, never to a
  hard-coded address;
* **timeouts** — every call is bounded, including composite chains;
* **retry** — only for calls the recovery classification marks safe, and only
  while an attempt budget remains. A motion tool is never silently retried;
* **health** — a node that stops reporting is marked unreachable, so its tools
  fail with ``UNREACHABLE`` instead of hanging;
* **concurrency** — a write tool takes an exclusive lock on its target node so
  the base and the arm cannot be driven by two tasks at once.

It does not re-implement safety. A physical call still goes through the
runtime's supervisor, which may veto it regardless of what the executor wants.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .tool_layer import (
    RobotTool,
    SafetyLevel,
    ToolError,
    ToolRegistry,
    ToolResult,
    standard_error,
)

#: How a node's registration is treated after this long without a heartbeat.
DEFAULT_NODE_TTL_S = 30.0


@dataclass(frozen=True)
class ToolCall:
    """One LLM tool call, as it arrives from a function-calling client."""

    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    tool_call_id: str | None = None
    timeout_s: float | None = None
    cancel_event: Any = None


@dataclass
class NodeRegistration:
    """A node's advertised liveness. Discovery is by tool name, not address."""

    node_id: str
    tools: tuple[str, ...]
    registered_at: float
    last_heartbeat: float
    healthy: bool = True
    detail: Mapping[str, Any] = field(default_factory=dict)

    def age_s(self, now: float) -> float:
        return max(0.0, now - self.last_heartbeat)


class ToolExecutor:
    """Runs tool calls against a registry with bounded, auditable behaviour."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        node_ttl_s: float = DEFAULT_NODE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
        max_attempts: int = 2,
        backoff_s: float = 0.05,
        lock_timeout_s: float = 0.0,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.registry = registry
        self.node_ttl_s = node_ttl_s
        self.clock = clock
        self.max_attempts = max_attempts
        self.backoff_s = backoff_s
        self.lock_timeout_s = lock_timeout_s
        self._nodes: dict[str, NodeRegistration] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._register_registry_nodes()

    # -- registration ----------------------------------------------------

    def _register_registry_nodes(self) -> None:
        now = self.clock()
        grouped: dict[str, list[str]] = {}
        for tool in self.registry:
            grouped.setdefault(tool.distributed_node, []).append(tool.name)
        for node_id, tool_names in grouped.items():
            self._nodes[node_id] = NodeRegistration(
                node_id=node_id, tools=tuple(sorted(tool_names)),
                registered_at=now, last_heartbeat=now,
            )
            self._locks[node_id] = threading.Lock()

    def heartbeat(self, node_id: str, *, healthy: bool = True, detail: Mapping[str, Any] | None = None) -> None:
        """Record that a node is alive. Unknown nodes are added on first report."""

        with self._guard:
            now = self.clock()
            existing = self._nodes.get(node_id)
            if existing is None:
                self._nodes[node_id] = NodeRegistration(
                    node_id=node_id, tools=(), registered_at=now, last_heartbeat=now,
                    healthy=healthy, detail=dict(detail or {}),
                )
                self._locks.setdefault(node_id, threading.Lock())
                return
            existing.last_heartbeat = now
            existing.healthy = healthy
            if detail is not None:
                existing.detail = dict(detail)

    def mark_unhealthy(self, node_id: str, *, detail: Mapping[str, Any] | None = None) -> None:
        with self._guard:
            registration = self._nodes.get(node_id)
            if registration is None:
                return
            registration.healthy = False
            if detail is not None:
                registration.detail = dict(detail)

    def node_for(self, tool_name: str) -> str | None:
        tool = self.registry.get(tool_name)
        return None if tool is None else tool.distributed_node

    def node_health(self) -> dict[str, dict[str, Any]]:
        """Snapshot for diagnostics and for a capability report."""

        now = self.clock()
        with self._guard:
            report = {}
            for node_id, registration in self._nodes.items():
                expired = registration.age_s(now) > self.node_ttl_s
                report[node_id] = {
                    "tools": list(registration.tools),
                    "healthy": registration.healthy and not expired,
                    "expired": expired,
                    "age_s": round(registration.age_s(now), 3),
                    **dict(registration.detail),
                }
            return report

    def unavailable_tools(self) -> dict[str, list[str]]:
        """Tool name -> blockers, for the runtime capability view."""

        now = self.clock()
        unavailable: dict[str, list[str]] = {}
        with self._guard:
            for registration in self._nodes.values():
                if registration.healthy and registration.age_s(now) <= self.node_ttl_s:
                    continue
                reason = "NODE_UNREACHABLE" if not registration.healthy else "NODE_HEARTBEAT_EXPIRED"
                for tool_name in registration.tools:
                    unavailable[tool_name] = [reason]
        return unavailable

    # -- execution -------------------------------------------------------

    def execute(self, call: ToolCall) -> ToolResult:
        """Run one call, bounded by timeout, retry policy and node health."""

        tool = self.registry.get(call.name)
        if tool is None:
            return ToolResult.failure(
                ToolError.NOT_FOUND, f"tool {call.name!r} is not registered",
                known_tools=list(self.registry.names()),
            ).with_call_id(call.tool_call_id)

        node_id = tool.distributed_node
        status = self.node_health().get(node_id, {})
        if status and not status.get("healthy", True):
            return ToolResult.failure(
                ToolError.UNREACHABLE,
                f"node {node_id!r} is not reporting health; its tools cannot be called",
                node=node_id, blockers=self.unavailable_tools().get(call.name, []),
            ).with_call_id(call.tool_call_id)

        timeout_s = float(call.timeout_s if call.timeout_s is not None else tool.timeout_s)
        if timeout_s <= 0:
            return ToolResult.failure(
                ToolError.INVALID_PARAM, "timeout_s must be positive",
            ).with_call_id(call.tool_call_id)

        lock = self._locks.setdefault(node_id, threading.Lock())
        acquired = lock.acquire(timeout=max(0, self.lock_timeout_s))
        if not acquired:
            return ToolResult.failure(
                ToolError.BUSY,
                f"node {node_id!r} is executing another command",
                node=node_id,
            ).with_call_id(call.tool_call_id)
        try:
            return self._execute_with_retry(tool, call, timeout_s)
        finally:
            lock.release()

    def _execute_with_retry(self, tool: RobotTool, call: ToolCall, timeout_s: float) -> ToolResult:
        attempts = self.max_attempts if _retry_allowed(tool) else 1
        result = ToolResult.failure(ToolError.HARDWARE_ERROR, "no attempt was made")
        for attempt in range(1, attempts + 1):
            if _cancelled(call.cancel_event):
                return ToolResult.failure(
                    ToolError.CANCELLED, "call was cancelled before execution",
                ).with_call_id(call.tool_call_id)
            started = self.clock()
            result = _invoke_with_timeout(tool, call, timeout_s).with_data(
                attempt=attempt, duration_s=round(self.clock() - started, 4),
            ).with_call_id(call.tool_call_id)
            if result.success or not result.recoverable or attempt == attempts:
                return result
            if call.cancel_event is not None and call.cancel_event.wait(self.backoff_s * attempt):
                return ToolResult.failure(
                    ToolError.CANCELLED, "call was cancelled during retry backoff",
                ).with_call_id(call.tool_call_id)
            elif call.cancel_event is None:
                time.sleep(self.backoff_s * attempt)
        return result

    def openai_tools(self, *, llm_only: bool = True) -> list[dict[str, Any]]:
        """Function-calling schema, excluding tools whose nodes are down."""

        unavailable = self.unavailable_tools()
        return [
            tool.openai_schema() for tool in self.registry.select(llm_only=llm_only)
            if tool.name not in unavailable
        ]

    def capability_infos(self):
        return self.registry.capability_infos(unavailable=self.unavailable_tools())

    def validate_arguments(self, call: ToolCall) -> ToolResult | None:
        """Reject a call whose arguments do not match the declared schema.

        Only the checks the schema can express without a JSON Schema engine are
        applied: required presence and unknown keys. The tool itself still
        validates types and ranges, which is where embodiment limits live.
        """

        tool = self.registry.get(call.name)
        if tool is None:
            return None
        schema = tool.parameters_schema or {}
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        missing = [key for key in required if key not in call.arguments]
        if missing:
            return ToolResult.failure(
                ToolError.INVALID_PARAM, f"missing required argument(s): {', '.join(missing)}",
                missing_arguments=missing,
            ).with_call_id(call.tool_call_id)
        unknown = [key for key in call.arguments if properties and key not in properties]
        if unknown:
            return ToolResult.failure(
                ToolError.INVALID_PARAM, f"unknown argument(s): {', '.join(sorted(unknown))}",
                unknown_arguments=sorted(unknown), accepted_arguments=sorted(properties),
            ).with_call_id(call.tool_call_id)
        return None


def _retry_allowed(tool: RobotTool) -> bool:
    """Read-only and idempotent tools may be retried; motion may not.

    This mirrors the rule the operator guide states: a query is safe to repeat,
    a physical action is not, because repeating it can duplicate the effect.
    """

    if tool.mutates_world:
        return False
    return tool.idempotent or tool.safety_level <= SafetyLevel.QUERY


def _cancelled(cancel_event: Any) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _invoke_with_timeout(tool: RobotTool, call: ToolCall, timeout_s: float) -> ToolResult:
    """Run a tool so a hung handler cannot block the caller forever.

    The handler runs in a worker thread because a vendor SDK call may block
    without honouring any cancellation. The executor gives up at the deadline
    and reports TIMEOUT; it does not claim the robot stopped, because a
    background action may still be in flight.
    """

    outcome: dict[str, ToolResult] = {}

    def worker() -> None:
        try:
            outcome["result"] = tool.execute(**dict(call.arguments))
        except Exception as exc:  # noqa: BLE001 - the contract must not leak a raw fault
            outcome["result"] = ToolResult.from_exception(exc, context=f"{call.name} failed")

    thread = threading.Thread(target=worker, name=f"tool-{tool.name}", daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        return ToolResult.failure(
            ToolError.TIMEOUT,
            f"{tool.name} did not finish within {timeout_s:.1f}s; "
            "the command may still be in flight, verify state before retrying",
            timeout_s=timeout_s, outcome_uncertain=True,
        )
    return outcome.get(
        "result", ToolResult.failure(ToolError.HARDWARE_ERROR, "tool produced no result"),
    )


def describe_failures(results: Iterable[ToolResult]) -> list[dict[str, Any]]:
    """Compact failure list for a model-facing transcript."""

    described = []
    for result in results:
        if result.success:
            continue
        error, recovery, retryable = standard_error(result.error_code or "")
        described.append({
            "tool_call_id": result.tool_call_id, "error_code": str(error),
            "recovery_class": recovery.value, "retryable": retryable,
            "message": result.error_message,
        })
    return described
