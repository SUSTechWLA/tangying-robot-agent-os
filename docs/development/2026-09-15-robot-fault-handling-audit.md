# 机器人异常处理审计：能不能恢复、异常有没有入表

**审计范围**：机器人侧（边端/运行时/本地 Agent）与云侧（Fleet）的故障处理；重点是三件事——**行为是否正确**（失败关闭还是继续）、**任务能否恢复**、**异常是否进入持久记录**。
**方法**：先列出代码里所有故障码与恢复分支，再逐项找出对应测试并实跑；对没有测试的项补测试；发现一处行为缺陷并修复。
**本次实跑结果**：`tests/e2e/test_fleet_faults.py`（云侧 10 类故障 + 1 个断线重连集成）**11 通过**；`tests/e2e/test_robocasa_faults.py` + `test_rgbd_recovery.py` **10 通过 / 2 跳过**（跳过项需要 RoboCasa 可选依赖）；Go 全包通过；Gateway 522 通过。

---

## 一、结论摘要

| 问题 | 结论 |
| --- | --- |
| 行为是否正确？ | **是，且是"失败关闭"**：不确定的物理动作**永不重放**、低 fence/旧版本**写不进去**、观测有洞**要求重新同步**、急停**不能被任务动作解除**。没有发现"把失败当成功"的路径 |
| 能否恢复任务？ | **能，但分三类**：① 重试即可（预检拒绝，如 `NAV_MAP_NOT_READY`）；② 暂停/重启后继续（已完成步骤不重放）；③ **不可自动恢复**（物理结果未知、急停锁定）——必须人工核对现场 |
| 异常入表？ | **是**。异常落在四个持久层：任务事件流（`tasks.TaskEvent`）、步骤执行历史（`middleware.StepRun` 状态）、证据库（`tasks.EvidenceStore`）、世界快照（WorldHub revision）。本轮补上了**勘测中断时的部分地图记录**（此前会整张丢弃） |
| 发现的问题 | 一处**真实缺陷**：`STALE_CAPTURE` / `CALIBRATION_CHANGED` / 任何会话级故障会**丢弃已测绘的地图**（实测：74% 覆盖率的地图完全没保存）。**已修复**：故障时发布已测绘部分，并把故障码与 `partial: true` 写进地图 provenance |

---

## 二、机器人侧异常：行为、恢复、入表

