# 第 6 章与第 9 章核心素材：世界模型与任务编排

> **调研对象**：`tangying-robot-agent-os` v0.6.0（`VERSION`），工作树 HEAD `774bd2a2f`。
> **方法**：直接读 Go / Python 源码、测试与 `git log -1 --format=%B` 的提交正文；文档只当作作者意图的证据。
> 凡推断标注「(推断)」；凡文档与代码冲突，单独标注。所有行号以 HEAD 为准。
> **一句话结论**：这两章真正的教学点不是「系统怎么工作」，而是**每一条看起来多余的检查，都是某一次真实失败的化石**——
> 而每一条检查的注释里都写着那次失败长什么样。

---

# 第一部分 A：世界模型

## A1. 世界状态的构成（`core/worldmodel/`，共 5 个文件）

世界状态不是一个类，而是**一次投影的产物**。`Projector` 是一个纯函数式 reducer：吃 `observation.Envelope`，吐 `Snapshot`。

**真实的 Go 类型**（`core/worldmodel/types.go`）：

| 类型 | 位置 | 关键字段与含义 |
| --- | --- | --- |
| `Snapshot` | `types.go:84-96` | `SchemaVersion="world.snapshot.v1"`、`WorldID`、`Revision uint64`（单调递增的接受计数）、`EventCursor`（= 最新被接受的 `ObservationID`）、`ProjectedAt`、四张 map、`ActiveTasks`、`Health` |
| `RobotState` | `types.go:29-44` | `Pose []float64`、`Activity`、**`Held string`**、`EmergencyStopped`、`State map[string]float64`、**`Faults *robotcontract.FaultReport`**、`Confidence`、`Freshness`、`Evidence` |
| `EntityState` | `types.go:46-57` | `EntityID`、`Category`、`Attributes map[string]string`、`Pose`、**`Relations map[string]string`**、`Confidence`、`Freshness`、`ObservationCount`、`StableObservations`、`Evidence` |
| `ResourceState` | `types.go:59-66` | `ResourceID`、`Owner`、`FencingToken`、`ExpiresAt`、`Freshness`、`Evidence` |
| `SourceState` | `types.go:68-77` | 源的 `SourceSequence` 高水位、`TransformRevision`、`LastObservedAt` / `LastReceivedAt`、`Anomalies` |
| `EvidenceRef` | `types.go:20-27` | **每一条事实都能被指回一次观测**：`ObservationID`、`SourceID`、`SourceSequence`、`ObservedAt`、`FrameID`、`TransformRevision` |
| `Freshness` | `types.go:11-18` | `FRESH` / `STALE` / `UNKNOWN` / `DEGRADED` |
| `Delta` | `types.go:98-106` | 一次被接受的变更：新 `Revision` + `Previous` + 触发它的 `ObservationID` + 完整快照 |

**身份（identity）怎么表示——四张 map 的 key 就是身份，且每个值内部再存一份同名 ID。** `EntityState.EntityID`、`RobotState.RobotID`、`ResourceState.ResourceID`、`SourceState.SourceID` 都被 `validateCheckpoint` 要求与 map key 相等（`checkpoint.go:137,142,147`）。这是刻意的冗余：一个被序列化过、又被人改过的 checkpoint 必须自证身份。

**房间/位置怎么表示——世界模型里没有"房间"这个类型。** 这是一个值得写进书里的设计事实：
- 实体只有 `Pose []float64`（地图系米）和 `Relations["inside"]=<zoneID>`（`predicate.go:51`）；
- "房间"是**导航层给一个已委托位姿起的名字**。只有编排层把它读成字符串：`orchestration.World.RobotRoom`（`orchestration/world.go:35`），来源是 `roomOf()` 把 `base_pose` 与 `semantic_navigation.goals` 里的登记点逐个比距离取最近（`world.go:172-215`），超过 `roomMatchRadiusM = 4.0`（`world.go:219`）就**拒绝认领**，返回空串当"未知"。
- 物体的房间来自 `semantic_objects[].workArea`（`world.go:222-248`），按 `lower(category)@room` 去重。

**"谁抓着什么"怎么表示**：`RobotState.Held` 放**实体的 ID**（不是布尔）。判据是 `RobotHeld(robotID, entityID, maxAge)`（`predicate.go:74-88`），失败码 `HELD_MISMATCH`（`:21`）。放置判据是对称的：`EntityInside(entityID, zoneID, maxAge)` 查 `Relations["inside"]`（`predicate.go:42-56`）。

**三值逻辑是这个包的灵魂**（`predicate.go:5-11`）：`TRUE` / `FALSE` / **`UNKNOWN`**，并且每条结果必须带 `Reason`。`UNKNOWN` 的触发条件写死在 `evidenceStale()`（`predicate.go:138-140`）：观测时刻为零、`maxAge<=0`、或已超龄——**"证据不在"和"事实不成立"是两种答案，永远不会被混成一个 `false`**。`All()`（`:119-136`）的合并规则也非对称：任一 `FALSE` 立刻短路返回 `FALSE`；只有全部非假时才把状态降级为 `UNKNOWN`。

**投影器的六道闸**（`projector.go:66-95`，`Apply`）：schema 校验 → `WorldID` 不符报 `ErrWorldMismatch` → `ObservationID` 去重 → `SourceSequence` 不得倒退 → `TransformRevision` 冲突报错 → 静态实体无变化则只推进私有高水位不推进 `Revision`。**只有真正改变了状态的事件才让 `revision++`（`:92`）**，所以 `Revision` 是"世界变过几次"，不是"收到过几条消息"。

**恢复语义**（`checkpoint.go:55-69`）：`RestoreProjector` 把**所有**恢复进来的证据登记进 `recoveredObservationIDs`，`snapshotLocked()` 里凡命中者一律标 `STALE`（`projector.go:197-198,217-219,226-228,239-241`）——重启后世界不假装自己还新鲜，直到那条记录被一次新观测刷新。

---

## A2. 语义地图与物体层：从感知到 `resolve_targets`

**物体层的产生**（`docs/experiments/2026-09-14-semantic-object-layer-upgrade.md`）：建图期间以 **1 Hz** 复用 `observe_scene` 那条普通观测路径向驱动索取实体（不新增采集工作），按地图锚点折算累积；发布地图时**重新锚定**到最终锚点（与点云同一锚点）；三条规则让它成为证据：①只有观测进来的才算；②每次目击带时间（`lastSeenUnixMs`/`ageMs`/`sightings`）；③关联有上限——同类别、属性不矛盾、**0.12 m 门限**内才归为同一实例，**被移动的物体变成新实例，而不是瞬移的旧实例**。续建继承物体层但**保留各自时间戳**。层规模上限 512 条。

**`resolve_targets` 的真实语义与一个常见误解。** 原型里的名字解析在 `sim/mujoco/tangying_sim/tools.py:76-110`：给 `objectId`/`destinationId` 就**只确认身份**（`OBJECT_NOT_FOUND` / `DESTINATION_NOT_FOUND`），给 `category`+`color`+`relation` 才做匹配，命中多于一个返回 **`TARGET_AMBIGUOUS`「found N targets」**；**什么都不给时返回实体清单，这是合法的成功**（`ToolResult(True, payload={"entities": ...})`）。它的测试名字就写着这条约定：`test_resolve_targets_returns_both_grounded_ids_and_allows_empty_observation`。

**但在生产路径上，自然语言名词早在这之前就被解析掉了。** 计划里的 `resolve_targets` 参数是 `@object`/`@destination` 占位符（`orchestration/llm.go:152,166-171`），运行时在 `edge/agent/runner.go:846-855` 把它**重新绑定**为 grounding 出来的实体 ID 与置信度。也就是说：

```
"陶瓷杯" → (grounder: 委托目录 + 测量层) → 实体 ID → 计划参数 → resolve_targets 只做确认
```

"找不到"发生在前一段，不在 `resolve_targets` 里——**这是本章最容易讲错的一处**。

**地点名词的真实解析链**（`docs/experiments/2026-09-20-semantic-map-representations-and-nl-navigation.md`）：`normalize_location_name`（剥英文冠词）→ 别名表 → 房间名 → 地图位姿。这里有一个已修的真实缺陷：`'厨房' → kitchen ✅`，`'去厨房' → UNRESOLVED ❌`——生产代码只剥英文冠词，不认识中文动作前缀，而失败的表现在是 `NOT_FOUND` 并列出已知地点，**读起来像"这个房间不存在"**。修法是加一张**封闭的**中文动作前缀表、最长优先、且要求 `len(text) > len(prefix)`（否则"回民街"会被吃掉"回"字）。

**同一个词在三层得到三个答案**（同上报告 §5.2）：`assets/home_locations.json` 有 19 个别名（NFKC+casefold+去冠词）；地图包 `semantics.json` 有 7 个（仅 casefold 精确）；Go 侧 `semantic.navigation.v1` 有 7 个（**大小写敏感**）。`「the kitchen」`在第一层能解析，在第二层不能；`「Kitchen」`在前两层能，在第三层不能。

---

