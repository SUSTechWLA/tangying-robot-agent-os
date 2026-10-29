#!/usr/bin/env python3
"""Summarize the action-loop accounting ledger without estimating missing tokens.

Only agent.model-usage.v1 start/result records are accepted. Planning recipe
traces are intentionally not inputs: avoiding a call is not a zero-token call.
No prices, model-independent tokenizers, or inference of missing total_tokens.
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

TOKEN_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cached_tokens",
    "cache_write_tokens",
)
IDENTITY_FIELDS = (
    "call_id",
    "stage",
    "model",
    "endpoint_sha256",
    "output_tokens",
    "started_at",
    "prompt_template_version",
    "tools_sha256",
    "cache_mode",
    "cache_identity_sha256",
    "request_bytes",
)
MAX_LINE_BYTES = (64 << 10) + 1
MAX_PORTABLE_INTEGER = (1 << 53) - 1


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _integer(value):
    return type(value) is int and 0 <= value <= MAX_PORTABLE_INTEGER


def _reject_constant(_value):
    raise ValueError("non-finite number is not valid JSON")


def _usage(call):
    status = call.get("usage_status", "missing")
    raw = call.get("usage")
    if status != "valid":
        return (status if status in ("missing", "invalid") else "invalid"), {}
    if not isinstance(raw, dict):
        return "invalid", {}
    known = {key: raw[key] for key in TOKEN_FIELDS if raw.get(key) is not None}
    if not known:
        return "missing", {}
    if any(not _integer(value) for value in known.values()):
        return "invalid", {}
    if (
        "cached_tokens" in known
        and "prompt_tokens" in known
        and known["cached_tokens"] > known["prompt_tokens"]
    ):
        return "invalid", {}
    return "valid", known


def _metrics():
    return {
        "attempts": 0,
        "finished": 0,
        "unfinished": 0,
        "usage_status": {"valid": 0, "missing": 0, "invalid": 0, "unfinished": 0},
        "tokens": {name: {"known_field_samples": 0, "sum": None} for name in TOKEN_FIELDS},
        "paired_cache": {
            "known_pair_samples": 0,
            "prompt_tokens": 0,
            "cached_tokens": 0,
            "ratio": None,
        },
        "duration_ms": {"known_field_samples": 0, "sum": None},
        "request_bytes": {"known_field_samples": 0, "sum": None},
        "response_bytes": {"known_field_samples": 0, "sum": None},
        "response_truncated_calls": 0,
    }


def _add(metric, value):
    if _integer(value):
        metric["known_field_samples"] += 1
        metric["sum"] = (metric["sum"] or 0) + value


def _accumulate(metrics, call, finished):
    metrics["attempts"] += 1
    _add(metrics["request_bytes"], call.get("request_bytes"))
    if not finished:
        metrics["unfinished"] += 1
        metrics["usage_status"]["unfinished"] += 1
        return
    metrics["finished"] += 1
    status, usage = _usage(call)
    metrics["usage_status"][status] += 1
    for name, value in usage.items():
        _add(metrics["tokens"][name], value)
    if "prompt_tokens" in usage and "cached_tokens" in usage:
        paired = metrics["paired_cache"]
        paired["known_pair_samples"] += 1
        paired["prompt_tokens"] += usage["prompt_tokens"]
        paired["cached_tokens"] += usage["cached_tokens"]
        paired["ratio"] = (
            paired["cached_tokens"] / paired["prompt_tokens"] if paired["prompt_tokens"] else None
        )
    for name in ("duration_ms", "response_bytes"):
        _add(metrics[name], call.get(name))
    metrics["response_truncated_calls"] += call.get("response_truncated") is True


def summarize(paths):
    calls, conflicts, issues = {}, set(), []
    records = duplicates = 0
    for path in paths:
        try:
            with Path(path).open("rb") as stream:
                line_number = 0
                while raw := stream.readline(MAX_LINE_BYTES + 1):
                    line_number += 1
                    if len(raw) > MAX_LINE_BYTES:
                        # Drain just this oversized record without retaining its body.
                        while not raw.endswith(b"\n"):
                            raw = stream.readline(MAX_LINE_BYTES + 1)
                            if not raw:
                                break
                        issues.append(
                            {
                                "file": str(path),
                                "line": line_number,
                                "reason": "record exceeds 64 KiB",
                            }
                        )
                        continue
                    if not raw.endswith(b"\n"):
                        issues.append(
                            {
                                "file": str(path),
                                "line": line_number,
                                "reason": "incomplete trailing line",
                            }
                        )
                        continue
                    try:
                        entry = json.loads(
                            raw, object_pairs_hook=_object, parse_constant=_reject_constant
                        )
                        if (
                            not isinstance(entry, dict)
                            or entry.get("schema_version") != "agent.model-usage.v1"
                            or entry.get("event") not in ("start", "result")
                        ):
                            raise ValueError("unsupported usage event")
                        call, scope = entry.get("call"), entry.get("scope")
                        if (
                            not isinstance(call, dict)
                            or not isinstance(scope, dict)
                            or not isinstance(call.get("call_id"), str)
                            or not call["call_id"]
                        ):
                            raise ValueError("missing call identity or scope")
                        for field in ("stage", "model", "endpoint_sha256", "outcome", "cache_mode"):
                            if field in call and not isinstance(call[field], str):
                                raise ValueError("invalid call grouping field")
                        if "robot_id" in scope and not isinstance(scope["robot_id"], str):
                            raise ValueError("invalid robot scope")
                    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
                        issues.append({"file": str(path), "line": line_number, "reason": str(exc)})
                        continue
                    records += 1
                    pair = calls.setdefault(call["call_id"], {})
                    previous = pair.get(entry["event"])
                    if previous is not None:
                        if previous == entry:
                            duplicates += 1
                        else:
                            conflicts.add(call["call_id"])
                        continue
                    pair[entry["event"]] = entry
                    if "start" in pair and "result" in pair:
                        start, result = pair["start"], pair["result"]
                        if start["scope"] != result["scope"] or any(
                            start["call"].get(key) != result["call"].get(key)
                            for key in IDENTITY_FIELDS
                        ):
                            conflicts.add(call["call_id"])
        except OSError as exc:
            issues.append(
                {"file": str(path), "line": None, "reason": f"cannot read: {exc.strerror}"}
            )

    totals, grouped = _metrics(), {}
    unfinished, orphan_results = [], []
    for call_id, pair in sorted(calls.items()):
        if call_id in conflicts:
            continue
        finished = "result" in pair
        entry = pair["result"] if finished else pair["start"]
        call, scope = entry["call"], entry["scope"]
        if not finished:
            unfinished.append(call_id)
        elif "start" not in pair:
            orphan_results.append(call_id)
        fields = {
            "robot_id": scope.get("robot_id") or "unspecified",
            "stage": call.get("stage") or "unspecified",
            "model": call.get("model") or "unspecified",
            "endpoint_sha256": call.get("endpoint_sha256") or "unspecified",
            "outcome": call.get("outcome") or "unspecified" if finished else "unfinished",
            "cache_mode": call.get("cache_mode") or "off",
        }
        key = tuple(fields.values())
        group = grouped.setdefault(key, {"group": fields, "metrics": _metrics()})
        _accumulate(totals, call, finished)
        _accumulate(group["metrics"], call, finished)
    return {
        "schema_version": "agent.model-usage-report.v1",
        "source_files": [str(path) for path in paths],
        "records": records,
        "duplicate_records": duplicates,
        "calls_seen": len(calls),
        "conflicting_call_ids": sorted(conflicts),
        "unfinished_call_ids": unfinished,
        "orphan_result_call_ids": orphan_results,
        "invalid_records": issues,
        "lifecycle_complete": not (issues or conflicts or unfinished or orphan_results),
        "totals": totals,
        "groups": [grouped[key] for key in sorted(grouped)],
        "interpretation": [
            "Only observed provider fields are summed; missing does not mean zero.",
            "Unfinished calls may have reached the provider and incurred usage; sending and cost are unknown.",
            "Conflicting call identities are excluded from all sums; exact duplicate events are counted once.",
            "Orphan results retain observed usage but indicate missing lifecycle evidence.",
            "Cache ratio uses only paired prompt/cached fields; it is not measured GPU speedup.",
            "Duration includes start-accounting persistence and the non-streaming call; it is not TTFT.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="usage JSONL files, including retained rotation segments",
    )
    parser.add_argument("--output", type=Path, help="write report JSON instead of stdout")
    args = parser.parse_args(argv)
    if args.output is not None:
        for path in args.paths:
            if (
                args.output.resolve() == path.resolve()
                or args.output.exists()
                and path.exists()
                and os.path.samefile(args.output, path)
            ):
                parser.error(
                    "report output must not overwrite an input ledger, including a hard link"
                )
    report = summarize(args.paths)
    result = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        sys.stdout.write(result)
    else:
        # Replacing a separate temporary inode also avoids truncating a ledger
        # if someone changes the output to a hard link after the check above.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=args.output.parent,
                prefix=".usage-report-",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(result)
            os.replace(temporary, args.output)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return 2 if report["invalid_records"] or report["conflicting_call_ids"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