| 异常 | 期望行为 | 实现位置 | 测试 | 入表方式 |
| --- | --- | --- | --- | --- |
| **物理动作结果未知**（命令发出后失联/中断） | 步骤**保持 STARTED**，永不判定完成、永不重放；恢复前必须核对现场 | `edge/agent/runner.go` `ErrPhysicalOutcomeUnknown`；`internal/localapp/recovery.go` `RequiresReconciliation` | `edge/agent/recovery_test.go::TestUnknownPhysicalStepCannotBeBypassedWithAnotherRevision`（断言拿取调用 **0 次**）、`internal/localapp/recovery_test.go::TestUnknownPhysicalReceiptBlocksResumeAndRevisionAfterReopen` | 步骤历史 STARTED + 任务事件 `EXECUTION_OUTCOME_UNKNOWN` |
| **预检拒绝**（地图未就绪、标定中） | 步骤记 `FAILED` 且**可重试**（不派发任何运动） | `edge/agent/runner.go` `preflightFailure` | `edge/agent/runner_test.go::TestRunnerKeepsNeverDispatchedNavigationRetryable` | 步骤历史 FAILED + 工具活动事件 |
| **急停锁定** | 任务进 `SAFETY_STOPPED`，**任务动作不能解除**；恢复视图明确拒绝 | `core/taskgraph/state.go` 转移表；`internal/localapp/recovery.go` | **本轮新增** `internal/localapp/recovery_test.go::TestRecoveryRefusesToClearALatchedSafetyStop`（`CanResume=false`、`reasonCode=SAFETY_STOPPED`、事件在库里）、`tasks/service_test.go::TestSafetyStopCannotBeAutomaticallyResumed` | 任务事件 `SAFETY_STOPPED` |
| **可恢复失败**（目标不可达、需重新定位等） | 置 `RECOVERABLE_FAILURE`，允许从**安全恢复**继续；不重放已完成步骤 | `internal/localapp/recovery.go` `CanResume` | **本轮新增** `TestRecoveryOffersResumeForARecoverableFailureWithoutUncertainSteps`（`RESUME_AVAILABLE`）；`TestPauseReopenAndResumeDoesNotReplayCompletedPhysicalSteps` | 任务事件 `STATE_CHANGED` + 恢复视图 `reasonCode` |
| **验证未通过**（置信度不足/闭环门拒绝） | 步骤不算完成；写操作保持 STARTED，交给恢复对账 | `edge/agent/runner.go` 置信度门 + `core/closedloop` | `edge/agent/evidence_test.go::TestFailedVerificationRetainsItsOriginalEvidence` | 证据库保留失败时的原始采集 |
| **暂停/重开/继续** | 继续时**重新感知再验证**，只做未完成动作 | `internal/localapp` | `TestPauseReopenAndResumeDoesNotReplayCompletedPhysicalSteps`、`TestResumeRechecksGraspInsteadOfTrustingPrePauseVerification`、`TestResumeRefusesToAttachCompletedPickToDifferentGroundedObject` | 事件 `LOCAL_PAUSE_REQUESTED`、`RECOVERY_ACTIVITY`、`LOCAL_RECOVERY_BLOCKED` |
| **标定中途变化** | 停止扫描，**保留已测绘部分**并记下原因 | `robot_workflow._sample` 抛 `CALIBRATION_CHANGED` → `_handle_session_fault` | **本轮新增** `robot/gateway/tests/test_robot_services.py::test_a_calibration_change_mid_scan_keeps_the_map` | 地图 provenance：`partial: true` + `fault.code` |
| **采集过期**（RGB-D/位姿超时） | 同上 | 同上（`STALE_CAPTURE`） | **本轮新增** `test_a_stale_capture_publishes_what_was_mapped_and_names_the_fault` | 同上 |
| **深度点不足**（对着近墙/暗角） | **跳过该帧继续**；连续 25 帧才优雅收尾并发布 | `robot_workflow._sample` / `EXPLORATION["depthStarvedLimit"]` | `test_a_depth_starved_view_does_not_throw_the_survey_away` | 地图 provenance：原因 `depth_starved` |
| **非预期内部故障**（求解器崩溃等） | 仍发布已测绘部分，reason 记 `WORKFLOW_FAULT` + 类型 | `_fault_of` | **本轮新增** `test_an_unexpected_fault_is_still_named_in_the_record` | 同上 |
| **什么都没测到** | **不发布空地图**，明确报"未能开始" | `_build` 的 `SCAN_TOO_SMALL` 门 | **本轮新增** `test_a_session_with_nothing_measured_is_not_published_as_a_map`、`test_a_leg_that_measured_nothing_is_reported_and_not_published` | 服务状态 + 无产物（正确地什么都不写） |
| **导航类拒绝**（目标不可达/定位不足/超转角/观察越界/模型碰撞/命令过期/未收臂） | 拒绝并给出可执行原因；其中**转角超限现在由前置摆位步骤消除** | `grid_navigation`、`rgbd_navigation`、`rgbd_runtime` | `sim/mujoco/tests/test_rgbd_navigation.py`（`NAV_PATH_OUT_OF_VIEW`/`NAV_OBSERVATION_INVALID`）、`test_robot_services.py`（`NAV_MODEL_COLLISION`）、`test_rtabmap_client.py`（`NAV_COMMAND_STALE`）、`test_navigation_return_stow.py`（`NAV_ARMS_STOWED`）、`test_furnished_home.py`/`test_rgbd_runtime.py`（`LOCALIZATION_NOT_CLEAR`） | 工具活动事件 `error` 字段 + 步骤状态 |
| **受限工作区** | 越界目标在落地前被拒（不会"试着开过去"） | `edge/robotclient` `withinWorkspace` | `edge/robotclient` 语义路由测试 | 同上 |
| **机器人离线**（云侧） | 设备 OFFLINE、步骤等待，**不向离线机器人分派**；租约过期即判离线 | `fleet/registry`、`fleet/lease` | `registry_test.go::TestDeviceGoesOfflineAfterLeaseExpiry`、`TestRegisterAndHeartbeatRenewLease` | 设备表 + world snapshot |

---

## 三、云侧（Fleet）异常：本次实跑的 10 类

`pytest tests/e2e/test_fleet_faults.py` → **11 通过（23 s）**。每类由一个或多个真测试支撑：

| 故障 | 断言的核心不变量 | 测试 |
| --- | --- | --- |
| 观测重复/乱序 | 投影器拒绝重复与乱序的 source sequence | `core/worldmodel` `TestProjectorRejectsDuplicateAndOutOfOrderSourceSequence` |
| 放置后 worker 崩溃 | 物理结果未知时**不推进**、不重复放置 | `TestWorkerCrashAfterPlaceLeavesUncertainIntent` |
| 协调器重启 | 修订身份跨重启存活 | `fleet/coordinator/revisions_test.go::TestRevisionIdentitySurvivesCoordinatorFailover` |
| Redis 队列宕机 | 已提交的交接进 outbox，任务图不被改坏，发布者恢复后可重投 | `TestQueueOutageLeavesCommittedHandoffOutboxForRecovery`、`TestOutboxDispatcherRecoversAfterPublisherReturns` |
| 旧 fencing token | 低 fence 完成被拒且**不写事件**；完全相同的重复是幂等的 | `TestCompletionRejectsLowerFenceAndExactDuplicateIsIdempotent` |
| 交接后接收方离线 | 既不能领取也不能交付 | `TestReceiverOfflineAfterHandoffCannotClaimOrDeliver` |
| 相机丢失 + UI 重连 | 语义真值交接不被阻断；渲染失败非致命且报为异常 | `TestCameraLossDoesNotBlockSemanticGroundTruthHandoff`、`test_renderer_failure_is_nonfatal_and_reported_as_anomaly` |
| 外部搬动方块 | 外部改变阻止"已验证完成" | `TestExternalBlockMovePreventsVerifiedCompletion` |
| 版本变更与 fencing | 只有持当前 fence 的 leader 能推进；旧版本/错命令写不进 | `TestConfirmRevisionWaitsForRunningIntentThenActivatesAtHarnessSafePoint` 等 |
| 版本化任务的事件空档 | 前端拒绝陈旧事实并要求重新同步 | `web/app_test.mjs` 对应用例 |