## A3. 地图身份与新鲜度

**身份**：`mapRevision = manifest 的内容哈希`（`robot/.../robot_workflow.py:1338`、`map_catalog.py:100`）。在用地图是三元组 `active_map = {mapId, mapRevision, calibrationRevision}`，`_active_map()` 强制 `calibrationRevision` 必须等于当前标定（`sim/mujoco/tangying_sim/semantic_services.py:275-283`）。

**这里有一个堪称教科书级的自我指涉缺陷（grounding 的第二个根因）。** 物体层 `objects.json` 是**地图自己的产物**，它的字节是 manifest 哈希的一部分；因此**一份文档不可能包含自己的哈希**。而 `_recall()` 曾经要求 `objects.json` 里的 `mapRevision` 等于在用地图的 `mapRevision`——于是**每一份被发布的层都被拒识**，`semantic_recall` 永远为空，grounding 在一台已经扫过房间的机器人上看到 0 个物体。代码把这个错误连同它为什么活下来写得非常清楚（`semantic_services.py:175-201`）：

> 这个错误之所以活下来，是因为单元测试用自己的 helper 伪造了带匹配 `mapRevision` 的文档——**在被检查的地方字段总是存在，在被生产的地方它从不出现**。

修法是：身份改由**文档内部真正能有的字段**（`mapId` + `calibrationRevision`）+ "读的人是从这张地图目录里拿到的"这一事实共同建立。

**"地图过期"如何检测**：`mapping.conflicts()`（`robot_workflow.py:600-632`）把最近采集的点与在用地图逐格比对，报 `occupiedInMapFreeInCloud`、`freeInMapOccupiedInCloud`、`conflictFraction`，并在 `conflictFraction >= 0.02` 时给 `suggestsRescan: true`。返回值里写着一句设计声明：

> `"A suggestion, never an action"` —— 地图够不够旧到该重扫，是操作员的判断，证据摆在这里。

**它只读，不改写在用地图**，测试断言地图数组逐位不变；要更新仍走 `mapping.start {baseMapId}` 续建——这样"昨天能走今天为什么不能走"始终可解释。文档 §四还如实写了缺口：**任务期间不会更新在用地图**，沙发被挪动这类持久变化任务期只能靠局部层躲开，躲不开就失败。

**"回忆新鲜度"是一个部署参数，真实名字是 `TANGYING_RECALL_GOAL_MAX_AGE_MS`。**

```go
// edge/robotclient/recall_goal.go:24
const RecallGoalMaxAgeMS = 15 * 60 * 1000   // 默认 15 分钟
const RecallGoalMaxAgeEnv = "TANGYING_RECALL_GOAL_MAX_AGE_MS"  // :27
```

`RecallGoalMaxAge()`（`:33-46`）的解析规则是本章最值得讲的一句代码级设计：**取值非法（解析失败、`<=0`、或超过 7 天）一律回退到 15 分钟默认值**，理由是注释里的原话——`"unset" and "typo" must not both mean "trust anything"`。

**为什么它会成为部署参数**（`docs/experiments/2026-09-15-survey-lookback-and-pre-position.md:59-74`）：它原本是写死的常量，导致两臂对照实验必须在制图后 15 分钟内跑完，否则回忆按设计回退登记点、实验根本测不到差异。注释里给了两个真实场景：**一周建一次图的场地要大窗口，每班前建图的场地要小窗口**。

`recalledGoal()`（`recall_goal.go:71-147`）的判定顺序是完整的证据链：schema 必须是 `semantic.recall.v1` → `frameId` 必须是 `map` → `mapId`/`mapRevision` 必须等于在用地图 → 条目按年龄升序，**遇到第一条超龄就结束搜索**（`:126-129`）→ 目标取 **`vantagePose`（当时机器人站的地方）而不是物体自身位置**，因为"底盘开不到桌上的杯子"（`:130-136`）→ 每条目还要过 `withinWorkspace`。**没有回忆契约、没有该类别条目、只有过期目击，三者都返回"用登记点"而不是报错**（`:68-70`）——这是刻意的兼容语义。`recallRecord`（`:152-163`）把 `goalSource`（`commissioned`/`recalled`）与 `recallAgeMs` 随计划一起发布，好让读者不必猜这次导航目标从哪来。

**真实对照数字**（同报告）：同一个条目、目击年龄 = 15 min + 1 min——默认 15 min 窗口下**拒绝**并回退登记点（`pose == nil`）；24 h 窗口下**接受**并如实上报 `recallAgeMs`。报告同时如实记录：任务级两臂对照**仍未取得差异数据**，因为运行时没有把 `semantic_recall` 送到落地环节（`goalSource` 仍是 `commissioned`）——这是一个可复现的具体断点，不被当作已完成。

---

## A4. 认证目的地：`GOAL_NOT_CLEAR` 与 `PLACEMENT_NOT_OBSERVED`

**`GOAL_NOT_CLEAR` 的完整判定逻辑**在 `robot/gateway/tangying_robot_gateway/grid_navigation.py:137-172`，三条拒绝码对应三个物理事实：

```python
if not sources or not point_clear(start, radius):
    raise ServiceError("LOCALIZATION_NOT_CLEAR", "当前底盘位置没有足够已知通行空间，请重新定位或补扫周围。")
if not targets or not point_clear(goal, radius):
    raise ServiceError("GOAL_NOT_CLEAR", "目标工作区尚未扫描，或底盘安全间距不足，请补扫或选择其他位置。")
...
if target not in parent:
    raise ServiceError("NO_KNOWN_PATH", "当前地图没有连接到目标工作区的通路，请补扫连接区域。")
```

"不进去"这条原则就实现在通行判据里：`clearance = distance_transform_edt(np.pad(grid["cells"]==0, 1, constant_values=False))`（`:112`）——**未知格（负数）不是自由格**，所以未扫描区域直接不可通行；`free = clearance > radius + resolution/√2`，并在**不确定带**上用真实格子盒精确复算（`:117-121`，注释写明 EDT 的半对角界会把一条 0.325 m 的窄通道误判为不可通行）。规划只在**认证净空位姿之间**做，且每一条边都要求**整条扫掠圆盘**无碰（`:158`）。

**`PLACEMENT_NOT_OBSERVED` 的真实来源与含义**：`sim/mujoco/tangying_sim/rgbd_runtime.py:475` 的 `self._verify_relation(entity_id, f"inside:{destination_id}", "PLACEMENT_NOT_OBSERVED")`——**动作跑完了，但期望关系没被观测到**。所以它在闭环契约里被分类为 `UNKNOWN_OUTCOME`，**禁止自动重试**（`core/closedloop/closedloop.go:101`），理由注释写得很硬：受理为感知失败就等于允许重放一个**可能已经成功**的物理动作，对放置而言物体可能已经在盘子里了。

它的真实根因（`docs/development/2026-09-15-placement-verification-and-certified-goals.md`）：世界快照里 `ceramic-mug` 在 `(2.464, 3.342, 0.79)`、`kitchen-tray` 在 `(2.459, 3.334, 0.73)`——**水平偏差 5–8 mm、底部与盘面同高**，判据本身满足（要求水平在盘内、底部与盘中心高差 < 3.5 cm）。失败发生在"核验时没能连续取到 3 个稳定样本"，不在"没放好"。而当时**任务事件只有一行错误码，没有任何可判读的原因**——于是有了修复 1：`_verify_relation` 把 `passed`/`sample_count`/`expected_relation`/`observed_relation`/`stable_duration_s`/`max_displacement_m` 记进 `self._verification_record`，随 `ToolResult.payload["verification"]` 返回，失败消息变成一句话：`observed 1/3 stable samples of 'inside:kitchen-tray'; last relation 'none', max displacement 0.0000 m, stable for 0.000 s`。

**"不知道的地方不进去"的另一半：把目标吸附到地图认证过的位姿。** `_certify_semantic_goals`（`rgbd_runtime.py:941-998`）：

- 房间目标是从登记点来的单个点，新地图可能让这个点不可规划——实测就是它导致返程腿 `GOAL_NOT_CLEAR`；
- 若 `pose_is_clear` 不通过，按**环搜索** `ring=1..6`（每环 0.1 m）× 8 个方向找**同房间内**最近的认证净空位姿，**朝向保持不变**；
- 找不到就**如实报** `{"adjusted": False, "reason": "no certified pose nearby"}`，绝不把目标悄悄挪到地图从未批准的地方；
- 调整量随目标一起发布（`goalAdjustments`：`adjusted`/`offsetM`/`commissioned`），于是**计划下发的位姿、`verify_arrival` 核验的位姿、读者看到的位姿是同一个**。

**真实数字**：这张地图上 3 个登记点里有 2 个已不可用——`kitchen (2.05, 3.00)` 周围 0.32 m 内 **12 个非自由格**、`living_room (0.0, −1.25)` **11 个**、`home_corridor (0.0, 1.85)` **0 个**。客厅返程点 0.32 m 邻域曾有 **13 个未知格**，勘测加一步"回望"后降到 **6 个**（剩下的正是底盘自己脚下那块，任何前向视角都看不到）。

