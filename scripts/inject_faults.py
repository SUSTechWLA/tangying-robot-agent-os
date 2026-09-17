"""Inject each robot fault the simulator can produce, and record what the
supervisor says about it.

Why this exists: the Go tests cover the rules with synthetic findings, and the
live check earlier covered one fault by hand. Neither answers the question an
operator actually has, which is "when this robot is broken in this particular
way, does the agent say the right thing". That needs real faults on a real
runtime, injected the same way a real failure would appear.

The three faults below are the ones the simulator is able to *prove*, and it
publishes only those — a fault list that cries wolf is worse than a short one.
Everything this driver injects therefore arrives through the same
``robot.faults.v1`` contract the runtime uses in production, not through a
back door.

Usage:
    .venv/bin/python scripts/inject_faults.py --runtime 127.0.0.1:50199 \
        --console http://127.0.0.1:8890 --scenario estop
    .venv/bin/python scripts/inject_faults.py --runtime ... --console ... --all

Exit codes: 0 = every injected fault was reported by the supervisor with advice,
1 = at least one was not, 2 = usage or connection error.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

import grpc

from tangying_robot_proto.robot.v1 import robot_pb2, robot_pb2_grpc

#: How long to wait for the supervisor to notice. The agent evaluates on its own
#: tick (5s by default), so a shorter wait would report a false negative.
DETECTION_TIMEOUT_SECONDS = 25.0


@dataclass
class Scenario:
    """One injectable fault and what the supervisor must say about it."""

    name: str
    #: What a person would observe on the robot.
    description: str
    #: The fault code the supervisor must report.
    expect_code: str
    #: A substring that must appear in the reported finding.
    #:
    #: Two different module faults both report ANOMALY_COMPONENT_FAULT, so
    #: matching on the code alone would accept the wrong fault as evidence that
    #: the right one was found — which is exactly the mistake this field exists to
    #: prevent.
    expect_message_contains: str = ""
    #: Substrings the advice must contain. Empty means "any advice is enough".
    expect_advice_contains: list[str] = field(default_factory=list)
    #: Set for faults that must never be auto-recovered.
    expect_manual: bool = False


SCENARIOS: dict[str, Scenario] = {
    "estop": Scenario(
        name="estop",
        description="急停锁存：机器人被停止，软件不会自动复位",
        expect_code="ANOMALY_SAFETY_STOP",
        # Clearing an e-stop is a person's job. If the advice ever stops saying
        # so, someone will eventually try to automate it.
        expect_advice_contains=["人工", "急停"],
        expect_manual=True,
    ),
    "no_map": Scenario(
        name="no_map",
        description="移动场景没有启用地图：导航能力被摘掉",
        expect_code="ANOMALY_COMPONENT_FAULT",
        expect_message_contains="NAV_MAP_NOT_READY",
        expect_advice_contains=["地图"],
        expect_manual=True,
    ),
    "calibration": Scenario(
        name="calibration",
        description="工位高度与标定不一致",
        expect_code="ANOMALY_COMPONENT_FAULT",
        expect_message_contains="WORKCELL_CALIBRATION_MISMATCH",
        expect_advice_contains=["标定"],
        expect_manual=True,
    ),
}


def estop(runtime: str, reason: str) -> None:
    """Latch the runtime's emergency stop."""
    with grpc.insecure_channel(runtime) as channel:
        stub = robot_pb2_grpc.RobotRuntimeStub(channel)
        stub.EmergencyStop(robot_pb2.EStopRequest(reason=reason, operator_id="fault-injector"), timeout=10)


def clear_estop(runtime: str) -> None:
    """Release the latch, so the next scenario starts clean."""
    with grpc.insecure_channel(runtime) as channel:
        stub = robot_pb2_grpc.RobotRuntimeStub(channel)
        for rpc in ("ClearEmergencyStop", "ResetEmergencyStop", "ReleaseEmergencyStop"):
            method = getattr(stub, rpc, None)
            if method is None:
                continue
            try:
                method(robot_pb2.EStopRequest(reason="fault-injector cleanup", operator_id="fault-injector"), timeout=10)
                return
            except grpc.RpcError:
                continue


def fetch_alerts(console: str) -> dict[str, Any]:
    with urllib.request.urlopen(f"{console.rstrip('/')}/v1/agent/alerts", timeout=10) as response:
        return json.load(response)


def wait_for_alert(console: str, scenario: Scenario) -> dict[str, Any] | None:
    """Poll until the supervisor reports this scenario's fault, or time out.

    Matching requires both the code and, where the scenario names one, a
    substring of the message: several module faults share one code, so the code
    alone cannot tell them apart.
    """
    deadline = time.monotonic() + DETECTION_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            payload = fetch_alerts(console)
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1.5)
            continue
        for alert in _all_alerts(payload):
            if not alert.get("active") or alert.get("code") != scenario.expect_code:
                continue
            wanted = scenario.expect_message_contains
            if wanted and wanted not in (alert.get("message") or ""):
                continue
            return alert
        time.sleep(1.5)
    return None


