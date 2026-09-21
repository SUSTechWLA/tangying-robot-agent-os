"""SMT 检查抽象契约，生产编码往返检查，以及信息擦除的可区分性证书。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import z3

from .factorial_cases import FACTORS, canonical, intervention
from .factorial_runner import load_cases, render, sha, verify, write_json


def verify_formal(root):
    protocol = verify(root)
    folder = root / "formal"
    folder.mkdir(exist_ok=True)
    checks = []

    def obligation(name, violation, expected=z3.unsat):
        solver = z3.Solver()
        solver.add(violation)
        result = solver.check()
        assert result == expected, (name, result)
        (folder / (name + ".smt2")).write_text(solver.to_smt2())
        checks.append(
            {
                "name": name,
                "result": str(result),
                "kind": "abstract_formula",
                "counterexample": str(solver.model()) if result == z3.sat else None,
            }
        )

    now, observed, expiry = z3.Ints("now observed expiry")
    task, robot, revision, episode, clock, known = z3.Bools(
        "task robot revision episode clock known"
    )
    valid = z3.And(task, robot, revision, episode, clock, known, observed <= now, now < expiry)
    for label, bad in [
        ("foreign_task", z3.Not(task)),
        ("foreign_robot", z3.Not(robot)),
        ("foreign_revision", z3.Not(revision)),
        ("unknown_metadata", z3.Not(known)),
        ("clock_domain_mismatch", z3.Not(clock)),
        ("expired", expiry <= now),
        ("future", observed > now),
    ]:
        obligation("metadata_excludes_" + label, z3.And(valid, bad))
    verifier, verified = z3.Bools("is_verifier reports_verified")
    published_verified = z3.And(valid, verifier, verified)
    obligation(
        "receipt_cannot_become_physical_verification", z3.And(published_verified, z3.Not(verifier))
    )
    kind, binding, ref, not_older = z3.Bools(
        "same_kind same_action_binding explicit_supersedes not_older"
    )
    replaces = z3.And(valid, kind, binding, ref, not_older)
    obligation("invalid_record_cannot_supersede", z3.And(replaces, z3.Not(valid)))
    obligation("different_attempt_cannot_supersede", z3.And(replaces, z3.Not(binding)))
    terminal, falsified, fresh, approved, cancelled, scope_match = z3.Bools(
        "terminal_known falsified fresh approved cancelled approval_binding_matches"
    )
    budget = z3.Int("budget_remaining")
    retry = z3.And(terminal, falsified, fresh, approved, scope_match, z3.Not(cancelled), budget > 0)
    for label, bad in [
        ("unknown_terminal", z3.Not(terminal)),
        ("wrong_approval_binding", z3.Not(scope_match)),
        ("cancelled", cancelled),
        ("budget_exhausted", budget <= 0),
    ]:
        obligation("retry_excludes_" + label, z3.And(retry, bad))
    # Counterexample, not a theorem: dropping a dependency permits an invalid frontier.
    pending, dependency_done = z3.Bools("pending dependency_done")
    actual_ready = z3.And(pending, dependency_done)
    erased_ready = pending
    obligation(
        "dependency_erasure_counterexample", z3.And(erased_ready, z3.Not(actual_ready)), z3.sat
    )
    test = load_cases(root, "test")
    rendered = render(root, test, FACTORS)
    table = {
        (c["id"], s["syntax"], s["order"], s["annotation"], s["complete"]): r
        for c, s, r in rendered
    }
    pairs = 0
    indistinguishable = 0
    for a, b in zip(test[::2], test[1::2], strict=True):
        assert a["pair_id"] == b["pair_id"] and a["gold"] != b["gold"]
        assert canonical(intervention(a, {"complete": False})) == canonical(
            intervention(b, {"complete": False})
        )
        for s in FACTORS:
            if s["complete"]:
                continue
            key = (s["syntax"], s["order"], s["annotation"], False)
            assert table[(a["id"], *key)]["text"] == table[(b["id"], *key)]["text"]
            indistinguishable += 1
        pairs += 1
    result = {
        "z3_version": z3.get_version_string(),
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "protocol_sha256": sha(root / "protocol.json"),
        "dataset_sha256": protocol["dataset_sha256"],
        "checks": checks,
        "unsat_obligations": sum(c["result"] == "unsat" for c in checks),
        "sat_counterexamples": sum(c["result"] == "sat" for c in checks),
        "production_roundtrips": len(rendered),
        "counterfactual_pairs": pairs,
        "erased_prompt_equalities": indistinguishable,
        "erased_pair_decision_accuracy_upper_bound": 0.5,
        "assumptions": [
            "formulas describe a declared abstraction, not a proof of all Go code or physical safety",
            "source truth and authentication are external assumptions",
            "bijection proof applies to supported JSON values; executable roundtrips validate archived instances",
            "equal-input lower bound assumes balanced twins and no external evidence/tool calls",
            "LLM uncertainty output is evaluated separately from impossible full-state prediction",
        ],
    }
    write_json(folder / "certificate.json", result)
    return result