**顺带一个"安全检查变成陷阱"的真实缺陷**（`docs/experiments/2026-09-20-semantic-map-...md:560-568`）：`_swept_model_collision` 从**当前位姿**开始采样（`linspace(0,1,n)` 含 `fraction=0`），于是底盘一旦落进包络半径内，"你离墙太近"对**每一个**候选脉冲都成立——**包括那个把它开离墙的脉冲**。实测：离墙 **0.373 m**、包络 **0.375 m**，差 2 mm，这个回合剩下的时间里它再也动不了。修法是有意不对称的：从合法位姿出发，任何越界都拒绝；从非法位姿出发，**只拒绝把它变得更糟的移动**。修复后现场验证：向东（离墙）→ `NAV_REACHED`，随后向南 → `NAV_REACHED`。

---

## A5. grounding 根因：三条独立的失败链

系统对"模型说的物体"和"地图里的物体"对不上时的处理，可以按**发现顺序**讲成三条链，每条都有代码与提交正文作证：

**根因 ①（规划层）——"隔着墙找杯子"。** 提交 `6f87aca04` 正文原话：

> 根因链：机器人停在客厅 `[0,−1.25]`，杯子在厨房 `[2.05,3]`，四米外隔一堵墙。计划第一步就在客厅找杯子：`observe_scene → resolve_targets → …`。检测器是几何式的，看不见就是看不见，`objects=0`。
> 这个故障活得久，是因为它**长得像感知问题，实际是规划问题**。模型漏掉导航不是疏忽：没有任何输入告诉它机器人在哪、杯子在哪。**看不见世界的规划器，会为它想象出来的世界写计划。**

**根因 ②（数据层）——物体层被自己的哈希拒识**（A3 节的自我指涉缺陷）。这一条最隐蔽，因为它让 grounding 在一台扫过房间的机器人上稳定返回 0 个物体。

**根因 ③（绑定层）——grounding 表拒收场景里真实存在的物体**（提交 `55ae5a5ca`）：`homeObjectID` 只为 `cup/red` 作答，其余全部拒绝，于是一个"两件物体"的请求把第一趟搬运跑完整整十二步、全部确认，然后停在 `ground subtask 2: home task object is not commissioned: blue/cup`。提交正文的判词值得整句引用：**"一条语法接受了、而 grounding 表拒绝的请求，是一个半支持的特性"**。修法是把表对齐场景（红/蓝/绿杯 + 一个黄盘），并同时钉住反向的测试：**场景里没有的物体必须仍然被拒绝**，否则"要一个不存在的东西"会先 ground 成一个像样的名字、然后死在夹爪上而不是死在这里。

**系统怎么处理"对不上"**：计划里只有占位符；运行时在 `edge/agent/runner.go:846-855` 用 grounding 结果**重写** `resolve_targets` 的 `objectId`/`objectConfidence`/`destinationId`/`destinationConfidence`；grounding 失败则在任何物理步骤之前终止。**"对不上"不是一个可以被模型说服的判断**——模型只能规划和建议，实体身份由运行时注入。

**诚实标注**：同一批提交里明确写了**不是**根因的东西——`TelemetryHub.Unfiled` 计数器（实测 `unfiled = 0`），保留它只是因为"静默丢数据本身就该可见"，把它说成"修复"会是假声称。

**以及一条当时没修掉的一半**（同提交正文）：计划里的导航步骤执行不到，因为 `edge/agent/runner.go` **先 grounding（`:241` `grounder.Ground(ctx, intent)`）、后建计划（`:266` `planForIntent`）**，而"先导航到厨房"是计划里的一步，不可能排在它赖以生成的那次 grounding 之前。提交把它定性为**循环依赖，不是接线疏忽**，真正的修法要在执行顺序上。（提交正文当时记的是 `:225` / `:741`，HEAD 上已漂移到 `:241` / `:266`——引用行号时二者都要能对上。）

---

## A6. 世界状态的可扩展性

**设计一：物体物理属性成为数据，不是代码常量**（`robot/gateway/tangying_robot_gateway/physical_attributes.py`）。动机写在模块 docstring 里：陶瓷杯、纸杯、玻璃花瓶在谁的目录里都不共享前缀，却需要不同待遇；而系统此前把知识写成运行时的字面量——放置高度是 `{"cup": 0.06, "bottle": 0.08}[category]`，抓取容差是类常量——**于是"新物体"等于改驱动，"未知物体"抛 `KeyError` 而不是说"我没有这类物体的配置"**。

具体做法，三条与实现一一对应：

1. **属性可选**：`material ∈ {rigid, soft, fragile, deformable, granular, unknown}`、可选 `mass_g`、`max_grip_force_n`，走现有 `attributes map[string]string` 通道，**协议不变**。未声明 = 与今天**逐位一致**（默认值就是被替换掉的那两个常量：`GRASP_TOLERANCE = 0.055`、`PLACEMENT_HALF_HEIGHT_M`）。
2. **未知是值，不是错误**：`unknown` 是合法值，表示"看过但没识别出来"（与"检测器根本没跑"不同）。
3. **非法是错误，且可诊断**：未声明的类别返回 `PLACEMENT_PROFILE_UNAVAILABLE` 并**列出已知类别**；非法 `material`/`mass_g`/`max_grip_force_n` 分别是 `PHYSICAL_ATTRIBUTE_UNKNOWN` / `PHYSICAL_ATTRIBUTE_INVALID`。物体自报的 `max_grip_force_n` **优先于**材质默认。
4. **预算随证据发布**：`grasp_budget` 进状态——"为什么抓得这么轻"能从记录里回答，而不是去读代码。

**同时如实记录的"还写死在哪"**：`rgbd_runtime.py:412` 的 `half_height = {"cup":0.06,"bottle":0.08}[entity.category]`、`:346` 的 `e.category in {"cup","bottle"}`；以及后置条件本身是**关系谓词**（3 帧稳定 `held_by:robot` / `inside:<dest>`），**无法表达"抓稳且未压碎"**。

**设计二：地图冲突只读可查**（`robot_workflow.conflicts()` → 服务 `mapping.conflicts`）。见 A3；关键是**只统计与返回，不改写地图**，测试断言地图数组逐位不变。文档给出的理由很实在：**地图是已验收的导航依据，静默变更会让"昨天能走的路线今天为什么不能走"无法解释**。

**扩展点的成本模型**（`docs/development/2026-09-15-extensibility-review-tools-and-navigation.md` §一）：一个能力由**五处**共同定义——规范工具名（Python `CANONICAL_TOOLS` + Go `canonicalTools`，两侧必须一致否则 profile 校验直接拒）、参数契约、安全分类（`PHYSICAL_TOOLS`/`MUTATES_WORLD_TOOLS`）、技能清单、本体声明（`RobotProfile.tools`）。这一节的价值在于它量化了"加一个能力"到底是几处改动。

---

# 第二部分 B：任务编排

## B7. 从一句中文到 12 步计划的完整数据流

| # | 动作 | 真实位置 |
| --- | --- | --- |
| 1 | `tasks.Service.Create(request, adapter)` | `tasks/service.go:168` |
| 2 | `s.currentParser().Parse(request)` —— 解析器在锁后读，可运行时替换 | `tasks/service.go:169` |
| 3 | `agent.Parser.Parse`：`ValidateRequest` → **确定性优先** → 模型兜底 → 两错合一 | `agent/agent.go:53,59,77-83,84-91` |
| 4 | `NormalizeAdapter`（`auto/sim`→`mujoco` 等） | `tasks/service.go:170` |
| 5 | `s.worldFor(adapter)`：`TANGYING_GVF_ENABLED=1` 走 grounded world，否则取该 adapter 最新遥测 → `WorldFrom` | `tasks/service.go:177,543-554` |
| 6 | `orchestration.New(...)`：provider=openai **且** BaseURL/APIKey/Model 三者齐备才返回 `LLMPlanner`，否则 `DeterministicPlanner` | `orchestration/llm.go:35-38` |
| 7 | `LLMPlanner.Plan`：`agentcontext.Project(..., "planning")` 生成模型输入（非 legacy 模式） | `orchestration/llm.go:74-83` |
| 8 | 采样循环：`marshal → post → parseBundle → validateBundle`，每个失败都追加进 `Rejections` 而不是中止 | `orchestration/llm.go:87-115,250,267` |
| 9 | 无候选 → `Bundle{Source: deterministic, Attempts, Rejections}`；有候选 → 按 **`sha256(plans)` 的众数** 选一份 | `orchestration/llm.go:117-123,124,304-330` |
| 10 | 规划整体报错 → `Bundle{Source: deterministic, Rejections: [err]}`（**失败可见，不静默**） | `tasks/service.go:178-184` |
| 11 | 落库：`Task{State: READY}` + `TaskRevision{ApprovalRequired: true, RiskClass: "physical"}` + `buildRevisionSteps` | `tasks/service.go:186-211,428-459` |
| 12 | 步骤级安全字段：`SafetyLevel`、`ApprovalID="approval:<taskID>:physical"`、`DeadlineUnixMS`、`LeaseMS`、`IdempotencyKey` | `skills/manipulation/plugin.go:69`、`edge/agent/runner.go:862-874` |
| 13 | 审批：`Task.Approved/ApprovedBy/ApprovedAt`（**谁批的、什么时候批的**是三个独立字段） | `tasks/service.go:31-45` |
| 14 | 执行：`Runner.RunControlled` → `grounder.Ground` → `planForIntent` → 逐步执行 | `edge/agent/runner.go:228,241,266` |
| 15 | 编译：`compiler.Compile` 校验形状 + 拓扑排序 + 环检测 | `core/compiler/compiler.go:23-55` |
| 16 | 运行态：`taskgraph.GraphRuntime` 维护 ready 集并在节点终态时刷新后继 | `core/taskgraph/runtime.go:40+` |

