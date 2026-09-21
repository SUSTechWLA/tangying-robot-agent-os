# 三项收口：勘测回望、任务前置摆位、新鲜度成为部署参数

上一轮留下三个具体问题（详见 [P0-1/P0-2 升级文档](2026-09-15-latency-and-recall-goal-upgrade.md) 第三节）。这一轮逐项解决，每项都带对照实验。

---

## ① 勘测离开起点后回望一次

**问题**：勘测/探索起点脚下那块地，是**前向相机永远看不到的地方**（站着不动转圈也没用——相机在头部朝前）。于是地图建成后，从该位姿发起的第一段导航被 `LOCALIZATION_NOT_CLEAR` 拒止。实测：勘测地图上客厅起点 (0, −1.25) 的 0.32 m 邻域内有 **13 个未知格**。

**改动**（`sim/mujoco/tangying_sim/workflow_services.py`）：勘测路线在走完客厅后**多一步回望**——保持朝向不变、向后退 0.6 m。保持朝向是必须的：驱动每次命令只允许 **0.5 rad** 的朝向变化（`max_rotation_rad = 0.5`），转身回望根本发不出去。

**对照实验**（同一场景，勘测模式，其余参数一致）：

| 指标 | 无回望 | 有回望 |
| --- | --- | --- |
| 起点 0.32 m 邻域未知格 | **13** | **6** |
| 该邻域的占用格 | 13 | 6 |
| 勘测里程 | 27.6 m | 27.6 m |
| 关键帧 | 218 | 161 |

**为什么只降到 6 而不是 0**：剩下的是**底盘自身脚下**的那一小块——任何单一视角都看不到它（相机在车上，车就站在那块地上）。客厅只有约 0.9 m 的后退余量（南墙在 y = −2.5），退到 1.2 m 就会让包络压到墙，所以不能靠"退更远"解决。

**因此这块交给 ②**：机器人站在未认证地面上这件事，本来就该由任务开始前的一步来纠正，而不是靠放宽净空判据。

---

## ② 任务前置"摆到已验证净空位姿"的步骤

**问题**（实测原话）：一次家居任务的第一段导航直接返回
`NAV_ROTATION_LIMIT: goal exceeds the bounded rotation limit`。
排查后确认：**不是当前位置不干净，而是目标朝向**——驱动要求一条导航命令的终点朝向与当前朝向之差不超过 0.5 rad，而探索结束时底盘朝向是随机的，厨房目标朝向却是固定的 1.421 rad。

**改动**（跨三层，都是"加一步能力"，不动框架）：

| 层 | 内容 |
| --- | --- |
| 运行时（`rgbd_runtime._pre_position`） | 新能力 `navigation.pre_position`：① 检查当前足印是否净空，不是就在 1.2 m 内**按环搜索**最近的认证净空位姿并开过去；② 若调用方给了 `alignYaw`，**原地分步转**（每步 ≤ 0.8×驱动上限，最多 8 步）朝向下一个目标；③ 全程走普通有界脉冲导航，安全准入/新鲜度/扫掠净空一个都没放松 |
| 契约 | `navigation.pre_position` 进 `CANONICAL_TOOLS`/`PHYSICAL_TOOLS`（Python 与 Go 两侧的规范工具表都要加，否则 profile 校验会拒）；参数只有可选的 `alignYaw`——**没有位置参数**：运行时选地方，调用方只能要求"朝哪边" |
| 计划（`skills/manipulation/plugin.go`） | 家居路线与移动操作计划在 `observe` 之后、第一段 `navigate` 之前插入该步，并把首个目标的朝向作为 `alignYaw` 传下去；`verify_arrival` 仍核验实际下达的位姿 |

**对照实验**（同一张新地图、同一个任务「从客厅出发，去厨房拿杯子，放进收纳盘，然后回到客厅」，唯一差别是计划里有没有这一步）：

| 步骤序列 | 无 pre_position（上几轮实测） | 有 pre_position（本轮实测） |
| --- | --- | --- |
| observe_scene | CONFIRMED | CONFIRMED |
| navigation.pre_position | **不存在** | **CONFIRMED**（`alignYaw=1.421`） |
| 第一段 navigation.navigate | **FAILED `NAV_ROTATION_LIMIT`** | **CONFIRMED** |
| verify_arrival | — | CONFIRMED |
| 后续 pick / place | — | pick CONFIRMED、place CONFIRMED |