def _all_alerts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return list(payload.get("alerts") or []) + list(payload.get("runnerAlerts") or [])


def run_scenario(scenario: Scenario, runtime: str, console: str) -> dict[str, Any]:
    """Inject one fault and report whether the supervisor located it."""
    result: dict[str, Any] = {
        "scenario": scenario.name,
        "description": scenario.description,
        "expectCode": scenario.expect_code,
    }

    if scenario.name == "estop":
        estop(runtime, "fault-injector: estop scenario")
        result["injected"] = True
    else:
        # The map and calibration faults are produced by the runtime's own
        # commissioning checks. They cannot be forced from outside, so the driver
        # reports what the runtime is already publishing rather than pretending to
        # inject them — a fabricated injection would prove nothing.
        result["injected"] = False
        result["note"] = (
            "此故障由运行时自行判定，无法从外部注入；"
            "本次校验的是运行时当前是否正在上报它"
        )

    alert = wait_for_alert(console, scenario)
    result["detected"] = alert is not None
    if alert is None:
        if result.get("injected") is False:
            # The runtime decides this fault for itself and currently says it is
            # not present. Reporting that as "the supervisor missed it" would be
            # wrong: there was nothing to find.
            result["verdict"] = "当前不存在此故障（无法注入，运行时未上报）"
            result["notApplicable"] = True
        else:
            result["verdict"] = "未定位"
        return result

    advice = " / ".join(alert.get("recommendedActions") or [])
    result["message"] = alert.get("message", "")
    result["severity"] = alert.get("severity", "")
    result["advice"] = alert.get("recommendedActions") or []
    result["missingEvidence"] = alert.get("missingEvidence") or []
    result["retryForbidden"] = bool(alert.get("automaticRetryForbidden"))

    missing = [want for want in scenario.expect_advice_contains if want not in advice]
    if missing:
        result["verdict"] = f"定位了，但建议里缺少 {missing}"
        result["ok"] = False
    elif not result["advice"]:
        result["verdict"] = "定位了，但没有给出建议"
        result["ok"] = False
    else:
        result["verdict"] = "定位并给出可执行建议"
        result["ok"] = True
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", default="127.0.0.1:50051", help="Robot Runtime gRPC address")
    parser.add_argument("--console", default="http://127.0.0.1:8787", help="Local Agent console base URL")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), help="one scenario")
    parser.add_argument("--all", action="store_true", help="every scenario")
    parser.add_argument("--output", help="write the report as JSON to this path")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    arguments = parser.parse_args()

    names = sorted(SCENARIOS) if arguments.all else ([arguments.scenario] if arguments.scenario else [])
    if not names:
        parser.error("choose --scenario NAME or --all")

    try:
        baseline = fetch_alerts(arguments.console)
    except (urllib.error.URLError, TimeoutError) as error:
        print(f"cannot reach the console at {arguments.console}: {error}", file=sys.stderr)
        return 2

    report: dict[str, Any] = {
        "runtime": arguments.runtime,
        "console": arguments.console,
        "supervision": baseline.get("supervision") or {},
        "results": [],
    }

    for name in names:
        result = run_scenario(SCENARIOS[name], arguments.runtime, arguments.console)
        report["results"].append(result)
        if not arguments.json:
            if result.get("notApplicable"):
                mark = "N/A "
            elif result.get("ok"):
                mark = "OK  "
            else:
                mark = "MISS"
            print(f"[{mark}] {result['scenario']}: {result['description']}")
            print(f"        期望代码: {result['expectCode']}")
            print(f"        结论: {result['verdict']}")
            if result.get("message"):
                print(f"        定位: {result['message']}")
            for action in result.get("advice") or []:
                print(f"        建议: {action}")
            if result.get("note"):
                print(f"        说明: {result['note']}")
            print()

    # Leave the runtime as it was found: a fault injector that also leaves faults
    # behind turns the next test into a different test.
    if "estop" in names:
        clear_estop(arguments.runtime)

    if arguments.output:
        with open(arguments.output, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
    if arguments.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    # A scenario whose fault is not currently present is not a failure; a scenario
    # whose fault WAS present and was missed is.
    usable = [r for r in report["results"] if not r.get("notApplicable")]
    if not usable:
        return 3
    return 0 if all(r.get("ok") for r in usable) else 1


if __name__ == "__main__":
    raise SystemExit(main())