**"12 步"就是 README 里那张表**（`README.md:161-176`）：`observe_scene → navigation.navigate → verify_arrival → observe_scene → resolve_targets → plan_grasp → manipulation.pick → verify_grasp → manipulation.place → verify_placement → navigation.navigate → verify_arrival`，实测全部闭合、**12 条观测证据**；返程那一步在当时因地图覆盖不足以 `GOAL_NOT_CLEAR` 结束（README 原话："这是『不知道的地方不进去』，不是缺陷，但**在这一版上返程确实没走通**"）。

> **文档冲突标注**：`README.md:151` 记返程未走通，而 `docs/experiments/2026-09-20-semantic-map-...md:581-592` 在修掉"守卫陷阱"与"委托拓扑未发布"两个缺陷后报告**同一栋房子 4 个房间 8 条腿全部 `NAV_REACHED` + `verify_arrival` 确认**。两处都对，但**说的是不同版本的代码**，引用时必须带日期，否则就是把后来的结论塞进从前的证据里。

---

## B8. 确定性解析 vs LLM 规划：三条规则的实现位置

| 规则 | 实现位置 | 代码事实 |
| --- | --- | --- |
| **① 已知意图优先走确定性解析，成功不被覆盖** | `agent/agent.go:57-62` | 注释原文：`Deterministic first: it is exact for the phrasings it knows, costs nothing and cannot hallucinate. A success here is not overridden.` |
| **② 解析失败返回原解析错误，不静默降级** | `agent/agent.go:84-91` | 只有 `errors.Is(deterministicErr, intent.ErrClarificationRequired)` 时才把模型错误**追加**在原错误之后：`fmt.Errorf("%w（模型也没能理解：%v）", deterministicErr, modelErr)`；否则**原样返回 `deterministicErr`** |
| **③ 规划层另有确定性后备** | `orchestration/llm.go:35-38,117-123`；`orchestration/types.go:47-51`；`tasks/service.go:149-151` | 未配置完整 → `DeterministicPlanner`；所有候选被拒 → 返回 `Source: deterministic` 但**保留 `Attempts` 与 `Rejections`**；`NewService` 无 planner 时同样兜底 |

规则 ② 的注释解释了这个设计为什么必须这样写（`agent/agent.go:84-88`）：

> 澄清文本保留在错误头部，因为那是操作员能据以行动的部分（"说清楚是哪个房间"）。**模型的失败是被追加而不是被替换**：一个模型够不到的部署，不能看起来像一个语法很窄的部署。

同一段注释还记着被这条规则修掉的旧行为：**"语法表达不了"和"这句话本身有歧义"曾被当成同一个答案**，于是一个已经为模型付了钱的部署，仍然对"把红色杯子放到桌上"回答"请明确交接区、目标区或收纳盒"——而模型对这句话毫不犹豫地就能解对。

**为什么这样设计（本书的教学点）**：
1. **确定性解析器的正确性是可证的，模型的是概率的**。前者对它能覆盖的说法是精确的、零成本、不会幻觉。
2. **两条路径的失败方向相反，且这一点被量出来了**：`deterministic 6/10（executable 0/4，refusals 6/6）` 对 `deepseek-flash 9/10（executable 4/4，refusals 5/6）`——**语法太保守，模型太配合**（`docs/architecture/orchestration-post-training.md:27-46`）。
3. **后备不是"降级"，是另一条一等路径**。注意 `DeterministicPlanner.Plan` 返回的 Bundle **不含 Steps**（`types.go:49-51`），注释写明它"忽略世界，因为它自己不产出任何需要与世界一致的步骤"——真正的确定性计划生产者是领域计划构造器 `skills/manipulation.Plan`（`plugin.go:68+`），由运行时调用。**"确定性后备"指的是那条经过验证的领域计划，不是 orchestration 包里那个空 Bundle**。
4. **指标层把"后备"当成一个可观测事件**：`LLMFallbackTasks` 的定义是"来源是 deterministic **且** `Attempts > 0`"（`orchestration/metrics.go:46-50`）——即"试过模型但没用上"，而不是"没配模型"。

---

## B9. 编排层拿到世界状态：`orchestration/world.go` 做了什么

`World`（`orchestration/world.go:28-38`）只有三个业务字段：`RobotRoom string`、`Objects []ObjectPlace{Category, Room}`，外加 `GroundedOnly bool` + `StateReports []string`（GVF 路径）。它的能力边界由三个方法钉住：`Known()`（`:52-54`，是否够格规划）、`Rooms(category)`（`:57-74`，去重+排序）、`Describe()`（`:82-139`，渲染成提示词句子）。

**"隔着墙找杯子"为什么是真实缺陷**——见 A5 根因 ①。缺陷的可怕之处不在失败，而在**每一层的行为都是正确的**：`resolve_targets` 正确地报告找不到，闭环正确地失败关闭，恢复正确地进入可恢复状态。**只有计划本身是错的**，而错误被伪装成感知问题。所以修复不是加一个检查，而是**改变规划器的输入**。

**三个值得写进书里的设计决定：**

1. **`Planner.Plan` 把世界当参数传，而不是存在 planner 上**（`orchestration/types.go:34-39`）：`"a plan is only meaningful against the state it was formed from, and a planner that cached the robot's location would keep planning for wherever it used to be."` 修订路径同样如此——`service.go:254-256` 注释："修订是**对着现在的世界**规划的，不是task创建时的世界：机器人已经移动过了。"
2. **提示词写的是约束带理由，不是数据字段**（`world.go:76-81,134-137`）：`**The robot cannot see through walls.** A skill that looks for or manipulates an object only works when the robot is in a room the object is known to be in.` 理由写在注释里：**写成字段，模型会当成可选上下文；写成带理由的规则，它才改变计划。**
3. **读不出来的一律留空，绝不猜**（`world.go:148-153,206-214`）：`"A robot placed in a room it is not in would produce a confidently wrong plan, which is worse than a plan that admits it lacks the navigation it needs."` 离所有登记点超过 4 m 就**不认领最近的那个房间**。

**当时没修掉的一半**：见 A5 结尾——grounding 在建计划之前，构成循环依赖。这是一个把"计划"和"世界"的关系讲透的绝佳案例：**世界状态注入解决了"计划不知道世界"，但没有解决"世界自己需要计划才能被观测"**。

**验证（提交正文）**：修前 `observe_scene` 开头；修后 `navigation.navigate → verify_arrival → observe_scene → resolve_targets → … → manipulation.place → verify_placement`。Go 50 包全绿，新增 5 个 `WorldFrom` 测试。

---

## B10. 计划的结构、stage、前后置条件

**计划**（`core/taskgraph/model.go:15-23`）：`TaskPlan{ID, Goal, Domain, Revision, Steps []SkillStep, Budget, StopPolicy}`。

**一步**（`:25-38`）：`SkillStep{ID, Skill, RobotID, BrainID, Arguments map[string]any, DependsOn []string, ExpectedOutput []string, SafetyLevel, ApprovalID, DeadlineUnixMS, LeaseMS, IdempotencyKey}`。

**形状校验**（`ValidateShape`，`:51-67`）：ID 非空且不重复；**依赖必须指向已经出现过的步骤**（"depends on unknown or later step"）——所以合法计划天然是**拓扑序 DAG**，`compiler.Compile` 只需做一次线性 Kahn 扫描并检测环（`core/compiler/compiler.go:38-53`）。

**"stage" 在这个仓库里有两个互不相干的含义，讲课必须分开：**

1. **决策环节 `Document.Stage`**（`core/agentcontext/context.go:59`，取值 `goal/planning/tool_result/verification/ops/reflection/recovery/handoff`）：表示"这次要做的是哪一个判断"，由 `StageForRole()` 映射（`routing.go:19-27`），并由内置 `stage-policy.json` 逐环节选定表达格式（planning = `nl_decision`，tool_result = `json`，recovery = `nl_sections`……）。`Project()` 返回的是**实际用过的渲染器**而不只是全局开关（`projection.go:3-13`）。
2. **路线阶段 `ManipulationRouteIndex`**（`skills/manipulation/plugin.go:172-180`）：机械臂动作绑定在路线的第几段。注释写明不许退回"第一个匹配的房间"，否则**操作会被搬到另一段路线上**。