**结论**：`NAV_ROTATION_LIMIT` 这一类"起步即失败"被消除，且**没有放宽任何安全判据**——净空判据仍是"零个未知/占用格"，只是多了一步把底盘挪到满足它的地方。

**顺带暴露的下一个真实问题**：这一步之后任务跑到了 `verify_placement`，返回
`PLACEMENT_NOT_OBSERVED`（放置未被观测到）。与本次改动无关（前提是前几轮从未跑到这一步），已作为下一条待办记录。

---

## ③ 15 分钟新鲜度：变成有文档的部署参数

**问题**：`RecallGoalMaxAgeMS` 原本是写死的 15 分钟常量，导致两臂对照实验必须在制图后 15 分钟内跑完，否则回忆按设计回退登记点、实验测不到差异。

**改动**：
- `TANGYING_RECALL_GOAL_MAX_AGE_MS` 覆盖默认值（毫秒，上限 7 天）；**非法值一律回退到 15 分钟**——"没设"和"打错字"不能都表示"什么都信"。
- 文档位置：本文件（机制与口径）、`docs/experiments/2026-09-15-latency-and-recall-goal-upgrade.md`（上一轮的边界与参数）、`compare_destination_policy.py` 的报告里逐次记录 `recallWindowEnv`，**每次运行都要说明它被允许多旧**，否则两臂不可比。

**对照实验**（单元级，同一个回忆条目、两个窗口）：

| 窗口 | 目击年龄 15 min + 1 min 的条目 | 结果 |
| --- | --- | --- |
| 默认 15 分钟 | 超出 1 分钟 | **拒绝**，回退登记点（`pose == nil`） |
| 24 小时 | 超出 1 分钟 | **接受**，返回 vantage 且 `recallAgeMs` 如实上报 |

> 说明：本轮的两臂任务级对照**仍未取得差异数据**——这次的地图里物体层有 9 个 `cup` 实例（含真实杯子 (2.24, 3.39)、vantage (2.24, 2.77)），但任务事件里的 `goalSource` 仍是 `commissioned`。也就是说**运行时没有把 `semantic_recall` 送到落地环节**，这是一个可复现的具体断点（不是"没有数据"），下一步直接查运行时状态里这个字段是否存在即可定位。这一点如实记录，不当作已完成。

---

## 复现

```bash
# ① 回望：勘测路线多一步，比较起点邻域未知格
.venv/bin/python scripts/build_sim_map.py --base-url http://127.0.0.1:8897 \
    --output /tmp/survey --name "回望勘测" --mode survey --timeout 1800

# ② 前置摆位：任务事件里应出现 navigation.pre_position（带 alignYaw），
#    且第一段 navigation.navigate 不再返回 NAV_ROTATION_LIMIT
TANGYING_RECALL_GOAL_MAX_AGE_MS=86400000 bash scripts/furnished-home-demo.sh restart \
    --sim-port 50161 --agent-port 8897
.venv/bin/python scripts/compare_destination_policy.py --arm recalled \
    --output artifacts/destination-policy/recalled

# 测试
go test ./skills/... ./agent/... ./core/robotcontract/ ./edge/...
.venv/bin/pytest -q sim/mujoco/tests/test_rgbd_runtime.py sim/mujoco/tests/test_room_cameras.py \
    sim/mujoco/tests/test_furnished_home.py
```

**相关测试**：回望 2 条（`test_furnished_home.py::test_the_survey_route_looks_back_at_where_it_started`、`test_room_cameras.py` 的路线朝向约束）、前置摆位 5 条（净空时不动、不净空时挪到净空且不改朝向、分步对齐朝向且每步在预算内、无净空位姿时明确拒绝、无活动地图时拒绝）+ Go 侧 1 条（计划里该步在首段导航之前且带 `alignYaw`）、新鲜度窗口 2 条。

## 仍未解决（下一条待办，按优先级）

1. **`semantic_recall` 没到落地环节**：地图有物体层、Go 侧有偏好逻辑、窗口也已放通，但 `goalSource` 仍是 `commissioned`。下一步查运行时观测状态里该字段是否存在（一次 curl 即可定位是"没发布"还是"被拒识"）。
2. **`PLACEMENT_NOT_OBSERVED`**：② 之后任务第一次跑到放置核验，未通过。与本次改动无关，但它是"能不能把活干完"的直接阻塞，优先级高于继续做语义层。
3. **回望的通用做法**：目前只在客厅写死一次回望；正确做法是把"起点回望"作为勘测路线的通用收尾（任何起点都适用），并让余量由房间几何决定而不是常量。
