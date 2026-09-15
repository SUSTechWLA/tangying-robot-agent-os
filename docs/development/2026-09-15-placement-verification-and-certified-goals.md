# `PLACEMENT_NOT_OBSERVED` 的定位、验证与两处修复

上一轮审计把这条列为最高优先级的未覆盖项：任务第一次跑到放置核验就失败，而它直接决定"能不能把活干完"。本轮做完了定位与两处修复，并**实测到抓取链第一次全绿**。

---

## 一、先回答"这个失败算什么"（实测，不是推断）

失败任务的恢复视图（`GET /v1/tasks/{id}/recovery`）：

```json
{"state": "RECOVERABLE_FAILURE", "canResume": true, "requiresReconciliation": false,
 "reasonCode": "RESUME_AVAILABLE",
 "completedStepIds": ["observe","pre_position","navigate_01","verify_arrival_01",
                      "observe_after_navigation","resolve","plan_grasp","pick","verify_grasp","place"]}
```

**语义是正确的**：放置动作（`place`）已完成并被闭环门确认，失败的是**只读的核验步骤**，因此：
- 不需要对账（`requiresReconciliation: false`）——没有"结果未知的物理动作"；
- 允许继续（`canResume: true`），继续时会**重新核验**而不是重放放置。

**实测续跑**（`POST /v1/tasks/{id}/resume`）证实了这一点：事件序列从 `verify_arrival` 直接跳到 `observe_scene → resolve_targets`，**pick/place 一次都没有重放**。续跑随后在 `resolve_targets` 上以 `DESTINATION_NOT_FOUND` 失败——这是世界状态已改变（杯子已在盘中）导致的另一次真实失败，不影响"恢复语义正确"的结论。

## 二、根因：放好了，但没被"看见"

从世界快照与运行时几何对齐后可以看出：

| 实体 | 位置 | 说明 |
| --- | --- | --- |
| `ceramic-mug` | (2.464, 3.342, **0.79**) | 杯子（半高 0.06 → 底部 0.73） |
| `kitchen-tray` | (2.459, 3.334, **0.73**) | 收纳盘中心高度 |

杯子**物理上就在盘里**（水平偏差 5–8 mm，底部与盘面同高）。判据本身也满足（`inside:kitchen-tray` 要求水平在盘内、底部与盘中心高差 < 3.5 cm）。也就是说：**失败发生在"核验时没能连续取到 3 个稳定样本"，而不是"没放好"**。

而当时的失败**没有留下任何可判读的原因**——任务事件只有一行 `PLACEMENT_NOT_OBSERVED`，要回答"到底放好没有"得同时翻世界快照、证据库和仿真。**这就是本轮第一个修复的动机。**

## 三、两处修复

### 修复 1：失败的核验必须自证（`sim/mujoco/tangying_sim/rgbd_runtime.py`）

- `_verify_relation` 现在把判定记录为 `self._verification_record`（`passed`、`sample_count`、`expected_relation`、`observed_relation`、`stable_duration_s`、`max_displacement_m`），并随 `ToolResult.payload["verification"]` 返回；
- 失败消息变成可判读的一句话，例如：
  `observed 1/3 stable samples of 'inside:kitchen-tray'; last relation 'none', max displacement 0.0000 m, stable for 0.000 s`；
- 同一份记录也进入**发布状态**（`robot_state.verification`）与遥测（`verification_confidence` 旁边），所以控制台/证据读者看到的是真实值，而不是一个"只有失败码"的黑洞。

**测试**：`sim/mujoco/tests/test_rgbd_runtime.py::test_a_failed_placement_verification_reports_what_it_saw`（断言失败消息里带期望关系、样本数与"观察到的关系"；断言判定记录字段齐全）。

### 修复 2：房间目标必须是被地图认证过的位姿（同文件 `_certify_semantic_goals`）

**新发现的同族问题**：本轮实测中，任务在抓取链全部通过后，**返回客厅的最后一段导航以 `GOAL_NOT_CLEAR` 失败**——登记的客厅点 (0, −1.25) 正落在"勘测起点脚下那块未被认证的地面"上（上一轮实测：该处 0.32 m 邻域仍有 6 个未知格）。

修复方式与前置摆位同源、且不放松任何判据：**运行时发布房间目标时，把落不到认证净空地面的目标吸附到同房间内最近的认证净空位姿**，朝向保持不变，并把调整量一并发布（`semantic_navigation.goalAdjustments`：`adjusted`、`offsetM`、`commissioned`）。于是：
- 计划下发的位姿、`verify_arrival` 核验的位姿、读者看到的位姿**是同一个**（没有"偷偷改目标"）；
- 附近找不到认证位姿时**如实报 `adjusted: false`**，而不是把目标悄悄挪到一个地图从未批准的地方。

**测试**：`test_room_goals_are_published_only_where_the_map_certifies_them`（未认证目标被吸附且朝向不变、已认证目标原样保留、无认证位姿时明确标记不可用）。

## 四、实测进展（含一次无效运行，如实记录）

| 运行 | 结果 |
| --- | --- |
| 修复前（上一轮） | `verify_placement` **FAILED `PLACEMENT_NOT_OBSERVED`**，原因不可判读 |
| 修复 1 之后 | 抓取链**首次全绿**：`pick` → `verify_grasp` → `place` → **`verify_placement` CONFIRMED**；随后在返回段的 `GOAL_NOT_CLEAR` 失败（催生修复 2） |
| 修复 2 之后的确认运行 | **无效**：仿真栈重启尚未就绪，建图脚本 `Connection refused`，任务跑在旧地图上（`commandedGoals: []`）。基础设施竞态，不作为结论 |

**因此仍未完成的是**：修复 2 生效后"整条任务（含返回客厅）在真实栈上一次跑完"的确认。命令行是现成的：

```bash
bash scripts/furnished-home-demo.sh restart --sim-port 50161 --agent-port 8897
sleep 25
.venv/bin/python scripts/build_sim_map.py --base-url http://127.0.0.1:8897 \
    --output /tmp/verify --name "验证地图" --mode explore --max-travel-m 24 --max-legs 2 --timeout 900
.venv/bin/python scripts/compare_destination_policy.py --arm recalled --output /tmp/verify-report
# 期望：任务事件里 pick/verify_grasp/place/verify_placement/navigate(返回)/verify_arrival 全 CONFIRMED
```

## 五、结论

- **`PLACEMENT_NOT_OBSERVED` 属于"可恢复失败"且恢复语义正确**：不重放已完成的物理动作，续跑时重新核验。这一点由实测恢复视图与续跑事件序列证实。
- **它当时的根因是"核验没取到稳定样本"，而系统没有把这个原因记录下来**——已修复：失败现在自证（样本数、实际关系、位移、稳定时长）。
- **同族的 `GOAL_NOT_CLEAR` 也已处理**：房间目标由地图认证，调整量随目标一起发布，核验对象与下达对象始终一致。
- **仍未做**：修复 2 的端到端确认（需一次干净的栈启动）；以及"为什么核验偶尔取不到 3 个稳定样本"的进一步定位——现在有了自证信息，下次失败可以直接判读，不必再做跨层取证。