**前置条件如何表达与校验**——计划本身**没有** precondition 字段，前置条件落在三处：

- **目录层的必需参数**：`SkillManifest.RequiredParameters`（`core/skills/manifest.go`），由 `validateBundle` 逐条检查（`orchestration/llm.go:288-292`）；
- **能力与安全前置**：`RobotProfile.tools` 广告的可用能力 + `AllowedSafetyProfiles`——**没有的能力在计划阶段就被拒，不是运行时才发现**；`SkillManifest.Validate` 还拒绝"物理技能没有租约/没有安全 profile"和"改世界的技能却没有副作用标记"（后者注释：调用方会被告知无需确认，而闭环门禁在等一份永远不会到的证据）；
- **运行时预检**：`GOAL_NOT_CLEAR` / `LOCALIZATION_NOT_CLEAR` / `NO_KNOWN_PATH` 这三个**在底盘动之前**就拒绝（`grid_navigation.py:137-172`），也正因为"底盘没动"，它们被分类为 `PERCEPTION` 而不是 `UNKNOWN_OUTCOME`（`core/closedloop/closedloop.go:187-190`）。

**后置条件如何表达**：`RevisionStep.RequiredPostcondition`（`tasks/revision.go:54`），由 `buildRevisionSteps` 构造（`tasks/service.go:428-446`）：

```
"<color>-<category> in <destinationCategory>[/<relation>][/<destinationId>]"
例：red-cup in storage_bin/right_side/blue-target_zone
路线任务：arrived at living_room -> home_corridor -> kitchen
```

它是一个**人类可读的、用于对账的字符串**，参与 `SemanticFingerprint` 与稳定步骤 ID 的推导（同 revision 内语义未变则沿用原 StepID、状态与证据）——**但它不是物理完成的判据**。物理完成由闭环门禁决定：`MutatesWorld` 的技能必须有**动作后的新鲜证据**才允许被记为完成（`core/skills/manifest.go` 的 `MutatesWorld` 注释：**成功的返回码是"去观测"的触发器，永远不是完成证明**）。

**编排层如何决定某步能不能执行**：三个独立层次，缺一不可——① 依赖是否满足（`taskgraph.GraphRuntime` 的 ready 集）；② 能力/安全/审批是否齐备（目录 + profile + `ApprovalID`）；③ **世界现在是不是真的那样**（`core/worldmodel/predicate.go` 对着 `Snapshot` 求值，返回带证据引用的三值结果）。第三层是机器人与 coding agent 最本质的区别所在。

**compiler 与 taskgraph 的分工**：`compiler` 是**一次性静态**的（校验形状、算出一个执行顺序、环即报错）；`GraphRuntime` 是**可变运行态**（节点终态时刷新后继）。`runtime.go:25-30` 写明了为什么必须分开：**多机器人计划里，机器人 A 的节点 N 可能要在机器人 B 的节点完成后才就绪**。

---

## B11. 编排层评测体系："先有刻度，再谈自训"

**设计取舍**（`orchestration/eval/eval.go` 包头 + `docs/architecture/orchestration-post-training.md:66-110`）：只问一个问题——"给定这句话，产出的是不是一份会执行的计划、以及该不该拒绝"。四条硬规则：

1. **拒绝是独立维度**，报告永远分两半打印（`executable: a/b` / `refusals: c/d`），因为"不敢做"和"做错了"需要**完全相反的修法**；
2. **运行失败 ≠ 答错**：模型不可达是 `Error`，不是 `Refused`；退出码 0/1/2 区分"全过/有错/跑不起来"；
3. **断言目标，不断言实现**——这条是被自己的 bug 教会的：第一版 `navigate-kitchen` 断言工具名 `navigate_route`，而计划里装的是技能 `navigation.navigate`，**一个正确的导航计划被判为失败**（`orchestration/eval/cases.go:63-73` 保留了这段记录）；
4. **换一个端点就是全部的接口**，供 checkpoint 准入。

**指标**（`orchestration/metrics.go:16-32,34-88`）：`LLMPlanRate`、`LLMCandidateRate`（= 接受候选 / 尝试次数，只在 `Attempts>0` 的记录上算）、`LLMRejectionCount`、`EndToEndSuccessRate`、`SuccessByPlanSource`，以及一个显式的合成分：

```go
metrics.OrchestrationScore = metrics.EndToEndSuccessRate * 60   // metrics.go:85
metrics.OrchestrationScore += metrics.LLMCandidateRate * 25     // :86
metrics.OrchestrationScore += metrics.LLMPlanRate * 15          // :87
```

文档对它的定性很克制：**"分数只用于迭代，不参与物理放行"**（`docs/architecture/orchestration.md:25`），并且提醒"不要用 fallback 率推断用户要求是否被完整保留"——因为"已知请求走确定性 Parser"和"Planner 是否采用 LLM"是两个独立决定。

**真实基线数字**（提交 `505af6577` 正文，同一套用例）：

| 规划器 | 总分 | executable | refusals | 备注 |
| --- | --- | --- | --- | --- |
| `deterministic` | **6/10** | **0/4** | **6/6** | 一条都规划不出来，但该拒的全拒了 |
| `deepseek-flash` | **9/10** | **4/4** | **5/6** | 把"红色和蓝色方块"一次性规划了 14 步，违反产品规则"一条指令一个物体" |

**两边失败方向相反**——这句话是第 9 章最好的开场。

**第 13 节因子实验（`docs/experiments/2026-09-21-agent-context-evaluation.md:488+`）的真实结果：**

- **数据规模**：480 个完整状态（train 80 / dev 80 / test 320），每环节 train 用族 5、dev 用族 0、test 用族 1–4；每族 5 个实例种子、每实例 2 个反事实状态。两模型产生 dev 1,280 + test 10,240 条评分行。最终归档引用 **12,484 个不同真实请求**，API 记录总 token **31,716,142**；**协议/传输失败 0 个**，触发传输重试 6 个。独立确认用新种子 49979687/49979693/49979701，192 个状态、1,152 条评分行。
- **四因素主效应**（正值 = CNL / decision 排列 / derived 标注 / 完整信息更好，单位百分点）：

| 模型 | syntax | order | annotation | complete |
| --- | --- | --- | --- | --- |
| deepseek-flash | +0.55（Holm 1） | +4.30（0.2446） | +3.20（0.2504） | **+73.55（0.00016）** |
| deepseek-v4-pro | +1.17（1） | **−4.30**（0.0608） | **+5.08（0.0391）** | **+65.51（0.00016）** |

  **语法主效应在两个模型上都不显著**——即"JSON 还是受控中文"这件事本身不是决定性的；**唯一稳健的显著项是确定性元数据标注（Pro +5.08 pp）**，而 `order` 甚至对 Pro 是负的。
- **masked 双生（命题 2 的实证）**：两个模型都是**状态答案正确 0.00%、证据支持正确 100.00%、拒答 100.00%、无支持断言 0.00%、危险建议 0.00%**。删掉必要字段后正确率塌到 0，同时拒答率升到 100%——**这是"模型不知道自己不知道"的反例，也是"信息不足时拒答"可以被系统性做到的证据**。
- **八环节完整信息矩阵（40 状态/格）最刺眼的结论**：**"规划"环节在两个模型 × 8 种表达下全部为 0.00%**；`反思 50–75%`、`交接 52.5–77.5%`、`恢复` 在 Pro 上低到 **2.50–50%**。也就是说，**上下文表达解决不了规划环节的能力问题**。
- **开发集锁定策略 vs JSON 基线**：flash 测试 75.62% vs 70.00%，确认 76.56% vs 70.31%，差 **+6.25 [+2.08, +10.94] pp，Holm p = 0.06552**；pro 测试 68.75% vs 64.38%，确认 70.31% vs 64.06%，差 **+6.25 [+1.04, +13.02] pp，Holm p = 0.1924**。**两个都未达到 0.05。**
- **统计功效被如实算出来**：每环节 n=4 时双侧符号翻转的**最小非零 p = 2/16 = 0.125**，所以本轮**无法提供严格的逐环节优越性证明**；要让命题 5 那个分布无关界 ≤ 5 个百分点，保守估算**至少需要 4,615 个独立族**。
- **追加实验 A（层级结构）**：flash 开发锁定组合在新实例上 71.88% vs flat 69.79%，差 +2.08 [+0.00, +5.73] pp、Holm p=0.9969，平均 token **降 36.70%**；pro 65.62% vs 61.46%，+4.17 [+0.52, +8.33] pp、Holm p=0.3651，token 降 27.14%。**规划仍然 0%**，所以不能反推"扁平化导致规划失败"。
- **追加实验 B（显式决策契约）**：pro 的恢复环节 contract JSON 从 **4.17% 升到 62.50%**，但**危险建议率仍有 25%**；flash 的契约候选**未通过准确率门禁，回退基线**。契约主效应 flash **−4.17** [−8.33, −1.04]，pro **+26.04** [+6.25, +44.79]（Holm 均 0.25）。**不能把其中一个模型的改善推广到所有模型。**
- **最终工程处置**：规划、验证（危险率 12.5%）、恢复（25%）被写进 `deployment-policy.json` 的 `blocked_stages`，`ProjectWithFactorPolicy` **拒绝自动套用**。文档原话：**"规划候选的相对门禁虽然『与零分基线持平』，不具备上线资格"**、**"不能用最高候选分数掩盖这个负结果"**。