**RoboCasa 层**（`test_robocasa_faults.py`，10 通过 / 2 跳过）：观测重复乱序、旧 fencing、接收方离线、相机丢失、外部搬动、急停与取消、检查点原子性与模型门、控制面重启后的目录恢复。

---

## 四、"异常入表"具体落在哪张表

| 层 | 记录 | 内容 | 谁读 |
| --- | --- | --- | --- |
| 任务事件流 | `tasks.TaskEvent`（`Type/StepID/Message/Payload/OccurredAt`） | `TASK_CREATED`、`TASK_APPROVED`、`STATE_CHANGED`、`TOOL_ACTIVITY`、`TOOL_RECEIPT`、`GROUNDING_EVIDENCE`、`LOCAL_PAUSE_REQUESTED`、`RECOVERY_ACTIVITY`、`LOCAL_RECOVERY_BLOCKED`、`EXECUTION_OUTCOME_UNKNOWN`、`SAFETY_STOPPED`、`REVISION_PROPOSED/STATUS_CHANGED` 等 | 控制台任务回放、`GET /v1/tasks/{id}/experience` |
| 步骤执行历史 | `middleware.StepRun`（PENDING/STARTED/COMPLETED/FAILED） | 每条步骤的幂等键、能力、安全级别与终态 | 恢复视图 `CanResume` / `RequiresReconciliation` |
| 证据库 | `tasks.EvidenceStore` | 每次核验的采集（含失败时的原始画面） | 回放、闭环门审计 |
| 世界快照 | WorldHub revision | 去重后的观测投影，gap 要求 resync | Harness、云侧一致性 |
| 地图产物（本轮新增） | `slam-session.json` 的 `partial` / `fault` | 中断原因码与"这是部分地图" | 任何读地图的人（不必问服务） |

---

## 五、本轮修的真实缺陷

**现象**：`STALE_CAPTURE`（采集过期）、`CALIBRATION_CHANGED`（标定变化）或任何会话级异常发生时，`_spawn` 的异常处理把状态置为 `failed` 并直接释放会话——**`_build()` 从未执行，已测绘的点云与关键帧全部丢弃**。实测：一张已探明 74% 的地图因一次"对着近墙的空白视野"而完全没保存。

**修复**（`robot/gateway/tangying_robot_gateway/robot_workflow.py`）：
- 抽出 `_handle_session_fault(error)`：有故障码且已有测量时，**尽力发布已测绘部分**（`_build(fault=...)`）；发布失败或什么都没测到才退回原来的失败行为（不写空地图）。
- 地图 provenance 新增 `partial` 与 `fault{code,message}`：**这是让"部分地图"可被识别的持久记录**——读地图的人不需要问产生它的服务。故障码来自 `ServiceError.code`；非预期异常记为 `WORKFLOW_FAULT` + 异常类型。
- `_build(fault=...)` 发布后状态为 `failed`，提示"已保存并启用已测绘部分，请处理后继续扫描以补全未知区域"——**部分成果可用且不冒充完整**。

**测试**：5 条（过期保留地图、标定变化保留地图、非预期故障带类型、无测量不发布空地图、provenance 记录 `partial/fault`）。

---

## 六、仍未覆盖 / 下一步

1. **`PLACEMENT_NOT_OBSERVED` 没有测试**：本轮实跑中第一次触发的真实阻塞（放置动作完成但三帧核验没观测到）。它决定"能不能把活干完"，应优先补：确认它是**可恢复失败**还是**结果未知**，并补对应测试。
2. **`RECOVERABLE_FAILURE → SAFE_RECOVERY` 的真实回位轨迹未配置**（`recover_to_safe_pose` 标为不可用），所以"安全恢复"目前只能人工介入；这是实机交付清单上的项，不是仿真能替代的。
3. **云侧缺一项**：协调器在**交接中途**崩溃（已提交但未派发）的组合场景只有 outbox 测试间接覆盖，值得单独造一个"提交后立刻杀协调器"的用例。
4. **没有"故障注入"的常驻手段**：目前每类故障靠专门测试脚本触发；若要做长期回归，应把 `tests/e2e` 的故障矩阵接进 CI 的定时任务（当前它们不在默认 `make test` 里）。
