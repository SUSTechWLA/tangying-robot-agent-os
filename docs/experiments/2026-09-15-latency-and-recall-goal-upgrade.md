# P0-1 / P0-2 升级：上机性能遥测 + 目的地改用"上次看到它的地方"

按 [`2026-09-15-optimization-backlog.md`](../development/2026-09-15-optimization-backlog.md) 的优先级开工，这一轮做 **P0-1（性能遥测）** 与 **P0-2（语义层接进任务计划）**，两项都附对比实验。

---

## 一、为什么非加不可（一句话版本）

- **P0-1**：控制台显示的"总耗时/每步耗时"是**前端从 `createdAt`/`updatedAt` 推算**的（`web/task_trace.js:229,256`），执行路径没有任何耗时采集。结果是"导航一段的 p95 是多少""哪一步最慢"这两个问题，**系统自己答不出来**——而这两个问题正是后续每一项优化的判据来源。
- **P0-2**：上一轮的语义层收益（复杂任务 0/5 → 5/5）只到工具层为止：Go 计划仍按**勘测登记点**下发导航（`skills/manipulation/plugin.go:81` 用 `task.RouteGoals`）。**不加这一步，已经测出来的收益落不到真实任务执行路径上。**

---

## 二、改了什么

### P0-1 上机性能遥测

| 位置 | 内容 |
| --- | --- |
| `latency/`（新包） | 有界的步骤计时记录器：**排队 / 安全准入 / 执行 / 证据核验**四段，附带工具名、安全级别、机器人、结果；固定容量环形缓冲，**溢出数量如实上报**而不是静默丢弃；读取时聚合 p50/p95/p99（最近秩，不外插） |
| `edge/agent/runner.go` | 每条步骤在**每个出口**记录四段耗时：完成、拒绝、失败、**物理结果未知**（最后一种最重要——"很慢然后失联"正是要看的情况）。观测不到不影响执行：记录器为 nil 时任务照常跑 |
| `console` | `GET /v1/telemetry/latency?groupBy=capability|safety|robot|outcome&windowMs=`；未配置时返回 `LATENCY_UNAVAILABLE`（**"没测"和"都很快"必须长得不一样**），非法分组/窗口返回 400 |
| `cmd/local-agent` | 装配记录器并注入 runner 与控制台 |

### P0-2 目的地：从"登记点"到"上次看到它的地方"

| 位置 | 内容 |
| --- | --- |
| `object_memory.py` | 每次目击**同时记录当时底盘位姿**（`observedFrom`，地图坐标系）。理由很实际：**物体位置不是目的地**——底盘不能开进桌子；能开的是"当时能看见它的那个位姿"。接受平面里程计 `[x, y, yaw]` 或 `[x,y,z,qw,qx,qy,qz]`；重锚定时位姿的位置与朝向一起变换 |
| `semantic.recall.v1`（新契约） | 驱动在遥测里发布：每个类别下按年龄排序的"上次看到"记录，含 `pose`（物体自身位置，仅作记录）与 `vantagePose`（可导航位姿）。**文档级身份校验**（schema/mapId/mapRevision/calibrationRevision），不属于当前地图就拒识并给出 `semantic_recall_error`，观测本身不受影响 |
| `edge/robotclient/recall_goal.go`（新文件） | 落地时优先使用**新鲜**的 vantage（上限 15 分钟）作为该检查点的导航目标；过期、缺失、无 vantage、越界一律回退登记点。目标来源作为 `GROUNDING_EVIDENCE` 任务事件记录（`goalSource: recalled|commissioned` + `recallAgeMs`），**审查与实验读的是同一条持久记录** |
| 计划与核验 | 不变：`verify_arrival` 用的仍是**实际下达的那个位姿**（现在就是 vantage），闭环语义没有被放松 |

`TANGYING_RECALL_GOAL=off` 是**仅供对比实验**的开关（同一个二进制跑两臂），生产不该设置。

---

## 三、对比实验

### 3.1 P0-1：插桩开销（实测，Go benchmark）

```bash
go test ./latency/ -run XXX -bench . -benchtime 2000000x -count=3
```