**"先有刻度，再谈自训"的完整论证**（`docs/architecture/orchestration-post-training.md`）：收益在成本/延迟/格式/离线；**域外与歧义处理明显更差，拒绝的准确性风险最高**。用 API 输出蒸馏会丢掉"该拒绝时拒绝"——所以顺序不可选：**先评测（已做），再蒸馏，最后才是 RL**。奖励设计三条硬规则：**拒绝必须能得分**；**`UNKNOWN_OUTCOME` 既不给正分也不给负分，而是屏蔽该样本**；**闭环门禁不参与训练**（训练只能读它的结论，不能改它，"反过来就是本仓库一直在防的那种自证"）。

---

## B12. 与通用 coding agent 的差异：五条有代码作证的差异

| # | coding agent 的世界 | 机器人的世界 | 代码证据 |
| --- | --- | --- | --- |
| 1 | 读文件是精确的、永远新鲜的、即真相 | 每条事实带 `Freshness` 与 `Confidence`，**陈旧即 `UNKNOWN`** | `types.go:20-27,46-57`；`predicate.go:138-140`。一个 coding agent 读不到文件得到 `ENOENT`（不存在），机器人看不到物体得到 `objects=0`（**不知道**）——这两者必须被区分 |
| 2 | 新鲜度是自然的（刚读的） | 新鲜度是**算出来的**，而且曾经是假的 | `edge/agent/runner.go:765` 现在写 `evidence.Freshness = string(snapshot.EvidenceFreshness(now))`，注释记录旧实现是字面量 `"FRESH"`——**那意味着门禁的过期规则永远不会触发** |
| 3 | 真相在两次操作之间不变 | 世界在**规划与执行之间**会变 | `orchestration/types.go:34-39`（世界按调用传入）；`tasks/service.go:254-256`（修订对着现在的世界规划） |
| 4 | 动作可重放（重跑测试/重写文件） | **物理动作不可撤销，且结果可能是 `UNKNOWN`** | `closedloop.go` 的 `UnknownOutcome` 类；`PLACEMENT_NOT_OBSERVED` 禁止自动重试（`:101`）——对放置而言，物体可能已经在盘子里了 |
| 5 | 路径即身份（`/etc/hosts` 就是它） | **"房间"是给一个已委托位姿起的名字**，不是一个可寻址对象 | `orchestration/world.go:172-219`；三张不一致的别名表（A2）——**同一句话在三层得到三个答案** |

**这五条如何改变了 planning 的设计**：

- **计划必须携带它赖以成立的世界**，所以 `Plan(request, intent, world)` 三参数（`types.go:40-42`）；
- **计划的正确性不能只靠 schema 校验**：`validateBundle` 只能证明"技能名合法、必需参数齐全、至少有一个副作用技能"（`llm.go:267-301`），**它证明不了这个计划在物理上可能**——那要靠世界状态注入与运行时预检；
- **"不知道"必须能被表达，并且必须能拦住动作**：`GOAL_NOT_CLEAR` 在底盘动之前拒绝、`PLACEMENT_NOT_OBSERVED` 禁止重试、`UNKNOWN` 在奖励函数里被屏蔽——三处不同的位置，同一条原则；
- **规划的失败会伪装成感知的失败**：A5 的三条根因里，前两条都以 `objects=0` 的形式出现，看起来都是检测器的问题。**这就是为什么本书要把这一章单独拿出来讲：在机器人系统里，一个错误的计划会以所有下游层都正确的方式失败。**

---

## B13. 教学价值

### 练习 1（区分"不存在"与"不知道"）

> 世界模型用三值逻辑（`TRUE`/`FALSE`/`UNKNOWN`）而不是布尔。请回答：如果 `EntityInside("ceramic-mug", "kitchen-tray", 5s)` 返回 `UNKNOWN`，为什么任务不应该把它当成"没放好"去重放放置动作？如果它返回 `FALSE` 呢？

**答案要点**：① `UNKNOWN` 的三种触发是实体不在快照、证据超龄、或 `maxAge<=0`（`predicate.go:42-56,138-140`）——这三种情况下**系统根本不知道杯子在哪**，而放置动作已经跑过一次，重放等于把一个可能已经成功的物理动作再做一遍（`closedloop.go:101` 的原文理由）；② `FALSE` 意味着**有新鲜证据**证明关系不成立（`RELATION_MISMATCH`），这时"重新核验 + 重新规划"是正确的；③ 真实的 `PLACEMENT_NOT_OBSERVED` 就是第一类：世界快照显示杯子物理上就在盘里（水平偏差 5–8 mm），失败在"没取到 3 个稳定样本"。**考点：错误码的类别决定了唯一安全的恢复动作，而这个仓库把这条规则写进了 `core/closedloop`。**

### 练习 2（后备路径为什么不能删）

> 有人提议：既然配了模型，就把确定性解析器删掉，让模型处理所有请求，代码更简单。请用评测数字反驳，并指出"确定性后备"在代码里到底指什么。

**答案要点**：① `deterministic 6/10（executable 0/4，refusals 6/6）` 对 `deepseek-flash 9/10（executable 4/4，refusals 5/6）`——**两边的失败方向相反**，语法太保守、模型太配合；删掉语法就删掉了"该拒的全拒"这一半（`docs/architecture/orchestration-post-training.md:27-46`）；② 拒绝是安全属性：一个"红色和蓝色方块"被模型规划成 14 步，而这违反产品规则；③ 代码上"确定性后备"有两层：`orchestration.New` 在配置不全时返回 `DeterministicPlanner`（`llm.go:35-38`），所有候选被拒时返回 `Source: deterministic` 但保留 `Attempts`/`Rejections`（`llm.go:117-123`）；而**真正产出步骤**的确定性路径是领域计划构造器 `skills/manipulation.Plan`，`DeterministicPlanner` 本身只返回一个空的 `Bundle`（`types.go:44-51`）；④ 还有一条独立的理由：确定性解析器零成本、无网络、不会幻觉，是**离线与实机验收的基线**（默认 `AGENT_PROVIDER=deterministic`，演示闭环不依赖任何模型服务）。

### 练习 3（"再检查一遍"什么时候会变成缺陷）

> 地图冲突只用只读方式报告（`conflicts()`），从不自动写回；而 PLACEMENT 核验失败时系统会记录自证信息。请说明这两个决定背后的同一条原则，并指出一个它可能被违反的地方。

**答案要点**：① 同一条原则是**"可解释性优先于自动化"**——地图是已验收的导航依据，静默变更会让"昨天能走今天为什么不能走"无法回答（`docs/development/2026-09-15-extensibility-review-tools-and-navigation.md` §三扩展点 ④）；核验失败如果不能自证，就得同时翻世界快照、证据库和仿真才知道"到底放好没有"。② 被违反的地方（真实的）：`_swept_model_collision` 从当前位姿开始采样，让"安全检查"把机器人**关进了它再也开不出来的陷阱**——离墙 0.373 m vs 包络 0.375 m，差 2 mm，一个回合内再也动不了。**一个防止碰撞的检查，变成了一个比碰撞更糟的失败模式。** 修法是有意不对称的：从非法位姿出发，只拒绝让它变得更糟的移动。③ 可以引申的追问：`GOAL_NOT_CLEAR` 是"不进去"，那"进得去但出不来"由谁负责？（答案是 8 条腿的闭环与 `routeEdges` 拓扑——它是**被写下来却从不发布**的两个缺陷之一。）

### 学生最容易误解的四点

1. **以为 `resolve_targets` 在做"自然语言名词 → 实体"的解析。** 在生产路径上，实体 ID 是 grounder 在计划生成之前决定的，`resolve_targets` 只做确认（`edge/agent/runner.go:846-855`）。
2. **以为"看不到"等于"不在那里"。** 在机器人里它只等于 `objects=0`——**不知道**。这是本章最重要的一条，也是三条 grounding 根因共同伪装成的那件事。
3. **以为 `mapRevision` 是一个可以随便塞进产物的字段。** 物体层是地图自己的产物，它的字节是 manifest 哈希的一部分，**它不可能包含自己的哈希**——这个自我指涉缺陷让一整条链路长期返回空值。
4. **以为 `orchestration.DeterministicPlanner` 就是确定性计划。** 它返回的 `Bundle` 里**没有 Steps**；真正产出步骤的是领域计划构造器。混淆这两者会把"后备是空操作"这个错误结论写进书里。

---

## 源码索引

**A 部分 · 世界模型**

