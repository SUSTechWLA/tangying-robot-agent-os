"""Finite-trace, three-valued GCL interpreter with local evidence validation."""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass
from pathlib import Path

from .model import (
    ActionContract,
    EvidenceSample,
    StateReport,
    canonical,
    resolve_contract,
)
from .predicates import FAILURES, PREDICATES


def load_contracts(path=None):
    path = Path(path) if path else Path(__file__).parents[1] / "assets/grounded-contracts.json"
    catalog = {k: ActionContract.model_validate(v) for k, v in json.loads(path.read_text()).items()}
    return {k: resolve_contract(k, catalog) for k in catalog}


@dataclass(frozen=True)
class Decision:
    verdict: str
    confidence: float
    completeness: float
    fact: str


def combine(items, op="all", fact=""):
    if not items:
        return Decision("UNKNOWN", 0.0, 0.0, fact)
    dominant = "FALSIFIED" if op == "all" else "VERIFIED"
    subordinate = "VERIFIED" if op == "all" else "FALSIFIED"
    selected = [i for i in items if i.verdict == dominant]
    verdict = (
        dominant
        if selected
        else (subordinate if all(i.verdict == subordinate for i in items) else "UNKNOWN")
    )
    confidence = (
        max(i.confidence for i in selected) if selected else min(i.confidence for i in items)
    )
    return Decision(verdict, confidence, sum(i.completeness for i in items) / len(items), fact)


