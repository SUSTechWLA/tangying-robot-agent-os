import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from model_usage_report import main, summarize


def event(call_id, kind="result", usage=None, status="valid", robot="r1"):
    call = {
        "call_id": call_id,
        "stage": "GOAL",
        "model": "fixture",
        "endpoint_sha256": "endpoint",
        "outcome": "accepted",
        "cache_mode": "off",
        "output_tokens": 512,
        "request_bytes": 80,
        "response_bytes": 40,
        "duration_ms": 9,
        "usage_status": status,
    }
    if usage is not None:
        call["usage"] = usage
    return {
        "schema_version": "agent.model-usage.v1",
        "event": kind,
        "scope": {"robot_id": robot, "task_id": "task"},
        "call": call,
    }


class UsageReportTest(unittest.TestCase):
    def report(self, events, tail=b""):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calls.jsonl"
            path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in events) + tail)
            return summarize([path])

    def test_partial_fields_zero_missing_and_cache_denominators(self):
        rows = [
            event("a", "start"),
            event("a", usage={"prompt_tokens": 100, "cached_tokens": 0}),
            event("b", "start"),
            event("b", usage={"cached_tokens": 20}),
            event("c", "start"),
            event("c", status="missing"),
            event("d", "start"),
            event("d", usage={"prompt_tokens": -1}),
            event("e", "start", robot="r2"),
            event("e", usage={"prompt_tokens": 100, "cached_tokens": 50}, robot="r2"),
        ]
        report = self.report(rows)
        totals = report["totals"]
        self.assertTrue(report["lifecycle_complete"])
        self.assertEqual(totals["tokens"]["total_tokens"], {"sum": None, "known_field_samples": 0})
        self.assertEqual(totals["tokens"]["cached_tokens"], {"sum": 70, "known_field_samples": 3})
        self.assertEqual(totals["paired_cache"]["ratio"], 0.25)
        self.assertEqual(totals["paired_cache"]["known_pair_samples"], 2)
        self.assertEqual(
            totals["usage_status"], {"valid": 3, "missing": 1, "invalid": 1, "unfinished": 0}
        )
        self.assertEqual({group["group"]["robot_id"] for group in report["groups"]}, {"r1", "r2"})

    def test_duplicates_conflicts_unfinished_and_orphan_results(self):
        start, result = event("same", "start"), event("same", usage={"total_tokens": 5})
        bad = copy.deepcopy(result)
        bad["call"]["usage"]["total_tokens"] = 500
        report = self.report(
            [
                start,
                start,
                result,
                result,
                event("conflict", "start"),
                event("unfinished", "start"),
                event("orphan", usage={"total_tokens": 7}),
                dict(result, call=dict(result["call"], call_id="conflict")),
                dict(bad, call=dict(bad["call"], call_id="conflict")),
            ]
        )
        self.assertEqual(report["duplicate_records"], 2)
        self.assertEqual(report["conflicting_call_ids"], ["conflict"])
        self.assertEqual(report["unfinished_call_ids"], ["unfinished"])
        self.assertEqual(report["orphan_result_call_ids"], ["orphan"])
        self.assertEqual(report["totals"]["tokens"]["total_tokens"]["sum"], 12)
        self.assertFalse(report["lifecycle_complete"])

    def test_cross_robot_or_model_changes_are_conflicts(self):
        for mutate in (
            lambda row: row["scope"].update(robot_id="other"),
            lambda row: row["call"].update(model="other"),
        ):
            result = event("a", usage={"total_tokens": 5})
            mutate(result)
            report = self.report([event("a", "start"), result])
            self.assertEqual(report["conflicting_call_ids"], ["a"])
            self.assertIsNone(report["totals"]["tokens"]["total_tokens"]["sum"])

    def test_torn_line_and_foreign_trace_are_reported_not_repaired(self):
        report = self.report(
            [event("a", "start"), {"type": "planning.trace", "recipeHit": True}], b'{"unfinished":'
        )
        self.assertEqual(len(report["invalid_records"]), 2)
        self.assertEqual(report["totals"]["attempts"], 1)
        self.assertEqual(report["totals"]["finished"], 0)
        self.assertIsNone(report["totals"]["tokens"]["total_tokens"]["sum"])

    def test_inconsistent_cache_and_non_integer_usage_are_invalid(self):
        for usage in (
            {"prompt_tokens": 2, "cached_tokens": 3},
            {"total_tokens": True},
            {"total_tokens": 0.5},
            {"total_tokens": 1 << 53},
        ):
            report = self.report([event("a", "start"), event("a", usage=usage)])
            self.assertEqual(report["totals"]["usage_status"]["invalid"], 1)
            self.assertIsNone(report["totals"]["tokens"]["total_tokens"]["sum"])

    def test_cli_cannot_overwrite_ledger_through_hardlink(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger, output = Path(directory) / "usage.jsonl", Path(directory) / "report.json"
            original = json.dumps(event("a", "start")).encode() + b"\n"
            ledger.write_bytes(original)
            os.link(ledger, output)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                main([str(ledger), "--output", str(output)])
            self.assertEqual(raised.exception.code, 2)
            self.assertEqual(ledger.read_bytes(), original)

    def test_cli_writes_a_report_without_changing_input(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger, output = Path(directory) / "usage.jsonl", Path(directory) / "report.json"
            original = json.dumps(event("a", "start")).encode() + b"\n"
            ledger.write_bytes(original)
            self.assertEqual(main([str(ledger), "--output", str(output)]), 0)
            self.assertEqual(ledger.read_bytes(), original)
            self.assertEqual(json.loads(output.read_text())["unfinished_call_ids"], ["a"])

    def test_read_error_keeps_observed_start_and_closes_input(self):
        class BrokenInput(io.BytesIO):
            def readline(self, size=-1):
                if self.tell():
                    raise OSError(5, "fixture read failure")
                return super().readline(size)

        stream = BrokenInput(json.dumps(event("a", "start")).encode() + b"\n")
        with patch.object(Path, "open", return_value=stream):
            report = summarize([Path("fixture.jsonl")])
        self.assertTrue(stream.closed)
        self.assertEqual(report["unfinished_call_ids"], ["a"])
        self.assertEqual(len(report["invalid_records"]), 1)
        self.assertIn("fixture read failure", report["invalid_records"][0]["reason"])
        self.assertFalse(report["lifecycle_complete"])

    def test_non_finite_and_over_nested_json_are_invalid_records(self):
        for malformed in (b'{"value":NaN}\n', b"[" * 4000 + b"]" * 4000 + b"\n"):
            report = self.report([event("a", "start")], malformed)
            self.assertEqual(len(report["invalid_records"]), 1)
            self.assertEqual(report["unfinished_call_ids"], ["a"])


if __name__ == "__main__":
    unittest.main()