| 文件:行 | 内容 |
| --- | --- |
| `core/worldmodel/types.go:9-18` | `SchemaVersion`、`Freshness` 四值 |
| `core/worldmodel/types.go:20-27` | `EvidenceRef`（每条事实的证据指针） |
| `core/worldmodel/types.go:29-44` | `RobotState`（含 `Held`、`Faults`） |
| `core/worldmodel/types.go:46-57` | `EntityState`（`Attributes`、`Relations`、`StableObservations`） |
| `core/worldmodel/types.go:59-77` | `ResourceState`（`Owner`/`FencingToken`）、`SourceState` |
| `core/worldmodel/types.go:84-106` | `Snapshot`、`Delta` |
| `core/worldmodel/projector.go:15-18` | `ErrWorldMismatch`、`ErrTransformRevisionConflict` |
| `core/worldmodel/projector.go:66-95` | `Apply` 的六道闸；仅真变更推进 `revision` |
| `core/worldmodel/projector.go:120-132` | `unchangedStaticEntity`（静态事实短路） |
| `core/worldmodel/projector.go:140-184` | `reduce`（各类事件的归约） |
| `core/worldmodel/projector.go:186-249` | `snapshotLocked`（逐类算新鲜度；恢复证据标 `STALE`） |
| `core/worldmodel/projector.go:259-267` | `freshnessAt` |
| `core/worldmodel/predicate.go:5-24` | 三值状态与 10 个 `Reason` 码 |
| `core/worldmodel/predicate.go:42-88` | `EntityInside`、`EntityStable`、`RobotHeld` |
| `core/worldmodel/predicate.go:119-140` | `All` 的非对称合并；`evidenceStale` |
| `core/worldmodel/checkpoint.go:13-23` | `Checkpoint`（含私有去重状态） |
| `core/worldmodel/checkpoint.go:55-69` | `RestoreProjector` 不信任恢复的证据 |
| `core/worldmodel/checkpoint.go:83-152` | `validateCheckpoint`（身份自证） |
| `core/observation/envelope.go:13-41` | 观测错误码、7 种源类型、6 种 kind |
| `core/observation/envelope.go:43-62` | `Envelope`（`SourceSequence`/`TransformRevision`/`Causation`） |
| `core/observation/envelope.go:122-177` | `Validate`（含故障报告不合就不算机器人事实） |
| `fleet/worldhub/hub.go:27-59` | `New`/`NewPersistent`（`local-default` 用 2s 新鲜度预算） |
| `fleet/worldhub/hub.go:61-116` | `Ingest`（先 Clone 候选、持久化成功才切换；失败即要求重启） |
| `fleet/worldhub/hub.go:127-167` | `Subscribe` 与 `ErrResyncRequired` |
| `core/worldmodel/reader.go:8-15` | `ErrResyncRequired`、`Reader` 边界 |

**A 部分 · 语义地图、身份与认证**

| 文件:行 | 内容 |
| --- | --- |
| `sim/mujoco/tangying_sim/tools.py:76-110` | `ResolveTargetsTool`：`OBJECT_NOT_FOUND`/`DESTINATION_NOT_FOUND`/`TARGET_AMBIGUOUS`；空观测是合法成功 |
| `robot/.../physical_attributes.py:24-52` | 模块 docstring（为什么属性是数据不是常量）、材质词汇表 `:33`、数值属性范围、放置半高表 `:45` |
| `robot/.../physical_attributes.py:57-104` | `PhysicalProfile` `:57`、`GraspBudget` `:71`、`as_dict()` |
| `sim/mujoco/tangying_sim/semantic_services.py:165-245` | `_recall`：schema/`mapId`/`calibrationRevision` 身份、重算年龄、拒未来时间戳、每类上限 8 |
| `sim/mujoco/tangying_sim/semantic_services.py:175-201` | **自我指涉缺陷的完整说明**（为什么 `mapRevision` 不能是身份字段） |
| `sim/mujoco/tangying_sim/semantic_services.py:275-300` | `_active_map`、`_validated_transform` |
| `edge/robotclient/recall_goal.go:14-27` | `RecallGoalMaxAgeMS = 15*60*1000`、环境变量名 |
| `edge/robotclient/recall_goal.go:29-46` | `RecallGoalMaxAge`（非法值一律回退默认；上限 7 天） |
| `edge/robotclient/recall_goal.go:71-147` | `recalledGoal`（身份校验→年龄→`vantagePose`→`withinWorkspace`） |
| `edge/robotclient/recall_goal.go:149-163` | `recallRecord`（`goalSource`/`recallAgeMs`） |
| `cmd/local-agent/main.go:255-262` | `WithoutRecallGoals`（实验开关）与 `RecallGoalMaxAgeMS` 的接线 |
| `robot/.../grid_navigation.py:105-125` | 净空判据与不确定带精确复算 |
| `robot/.../grid_navigation.py:137-172` | `LOCALIZATION_NOT_CLEAR` / `GOAL_NOT_CLEAR` / `NO_KNOWN_PATH` |
| `robot/.../tool_layer.py:190-280` | 错误码 → (`ToolError`, `RecoveryClass`) 映射表；"87 个码缺失"的说明 |
| `robot/.../robot_workflow.py:600-632` | `conflicts()`：只读比对、`conflictFraction`、`suggestsRescan >= 0.02` |
| `robot/.../robot_workflow.py:1338` | `active_map = {mapId, mapRevision=manifest hash, calibrationRevision}` |
| `robot/.../robot_workflow.py:1360-1405` | `_restore_active` 三态与 `active_map_error` |
| `sim/mujoco/tangying_sim/rgbd_runtime.py:475` | `_verify_relation(..., "PLACEMENT_NOT_OBSERVED")` |
| `sim/mujoco/tangying_sim/rgbd_runtime.py:941-998` | `_certify_semantic_goals`（环搜索 6×0.1 m×8 方向、朝向不变、`goalAdjustments`） |
| `sim/mujoco/tests/test_rgbd_runtime.py:1148-1180` | `PLACEMENT_NOT_OBSERVED` 与 `GOAL_NOT_CLEAR` 的回归测试注释 |
| `robot/.../room_segmentation.py` | 房间自动划分（门宽复合阈值；越界起点吸附） |
| `robot/.../semantic_workspaces.py` | 四种表达方式的构建器（`build_seeded` 报 `unavailable`/`degraded`） |
| `core/closedloop/closedloop.go:60-230` | 失败分类表；`PLACEMENT_NOT_OBSERVED` ∈ `UNKNOWN_OUTCOME`（`:101`） |
| `core/closedloop/closedloop.go:187-190` | `GOAL_NOT_CLEAR`/`LOCALIZATION_NOT_CLEAR` ∈ `PERCEPTION` 的理由 |
| `core/skills/manifest.go:20-76` | `SkillManifest`、`MutatesWorld`、`Validate` 的两条矛盾检查 |
| `skills/manipulation/plugin.go:17-64` | `Catalog()`：只读/物理技能、`navigation.pre_position` 的动机注释 |
| `skills/manipulation/plugin.go:68-96` | `Plan` 的 `step`/`physicalStep` 构造与 `MobilePreamble` |
| `tasks/grounded.go`、`tasks/service.go:536-556` | `worldFor`：GVF 与遥测两条世界来源 |

**B 部分 · 编排**