class RuntimeVerifier:
    def __init__(self, evidence_exists=None):
        # Validation without a local evidence resolver is deliberately inconclusive.
        self.evidence_exists = evidence_exists or (lambda ref: False)

    def _atom(self, expression, sample, params, minimum):
        fact = canonical(expression)
        digest = hashlib.sha256(
            canonical(sample.model_dump(exclude={"record_ref"})).encode()
        ).hexdigest()
        if (
            sample.record_ref is None
            or sample.record_ref.sha256 != digest
            or not self.evidence_exists(sample.record_ref)
        ):
            return Decision("UNKNOWN", 0.0, 0.0, fact)
        spec = PREDICATES.get(expression.predicate)
        if spec is None:
            return Decision("UNKNOWN", 0.0, 0.0, fact)
        args = {
            k: params.get(v[1:], "") if isinstance(v, str) and v.startswith("$") else v
            for k, v in expression.args.items()
        }
        if "object" in args and sample.object_id != args["object"]:
            return Decision("UNKNOWN", 0.0, 0.0, fact)
        modalities = {r.kind for r in sample.evidence_refs if self.evidence_exists(r)}
        signals = sum(s in sample.values for s in spec.signals)
        coverage = (signals + len(set(spec.modalities) & modalities)) / (
            len(spec.signals) + len(spec.modalities)
        )
        if coverage < 1 or sample.confidence < minimum or sample.values.get("occluded") is True:
            return Decision("UNKNOWN", sample.confidence if coverage == 1 else 0.0, coverage, fact)
        try:
            passed = spec.decide(sample.values, args)
        except (TypeError, ValueError, KeyError):
            return Decision("UNKNOWN", 0.0, coverage, fact)
        return Decision("VERIFIED" if passed else "FALSIFIED", sample.confidence, coverage, fact)

    def evaluate(self, expr, samples, params, contract, start_ns, end_ns):
        fact = canonical(expr)
        if not samples:
            return Decision("UNKNOWN", 0.0, 0.0, fact)
        if expr.op == "predicate":
            return self._atom(expr, samples[-1], params, contract.min_confidence)
        if expr.op in {"all", "any", "not"}:
            items = [
                self.evaluate(c, samples, params, contract, start_ns, end_ns) for c in expr.children
            ]
            if expr.op == "not":
                i = items[0]
                return Decision(
                    {"VERIFIED": "FALSIFIED", "FALSIFIED": "VERIFIED", "UNKNOWN": "UNKNOWN"}[
                        i.verdict
                    ],
                    i.confidence,
                    i.completeness,
                    fact,
                )
            return combine(items, expr.op, fact)
        horizon = start_ns + int(expr.duration_s * 1e9)
        if expr.op == "within":
            eligible = [s for s in samples if s.edge_monotonic_ts_ns <= horizon]
            items = [
                self.evaluate(
                    expr.children[0],
                    eligible[: i + 1],
                    params,
                    contract,
                    start_ns,
                    s.edge_monotonic_ts_ns,
                )
                for i, s in enumerate(eligible)
            ]
            result = combine(items, "any", fact)
            if result.verdict == "FALSIFIED" and end_ns < horizon:
                return Decision("UNKNOWN", 0.0, result.completeness, fact)
            return result
        if expr.op == "stable":
            eligible = samples[-expr.frames :]
            if len(eligible) < expr.frames:
                return Decision("UNKNOWN", 0.0, len(eligible) / expr.frames, fact)
        else:  # unchanged: G[0,d] over a bounded, adequately sampled trace
            eligible = [s for s in samples if s.edge_monotonic_ts_ns <= horizon]
            if (
                end_ns < horizon
                or len(eligible) < 2
                or eligible[0].edge_monotonic_ts_ns - start_ns > contract.max_gap_s * 1e9
                or horizon - eligible[-1].edge_monotonic_ts_ns > contract.max_gap_s * 1e9
            ):
                return Decision("UNKNOWN", 0.0, 0.0, fact)
        if any(
            b.edge_monotonic_ts_ns - a.edge_monotonic_ts_ns > contract.max_gap_s * 1e9
            for a, b in itertools.pairwise(eligible)
        ):
            return Decision("UNKNOWN", 0.0, 0.0, fact)
        return combine(
            [
                self.evaluate(expr.children[0], [s], params, contract, start_ns, end_ns)
                for s in eligible
            ],
            fact=fact,
        )

    def verify(
        self,
        contract: ActionContract,
        stream: list[EvidenceSample],
        *,
        action_id: str,
        edge_boot_id: str,
        start_ns: int,
        end_ns: int,
        params=None,
        task_id="",
        episode_id="",
        stage_id="",
        tool_return_status="SUCCESS",
        logical_clock=0,
        arrival_ts="",
        phase="post",
        action_stream=None,
        action_start_ns=None,
        action_end_ns=None,
    ) -> StateReport:
        if end_ns < start_ns:
            raise ValueError("verification window runs backwards")
        params = params or {}
        limit = start_ns + int(contract.window_s * 1e9)
        seen, samples = set(), []
        for s in sorted(stream, key=lambda s: s.edge_monotonic_ts_ns):
            # Counting one capture twice must never satisfy a temporal contract.
            identity = (s.source_id, s.sample_id)
            stamp = ("capture", s.edge_monotonic_ts_ns)
            if (
                identity in seen
                or stamp in seen
                or s.edge_boot_id != edge_boot_id
                or s.action_id != action_id
                or not start_ns < s.edge_monotonic_ts_ns <= min(limit, end_ns)
            ):
                continue
            seen.update((identity, stamp))
            samples.append(s)
        expressions = (
            contract.preconditions + contract.safety_constraints
            if phase == "pre"
            else contract.postconditions + contract.safety_constraints
        )
        decisions = [
            self.evaluate(e, samples, params, contract, start_ns, end_ns) for e in expressions
        ]
        if phase == "post" and contract.invariants:
            # End-point samples cannot establish an invariant during motion.
            trace = sorted(
                [
                    s
                    for s in (action_stream or [])
                    if s.action_id == action_id
                    and s.edge_boot_id == edge_boot_id
                    and action_start_ns is not None
                    and action_end_ns is not None
                    and action_start_ns <= s.edge_monotonic_ts_ns <= action_end_ns
                ],
                key=lambda s: s.edge_monotonic_ts_ns,
            )
            complete = bool(
                trace
                and action_end_ns >= action_start_ns
                and trace[0].edge_monotonic_ts_ns - action_start_ns <= contract.max_gap_s * 1e9
                and action_end_ns - trace[-1].edge_monotonic_ts_ns <= contract.max_gap_s * 1e9
                and all(
                    0 < b.edge_monotonic_ts_ns - a.edge_monotonic_ts_ns <= contract.max_gap_s * 1e9
                    for a, b in itertools.pairwise(trace)
                )
            )
            for expr in contract.invariants:
                decisions.append(
                    combine(
                        [
                            self.evaluate(
                                expr, [sample], params, contract, action_start_ns, action_end_ns
                            )
                            for sample in trace
                        ],
                        fact=canonical(expr),
                    )
                    if complete
                    else Decision("UNKNOWN", 0.0, 0.0, canonical(expr))
                )
            samples += trace
        result = combine(decisions)
        # An empty precondition list imposes no restriction; an empty postcondition proves nothing.
        if not expressions and phase == "pre":
            result = Decision("VERIFIED", 1.0, 1.0, "")
        failure = self._classify(result.verdict, contract, samples, params, decisions, phase)
        family, _, recovery = FAILURES[failure]
        if result.verdict == "UNKNOWN":
            family = "UNKNOWN_OUTCOME"  # uncertainty outranks a diagnostic guess about occlusion
        refs = {
            r.uri: r
            for s in samples
            for r in [*s.evidence_refs, s.record_ref]
            if r is not None and self.evidence_exists(r)
        }
        data = {
            "schema_version": "state-report.v1",
            "verifier_version": "gvf-1.0.0",
            "task_id": task_id,
            "episode_id": episode_id,
            "stage_id": stage_id,
            "action_id": action_id,
            "action_name": contract.name,
            "action_params": params,
            "tool_return_status": tool_return_status,
            "verdict": result.verdict,
            "failure_type": failure,
            "failure_class": family,
            "confidence": result.confidence,
            "evidence_completeness": result.completeness,
            "evidence_refs": [refs[k] for k in sorted(refs)],
            "verified_facts": [d.fact for d in decisions if d.verdict == "VERIFIED"],
            "falsified_facts": [d.fact for d in decisions if d.verdict == "FALSIFIED"],
            "unknowns": [d.fact for d in decisions if d.verdict == "UNKNOWN"]
            or (["没有可执行的后置合约"] if not expressions and phase != "pre" else []),
            "suggested_recovery": recovery,
            "forbidden_actions": []
            if result.verdict == "VERIFIED"
            else ["自动重试硬件动作", "未核对结果就执行后续物理动作"],
            "requires_human": result.verdict != "VERIFIED",
            "edge_boot_id": edge_boot_id,
            "edge_monotonic_ts_ns": end_ns,
            "logical_clock": logical_clock,
            "arrival_ts": arrival_ts,
        }
        identity = hashlib.sha256(canonical(data).encode()).hexdigest()
        return StateReport(report_id=f"gvf-{identity}", **data)

    def _classify(self, verdict, contract, samples, params, decisions, phase):
        if verdict == "VERIFIED":
            return "NONE"
        valid = [
            s
            for s in samples
            if s.confidence >= contract.min_confidence
            and all(self.evidence_exists(r) for r in s.evidence_refs)
            and s.evidence_refs
            and s.record_ref is not None
            and self.evidence_exists(s.record_ref)
            and s.record_ref.sha256
            == hashlib.sha256(canonical(s.model_dump(exclude={"record_ref"})).encode()).hexdigest()
        ]
        if verdict == "UNKNOWN":
            return (
                "PERCEPTION_OCCLUDED"
                if any(s.values.get("occluded") is True for s in valid)
                else "EVIDENCE_INSUFFICIENT"
            )
        if phase == "pre" or any(
            d.verdict == "FALSIFIED" for d in decisions[len(contract.postconditions) :]
        ):
            return "CONTRACT_VIOLATION"
        values = valid[-1].values if valid else {}
        if values.get("container_full") is True:
            return "CONTAINER_FULL"
        if "pick" in contract.name or "grasp" in contract.name:
            held = values.get("held_object_id")
            if held and held != params.get("object"):
                return "WRONG_OBJECT"
            if any(
                s.values.get("load_n", 0) >= 0.1
                and s.values.get("height_above_surface_m", 0) > 0.02
                for s in valid[:-1]
            ):
                return "GRASP_SLIP"
            return "GRASP_MISS"
        if "place" in contract.name:
            return "PLACE_UNSTABLE"
        if "navigat" in contract.name or "arrival" in contract.name:
            return "NAV_NOT_REACHED"
        return "CONTRACT_VIOLATION"


def render_report(report: StateReport) -> str:
    lines = [
        ("当前阶段", canonical(report.stage_id)),
        ("刚执行动作", canonical({"action": report.action_name, "params": report.action_params})),
        (
            "工具返回",
            canonical(report.tool_return_status)
            + "（注意：此仅为命令执行结果，不代表物理世界已改变）",
        ),
        (
            "验证结论",
            f"{report.verdict}，失败类型：{report.failure_type}，置信度：{report.confidence:.6f}",
        ),
        ("证据摘要", canonical([r.model_dump() for r in report.evidence_refs])),
        ("已验证事实", canonical(report.verified_facts)),
        ("已证伪事实", canonical(report.falsified_facts)),
        ("未知", canonical(report.unknowns)),
        ("建议恢复", canonical(report.suggested_recovery)),
        ("禁止动作", canonical(report.forbidden_actions)),
        ("LLM 推断", "未包含；建议恢复属于策略建议，不属于物理事实"),
    ]
    return "\n".join(f"【{title}】{value}" for title, value in lines)


def validate_render(report: StateReport, text: str) -> bool:
    # Exact regeneration is stricter than trying to extract facts back out of prose.
    return text == render_report(report)
