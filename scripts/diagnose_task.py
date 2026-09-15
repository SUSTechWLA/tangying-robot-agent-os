"""Assemble one task failure into a machine-readable incident, with a first diagnosis.

An AI (or a coding agent) cannot diagnose a robot from four unrelated endpoints:
"which step failed", "what the camera saw", "can it resume", "how long each phase
took" and "was the map complete" live in different stores, and none of them says
*what to check first*. This assembles them into one ``incident.v1`` record and
attaches a deterministic, auditable first diagnosis.

The division of labour is deliberate:

* **The system states what it knows.** Terminal states, reason codes, timings,
  evidence ids, revisions and recovery verdicts are facts read from the robot.
* **The classifier states what it suspects.** A fixed table maps an observed code
  to a fault family, ranked probable causes, the checks to run first, and the
  tests that already cover that family. It never guesses beyond the table: an
  unknown code produces a family of ``unclassified`` plus **what evidence is
  missing**, which is the honest answer and the instruction for what to add.
* **The loop is not automated here.** This writes a record and a proposal; it
  changes no code and touches no robot. Acting on it is a reviewed step.

Usage:
    .venv/bin/python scripts/diagnose_task.py --task task-abc --base-url http://127.0.0.1:8897
    .venv/bin/python scripts/diagnose_task.py --fixture tests/fixtures/incident --output /tmp/i
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

SCHEMA_VERSION = "incident.v1"

#: Fault families. Each entry: what it means, what usually causes it, what to
#: check first, and the tests that already cover it. Test references are real
#: paths and names - a coding agent can run them directly.
FAMILIES: dict[str, dict[str, Any]] = {
    "physical_outcome_unknown": {
        "summary": "一个物理动作已经开始但结果没有记录：系统不会重放，也不允许靠改版绕过。",
        "codes": ["PHYSICAL_OUTCOME_UNKNOWN", "EXECUTION_OUTCOME_UNKNOWN", "UNVERIFIED_WORLD_MUTATION"],
        "causes": [
            "命令执行期间进程/连接中断（掉电、崩溃、网络）",
            "运行时报了成功但没有提供动作后的新观测，闭环门拒绝判定完成",
            "验证未通过（置信度不足）而写操作已经发生",
        ],
        "checks": [
            "GET /v1/tasks/{id}/recovery 看 requiresReconciliation 与 uncertainStepIds",
            "核对现场：物体是否在夹爪中、是否已在目标位置（这一步不能靠软件推断）",
            "GET /v1/tasks/{id}/observations 找该步骤的 evidenceId，看采集到的那一刻",
        ],
        "resolution": "人工核对现场后，用新的证据让步骤重新判定；绝不重放未知结果的物理动作。",
        "tests": [
            "edge/agent/recovery_test.go::TestUnknownPhysicalStepCannotBeBypassedWithAnotherRevision",
            "internal/localapp/recovery_test.go::TestUnknownPhysicalReceiptBlocksResumeAndRevisionAfterReopen",
        ],
    },
    "verification_not_observed": {
        "summary": "动作完成，但核验没有连续观察到预期关系（3 帧稳定样本）。",
        "codes": ["PLACEMENT_NOT_OBSERVED", "GRASP_NOT_OBSERVED"],
        "causes": [
            "核验视角看不到目标（遮挡、距离、工作体积之外）",
            "物体仍在运动/回弹，位移超过稳定阈值",
            "关系判据不满足（例如未落在容器几何范围内）",
            "检测器在该帧漏检目标",
        ],
        "checks": [
            "读步骤回执里的 verification 块：sample_count / observed_relation / max_displacement_m",
            "GET /v1/tasks/{id}/observations/{evidence} 看核验帧的 RGB 与深度",
            "确认目标物体与容器的实际相对位置",
        ],
        "resolution": "先判断是真的没放好还是没看见：前者重新放置，后者调整核验视角或补一帧再核验。",
        "tests": [
            "sim/mujoco/tests/test_rgbd_runtime.py::test_a_failed_placement_verification_reports_what_it_saw",
            "tests/e2e/test_pick_place.py::test_natural_language_reaches_verified_simulation_result",
        ],
    },
    "goal_or_localization_unclear": {
        "summary": "地图上没有可通行的目标/起点：目标点未扫描、安全间距不足，或起点周围未知。",
        "codes": ["GOAL_NOT_CLEAR", "LOCALIZATION_NOT_CLEAR", "NAV_ROTATION_LIMIT", "NO_KNOWN_PATH",
                  # The reference driver's per-pulse refusals: each one means the
                  # same thing to a diagnostician - this path is not certifiable.
                  "NAV_MODEL_COLLISION", "NAV_PATH_OCCLUDED", "NAV_OBSTACLE_OBSERVED",
                  "NAV_DEPTH_UNKNOWN", "NAV_PATH_OUT_OF_VIEW", "NAV_WORKSPACE_LIMIT"],
        "causes": [
            "勘测没覆盖到该区域，或起点脚下那块地从未被观测",
            "地图上的膨胀把门口/通道判死（自车体点云、幽灵占用）",
            "目标朝向与当前朝向之差超过驱动的单命令转角上限",
            "地图过期：现场出现了地图上不存在的障碍",
        ],
        "checks": [
            "POST /v1/robot/services {\"name\":\"mapping.conflicts\"} 看地图与最新观测是否冲突",
            "GET /v1/maps/{id} 看该地图的 provenance：partial / fault / 覆盖情况",
            "确认目标工作区有认证净空位姿（goalAdjustments）",
        ],
        "resolution": "用 mapping.start {baseMapId} 续建补扫，或让目标落在认证净空位姿上；不要去放宽净空判据。",
        "tests": [
            "sim/mujoco/tests/test_furnished_home.py::test_the_survey_route_looks_back_at_where_it_started",
            "sim/mujoco/tests/test_rgbd_runtime.py::test_room_goals_are_published_only_where_the_map_certifies_them",
        ],
    },
    "mapping_session_fault": {
        "summary": "建图/勘测会话被故障中断：已测绘部分会保存，并带上原因。",
        "codes": ["STALE_CAPTURE", "CALIBRATION_CHANGED", "DEPTH_STARVED", "WORKFLOW_FAULT", "SCAN_TOO_SMALL"],
        "causes": [
            "RGB-D 或底盘位姿过期（采集跟不上）",
            "标定在扫描期间被重新执行",
            "连续多帧深度点不足（对着近墙/暗角）",
            "内部异常（求解器、驱动）",
        ],
        "checks": [
            "GET /v1/maps/{id}/artifact/slam_session 看 partial 与 fault.code",
            "mapping.status 看停止原因与已测绘比例",
            "必要时续建：mapping.start {mode:\"explore\", baseMapId}",
        ],
        "resolution": "处理触发原因（重新标定/改善视野）后用 baseMapId 续建，而不是重扫整间屋。",
        "tests": [
            "robot/gateway/tests/test_robot_services.py::test_a_stale_capture_publishes_what_was_mapped_and_names_the_fault",
            "robot/gateway/tests/test_robot_services.py::test_a_depth_starved_view_does_not_throw_the_survey_away",
        ],
    },
    "safety_stop": {
        "summary": "机器人处于急停/安全停止：不能通过任务动作解除。",
        "codes": ["EMERGENCY_STOP_LATCHED", "SAFETY_STOPPED", "SAFETY_STOP"],
        "causes": ["物理急停被按下", "安全监督触发（碰撞风险、越界、失控）"],
        "checks": [
            "GET /v1/tasks/{id}/recovery 看 reasonCode=SAFETY_STOPPED",
            "现场确认危险源已排除",
        ],
        "resolution": "现场解除急停后重新创建或继续任务；软件不会自动复位。",
        "tests": [
            "internal/localapp/recovery_test.go::TestRecoveryRefusesToClearALatchedSafetyStop",
            "tasks/service_test.go::TestSafetyStopCannotBeAutomaticallyResumed",
        ],
    },
    "grounding_failure": {
        "summary": "自然语言落地失败：目标/容器找不到、不唯一，或不在要求的来源里。",
        "codes": ["DESTINATION_NOT_FOUND", "GROUNDING_ABSENT", "OBJECT_NOT_FOUND", "SEMANTIC_AMBIGUOUS"],
        "causes": [
            "该帧没看到目标物体（视角/遮挡/工作体积之外）",
            "同类别多实例，选择器不唯一",
            "物体不在用户指定的来源（inside/on 关系不匹配）",
        ],
        "checks": [
            "看任务事件里 grounding 步骤的失败消息（含 objects/destinations 计数）",
            "observe_scene 当前帧的可见实体清单",
        ],
        "resolution": "换视角重新观测、把说法收窄（颜色/位置），或先补建物体语义层。",
        "tests": [
            "edge/robotclient/grounding_test.go::TestGroundingEnforcesTheRequestedSourceBeforePlanningMotion",
        ],
    },
    "fleet_consistency": {
        "summary": "云侧一致性边界：fence/租约/队列/协调器/观测序列。",
        "codes": ["ErrStaleFencingToken", "ErrIntentIdentityConflict", "EOF_LEASE_EXPIRED", "ErrResyncRequired"],
        "causes": [
            "旧主的完成上报在监护权转移之后到达",
            "队列/Redis 不可用",
            "订阅游标落后于保留窗口",
        ],
        "checks": ["fleet 事件日志与 outbox 记录", "设备租约状态", "world hub 的 revision 与游标"],
        "resolution": "按 fence 语义拒绝旧写入；队列恢复后重投；游标落后要求重新同步。",
        "tests": ["tests/e2e/test_fleet_faults.py::test_fault_boundary_preserves_consistency_invariant"],
    },
}

#: Codes that mean "a task that ran cannot simply be retried".
_TERMINAL_HINTS = {"PHYSICAL_OUTCOME_UNKNOWN": "needs_human_reconciliation"}


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def classify(codes: list[str], *, terminal_state: str = "", reconciliation: bool = False) -> dict[str, Any]:
    """Map observed codes to a fault family with causes, checks and tests.

    An unrecognised code is not forced into a family: the classifier reports
    ``unclassified`` and says which evidence would have let it decide.
    """
    observed = [code for code in codes if code]
    for name, family in FAMILIES.items():
        matched = [code for code in observed if code in family["codes"]]
        if matched:
            return {
                "family": name,
                "matchedCodes": sorted(set(matched)),
                "unmatchedCodes": sorted(set(observed) - set(matched)),
                "summary": family["summary"],
                "probableCauses": list(family["causes"]),
                "checks": list(family["checks"]),
                "proposedResolution": family["resolution"],
                "coveringTests": list(family["tests"]),
                "needsHuman": observed[0] in _TERMINAL_HINTS or reconciliation,
            }
    missing = []
    if not observed:
        missing.append("任务事件里没有任何错误码：确认任务是否到达终态、或运行是否仍在进行")
    if not terminal_state:
        missing.append("任务终态未知")
    return {
        "family": "unclassified",
        "matchedCodes": [],
        "unmatchedCodes": sorted(set(observed)),
        "summary": "错误码不在已知故障族里，系统不猜测根因。",
        "probableCauses": [],
        "checks": [
            "GET /v1/tasks/{id} 完整事件流（含每步参数与错误）",
            "GET /v1/tasks/{id}/recovery 恢复判定",
            "GET /v1/telemetry/latency?windowMs=0 该步骤四段耗时",
        ],
        "proposedResolution": "按上面的检查逐项定位；确认后把该故障补进 FAMILIES 与回归测试。",
        "coveringTests": [],
        "missingEvidence": missing or ["该错误码尚未归类"],
        "needsHuman": True,
    }


def observed_codes(task: dict[str, Any]) -> list[str]:
    """Every error code the task's event stream actually recorded."""
    codes: list[str] = []
    for event in task.get("events") or []:
        payload = event.get("payload") or {}
        for key in ("error", "errorCode", "reasonCode"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                codes.append(value)
        message = event.get("message") or ""
        if event.get("type") == "STATE_CHANGED" and "failed:" in message:
            codes.append(message.rsplit("failed:", 1)[1].strip().split(" ", 1)[0])
    return codes


def step_timings(latency: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Per-step phase timings, when the deployment records them."""
    rows = []
    for group in (latency or {}).get("groups") or []:
        phases = group.get("phases") or {}
        rows.append({
            "capability": group.get("key"),
            "count": group.get("count"),
            "queueMs": (phases.get("queue") or {}).get("p50Ms"),
            "executeMs": (phases.get("execute") or {}).get("p50Ms"),
            "executeMaxMs": (phases.get("execute") or {}).get("maxMs"),
            "verifyMs": (phases.get("verify") or {}).get("p50Ms"),
            "outcomes": group.get("outcomes"),
            "slowestStepId": group.get("slowestStepId"),
        })
    return rows


def build_incident(task: dict[str, Any], recovery: dict[str, Any] | None,
                   observations: dict[str, Any] | None, latency: dict[str, Any] | None,
                   environment: dict[str, Any] | None = None) -> dict[str, Any]:
    """One failure, one record: facts first, then a labelled first diagnosis."""
    codes = observed_codes(task)
    terminal = str(task.get("state") or "")
    reconciliation = bool((recovery or {}).get("requiresReconciliation"))
    diagnosis = classify(codes, terminal_state=terminal, reconciliation=reconciliation)
    records = (observations or {}).get("records") or []
    evidence = [
        {"stepId": record.get("stepId"), "id": record.get("id"), "captureId": record.get("captureId"),
         "observedAtUnixMs": record.get("observedAtUnixMs"), "sourceId": record.get("sourceId"),
         "rgbSha256": record.get("rgbSha256"), "depthSha256": record.get("depthSha256")}
        for record in records
    ]
    timeline = [
        {"sequence": event.get("sequence"), "type": event.get("type"),
         "occurredAt": event.get("occurredAt"),
         "stepId": (event.get("payload") or {}).get("stepId") or event.get("stepId"),
         "toolName": (event.get("payload") or {}).get("toolName"),
         "status": (event.get("payload") or {}).get("activityStatus"),
         "error": (event.get("payload") or {}).get("error"),
         "message": event.get("message")}
        for event in task.get("events") or []
    ]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "task": {"id": task.get("id"), "request": task.get("request"), "adapter": task.get("adapter"),
                 "revision": task.get("currentRevision"), "state": terminal,
                 "createdAt": task.get("createdAt"), "updatedAt": task.get("updatedAt")},
        "recovery": {key: (recovery or {}).get(key) for key in
                     ("canResume", "requiresReconciliation", "reasonCode", "reason",
                      "completedStepIds", "uncertainStepIds")},
        # The environment fingerprint is what makes a regression traceable to a
        # change: same code, different calibration or map revision is a different
        # system.
        "environment": environment or {},
        "timeline": timeline,
        "stepTimings": step_timings(latency),
        "evidence": evidence,
        "observedCodes": sorted(set(codes)),
        "diagnosis": diagnosis,
        # Explicitly not done here. The record proposes; a human or a reviewed
        # coding-agent change acts.
        "nextActions": (["核对现场后再决定继续或重放（系统不会重放未知结果的物理动作）"]
                        if diagnosis["needsHuman"] else [])
                       + ["运行 coveringTests 里的回归测试确认现有行为",
                          "若确认是缺陷：修复 + 补一条覆盖该故障的回归测试 + 重新跑故障矩阵"],
        "automation": {"acted": False, "reason": "本工具只产出记录与建议，不修改代码、不动机器人。"},
    }


def collect_live(base_url: str, task_id: str) -> dict[str, Any]:
    def get(path: str) -> Any:
        try:
            with urlopen(base_url.rstrip("/") + path, timeout=20) as response:
                return json.loads(response.read().decode() or "{}")
        except (URLError, ValueError):
            return None

    return build_incident(
        get(f"/v1/tasks/{task_id}") or {},
        get(f"/v1/tasks/{task_id}/recovery"),
        get(f"/v1/tasks/{task_id}/observations"),
        get("/v1/telemetry/latency?groupBy=capability&windowMs=0"),
        environment={"runtime": get("/v1/runtime")},
    )


def collect_fixture(directory: Path) -> dict[str, Any]:
    return build_incident(
        _read(directory / "task.json") or {},
        _read(directory / "recovery.json"),
        _read(directory / "observations.json"),
        _read(directory / "latency.json"),
        _read(directory / "environment.json"),
    )


def summarise(incident: dict[str, Any]) -> str:
    diagnosis = incident["diagnosis"]
    lines = [
        f"任务 {incident['task']['id']} · {incident['task']['state']} · 修订 {incident['task']['revision']}",
        f"请求：{incident['task']['request']}",
        f"观察到的错误码：{', '.join(incident['observedCodes']) or '（无）'}",
        f"故障族：{diagnosis['family']}"
        + (f"（命中 {', '.join(diagnosis['matchedCodes'])}）" if diagnosis["matchedCodes"] else ""),
        f"含义：{diagnosis['summary']}",
    ]
    if diagnosis["unmatchedCodes"]:
        # A task can fail more than once. The family explains one code; the rest
        # are listed rather than dropped, so a second failure is not hidden.
        lines.append("同一任务里还有未归类的错误码：" + "、".join(diagnosis["unmatchedCodes"]))
    if diagnosis.get("missingEvidence"):
        lines.append("缺失证据：" + "；".join(diagnosis["missingEvidence"]))
    if diagnosis["probableCauses"]:
        lines.append("可能根因（按表内顺序）：")
        lines += [f"  {index}. {cause}" for index, cause in enumerate(diagnosis["probableCauses"], 1)]
    if diagnosis["checks"]:
        lines.append("先查这些：")
        lines += [f"  - {check}" for check in diagnosis["checks"]]
    lines.append(f"建议处置：{diagnosis['proposedResolution']}")
    if diagnosis["coveringTests"]:
        lines.append("已有回归测试：")
        lines += [f"  - {test}" for test in diagnosis["coveringTests"]]
    if incident["recovery"].get("canResume") is not None:
        lines.append(f"恢复判定：canResume={incident['recovery']['canResume']} "
                     f"requiresReconciliation={incident['recovery']['requiresReconciliation']} "
                     f"({incident['recovery'].get('reasonCode')})")
    if incident["evidence"]:
        lines.append(f"证据采集 {len(incident['evidence'])} 份，最近一份 {incident['evidence'][-1]['id'][:12]}…")
    if incident["stepTimings"]:
        slowest = max(incident["stepTimings"],
                      key=lambda row: row.get("executeMaxMs") or 0)
        lines.append(f"最慢能力：{slowest['capability']} 执行 p50={slowest['executeMs']} ms "
                     f"max={slowest['executeMaxMs']} ms")
    lines.append("自动化：未执行任何修改（本工具只产出记录与建议）。")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", help="task id to diagnose on a live console")
    parser.add_argument("--base-url", default="http://127.0.0.1:8897")
    parser.add_argument("--fixture", type=Path, help="directory of recorded API responses")
    parser.add_argument("--output", type=Path, help="write the incident JSON here")
    args = parser.parse_args()
    if args.fixture:
        incident = collect_fixture(args.fixture)
    elif args.task:
        incident = collect_live(args.base_url, args.task)
    else:
        parser.error("give --task (live) or --fixture (offline)")
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        target = args.output / (f"{incident['task'].get('id') or 'task'}-incident.json")
        target.write_text(json.dumps(incident, ensure_ascii=False, indent=2) + "\n")
        print(f"incident → {target}")
    print(summarise(incident))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