| 文件:行 | 内容 |
| --- | --- |
| `agent/agent.go:25-51` | `ProviderDeterministic`/`ProviderOpenAI`、`NewParser` 的四条件 |
| `agent/agent.go:53-92` | **三条规则的实现**：确定性优先（`:57-62`）、模型兜底（`:77-83`）、原错误不被替换（`:84-91`） |
| `agent/agent.go:114-176` | `llmPlanner.Plan`（tool_calls 优先，content 兜底） |
| `agent/agent.go:223-286` | 系统提示与三个工具 schema（`navigate_route`/`pick_and_place`/`fetch`） |
| `agent/agent.go:369-431` | `decodeStrict`（`DisallowUnknownFields` + 尾随对象检查）、`normalizeIntent` |
| `agent/intent/parser.go` | 确定性语法（动作/物体/起点/终点分别解析；否定/条件/多物体要求澄清） |
| `orchestration/types.go:1-8` | 包头：确定性是 fail-safe 基线，安全字段绝不信模型 |
| `orchestration/types.go:15-32` | `SourceDeterministic`/`SourceLLM`/`SourceConsensus`、`Bundle`、`LLMGenerated` |
| `orchestration/types.go:34-51` | `Planner` 接口（世界按调用传）；`DeterministicPlanner` 返回空 Bundle 的理由 |
| `orchestration/world.go:12-27` | **"隔着墙找杯子"的完整动机注释** |
| `orchestration/world.go:28-54` | `World`、`ObjectPlace`、`Known()` |
| `orchestration/world.go:76-139` | `Describe()`：写成带理由的约束，而不是字段 |
| `orchestration/world.go:141-163` | `WorldFrom`（读不出来就留空，不猜） |
| `orchestration/world.go:165-219` | `roomOf` + `roomMatchRadiusM = 4.0` |
| `orchestration/world.go:221-248` | `placesOf`（`semantic_objects[].workArea`，按类别@房间去重） |
| `orchestration/llm.go:22-58` | `Config`、`New`（配置不全 → `DeterministicPlanner`）、采样数夹在 1..5 |
| `orchestration/llm.go:74-133` | `Plan`：agentcontext 投影、采样循环、`len(candidates)==0` 的确定性回退（`:117-123`） |
| `orchestration/llm.go:135-176` | `systemPrompt`：技能目录 + 意图 + 8 条规则 + 7 步示例（含 `@object`/`@destination`） |
| `orchestration/llm.go:250-302` | `parseBundle`、`validateBundle`（技能名/必需参数/至少一个副作用技能） |
| `orchestration/llm.go:304-330` | `hashBundle`（sha256(plans)）、`mostFrequent` 自一致性投票 |
| `orchestration/metrics.go:16-32` | `Metrics` 字段 |
| `orchestration/metrics.go:46-50` | `LLMFallbackTasks` = deterministic 且 `Attempts>0` |
| `orchestration/metrics.go:85-87` | `OrchestrationScore = 成功率×60 + 候选率×25 + LLM 计划率×15` |
| `orchestration/eval/eval.go:1-32` | 评测包的存在理由与"它故意不做的事" |
| `orchestration/eval/eval.go:36-100` | `Case`/`Outcome`/`Report`（拒绝是独立维度） |
| `orchestration/eval/cases.go:1-25` | 用例选取规则（只按"新的失败原因"增长） |
| `orchestration/eval/cases.go:63-73` | `navigate-kitchen`：断言目标不断言实现的自证记录 |
| `orchestration/eval/cases.go:93-106` | `multiple-objects` 保留为失败的产品决定；`:107-110` `place-table`（能力边界而非解析边界） |
| `tasks/service.go:31-45` | `Task`：`Approved`/`ApprovedBy`/`ApprovedAt` 与"谁批的"为什么要有答案 |
| `tasks/service.go:140-152` | `NewService`：无 planner 时兜底 `DeterministicPlanner` |
| `tasks/service.go:168-211` | `Create` 全流程（解析→世界→规划→落库） |
| `tasks/service.go:177-184` | 规划报错 → `Bundle{Source: deterministic, Rejections: [err]}` |
| `tasks/service.go:254-256` | 修订对着**现在的**世界规划 |
| `tasks/service.go:428-459` | `buildRevisionSteps`：`ResourceID` + `RequiredPostcondition` + `SemanticFingerprint` |
| `tasks/service.go:536-556` | `worldFor`（`TANGYING_GVF_ENABLED` 分支） |
| `tasks/revision.go:36-58` | `StepStatus` 七态、`RevisionStep`（含 `RequiredPostcondition`） |
| `core/taskgraph/model.go:15-67` | `TaskPlan`/`SkillStep`/`Budget`/`StopPolicy`/`ValidateShape` |
| `core/taskgraph/state.go:3-43` | 17 个 `TaskState` 与合法迁移表 |
| `core/taskgraph/runtime.go:10-60` | `NodeStatus`、`GraphRuntime`、"为什么与 compiler 分开" |
| `core/compiler/compiler.go:9-55` | `Node`/`ExecutionGraph`/`Compile`（Kahn + 环检测） |
| `core/agentcontext/context.go:16-66` | `Version`/`RendererVersion`、`Record`、`Step`、`Document`（含 `Stage`） |
| `core/agentcontext/context.go:71-83` | `Mode()`（`TANGYING_AGENT_CONTEXT`，缺省即 legacy） |
| `core/agentcontext/routing.go:12-76` | `StageForRole`、`stage-policy.json` 加载与自校验 |
| `core/agentcontext/projection.go:3-40` | `Projection`（记录实际渲染器与哈希）、`Project` |
| `core/agentcontext/stage-policy.json` | 八环节格式选择（planning=`nl_decision`、tool_result=`json`…）+ 门禁结果 |
| `edge/agent/runner.go:228` | `RunControlled` 起始（先 `CheckRecovery`、加载闭包上下文） |
| `edge/agent/runner.go:241` | `grounder.Ground(ctx, intent)`——**在计划之前** |
| `edge/agent/runner.go:266` | `planForIntent(...)`——循环依赖的另一半 |
| `edge/agent/runner.go:765` | `evidence.Freshness` 由 `EvidenceFreshness(now)` 真算 |
| `edge/agent/runner.go:782-880` | `planForIntent`：把计划步骤绑到 grounded 实体（`:846-861`）、重建安全字段（`:862-874`） |
| `README.md:148-176` | 已验证/未验证能力表 + 12 步链路表 |
| `docs/architecture/orchestration.md:1-39` | 编排边界、指标口径、提升方法 |
| `docs/architecture/orchestration-post-training.md:11-63` | 自训的收益/代价矩阵、拒绝比规划更难、四条基线数字 |
| `docs/architecture/orchestration-post-training.md:66-110` | 评测体系四条设计取舍 |
| `docs/architecture/orchestration-post-training.md:145-201` | RL 奖励三规则、六种退化与防线、下一步顺序 |
| `docs/architecture/agent-events.md:7-27` | topic 词表与两种匹配形式 |
| `docs/architecture/agent-events.md:156-177` | OpsAgent 规则表、`ANOMALY_UNVERIFIED_MUTATION` 禁止自动重试、回放 |
| `docs/architecture/lifecycle-objects.md:220-246` | 十个对象的"崩溃后知不知道世界"矩阵 |
| `docs/architecture/lifecycle-objects.md:481-540` | 四条分界线（权威/派生、意图/事实、可逆/不可逆、时间尺度） |
| `docs/development/decision-context-fields.md:1-5,39-51,139-155` | 生产者字段字典；`execution_state` 的 UNKNOWN 禁止重发；工具的 `preconditions`/`postconditions` |
| `docs/experiments/2026-09-21-agent-context-evaluation.md:488-499` | 第三轮研究问题与两模型确认结果 |
| 同上 `:505-523` | 因果路径公式与四因素定义 |
| 同上 `:525-565` | 六个命题（含命题 5 的 4,615 族估算、命题 6 的价值误差界） |
| 同上 `:567-589` | 数据规模、12,484 请求 / 31,716,142 tokens、统计方法 |
| 同上 `:591-622` | **四因素主效应表**、masked 双生表 |
| 同上 `:624-654` | **八环节完整信息矩阵（规划全 0%）** |
| 同上 `:656-686` | 逐环节选择、确认差值与 Holm p、blocked_stages |
| 同上 `:688-752` | 追加实验 A（层级结构）与 B（决策契约）的全部四臂结果 |
| `docs/experiments/2026-09-21-grounded-verification.md:6-8,36-64` | GVF 九组主表：错误接受 58.57%→13.81%、准确率 41.43%→64.76%、A5 74.29%、4,596,735 tokens |
| 同上 `:79-127` | 配对 t 检验全表与混淆矩阵 |
| 同上 `:191-203` | 支持/不支持的假设；GVF 29 次错误接受的来源（轮式里程计） |
| `docs/development/2026-09-15-placement-verification-and-certified-goals.md:1-82` | `PLACEMENT_NOT_OBSERVED` 的定位、两处修复、实测表 |
| `docs/experiments/2026-09-14-semantic-object-layer-upgrade.md:53-109` | 物体层对比数字（0/5→5/5、7,271 B、606 B/条、1.43 ms 等） |
| `docs/experiments/2026-09-15-survey-lookback-and-pre-position.md:59-104` | 15 分钟新鲜度成为部署参数；回望 13→6 未知格；`NAV_ROTATION_LIMIT` 实测 |
| `docs/experiments/2026-09-20-semantic-map-representations-and-nl-navigation.md` | 四种地图表达对比、门宽阈值、中文动作前缀缺陷、§6.4 现场闭环 8/8、§6.4.4 `seeded` 在 36% 未知地图上失效 |
| `docs/development/2026-09-15-extensibility-review-tools-and-navigation.md:1-132` | 能力由五处定义、四个扩展点、写死的地方、地图冲突只读报告 |
| `docs/development/dense-map-operations.md:71-100` | 不可导出数据库的判据；60 FPS 数字作废的说明 |
| `docs/development/2026-09-21-map-visibility-and-inventory.md:1-110` | 三处"只找一层"假设；活跃地图被删时说什么 |
| `docs/production/configuration-and-security.md:45-49` | `AGENT_PROVIDER` 的语义与"不表示所有请求都经过模型" |
| `docs/development/natural-language-evaluation.md:1-27` | 13/13 的实际口径；修复前 3/12 → 修复后 12/12 |
| `docs/development/natural-language-evaluation.md:97-199` | 上下文评测、八环节策略、门禁与训练导出 |
| `git log -1 6f87aca04` | "编排层拿到世界状态"的完整正文（含未修掉的循环依赖） |
| `git log -1 505af6577` | 评测体系基线数字（6/10 vs 9/10）与设计文档 |
| `git log -1 d531a9116` | grounding 根因定位 + "不是根因"的诚实标注 |
| `git log -1 55ae5a5ca` | grounding 表拒收场景内真实物体 |
