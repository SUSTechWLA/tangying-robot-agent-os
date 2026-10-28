#!/usr/bin/env python3
"""Repeat an audited, software-only Go fault-test allowlist; retain actual JSON.

No CLI option accepts arbitrary packages, test patterns, robot endpoints or
models. This is an engineering regression campaign, not a robot success-rate
estimate. Existing evidence directories are never overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import signal
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
MODULE = "github.com/SUSTechWLA/tangying-robot-agent-os"
CASES = {
    "edge/exchange": ["TestScopedDispatchSurvivesPeerReconfigurationAndRestart"],
    "cmd/edge-exchange": ["TestRejectCrossPeerIncomingCredentialDisclosureRegardlessOfOrder"],
    "internal/edgeagent": [
        "TestSingleRobotCampaignSQLiteCloseReopen",
        "TestSingleRobotCampaignUnknownAfterLongConfirmedPrefix",
        "TestDelegatedCompletionReadFailureIsBoundedAcrossRestart",
    ],
    "internal/capabilityagent": ["TestPlanningReadBudgetLeavesArchiveThenTwoProposalDecisions"],
}
SOURCE_DIRS = ("agent", "agentruntime", "cmd", "console", "core", "incidents", "latency",
               "internal", "edge", "fleet", "middleware", "tasks", "orchestration", "skills", "gen/go")
MAX_LOG_BYTES = 8 * 1024 * 1024
ROUND_TIMEOUT_SECONDS = 240
PLAN_DATE = "2026-10-27"
CONTEXT_ENV = ("TANGYING_AGENT_CONTEXT", "TANGYING_CONTEXT_SYNTAX", "TANGYING_CONTEXT_ORDER",
               "TANGYING_CONTEXT_ANNOTATION", "TANGYING_CONTEXT_MAX_BYTES",
               "TANGYING_MODEL_REQUEST_MAX_BYTES", "TANGYING_MODEL_OUTPUT_TOKENS")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now() -> str:
    return datetime.now(UTC).isoformat()


def source_hashes() -> dict[str, str]:
    paths = {ROOT / "go.mod", ROOT / "go.sum", Path(__file__).resolve()}
    for directory in SOURCE_DIRS:
        paths.update((ROOT / directory).rglob("*.go"))
    return {path.relative_to(ROOT).as_posix(): sha(path) for path in sorted(paths) if path.is_file()}


def retain_sources(output: Path, sources: dict[str, str]) -> str:
    digest = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()
    target = output / "sources" / (digest + ".json")
    if not target.exists():
        write_json(target, sources)
    return digest


def records_from(events: list[dict]) -> list[dict]:
    # test2json splits long t.Log output, including a checkpoint JSON object.
    pending: dict[tuple[str, str], str] = {}
    records = []
    for event in events:
        if event.get("Action") != "output":
            continue
        key = event.get("Package", ""), event.get("Test", "")
        lines = (pending.get(key, "") + event.get("Output", "")).split("\n")
        pending[key] = lines.pop()
        for line in lines:
            if "ROBOT_EXPERIMENT " in line:
                row = json.loads(line.split("ROBOT_EXPERIMENT ", 1)[1])
                if row.get("recordType") == "task":
                    fields = ("scenario", "taskId", "outcome", "targetCount", "writes", "confirmed",
                              "complete", "pending", "restartWrites", "restartPlans", "duplicateStarts",
                              "durationNs", "durationScope", "runError", "restartError")
                    records.append({"package": key[0], "test": key[1],
                                    **{field: row.get(field) for field in fields}})
    if any("ROBOT_EXPERIMENT " in value for value in pending.values()):
        raise ValueError("incomplete campaign record in raw Go output")
    return records


def execute(command: list[str], log: Path, env: dict[str, str]) -> tuple[int, str | None]:
    reason = None
    with log.open("wb") as stream:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stream,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + ROUND_TIMEOUT_SECONDS
        while process.poll() is None:
            if log.stat().st_size > MAX_LOG_BYTES:
                reason = "log_limit_exceeded"
            elif time.monotonic() > deadline:
                reason = "outer_timeout"
            if reason:
                os.killpg(process.pid, signal.SIGKILL)
                break
            time.sleep(0.1)
        code = process.wait()
        if log.stat().st_size > MAX_LOG_BYTES:
            reason = "log_limit_exceeded"
        return code, reason


def run(output: Path, repeats: int) -> int:
    output.mkdir(parents=True, exist_ok=False)
    (output / "sources").mkdir()
    (output / "runner-source.py.txt").write_bytes(Path(__file__).read_bytes())
    packages = ["./" + package for package in CASES]
    tests = [name for names in CASES.values() for name in names]
    pattern = "^(?:" + "|".join(map(re.escape, tests)) + ")$"
    # Explicit flags prevent cached test results; package/test selection is fixed.
    command = ["go", "test", "-json", "-race", "-count=1", "-timeout=180s", "-run", pattern, *packages]
    env = os.environ.copy()
    env["GOFLAGS"] = ""
    env["GORACE"] = ""
    env["GOTOOLCHAIN"] = "local"
    env["GOPROXY"] = "off"
    env["GOSUMDB"] = "off"
    for key in CONTEXT_ENV:
        env.pop(key, None)
    expected = {(MODULE + "/" + package, name) for package, names in CASES.items() for name in names}
    start = now()
    report_date = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    initial_sources = retain_sources(output, source_hashes())
    environment = {
        "schemaVersion": "edge.brain.reliability.v1", "startedAtUTC": start,
        "planDate": PLAN_DATE, "reportDateAsiaShanghai": report_date, "command": command, "repeats": repeats,
        "platform": platform.platform(), "machine": platform.machine(), "cpuCount": os.cpu_count(),
        "pythonVersion": platform.python_version(),
        "goVersion": subprocess.check_output(["go", "version"], cwd=ROOT, env=env, text=True).strip(),
        "goEnvironment": json.loads(subprocess.check_output(
            ["go", "env", "-json", "GOOS", "GOARCH", "GOVERSION", "CGO_ENABLED", "GOTOOLCHAIN"],
            cwd=ROOT, env=env, text=True)),
        "explicitEnvironmentOverrides": {key: env[key] for key in ("GOFLAGS", "GORACE", "GOTOOLCHAIN", "GOPROXY", "GOSUMDB")},
        "removedContextEnvironmentVariables": list(CONTEXT_ENV),
        "sourceIdentity": "SHA-256 of the saved repository Go sources, go.mod/go.sum and this runner; no Git command is executed",
        "initialSourceSetDigest": initial_sources,
        "evidenceType": "deterministic_in_process_software_contract_fixture",
        "gazebo": False, "realRobot": False, "externalModelInference": False,
        "sampling": "Repeated deterministic scenarios on one host; repetitions are not independent robot trials",
        "limits": {"rawLogBytesPerRound": MAX_LOG_BYTES, "outerTimeoutSecondsPerRound": ROUND_TIMEOUT_SECONDS},
    }
    write_json(output / "run.json", environment)
    scope_review = """# 执行前范围核对