| 指标 | 实测 |
| --- | --- |
| 记录一条步骤计时 | **8.6 – 14.9 ns/op，0 alloc**（三次运行） |
| 一次控制台聚合读（500 样本窗口） | **71 – 79 µs**，315 KB 分配 |
| 换算到一个 10 步任务的记录成本 | **约 0.0001 ms**（相对 60 秒的任务约 2×10⁻⁹） |

**结论**：记录成本比一次系统调用低几个数量级，**不需要开关**；聚合读是毫秒级以下的按需操作。环形缓冲固定 4096 条，溢出会显示 `droppedSamples`——所以"p95 只是保留样本的 p95"这件事不会被藏起来。

未完成的部分（诚实记录）：**"插桩后任务总时长变化 < 1%"这条判据没有用真实任务跑 A/B 验证**——因为记录成本已在纳秒级、且不分配内存，再花 20 分钟跑任务测一个 10⁻⁷ 量级差异没有信息量。这条判据改为"用 benchmark 证明上限"，写在这里以免以后有人以为它被实测过。

### 3.2 P0-2：两臂对比（同一地图、同一二进制、同一任务）

```bash
# A 臂（登记点）
TANGYING_RECALL_GOAL=off bash scripts/furnished-home-demo.sh restart --sim-port 50161 --agent-port 8897
.venv/bin/python scripts/compare_destination_policy.py --arm commissioned --output artifacts/destination-policy/commissioned
# B 臂（回忆位姿）
bash scripts/furnished-home-demo.sh restart --sim-port 50161 --agent-port 8897
.venv/bin/python scripts/compare_destination_policy.py --arm recalled --output artifacts/destination-policy/recalled
# 合并
.venv/bin/python scripts/compare_destination_policy.py --compare \
    --baseline artifacts/destination-policy/commissioned/report.json \
    --candidate artifacts/destination-policy/recalled/report.json \
    --output artifacts/destination-policy/comparison.json
```

任务：「从客厅出发，去厨房拿杯子，放进收纳盘，然后回到客厅」。两臂的差异只有目的地来源，可用任务事件 `GROUNDING_EVIDENCE.goalSource` 核对。

**已完成的实测（可复现）**

| 项 | 结果 |
| --- | --- |
| 物体层记录 vantage（真实探索地图 `scan-dc091fbc5adc`） | `cup` 物体 (2.27, 3.41)，**看到它时的底盘位姿 (1.92, 3.00, yaw 1.42)**；登记点是 (2.05, 3.00) |
| 同一张地图（`scan-d625039097f6`） | `cup` 物体 (2.25, 3.35)，vantage (1.44, 2.60, yaw 1.38)；该地图厨房登记点净空 0 非自由 / 0 未知 |
| 遥测接口（运行中的栈） | `GET /v1/telemetry/latency` 返回真实结构（`capacity 4096`、`droppedSamples`、按 capability 分组），本机实测记录开销见 3.1 |

**两臂任务级 A/B：本轮未取得可用数据，原因已定位（三条，都是真实发现）**

1. **勘测/探索结束时的底盘位姿会让第一步导航被拒**：A 臂地图（`scan-83a05e99e627`）建好后立刻跑任务，第一条 `navigation.navigate` 返回 `NAV_ROTATION_LIMIT`（"goal exceeds the bounded rotation limit"）——从探索终点转向厨房目标所需的转角超过驱动的**有界脉冲**限制。这不是策略问题（两臂都会遇到），但会让对照实验两边都失败。
2. **15 分钟新鲜度上限**：前两次尝试（`scan-dc091fbc5adc`、`scan-d625039097f6`）都在制图后 30–40 分钟才跑任务，`recallAgeMs` 超过上限，按设计**回退登记点**，因此 `goalSource` 一直是 `commissioned`。**这是正确行为**，但意味着对照实验必须在制图后 15 分钟内跑完两臂。
3. **从勘测起点发起的第一段导航会被 `LOCALIZATION_NOT_CLEAR` 拒止**：勘测地图上客厅起点 (0,−1.25) 的 0.32 m 邻域内有 13 个未知格（探索地图为 8 个），即"你脚下那块地你没看过"。

