"""Opt-in wrapper inside the admitted execution boundary, before terminal success."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime

from ..runtime import Result
from .model import ActionContract, canonical
from .store import EvidenceStore
from .verifier import RuntimeVerifier, load_contracts, render_report


class GroundedRuntime:
    def __init__(self, store, *, contracts=None, boot_id=None, clock=time.monotonic_ns):
        self.store = store
        self.contracts = contracts or load_contracts()
        self.boot_id = boot_id or uuid.uuid4().hex
        self.clock = clock
        self.verifier = RuntimeVerifier(store.exists)

    @classmethod
    def from_environment(cls):
        if os.environ.get("TANGYING_GVF_ENABLED", "0") != "1":
            return None
        return cls(EvidenceStore(os.environ.get("TANGYING_GVF_ROOT", "artifacts/grounded-runtime")))

    def _params(self, command):
        parameters = command.parameters
        return {
            **parameters,
            "object": str(parameters.get("objectId", command.target_ref)),
            "container": str(parameters.get("destinationId", "")),
            "surface": str(parameters.get("surface", "table")),
            "gripper": str(parameters.get("gripper", parameters.get("arm", "right"))),
            "robot": command.robot_id,
            "location": str(parameters.get("location", command.target_ref)),
        }

    def _collect(self, backend, command, start, phase):
        provider = getattr(backend, "collect_grounded_evidence", None)
        if provider is None:
            return []
        try:
            return list(
                provider(
                    command=command,
                    action_id=command.command_id,
                    start_ns=start,
                    edge_boot_id=self.boot_id,
                    store=self.store,
                    phase=phase,
                )
            )
        except Exception:  # noqa: BLE001 - A sensor failure cannot promote a tool return to a world fact.
            return []

    def _report(self, command, contract, stream, start, status, phase="post", **trace):
        return self.verifier.verify(
            contract,
            stream,
            action_id=command.command_id,
            edge_boot_id=self.boot_id,
            start_ns=start,
            end_ns=self.clock(),
            params=self._params(command),
            task_id=command.task_id,
            episode_id=command.task_id,
            stage_id=command.capability,
            tool_return_status=status,
            logical_clock=self.store.next_clock(),
            arrival_ts=datetime.now(UTC).isoformat(),
            phase=phase,
            **trace,
        )

    @staticmethod
    def result(report, original=None):
        payload = {"state_report_json": canonical(report), "state_report_nl": render_report(report)}
        passed = report.verdict == "VERIFIED" and (original is None or original.success)
        return Result(
            passed,
            (
                original.code
                if original and (passed or report.verdict == "VERIFIED")
                else "OK"
                if passed
                else report.failure_type
            ),
            render_report(report),
            confidence=report.confidence,
            payload=payload,
        )

    def execute(self, backend, command, invoke, *, physical):
        if command.capability == "emergency_stop":
            return invoke()  # Stopping is never gated by evidence availability.
        if not command.robot_id:
            command = replace(command, robot_id=backend.capabilities().robot_id)
        blocked = self.store.blocked(command.robot_id)
        if physical and blocked:
            contract = self.contracts.get(
                command.capability, ActionContract(name=command.capability, postconditions=[])
            )
            report = self._report(command, contract, [], self.clock(), "NOT_DISPATCHED")
            self.store.append(
                report
            )  # Keep the original barrier's action identity for reconciliation.
            return self.result(report)
        if not physical and command.capability not in {
            "verify_grasp",
            "verify_placement",
            "verify_arrival",
        }:
            return invoke()
        contract = self.contracts.get(
            command.capability, ActionContract(name=command.capability, postconditions=[])
        )
        if physical and not contract.postconditions:
            start = self.clock()
            report = self._report(command, contract, [], start, "NOT_DISPATCHED")
            self.store.append(report, command.robot_id)
            return self.result(report)
        if physical and (contract.preconditions or contract.safety_constraints):
            start = self.clock()
            report = self._report(
                command,
                contract,
                self._collect(backend, command, start, "pre"),
                start,
                "NOT_DISPATCHED",
                "pre",
            )
            self.store.append(report, command.robot_id if report.verdict != "VERIFIED" else "")
            if report.verdict != "VERIFIED":
                return self.result(report)
        execution_start = self.clock()
        timed_out = threading.Event()

        def stop_at_contract_timeout():
            timed_out.set()
            backend.stop("CONTRACT_TIMEOUT")

        timer = threading.Timer(contract.timeout_s, stop_at_contract_timeout)
        timer.daemon = True
        timer.start()
        invocation_failed = False
        try:
            result = invoke()
        except Exception as error:  # noqa: BLE001 - Hardware may have moved before the adapter raised.
            invocation_failed = True
            result = Result(False, "UNKNOWN_OUTCOME", f"Adapter raised {type(error).__name__}")
            try:
                backend.stop("UNKNOWN_OUTCOME")
            except Exception:
                logging.getLogger(__name__).exception("Grounded uncertainty stop failed")
        finally:
            timer.cancel()
        execution_end = self.clock()
        receipt = self.store.put(
            canonical(
                {
                    "action_id": command.command_id,
                    "start_ns": execution_start,
                    "end_ns": execution_end,
                    "success": result.success,
                    "code": result.code,
                    "message": result.message,
                }
            ).encode(),
            "tool_return",
        )
        start = self.clock()  # The postcondition window begins after the tool finishes.
        stream = self._collect(backend, command, start, "post")
        stream = [
            self.store.record_sample(
                **{
                    **sample.model_dump(exclude={"record_ref", "evidence_refs"}),
                    "evidence_refs": [*sample.evidence_refs, receipt],
                }
            )
            for sample in stream
        ]
        trace_provider = getattr(backend, "grounded_action_trace", None)
        try:
            action_trace = trace_provider(command.command_id) if callable(trace_provider) else []
        except Exception:  # noqa: BLE001 - Sensor faults must yield UNKNOWN.
            action_trace = []  # Missing action-time sensors cannot bypass report persistence.
        report = self._report(
            command,
            contract,
            stream,
            start,
            "SUCCESS" if result.success else result.code,
            action_stream=action_trace,
            action_start_ns=execution_start,
            action_end_ns=execution_end,
        )
        if receipt not in report.evidence_refs:
            data = report.model_dump(exclude={"report_id"})
            data["evidence_refs"].append(receipt.model_dump())
            report = type(report)(
                report_id="gvf-" + hashlib.sha256(canonical(data).encode()).hexdigest(), **data
            )
        if (
            invocation_failed
            or timed_out.is_set()
            or execution_end - execution_start > contract.timeout_s * 1e9
        ):
            data = report.model_dump(exclude={"report_id"})
            data.update(
                verdict="UNKNOWN",
                failure_type="EVIDENCE_INSUFFICIENT",
                failure_class="UNKNOWN_OUTCOME",
                confidence=0.0,
                unknowns=[
                    *report.unknowns,
                    "适配器异常；动作结果未知，停止后必须重新观察"
                    if invocation_failed
                    else "动作超过合约超时；停止后必须重新观察",
                ],
                forbidden_actions=["自动重试硬件动作", "未核对结果就执行后续物理动作"],
                requires_human=True,
            )
            report = type(report)(
                report_id="gvf-" + hashlib.sha256(canonical(data).encode()).hexdigest(), **data
            )
        # A read-only verifier can clear a barrier only for the very same intended effect,
        # from fresh evidence; calling an unrelated observation is not reconciliation.
        equivalent = {
            "verify_grasp": "manipulation.pick",
            "verify_placement": "manipulation.place",
            "verify_arrival": "navigation.navigate",
        }
        effect = equivalent.get(command.capability, command.capability)
        keys = {
            "manipulation.pick": ("object", "gripper"),
            "manipulation.place": ("object", "container", "gripper"),
            "navigation.navigate": ("goalPose", "location"),
        }.get(effect, ())
        release = bool(
            blocked
            and report.verdict == "VERIFIED"
            and effect == equivalent.get(blocked.action_name, blocked.action_name)
            and keys
            and all(report.action_params.get(key) == blocked.action_params.get(key) for key in keys)
        )
        self.store.append(
            report, command.robot_id if not blocked or release else "", release=release
        )
        return self.result(report, result)
