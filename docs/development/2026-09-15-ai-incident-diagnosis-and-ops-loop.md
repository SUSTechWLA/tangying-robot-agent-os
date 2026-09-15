# AI 回溯诊断与自动运维闭环：现状能力、缺口与设计

**问题**：机器人/系统出故障时，能不能让 AI 回溯任务、给出根因与解决办法？能不能让 coding agent 自动复盘并做系统级 loop 升级，为后续 AI 自动运维铺路？

**一句话回答**：**证据够，但今天不是"一条故障=一条可诊断记录"**。系统已经记录了几乎所有事实，却散在五个接口里、且没有"可能的根因 + 该跑哪些测试"的知识层。本轮把这条链补齐到"AI 能直接吃"的程度：新增 `scripts/diagnose_task.py` 产出 `incident.v1` 记录 + 确定性分类，并用**真实失败任务**验证过。

---

## 一、今天能回答什么（逐项实测）

以一次真实失败 `task-35ec47d5652d8bd669b82d61` 为例，跑

```bash
.venv/bin/python scripts/diagnose_task.py --task <id> --output artifacts/incidents
```

得到（节选，真实输出）：

```
任务 task-35ec47d5652d8bd669b82d61 · RECOVERABLE_FAILURE · 修订 1
观察到的错误码：DESTINATION_NOT_FOUND, PLACEMENT_NOT_OBSERVED
故障族：verification_not_observed（命中 PLACEMENT_NOT_OBSERVED）
含义：动作完成，但核验没有连续观察到预期关系（3 帧稳定样本）。
可能根因（按表内顺序）：1. 核验视角看不到目标… 2. 物体仍在运动/回弹… 3. 关系判据不满足… 4. 检测器漏检
先查这些：读 verification 块 / 看核验帧 RGB+深度 / 确认实际相对位置
已有回归测试：test_a_failed_placement_verification_reports_what_it_saw …
恢复判定：canResume=True requiresReconciliation=False (RESUME_AVAILABLE)
证据采集 16 份；最慢能力：observe_scene 执行 p50=0.475 ms max=0.475 ms
```

| 诊断问题 | 今天 | 证据来源 |
| --- | --- | --- |
| 哪一步失败、什么码 | ✅ | 任务事件流（`TOOL_ACTIVITY.error`）+ 步骤执行历史 |
| 当时机器人看到什么 | ✅ | 证据库（RGB/深度 + 哈希 + 采集号）`GET /v1/tasks/{id}/observations` |
| 能不能续跑、要不要对账 | ✅ | `GET /v1/tasks/{id}/recovery`（`canResume`/`requiresReconciliation`/`reasonCode`） |
| 每步花了多久、哪步最慢 | ✅（本轮新增的 P0-1） | `GET /v1/telemetry/latency`（排队/准入/执行/核验四段） |
| 地图是不是被中断的部分地图 | ✅（本轮新增） | 地图 `slam_session` 的 `partial`/`fault` |
| 地图与现场是否已冲突 | ✅（本轮新增） | `mapping.conflicts`（只读比对） |
| 核验为什么失败 | ✅（本轮新增） | 步骤回执的 `verification` 块（样本数/实际关系/位移/稳定时长） |
| 抓取为什么这么轻 | ✅（本轮新增） | 状态里的 `grasp_budget`（材质/力上限/来源） |
| **有哪些可能的根因、先查什么** | ⚠️ **本轮补上**：确定性故障族表（7 族） | `diagnose_task.FAMILIES` |
| **这次故障该跑哪些回归测试** | ⚠️ **本轮补上**：每个故障族指向真实测试（有测试断言这些路径存在） | 同上 |
| **这次失败对应哪个版本/标定/地图** | ⚠️ 部分 | `/v1/runtime` 指纹 + 地图 provenance（本轮进 incident 记录） |

**仍然不能回答（诚实清单）**：

1. **世界历史不持久**（本地大脑的 WorldHub 在内存里，512 条环形）：任务之后无法回放"当时系统认为世界是什么样"，只能看证据采集与任务事件。
2. **耗时统计不持久**（P0-1 的记录器在进程内）：重启即丢，跨重启的"变慢了吗"回答不了。
3. **没有因果链，只有时间序**：能列出"谁先谁后"，但"这个失败是不是由上一段导航的偏差引起的"需要人（或模型）读几何证据判断。
4. **没有变更指纹的强绑定**：任务的 revision 与地图/标定对得上，但没有"这次运行使用的工具目录/策略清单哈希"的完整快照（`CatalogRevision` 有，策略 manifest 只在用时才记录）。
5. **故障族表是人工维护的**：新故障码默认落在 `unclassified`（这是刻意的——不猜），但需要有人把它补进表里并配回归测试。

---

## 二、`incident.v1`：一次故障一条记录

`scripts/diagnose_task.py` 产出的记录结构（可离线跑 fixture，也可连真实控制台）：