以下选取已经阅读测试及其 helper，仅使用生产模块配合进程内替身；这里没有通过测试名推断设备安全性。

| 测试 | 已读取的实现与替身 | 注入 / 预期行为 |
| --- | --- | --- |
| TestScopedDispatchSurvivesPeerReconfigurationAndRestart | edge/exchange/service_test.go：receiverTransport 直接调用 Service.Receive，t.TempDir SQLite | domain / robotId / agentId 变化后，新消息不受旧信封阻塞；关闭重开 SQLite；保留旧信封 |
| TestRejectCrossPeerIncomingCredentialDisclosureRegardlessOfOrder | cmd/edge-exchange/main_test.go：testConfig 写临时合成 token，只调用 loadConfig | 两种配置顺序都拒绝跨 peer 凭据复用；不监听端口、不创建状态目录 |
| TestSingleRobotCampaignSQLiteCloseReopen | internal/edgeagent/campaign_test.go、host_test.go：campaignRobot / missionRobot 的 CallService 直接改内存，scripted campaignDecider | 32 步确认后关闭重开 SQLite，恢复后不重复动作或规划 |
| TestSingleRobotCampaignUnknownAfterLongConfirmedPrefix | 同上，lostAt=25 返回合成 transport error | 24 步确认，第 25 次写响应未知；保留 pending，重启不重放、不假完成 |
| TestDelegatedCompletionReadFailureIsBoundedAcrossRestart | internal/edgeagent/completion_test.go：completionReadFault 包装 missionRobot | 重试配额、读取预算、权限拒绝分别安全停止；不会新增物理写 |
| TestPlanningReadBudgetLeavesArchiveThenTwoProposalDecisions | internal/capabilityagent/planning_convergence_test.go、planning_context_test.go：largePlanningProvider 返回合成字符串，contextPlanningDecider 直接调用 Go 函数 | 6 次业务读后仅回查冷档两页，再保留两轮提案；无外部模型调用 |

这些包没有 TestMain/init 运行外部服务。每轮固定 6 个顶层测试；子测试单独记为 leaf，避免父子重复计入分母。日志里的 physicalStarts/writes 是 fixture 写计数，不是机器人实际运动。32 步目标完成与未知结果待核对分别统计。exchange 测试使用固定 testNow 逻辑时钟，不将信封时间当作实际实验发生日期。