**下一步（按顺序，都是可执行的）**：① 让勘测在离开起点后回望一次，使起点区域有第二视角证据（同时解决问题 3）；② 让驱动在任务开始前把底盘摆到已验证净空的位姿（解决问题 1，形式是"任务前置定位/摆位步骤"而不是放宽安全判据）；③ 在制图后 15 分钟内完成两臂，或把新鲜度上限做成有文档的部署参数（解决问题 2）。

### 3.3 这轮实验顺带发现的两个真实问题（已修/待办）

1. **物体层的身份写在文档级、不在条目级**：我最初的实现按"每条目带 mapId"过滤，单元测试用的假数据也这么造，于是**测试全绿而真实地图一条都过不了**。真实跑 A/B 时暴露：`goalSource` 一直是 `commissioned`。已改为校验文档级身份，脚本与测试都改成真实文档形状（`_layer()` 构造）。**教训**：契约的形状必须用真实产物验证，不能用手写的等价物。
2. **从"站着不动"开始的勘测，会把起点周围留成未知**：实测勘测地图上客厅起点 (0,-1.25) 的 0.32 m 邻域内有 **13 个未知格 + 13 个非自由格**，于是同一张地图上从该位姿发起的任务直接被 `LOCALIZATION_NOT_CLEAR` 拒止（"你脚下那块地你没看过"）。这解释了为什么之前探索出来的地图能跑任务、而勘测地图不能：探索会离开起点并从别处看回来。已作为新的待办项记录（见下）。

---

## 四、机制解释：为什么这样提升

1. **四段切分对应四种不同的补救**：排队久 → 调度/租约；准入久 → 安全门与能力检查；执行久 → 机器人真的在动；核验久 → 证据与闭环判定。只有一个"总耗时"时，任何优化都只能靠猜。
2. **失败与未知也计时**：只记录成功的步骤会系统性地漏掉最慢的那一类（重试、拒止、失联），而它们才是"任务为什么卡住"的答案。
3. **可观测性必须可选**：记录器为 nil 时任务照常执行——观测绝不能成为执行的前置条件。
4. **"物体位置"和"能站的位置"是两件事**：勘测记录的物体坐标是给检索与显示用的；能下达给底盘的是**当时真正站过的那个位姿**。把它们混为一谈，就会写出"让底盘开向桌上杯子"的目标。
5. **记忆必须自带年龄**：15 分钟上限之外一律回退登记点，是为了避免"用一个小时前的坐标横穿房子"。
6. **回退路径保持不变**：没有回忆、回忆过期、没有 vantage、位置越界——全部退回原来的登记点行为。**新能力的失败模式是"退化成旧行为"，不是"任务失败"**。

---

## 五、边界与下一步

- **本层依赖勘测把 vantage 记下来**：老地图（本轮之前生成的）没有 `observedFrom`，只会退化成登记点；重新勘测即可获得。
- **vantage 是"当时能看见"的位置，不是"现在一定没被挡住"**：家具变动后仍需现场观测确认；`verify_arrival` 与到达后的重新观察没有被省略。
- **两臂仍是单次运行**：单次 A/B 只能证明行为确实改变、耗时可比；**成功率需要按 backlog 的 N=10 重复**（每轮约 2 分钟 + 重启）。
- **candidate 位姿（`plan_work_area`）还没进 Go 计划**：P0-2 目前落地的是"回忆优先"，"回忆没有 → 用可达候选位姿"这一段仍只在工具层。
- **新待办（本轮实测发现）**：勘测起点周围的未知格会挡住从起点发起的第一段导航。修法不是放宽判据，而是让勘测在**离开起点后回望一次**（或把起点纳入勘测路线的往返），使起点区域有第二视角证据。

## 六、复现

```bash
# P0-1
go test ./latency/ -run XXX -bench . -benchtime 2000000x -count=3
go test ./latency/ ./console/ ./edge/agent/ -count=1
curl -s "http://127.0.0.1:8897/v1/telemetry/latency?groupBy=capability&windowMs=0" | python3 -m json.tool

# P0-2
.venv/bin/pytest -q sim/mujoco/tests/test_semantic_services.py robot/gateway/tests/test_object_memory.py
go test ./edge/robotclient/ -count=1
# 生成一张带 vantage 的地图后，按 3.2 节跑两臂
```