```json
{
  "schemaVersion": "incident.v1",
  "task":       {"id","request","adapter","revision","state","createdAt","updatedAt"},
  "recovery":   {"canResume","requiresReconciliation","reasonCode","completedStepIds","uncertainStepIds"},
  "environment":{...运行时指纹、地图与标定修订...},
  "timeline":   [{sequence,type,occurredAt,stepId,toolName,status,error,message}...],
  "stepTimings":[{capability,count,queueMs,executeMs,executeMaxMs,verifyMs,outcomes,slowestStepId}...],
  "evidence":   [{stepId,id,captureId,observedAtUnixMs,sourceId,rgbSha256,depthSha256}...],
  "observedCodes":["PLACEMENT_NOT_OBSERVED"],
  "diagnosis":  {family,matchedCodes,unmatchedCodes,summary,probableCauses,checks,
                 proposedResolution,coveringTests,needsHuman,missingEvidence?},
  "nextActions":[...],
  "automation":{"acted":false,"reason":"本工具只产出记录与建议，不修改代码、不动机器人。"}
}
```

三条设计原则：

1. **事实与推测分开**：`timeline`/`evidence`/`stepTimings`/`recovery` 是读出来的事实；`diagnosis` 明确标注是分类器的推断，并带 `matchedCodes`。
2. **不知道就说不知道**：未归类的码进 `unclassified`，`probableCauses` 为空，并给出 `missingEvidence`（"要判定还缺什么"）。
3. **只提议，不行动**：`automation.acted` 恒为 `false`；是否重放、是否改代码由人/受审的 agent 决定。

**故障族（本轮 7 族，每族配真实回归测试）**：`physical_outcome_unknown`、`verification_not_observed`、`goal_or_localization_unclear`、`mapping_session_fault`、`safety_stop`、`grounding_failure`、`fleet_consistency`。有一类测试专门断言"每个族引用的测试文件与测试名都真实存在"——**指向不存在的测试比不指更糟**。

---

## 三、coding agent 自动复盘：可执行的闭环

现在这条链已经能跑通"机器可读的事故 → 受审的修复"：

```
① 采集     diagnose_task.py --task <id>  →  incident.v1（含证据 id、耗时、指纹、故障族、覆盖测试）
② 定位     按 diagnosis.checks 逐项取证：证据采集的 RGB/深度、verification 块、mapping.conflicts
③ 复现     用 incident 里的 tests 跑一遍（全部为真实可运行的用例），失败即拿到最小复现
④ 修复     在对应模块改代码
⑤ 加固     为该故障族补一条回归测试（并把它加进 FAMILIES 的 tests 列表）
⑥ 验证     跑故障矩阵：tests/e2e/test_fleet_faults.py、test_robocasa_faults.py、test_rgbd_recovery.py
⑦ 归档     incident 记录留在 artifacts/incidents/，作为"这次为什么改"的证据
```

**已经具备的、让闭环安全的前提**（不是本轮新加，而是这一路积累的）：物理结果未知**永不重放**；安全停止**不能由任务动作解除**；观测不到不影响执行（可观测性可选）；故障时**保留已测绘部分**而不是丢掉；计划与执行对"实际下达的位姿"负责（核验对象=下达对象）。

**必须有的护栏**（写进文档，也写进工具的默认行为）：

| 护栏 | 现状 |
| --- | --- |
| 自动复盘**不得**动机器人 | `diagnose_task.py` 只读接口；无任何写操作 |
| 自动修复**不得**自动合入/发布 | 仓库仍是人工审阅 + CI；本轮未引入自动提交 |
| 任何物理动作需人工批准 | 既有 `ApprovalPolicy` 与审批事件 |
| 修复必须带回归测试 | 故障族表里 `tests` 非空校验 + 评审要求 |
| 新故障码不得被"猜" | `unclassified` + `missingEvidence` |

---

## 四、为 AI 自动运维铺路的下一步（按优先级）

1. **持久化 incident 与耗时**：把 `latency` 记录器与 incident 记录落盘（SQLite/JSONL），使"跨重启的回归"可判定。当前耗时统计随进程消失。
2. **世界历史落盘（本地）**：本地 WorldHub 现在是内存环形（512）。任务级诊断最缺的就是"当时系统认为的世界"。最小形式：只落**每个任务步骤结束时的世界快照**，而不是全量流。
3. **变更指纹进 incident**：把工具目录哈希、策略 manifest 修订、驱动版本一起写进 `environment`，让"同一个错误码在不同版本下的不同含义"可区分。
4. **故障族与测试自动对账**：CI 里断言"每个故障族的 tests 至少有一个与该码相关的新用例"；新代码引入新错误码时提示补族。
5. **闭环的入口自动化**：把 `diagnose_task.py` 接进任务终态钩子，异常终态自动产出 incident（现在是手动/按需调用）。

**明确不做**：不让 agent 自动改写在用地图、不让 agent 自动复位急停、不让 agent 把仿真结论当实机结论、不让"自动复盘"直接产生合入的代码改动。