不执行 Gazebo、ROS 2 Runtime、Docker、真实模型或机器人接口；不复制临时 SQLite 数据库。没有对照组，也没有独立随机场景，重复次数只用于检测这组确定性回归的偶发失败。源码前后变化时保留日志并排除单版本验收结论。
"""
    (output / "scope-review.md").write_text(scope_review, encoding="utf-8")
    outcomes = []
    for repetition in range(1, repeats + 1):
        directory = output / f"run-{repetition:02d}"
        directory.mkdir()
        before = retain_sources(output, source_hashes())
        started = now()
        clock = time.monotonic()
        log = directory / "go-test-events.jsonl"
        code, termination = execute(command, log, env)
        elapsed = time.monotonic() - clock
        after = retain_sources(output, source_hashes())
        events, bad_lines = [], []
        for index, line in enumerate(log.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("not an event object")
                events.append(event)
            except (ValueError, TypeError):
                bad_lines.append(index)
        terminals = [{"package": e["Package"], "test": e["Test"], "outcome": e["Action"], "elapsedSeconds": e.get("Elapsed")}
                     for e in events if e.get("Test") and e.get("Action") in ("pass", "fail", "skip")]
        top = [row for row in terminals if "/" not in row["test"]]
        passed = {(row["package"], row["test"]) for row in top if row["outcome"] == "pass"}
        observed = [(row["package"], row["test"]) for row in top]
        missing = sorted(expected - passed)
        unexpected = sorted(set(observed) - expected)
        duplicate_top = len(observed) != len(set(observed))
        extraction_error = None
        try:
            tasks = records_from(events)
        except (ValueError, TypeError, AttributeError) as exc:
            tasks, extraction_error = [], str(exc)
        expected_scenarios = {"sqlite_close_reopen_verified_goal": "SUCCEEDED",
                              "unknown_effect_after_long_confirmed_prefix": "RECONCILIATION"}
        task_outcomes_ok = (len(tasks) == 2 and
                            {row["scenario"]: row["outcome"] for row in tasks} == expected_scenarios)
        leaves = [row for row in terminals if not any(
            other["package"] == row["package"] and other["test"].startswith(row["test"] + "/") for other in terminals)]
        result = {
            "repetition": repetition, "startedAtUTC": started, "finishedAtUTC": now(),
            "wallSeconds": elapsed, "exitCode": code, "terminationReason": termination,
            "sourceBefore": before, "sourceAfter": after, "sourceChangedDuringRound": before != after,
            "topLevelTests": top, "leafTests": leaves,
            "requiredTestsNotPassed": missing, "unexpectedTopLevelTests": unexpected,
            "duplicateTopLevelTerminals": duplicate_top, "nonJSONLines": bad_lines,
            "extractionError": extraction_error, "taskOutcomesExpected": task_outcomes_ok,
            "taskRecords": tasks, "rawLogSHA256": sha(log), "rawLogBytes": log.stat().st_size,
        }
        result["testAssertionsPassed"] = code == 0 and not termination and not missing and not unexpected and not duplicate_top and not bad_lines and not extraction_error and task_outcomes_ok
        result["validForRecordedSource"] = result["testAssertionsPassed"] and before == after
        write_json(directory / "result.json", result)
        outcomes.append(result)
        print(json.dumps({"repetition": repetition, "of": repeats, "passed": result["testAssertionsPassed"],
                          "sourceStable": before == after, "wallSeconds": round(elapsed, 3)}), flush=True)
    final_sources = retain_sources(output, source_hashes())
    stable_campaign = initial_sources == final_sources and all(
        row["sourceBefore"] == initial_sources and row["sourceAfter"] == initial_sources for row in outcomes)
    per_test = []
    for package, name in sorted(expected):
        rows = [test for run in outcomes for test in run["topLevelTests"] if test["package"] == package and test["test"] == name]
        durations = [row["elapsedSeconds"] for row in rows if isinstance(row["elapsedSeconds"], (int, float))]
        per_test.append({"package": package, "test": name, "plannedExecutions": repeats, "observedExecutions": len(rows),
                         "passed": sum(row["outcome"] == "pass" for row in rows), "failed": sum(row["outcome"] == "fail" for row in rows),
                         "skipped": sum(row["outcome"] == "skip" for row in rows),
                         "medianTestSeconds": statistics.median(durations) if durations else None,
                         "maxTestSeconds": max(durations) if durations else None})
    top_total = sum(len(row["topLevelTests"]) for row in outcomes)
    top_failed = sum(test["outcome"] == "fail" for row in outcomes for test in row["topLevelTests"])
    leaves = [test for row in outcomes for test in row["leafTests"]]
    summary = {
        "schemaVersion": "edge.brain.reliability.summary.v1", "startedAtUTC": start, "finishedAtUTC": now(),
        "passed": stable_campaign and all(row["validForRecordedSource"] for row in outcomes),
        "stableSourceAcrossCampaign": stable_campaign, "initialSourceSetDigest": initial_sources,
        "finalSourceSetDigest": final_sources, "plannedRounds": repeats, "executedRounds": len(outcomes),
        "roundsPassingAssertions": sum(row["testAssertionsPassed"] for row in outcomes),
        "roundsWithSourceChanges": [row["repetition"] for row in outcomes if row["sourceChangedDuringRound"]],
        "denominators": {"plannedTopLevelTestExecutions": repeats * len(expected), "observedTopLevelTestExecutions": top_total,
                         "failedTopLevelTestExecutions": top_failed, "observedLeafTestExecutions": len(leaves),
                         "failedLeafTestExecutions": sum(row["outcome"] == "fail" for row in leaves)},
        "totalWallSeconds": sum(row["wallSeconds"] for row in outcomes), "perTest": per_test,
        "taskRecordOutcomes": {outcome: sum(task["outcome"] == outcome for row in outcomes for task in row["taskRecords"])
                               for outcome in ("SUCCEEDED", "RECONCILIATION", "FAILED_UNEXPECTED")},
        "limitations": ["Software contract assertions only; no Gazebo, ROS 2 runtime, real robot, or external LLM was used.",
                        "32-step success and 64-step target stopped at unknown write 25 have different outcomes and denominators.",
                        "Repeated deterministic cases are not independent samples of physical mission reliability.",
                        "Go race instrumentation, compilation and host contention affect wall time; no real-time latency or MTBF estimate.",
                        "Any source change excludes the campaign from a single-version acceptance claim; raw outcomes remain retained."],
    }
    write_json(output / "summary.json", summary)
    lines = [f"# 边缘大脑重复故障实验（{report_date}）", "", f"计划日期：{PLAN_DATE}；实际运行日期（Asia/Shanghai）：{report_date}。目录沿用计划日期；采集器起止时间使用 UTC，Go 原始事件保留其 Time 字段携带的时区偏移。", "",
             "类型：有界、确定性软件合同回归；无 Gazebo、无实机、无外部模型推理。执行前已逐项检查 [测试与替身实现](scope-review.md)。", "",
             f"实际执行 {repeats} 轮，顶层测试失败 {top_failed}/{top_total}；各轮前后源码摘要一致：{stable_campaign}；验收结论：{summary['passed']}。", "",
             "任务结局按实际 ROBOT_EXPERIMENT 日志分别报告；未知效果案例通过测试意味着保持待核对并拒绝重放，不意味着完成了任务。", "",
             "完整命令、环境和起止时间见 [run.json](run.json)；分母、逐测试耗时与限制见 [summary.json](summary.json)。每轮 result.json 指向原始 go-test-events.jsonl 的 SHA-256；sources 保存实际源文件哈希集合，[采集器原文](runner-source.py.txt)保存当时执行的脚本。临时 SQLite 只由 t.TempDir 创建，不保留数据库。", "",
             "| 轮次 | 软件断言通过 | 源码稳定 | 耗时（秒） | 证据 |", "| --- | --- | --- | --- | --- |"]
    lines.extend(f"| {row['repetition']} | {row['testAssertionsPassed']} | {not row['sourceChangedDuringRound']} | {row['wallSeconds']:.3f} | [结果](run-{row['repetition']:02d}/result.json) · [原始事件](run-{row['repetition']:02d}/go-test-events.jsonl) |" for row in outcomes)
    lines += ["", "重复执行使用：", "", "```sh", "python3 scripts/experiment_edge_brain_reliability.py --output <全新证据目录> --repeats 10", "```", "",
              "该脚本限定 4 个包中的 6 个顶层测试，拒绝覆盖已有目录。源码哈希包含维护目录的 Go 源码、go.mod/go.sum 和脚本本身；未执行 Git 操作。"]
    (output / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(output / "manifest.json", {"algorithm": "sha256", "files": {
        path.relative_to(output).as_posix(): sha(path) for path in sorted(output.rglob("*")) if path.is_file()}})
    print(json.dumps({"output": str(output), "passed": summary["passed"], "denominators": summary["denominators"]}), flush=True)
    return 0 if summary["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="New evidence directory; existing paths are rejected")
    parser.add_argument("--repeats", type=int, default=10, choices=range(10, 21), metavar="10..20")
    args = parser.parse_args()
    return run(args.output.resolve(), args.repeats)


if __name__ == "__main__":
    sys.exit(main())
