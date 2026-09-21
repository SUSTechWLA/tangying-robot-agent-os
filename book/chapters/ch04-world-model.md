# 第 4 章 世界模型：当「真相」只能被观测

> **本章的核心命题**
>
> 在通用软件里，真相是**读出来**的：`stat`、`read`、`SELECT`。
> 在机器人系统里，真相**只能被观测**——而观测是昂贵的、会过期的、有时根本不可得的。
>
> 这两句话的差别，决定了一个世界模型需要哪些字段。

---

## 4.1 一条不能省的字段：`EvidenceRef`

先看世界模型里最基础的结构。

世界状态里**每一条事实**都带一个证据指针（`core/worldmodel/types.go:20-27`）：

```go
// EvidenceRef identifies the observation that produced a fact.
type EvidenceRef struct {
	ObservationID     string
	SourceID          string
	SourceSequence    uint64
	ObservedAt        time.Time
	FrameID           string
	TransformRevision string
}
```

**注意这条设计：这不是可选的元数据，是每条事实的组成部分。**

也就是说，世界模型里不存在"一个物体在 `(2.464, 3.342, 0.79)`"这样的裸事实。存在的只有：

```
物体 ceramic-mug 在 (2.464, 3.342, 0.79)，
  这个说法来自观测 obs-8f3a…，
  由源 head_camera 的第 4821 号采集产生，
  采集时间 2026-09-15T10:23:41.208Z，
  坐标系 map，变换版本 cal-7。
```

**为什么必须这样？** 因为后面所有需要"相信"这个事实的地方，都要检查它是不是够新鲜、是不是来自正确的源、坐标系是不是当前版本。

在这种设计下，一个初学者常犯的错误暴露出来了：**把世界模型当成一个数据库。**

数据库的语义是"存了就是真的，读到就是当时的真值"。世界模型的语义是"存了只是'某一次观测这么说过'，它可能已经不对了"。

### 六道闸：不是什么都能进世界模型

投影器 `Projector.Apply` 是一个**纯函数式 reducer**：吃 `observation.Envelope`，吐 `Snapshot`。它的入口有六道闸（`core/worldmodel/projector.go:66-95`）：

| # | 闸 | 判定 | 结果 |
| --- | --- | --- | --- |
| 1 | schema 校验 | `SchemaVersion` 必须是 `world.observation.v1` | 拒绝 |
| 2 | 世界身份 | `WorldID` 不符 | `ErrWorldMismatch` |
| 3 | 观测去重 | `ObservationID` 已见过 | **静默丢弃** |
| 4 | 源序列 | `SourceSequence <= 高水位`（倒退或重放） | **静默丢弃** |
| 5 | 坐标系 | `TransformRevision` 与已接受源冲突 | `ErrTransformRevisionConflict` |
| 6 | 静态实体 | 静态实体且无变化 | 只推进私有高水位，**不推进 `Revision`** |

第 6 道闸值得单独说：**只有真正改变了状态的事件才让 `revision++`。**

所以 `Snapshot.Revision` 的语义是：

> **"世界变过几次"，不是"收到过几条消息"。**

这个区分在跨机协调里至关重要——第 8 章会看到，Harness 用 `WORLD_REVISION_NOT_ADVANCED` 来拒绝"世界根本没变，但你说你完成了"这种上报。

### 三值逻辑：`UNKNOWN` 不是 `FALSE`

世界模型最核心的设计决定，写在一个只有七行的注释里（`core/worldmodel/predicate.go:5-11`）：

世界谓词的返回值不是布尔，是三值：**`TRUE` / `FALSE` / `UNKNOWN`**，而且每条结果**必须带 `Reason`**。

`UNKNOWN` 的触发条件写死在 `evidenceStale()`（`predicate.go:138-140`）：

```
UNKNOWN 当且仅当：
  - 观测时刻为零（时间戳缺失）
  - maxAge <= 0（调用方没有给出有效窗口）
  - 已超龄（证据过期）
```

**"证据不在"和"事实不成立"是两种答案，永远不会被混成一个 `false`。**

### 这个区别为什么是生死攸关的

用一个具体例子说明。判断"陶瓷杯在不在收纳盘里"：

```go
EntityInside("ceramic-mug", "kitchen-tray", 5*time.Second)
```

| 返回 | 含义 | 正确的下一步 |
| --- | --- | --- |
| `TRUE` + 新鲜证据 | 有证据表明在里面 | 记完成 |
| `FALSE`（`RELATION_MISMATCH`） | **有新鲜证据**证明不在里面 | 重新核验 + 重新规划 |
| `UNKNOWN` | **系统不知道杯子在哪**——它不在快照里、或证据超龄了 | **什么都别做，先去观测** |

现在问一个重要问题：如果返回 `UNKNOWN`，能不能当作"没放好"去**重放放置动作**？

**绝对不能。** 因为放置动作已经跑过一次了，重放等于把一个**可能已经成功**的物理动作再做一遍——第二次释放会把物体推离目的地。

这就是 `PLACEMENT_NOT_OBSERVED` 被分类为 `UNKNOWN_OUTCOME`（禁止自动重试）而不是 `PERCEPTION`（可重试）的全部理由（`core/closedloop/closedloop.go:91-101`）：

> 动作跑了，但预期关系从未被观测到。这是**真正未知**的情形，不是感知失败：夹爪闭上了、放置动作完成了，而它实际达成了什么并未被确立。
> 把它当作感知失败会允许重试一个**第一次可能已经成功**的物理动作——对放置来说，**物体可能已经在目的地了**。

**如果世界模型只有布尔值，这个区分就无法表达，`PLACEMENT_NOT_OBSERVED` 就只能被猜成两种之一——而猜错的代价是一次多余的物理动作。**

还有一个更微妙的设计：`All()` 的合并规则是**非对称**的（`predicate.go:119-136`）：

```
任一子谓词为 FALSE  → 立刻短路返回 FALSE     # 确凿的反例优先
全部非假            → 把状态降级为 UNKNOWN   # 没有反例但也没全通过
```

**确凿的"不成立"永远优先于"不确定"。** 这是一个 Kleene 三值逻辑的工程实现，而且方向是对的：一个反例就足以否定一切，而"没找到反例"什么都不能证明。

---

## 4.2 世界状态由什么组成：四张 map 和一个不要脸的缺失

`Snapshot` 的结构（`core/worldmodel/types.go:84-96`）：

```
Snapshot {
  SchemaVersion = "world.snapshot.v1"
  WorldID
  Revision        uint64      # 单调递增的接受计数（真变更才加）
  EventCursor     string      # = 最新被接受的 ObservationID
  ProjectedAt
  Robots          map[robotID]RobotState
  Entities        map[entityID]EntityState
  Resources       map[resourceID]ResourceState
  Sources         map[sourceID]SourceState
  ActiveTasks
  Health
}
```

### 身份 = map 的 key，而且值里再存一份同名 ID

`EntityState.EntityID`、`RobotState.RobotID`、`ResourceState.ResourceID`、`SourceState.SourceID` 都被 `validateCheckpoint` 要求**与 map key 相等**（`checkpoint.go:137,142,147`）。

这是**刻意的冗余**。理由是一个很实际的攻击/故障场景：

> 一个被序列化过、又被人改过的 checkpoint 必须**自证身份**。

如果一个 checkpoint 文件里的 `entities["cup-1"].EntityID == "cup-2"`，加载时必须失败，而不是"以 key 为准"或"以值为准"。**数据结构的冗余在这里是一种校验和。**

### 「谁抓着什么」：`Held` 放的是 ID，不是布尔

```go
// core/worldmodel/types.go:33
Held string    // 实体的 ID
```

判据（`predicate.go:74-88`）：

```go
RobotHeld(robotID, entityID, maxAge)   // 失败码 HELD_MISMATCH
```

放置判据是对称的（`predicate.go:42-56`）：

```go
EntityInside(entityID, zoneID, maxAge)  // 查 Relations["inside"]
```

**`Held` 是「观测」，不是「授权」。** 这一条极其重要，第 8 章会展开：遥测说 robot-1 抓着红方块，这是**机器人报告的**；而"红方块归 robot-1 处置"是**租约授予的**。前者是事实，后者是权力。混起来就会出问题。

### 缺失的一格：世界模型里没有「房间」

这是一个值得写进书里的设计事实：

**`core/worldmodel` 里没有 `Room` 这个类型。**

实体只有：

- `Pose []float64`（地图系，米）
- `Relations["inside"] = <zoneID>`（`predicate.go:51`）

那"机器人在客厅"这句话是怎么来的？答案是：

> **"房间"是导航层给一个已委托位姿起的名字。**

具体在编排层（`orchestration/world.go`）：

| 方法 | 做什么 |
| --- | --- |
| `roomOf()`（`:172-215`） | 把机器人的 `base_pose` 与 `semantic_navigation.goals` 里的登记点**逐个比距离取最近** |
| 阈值 `roomMatchRadiusM = 4.0`（`:219`） | 超过 4 米就**拒绝认领**，返回空串当"未知" |
| 物体的房间（`:222-248`） | 来自 `semantic_objects[].workArea`，按 `lower(category)@room` 去重 |

**为什么房间不是世界模型的类型？** 因为"房间"不是传感器能测到的东西——它是**人给区域起的名字**，来自地图的委托（commissioning）数据。把它当成世界事实会撒谎；把它当成"给位姿起的名字"才是诚实的。

对应地，读到空串时的行为是**不猜**（`world.go:206-214`）：

> 一台被放进一个它并不在的房间的机器人，会产出一份**自信的错误计划**——这比一份承认自己缺少所需导航信息的计划更糟。

---

## 4.3 语义物体层：从感知到「我见过这个杯子」

### 物体层是怎么产生的

建图期间以 **1 Hz** 复用 `observe_scene` 那条普通观测路径向驱动索取实体（**不新增采集工作**），按地图锚点折算累积；发布地图时**重新锚定**到最终锚点（与点云同一锚点）。

三条规则让它成为**证据**而不是猜测：

| # | 规则 | 实现 |
| --- | --- | --- |
| 1 | **只有观测进来的才算** | 没有观测就没有条目 |
| 2 | **每次目击带时间** | `lastSeenUnixMs` / `ageMs` / `sightings` |
| 3 | **关联有上限** | 同类别、属性不矛盾、**0.12 m 门限**内才归为同一实例 |

规则 3 的后半句是关键设计：

> **被移动的物体变成新实例，而不是瞬移的旧实例。**

这个选择很漂亮。如果关联门限无限大，一个被搬到隔壁房间的杯子会被系统认为"瞬移了"——世界模型会显示一个不可能的运动轨迹。设一个 0.12 m 的门限，就意味着"这个杯子动了 3 米"这个事实**必须由两次独立的目击来证明**，而不是由一次贪心的关联来伪造。

层规模上限 512 条。续建继承物体层但**保留各自时间戳**。

### 一个必须纠正的误解：`resolve_targets` 不做名词解析

**这是本章最容易讲错的一处，也是绝大多数人第一次读这套系统时会搞错的点。**

直觉上你会以为数据流是：

```
"陶瓷杯" → resolve_targets → 实体 ID
```

**不是。** 生产路径上的真实数据流是：

```
"陶瓷杯"
   ↓
grounder（委托目录 + 测量层）        ← 实体 ID 在这里决定
   ↓
实体 ID 注入计划参数                 ← edge/agent/runner.go:846-855
   ↓
resolve_targets  只做「确认」
```

也就是说，计划里的 `resolve_targets` 参数是 **`@object` / `@destination` 占位符**（`orchestration/llm.go:152,166-171`），运行时在 `edge/agent/runner.go:846-855` 把它**重新绑定**为 grounding 出来的实体 ID 与置信度。

**`resolve_targets` 本身做什么？** 原型实现在 `sim/mujoco/tangying_sim/tools.py:76-110`：

| 输入 | 行为 | 失败码 |
| --- | --- | --- |
| 给了 `objectId` / `destinationId` | **只确认身份** | `OBJECT_NOT_FOUND` / `DESTINATION_NOT_FOUND` |
| 给了 `category` + `color` + `relation` | 做匹配 | 命中多于一个 → `TARGET_AMBIGUOUS`「found N targets」 |
| **什么都不给** | 返回实体清单 | **这是合法的成功** |

最后一行值得注意：空观测是合法成功。它的测试名字就写着这条约定：

```python
test_resolve_targets_returns_both_grounded_ids_and_allows_empty_observation
```

**"找不到"发生在前一段（grounding），不在 `resolve_targets` 里。** 理解这一点，才能理解下一节的三个根因。

### 地点名词：同一个词在三层得到三个答案

`docs/experiments/2026-09-20-semantic-map-representations-and-nl-navigation.md` 记录了一个实测结果，很适合教学。

地点名词的解析链是：

```
normalize_location_name（剥英文冠词）
   → 别名表
   → 房间名
   → 地图位姿
```

但系统里**有三张不一致的别名表**：

| 层 | 条目数 | 归一化方式 |
| --- | --- | --- |
| `assets/home_locations.json` | **19** | NFKC + casefold + 去冠词 |
| 地图包 `semantics.json` | **7** | 仅 casefold 精确匹配 |
| Go 侧 `semantic.navigation.v1` | **7** | **大小写敏感** |

后果：

| 输入 | 第 1 层 | 第 2 层 | 第 3 层 |
| --- | --- | --- | --- |
| `"the kitchen"` | ✅ | ❌ | ❌ |
| `"Kitchen"` | ✅ | ✅ | ❌ |
| `"厨房"` | ✅ | ✅ | ✅ |

**同一句话在三层得到三个答案。** 这不是一个抽象的设计问题——它意味着"能听懂哪句话"取决于请求走到了哪一层。

还有一个已修的真实缺陷值得记录：

```
'厨房'    → kitchen      ✅
'去厨房'  → UNRESOLVED   ❌
```

原因：生产代码只剥**英文**冠词，不认识中文动作前缀。而失败的表现在是 `NOT_FOUND` **并列出已知地点**——读起来像"这个房间不存在"。

修法是加一张**封闭的**中文动作前缀表、**最长优先**、并且要求 `len(text) > len(prefix)`。

最后一个约束很讲究：**否则"回民街"会被吃掉"回"字。**

---

## 4.4 一个教科书级的自我指涉缺陷

这一节讲的是整个项目里最隐蔽的一个 bug，我认为它值得出现在任何一本讲系统设计的书里。

### 症状

一台**已经扫过整个房间**的机器人，grounding 稳定返回 **0 个物体**。

检测器正常、地图正常、物体层文件存在且内容正确——但系统就是"看不见"任何东西。

### 根因

物体层 `objects.json` 是**地图自己的产物**。而地图的身份 `mapRevision` 是**manifest 的内容哈希**（`robot/.../robot_workflow.py:1338`、`map_catalog.py:100`）。

于是：

```
objects.json 的字节
   ↓ 是
manifest 哈希的一部分
   ↓ 而 mapRevision = manifest 哈希
所以：
objects.json 不可能包含自己的 mapRevision
```

**一份文档不可能包含它自己的哈希。** 这是数学事实。

而 `_recall()` 曾经要求 `objects.json` 里的 `mapRevision` 等于在用地图的 `mapRevision`。

结果：**每一份被发布的层都被拒识。**

### 它为什么活了这么久

代码把这个错误连同它为什么活下来写得非常清楚（`sim/mujoco/tangying_sim/semantic_services.py:175-201`）：

> 这个错误之所以活下来，是因为单元测试**用自己的 helper 伪造了带匹配 `mapRevision` 的文档**——
> **在被检查的地方字段总是存在，在被生产的地方它从不出现。**

**这是全书最有教学价值的一段失败分析。**

它揭示了一类**通用**的测试失效模式：

> 当测试用手工构造的 fixture 替换真实生产者时，
> 测试验证的是"消费者能处理这个 fixture"，
> **而不是"消费者能处理真实生产者产出的东西"**。

而且这类 bug 有极强的不对称性：**它在测试里永远绿，在生产里永远坏。**

### 修法

身份改由**文档内部真正能有的字段**（`mapId` + `calibrationRevision`）+ **"读的人是从这张地图目录里拿到的"这一事实**共同建立。

注意这个修法的形状：**它不再要求文档自证身份，而是用"获取路径"作为身份的一部分。** 这是一个重要的思路转换——当一个字段在原理上不可能存在时，正确的做法不是"放松校验"，而是**换一个能建立的证据链**。

### 这个缺陷属于哪一类

现在把它放回上一章的 grounding 三条根因里：

| 根因 | 层 | 症状 | 为什么难找 |
| --- | --- | --- | --- |
| ① | **规划层** | 在客厅找厨房的杯子 | "长得像感知问题，实际是规划问题" |
| ② | **数据层** | 物体层被自己的哈希拒识 | **单元测试用伪造的 fixture 掩盖了它** |
| ③ | **绑定层** | grounding 表只为 `cup/red` 作答 | "一条语法接受了、而 grounding 表拒绝的请求，是一个半支持的特性" |

它们有**同一个表现形式**：`objects = 0`。

**这就是为什么这一章必须单独讲：在机器人系统里，一个错误的计划会以所有下游层都正确的方式失败。**

---

## 4.5 「不知道的地方不进去」：认证目的地

### `GOAL_NOT_CLEAR` 的完整判定

三个拒绝码对应三个物理事实（`robot/gateway/tangying_robot_gateway/grid_navigation.py:137-172`）：

```python
if not sources or not point_clear(start, radius):
    raise ServiceError("LOCALIZATION_NOT_CLEAR",
        "当前底盘位置没有足够已知通行空间，请重新定位或补扫周围。")
if not targets or not point_clear(goal, radius):
    raise ServiceError("GOAL_NOT_CLEAR",
        "目标工作区尚未扫描，或底盘安全间距不足，请补扫或选择其他位置。")
...
if target not in parent:
    raise ServiceError("NO_KNOWN_PATH",
        "当前地图没有连接到目标工作区的通路，请补扫连接区域。")
```

**"不进去"这条原则实现在通行判据里**（`:112`）：

```python
clearance = distance_transform_edt(np.pad(grid["cells"] == 0, 1, constant_values=False))
```

关键点：**未知格（负数）不是自由格**，所以未扫描区域直接不可通行。

```python
free = clearance > radius + resolution/√2
```

而且在**不确定带**上用真实格子盒精确复算（`:117-121`）。注释写明了为什么：EDT 的半对角界会把一条 **0.325 m 的窄通道**误判为不可通行。

规划只在**认证净空位姿之间**做，且每一条边都要求**整条扫掠圆盘**无碰（`:158`）。

### 为什么"冷启动后去厨房会失败"是刻意设计

README 里有一句很容易被误读的话：

> 全局代价地图的 `allow_unknown=false`，Nav2 拒绝规划穿过尚未建图的区域。
> 所以冷启动后只能导航到已覆盖范围（例如客厅），去厨房会以 `NAV_FAILED NAV2_ACTION_ENDED` 结束——
> **这不是缺陷，而是"不知道的地方不进去"**。

**一个会"因为地图不全而拒绝"的机器人，比一个"勇敢地开进未知区域"的机器人更安全。**

因为地图不全意味着两件事：① 可能有障碍物；② **可能没有地板**（楼梯、阳台）。第二种情况下"勇敢"的代价是摔下去。

**教学要点**：初学者看到"能连上但导航失败"的第一反应是"改成允许未知区域"。这是一个危险的直觉。正确的反应是问："**凭什么认为那里能走？**"——如果没有答案，拒绝就是正确答案。

### 「不知道的地方不进去」的另一半：把目标吸附到认证位姿

拒绝只是一半。另一半是**在能去的地方尽力去**。

`_certify_semantic_goals`（`rgbd_runtime.py:941-998`）：

| 步骤 | 行为 |
| --- | --- |
| 起点 | 房间目标是**从登记点来的单个点**——新地图可能让这个点不可规划（实测就是它导致返程腿 `GOAL_NOT_CLEAR`） |
| 调整 | 若 `pose_is_clear` 不通过，按**环搜索** `ring=1..6`（每环 0.1 m）× 8 个方向，找**同房间内**最近的认证净空位姿，**朝向保持不变** |
| 找不到 | **如实报** `{"adjusted": False, "reason": "no certified pose nearby"}` ——**绝不把目标悄悄挪到地图从未批准的地方** |
| 可审计 | 调整量随目标一起发布（`goalAdjustments`：`adjusted` / `offsetM` / `commissioned`） |

最后一行是要害：

> 于是**计划下发的位姿、`verify_arrival` 核验的位姿、读者看到的位姿是同一个**。

**如果调整量不外露，"目标被挪了 30 厘米"这件事就消失了**——而下游的 `verify_arrival` 会用一个不同的位姿判断到达。这种"静默修正"是最难查的一类问题。

### 真实数字

这张地图上 **3 个登记点里有 2 个已不可用**：

| 登记点 | 邻域 0.32 m 内非自由格数 |
| --- | --- |
| `kitchen (2.05, 3.00)` | **12** |
| `living_room (0.0, −1.25)` | **11** |
| `home_corridor (0.0, 1.85)` | **0** |

客厅返程点 0.32 m 邻域曾有 **13 个未知格**，勘测加一步"回望"后降到 **6 个**——**剩下的正是底盘自己脚下那块，任何前向视角都看不到。**

**这一组数字说明了一件很朴素的事**：委托（commissioning）时标注的点位，会在重新建图后失效。**地图不是一次标定就永久有效的资产。**

### 一个"安全检查变成陷阱"的真实缺陷

这一节是本章最好的教学素材之一。

`_swept_model_collision` 从**当前位姿**开始采样：

```python
linspace(0, 1, n)   # 含 fraction=0，即当前位置
```

后果：底盘一旦落进包络半径内，"**你离墙太近**"对**每一个**候选脉冲都成立——**包括那个把它开离墙的脉冲**。

实测数字：

| 量 | 值 |
| --- | --- |
| 离墙距离 | **0.373 m** |
| 包络半径 | **0.375 m** |
| 差值 | **2 mm** |

**差 2 毫米，机器人这个回合剩下的时间里再也动不了。**

文档对它的定评值得原文引用：

> **这不是安全属性，是陷阱。**

修法是**有意不对称**的：

| 从……出发 | 规则 |
| --- | --- |
| **合法位姿** | 任何越界都拒绝 |
| **非法位姿** | **只拒绝把它变得更糟的移动** |

修复后现场验证：向东（离墙）→ `NAV_REACHED`，随后向南 → `NAV_REACHED`。

**教学要点**：安全的正确方向不是"更严格"，而是"**在正确的地方严格**"。一个从非法状态出发的检查如果只允许"保持不动"，它就不是安全机制，是**锁死机制**。这个洞察可以推广到很多领域（限流、熔断、死锁恢复）。

---

## 4.6 地图身份、新鲜度与「回忆」

### 在地图的三元组

```
active_map = { mapId, mapRevision, calibrationRevision }
```

`_active_map()` 强制 `calibrationRevision` **必须等于当前标定**（`sim/mujoco/tangying_sim/semantic_services.py:275-283`）。

**为什么标定要进地图身份？** 因为地图的坐标系是由标定定义的外参决定的。**换了标定，同一份点云的含义就变了**——同一个"map"坐标系下的位置，在物理世界里指向不同的地方。

**这是机器人系统里一个通用原则：任何空间数据的身份，必须包含产生它的坐标系变换的版本。**

### 「地图过期」如何检测：一个只读的建议

`mapping.conflicts()`（`robot_workflow.py:600-632`）把最近采集的点与在用地图**逐格比对**：

| 输出 | 含义 |
| --- | --- |
| `occupiedInMapFreeInCloud` | 地图说有障碍，新点云说没有 |
| `freeInMapOccupiedInCloud` | 地图说能走，新点云说有东西 |
| `conflictFraction` | 冲突比例 |
| `suggestsRescan` | `conflictFraction >= 0.02` 时为 true |

返回值里写着一句设计声明：

> **`"A suggestion, never an action"`**

也就是说：**地图够不够旧到该重扫，是操作员的判断，证据摆在这里。**

而且它**只读，不改写在用地图**——测试断言地图数组**逐位不变**。要更新仍走 `mapping.start {baseMapId}` 续建。

理由文档写得很实在：

> **地图是已验收的导航依据**，静默变更会让"**昨天能走的路线今天为什么不能走**"无法解释。

**这也是一条可以推广的原则：一个被下游依赖的资产，它的变更必须是显式的、有版本的、可追溯的。**

文档同时如实写了缺口：

> **任务期间不会更新在用地图。** 沙发被挪动这类持久变化，任务期只能靠局部层躲开，**躲不开就失败**。

### 「回忆新鲜度」：一个变成部署参数的常量

真实参数名是 `TANGYING_RECALL_GOAL_MAX_AGE_MS`：

```go
// edge/robotclient/recall_goal.go:24,27
const RecallGoalMaxAgeMS  = 15 * 60 * 1000                        // 默认 15 分钟
const RecallGoalMaxAgeEnv = "TANGYING_RECALL_GOAL_MAX_AGE_MS"
```

解析规则（`:33-46`）有一句值得抄下来的注释：

> **取值非法（解析失败、`<=0`、或超过 7 天）一律回退到 15 分钟默认值。**
>
> 理由是：`"unset" and "typo" must not both mean "trust anything"`。

**"未设置"和"拼错了"不能都意味着"信任任何东西"。**

这是一个安全默认值的典范：**配置错误时的行为必须是收紧，不是放松。**

**为什么它从常量变成了部署参数**（`docs/experiments/2026-09-15-survey-lookback-and-pre-position.md:59-74`）：

它原本写死，导致两臂对照实验必须在制图后 **15 分钟内**跑完，否则回忆按设计回退登记点、实验根本测不到差异。而注释里给了两个**真实场景**：

| 场景 | 需要的窗口 |
| --- | --- |
| 一周建一次图的场地 | **大**窗口 |
| 每班前建图的场地 | **小**窗口 |

**同一份代码，两种正确的取值。** 这就是"它必须成为部署参数"的证明。

### `recalledGoal()` 的完整证据链

`recall_goal.go:71-147` 的判定顺序：

```
1. schema 必须是 semantic.recall.v1
2. frameId 必须是 map
3. mapId / mapRevision 必须等于在用地图
4. 条目按年龄升序，遇到第一条超龄就结束搜索        （:126-129）
5. 目标取 vantagePose（当时机器人站的地方），不取物体自身位置  （:130-136）
6. 每条目还要过 withinWorkspace
```

第 5 步的理由很实在：

> 因为**底盘开不到桌上的杯子**。

**"回到上次看到它的地方"是比"去物体的位置"更正确的目标**——因为机器人能到的是"能看见它的位置"，不是"它在的位置"。

而且有一条刻意的兼容语义（`:68-70`）：

| 情况 | 行为 |
| --- | --- |
| 没有回忆契约 | 用登记点 |
| 没有该类别条目 | 用登记点 |
| 只有过期目击 | 用登记点 |

**三者都不报错。** 这是工程上正确的选择：一个"没有历史记忆"的部署不应该因为缺少一份可选数据而完全不能导航。

最后，`recallRecord`（`:152-163`）把 `goalSource`（`commissioned` / `recalled`）与 `recallAgeMs` **随计划一起发布**：

> 好让读者不必猜这次导航目标从哪来。

### 一个真实的对照数字

同一个条目、目击年龄 = **15 min + 1 min**：

| 窗口 | 行为 |
| --- | --- |
| 默认 15 min | **拒绝**，回退登记点（`pose == nil`） |
| 24 h | **接受**，并如实上报 `recallAgeMs` |

报告同时如实记录了一个**未完成的断点**：

> 任务级两臂对照**仍未取得差异数据**，因为运行时没有把 `semantic_recall` 送到落地环节（`goalSource` 仍是 `commissioned`）。

**这是一个可复现的具体断点，不被当作已完成。** 在书里保留这种"未完成"的诚实标注很重要——它告诉学生，**一个机制"实现了"和"端到端生效了"是两件不同的事**。

---

## 4.7 可扩展性：把知识变成数据

### 设计一：物体物理属性成为数据

动机写在模块 docstring 里（`physical_attributes.py`）：

> 陶瓷杯、纸杯、玻璃花瓶在谁的目录里都不共享前缀，却需要不同待遇；而系统此前把知识写成运行时的字面量——放置高度是 `{"cup": 0.06, "bottle": 0.08}[category]`，抓取容差是类常量——于是"**新物体**"等于**改驱动**，"**未知物体**"抛 `KeyError` 而不是说"我没有这类物体的配置"。

具体做法，四条与实现一一对应：

| # | 设计 | 实现 |
| --- | --- | --- |
| 1 | **属性可选** | `material ∈ {rigid, soft, fragile, deformable, granular, unknown}`、可选 `mass_g`、`max_grip_force_n`，走现有 `attributes map[string]string` 通道，**协议不变**。未声明 = 与今天**逐位一致**（默认值就是被替换掉的那两个常量） |
| 2 | **未知是值，不是错误** | `unknown` 是合法值，表示"**看过但没识别出来**"（与"检测器根本没跑"不同） |
| 3 | **非法是错误，且可诊断** | 未声明的类别返回 `PLACEMENT_PROFILE_UNAVAILABLE` 并**列出已知类别** |
| 4 | **预算随证据发布** | `grasp_budget` 进状态——"**为什么抓得这么轻**"能从记录里回答，而不是去读代码 |

第 2 条特别值得讲："**未知是一个合法的值**"这个设计，让系统能区分：

| 状态 | 含义 | 正确的下一步 |
| --- | --- | --- |
| `material = "unknown"` | 看过这个物体，但没识别出材质 | 用保守默认值，或问人 |
| **没有 `material` 字段** | 检测器没跑 / 数据没采集到 | 先修感知 |

**"我不知道"和"我没看"是两件不同的事。** 这和世界模型的三值逻辑是同一条原则在另一层的应用。

### 同时如实记录的"还写死在哪"

- `rgbd_runtime.py:412`：`half_height = {"cup":0.06,"bottle":0.08}[entity.category]`
- `:346`：`e.category in {"cup","bottle"}`
- **后置条件本身是关系谓词**（3 帧稳定 `held_by:robot` / `inside:<dest>`）——**无法表达"抓稳且未压碎"**

最后一条是最本质的局限：**当前的后置条件语言表达能力有限**。它能说"在不在里面"，不能说"稳不稳"。这是一个开放问题，第 15 章会回到它。

### 设计二：一个能力由五处共同定义

`docs/development/2026-09-15-extensibility-review-tools-and-navigation.md` §一 量化了"加一个能力"到底是几处改动：

| # | 处 | 位置 |
| --- | --- | --- |
| 1 | 规范工具名 | Python `CANONICAL_TOOLS` + Go `canonicalTools`（**两侧必须一致，否则 profile 校验直接拒**） |
| 2 | 参数契约 | 两侧 schema |
| 3 | 安全分类 | `PHYSICAL_TOOLS` / `MUTATES_WORLD_TOOLS` |
| 4 | 技能清单 | `SkillManifest` |
| 5 | 本体声明 | `RobotProfile.tools` |

**这一节的价值在于它把"加一个能力"的成本从形容词变成了数字：五处。**

在架构评审里，这是最有用的信息之一——**"扩展性好不好"是一个无法回答的问题，"加一个能力要改几处"是可以回答的。**

---

## 4.8 与通用 coding agent 的五条差异

| # | coding agent 的世界 | 机器人的世界 | 代码证据 |
| --- | --- | --- | --- |
| 1 | 读文件是**精确的、永远新鲜的、即真相** | 每条事实带 `Freshness` 与 `Confidence`，**陈旧即 `UNKNOWN`** | `types.go:20-27,46-57`；`predicate.go:138-140` |
| 2 | 新鲜度是**自然的**（刚读的） | 新鲜度是**算出来的**，而且曾经是假的 | 旧实现写字面量 `"FRESH"` → 门禁的过期规则**永远不会触发** |
| 3 | 真相在两次操作之间**不变** | 世界在**规划与执行之间**会变 | `orchestration/types.go:34-39`；`tasks/service.go:254-256` |
| 4 | 动作**可重放** | 物理动作**不可撤销，且结果可能是 `UNKNOWN`** | `UnknownOutcome` 类；`PLACEMENT_NOT_OBSERVED` 禁止自动重试 |
| 5 | **路径即身份**（`/etc/hosts` 就是它） | **"房间"是给一个已委托位姿起的名字** | `world.go:172-219`；三张不一致的别名表 |

### 差异 1 的深层含义：`ENOENT` 与 `objects=0` 不是一回事

coding agent 读不到文件，得到 `ENOENT`——这是一个**确定的**答案："文件不存在"。

机器人看不到物体，得到 `objects = 0`——这是一个**不确定的**答案："**我不知道**"。

物体可能不在那里，也可能被遮挡了、光照太暗了、检测器阈值不对、相机脏了。

**把 `objects = 0` 当成"物体不存在"来推理，是机器人 agent 最危险的推理错误。**

而项目里三条 grounding 根因**全部**以 `objects=0` 的形式出现：

| 根因 | 真实含义 |
| --- | --- |
| 规划层：在客厅找厨房的杯子 | 物体**在**，只是**看不见** |
| 数据层：物体层被自己的哈希拒识 | 物体**在**，数据**被拒识了** |
| 绑定层：grounding 表只为 `cup/red` 作答 | 物体**在**，表里**没有它** |

**三个完全不同的原因，一个完全相同的外观。** 这就是为什么世界模型必须有 `UNKNOWN` 这个值——没有它，这三种情况会被压成一个错误的结论。

### 差异 3：计划必须携带它赖以成立的世界

`orchestration/types.go:34-39` 的注释：

> *a plan is only meaningful against the state it was formed from, and a planner that cached the robot's location would keep planning for wherever it used to be.*

翻译：**一份计划只对它形成时的那个状态有意义；一个缓存了机器人位置的规划器，会一直为机器人曾经在的地方做计划。**

所以 `Planner.Plan` 的三个参数是：

```go
Plan(request, intent, world)   // world 按调用传入，不缓存在 planner 上
```

修订路径同理（`tasks/service.go:254-256`）：

> 修订是**对着现在的世界**规划的，不是 task 创建时的世界：**机器人已经移动过了**。

**这是一条 coding agent 完全不需要的设计。** 文件系统不会在你读它的时候移动。

### 差异 5：三张别名表

同一句话在三层得到三个答案（见 4.3）。在 coding agent 里，路径就是身份——`/etc/hosts` 只有一个解释。

**在机器人系统里，"客厅"这个名字的解释取决于你在哪一层问。** 这不是懒，是**历史遗留 + 分层架构**的诚实后果，而它的修法（统一归一化）需要跨三层协调。

---

## 4.9 教学要点

### 三道练习题

**练习 1（区分"不存在"与"不知道"）**

世界模型用三值逻辑而不是布尔。请回答：如果 `EntityInside("ceramic-mug", "kitchen-tray", 5s)` 返回 `UNKNOWN`，为什么任务不应该把它当成"没放好"去重放放置动作？如果它返回 `FALSE` 呢？

<details>
<summary>答案要点</summary>

① `UNKNOWN` 的三种触发是**实体不在快照**、**证据超龄**、或 `maxAge<=0`（`predicate.go:42-56,138-140`）——这三种情况下**系统根本不知道杯子在哪**，而放置动作已经跑过一次，重放等于把一个**可能已经成功**的物理动作再做一遍。

② `FALSE` 意味着**有新鲜证据**证明关系不成立（`RELATION_MISMATCH`），这时"重新核验 + 重新规划"是正确的。

③ 真实的 `PLACEMENT_NOT_OBSERVED` 就是第一类：世界快照显示杯子物理上就在盘里（**水平偏差 5–8 mm**、底部与盘面同高），失败在"**没取到 3 个稳定样本**"。

**考点**：错误码的类别决定了唯一安全的恢复动作，而这个仓库把这条规则写进了 `core/closedloop`。

</details>

**练习 2（"再检查一遍"什么时候会变成缺陷）**

地图冲突只用只读方式报告（`conflicts()`），从不自动写回；而 `_swept_model_collision` 的安全检查曾经把机器人锁死。请说明这两个决定背后的同一条原则，并指出它们分别正确和错误在哪里。

<details>
<summary>答案要点</summary>

① 同一条原则是**"可解释性优先于自动化"**——地图是已验收的导航依据，静默变更会让"昨天能走今天为什么不能走"无法回答。核验失败如果不能自证，就得同时翻世界快照、证据库和仿真才知道"到底放好没有"。

② **正确的地方**：`conflicts()` 只读、只建议（`"A suggestion, never an action"`），测试断言地图数组逐位不变。

③ **错误的地方**：安全检查从**非法位姿**出发时，只允许"保持不动"——离墙 0.373 m vs 包络 0.375 m，差 **2 mm**，一个回合内再也动不了。**一个防止碰撞的检查，变成了一个比碰撞更糟的失败模式。**

④ **正确的修法**：有意不对称——从合法位姿出发，任何越界都拒绝；从非法位姿出发，**只拒绝让它变得更糟的移动**。

⑤ 可以引申的追问：`GOAL_NOT_CLEAR` 是"不进去"，那"**进得去但出不来**"由谁负责？

</details>

**练习 3（自我指涉与测试失效）**

物体层 `objects.json` 需要标注自己的 `mapRevision`，而 `mapRevision` 是包含 `objects.json` 的 manifest 的哈希。请说明为什么这个要求**在数学上不可能被满足**，并解释为什么单元测试没有发现这个 bug。然后给出两种可能的修法。

<details>
<summary>答案要点</summary>

① **数学不可能**：`mapRevision = H(manifest)`，而 `manifest` 包含 `objects.json` 的字节。若 `objects.json` 内含 `mapRevision`，则 `H` 的输入依赖 `H` 的输出——**一个哈希不可能包含它自己**（与哥德尔式自指同构）。

② **测试为什么没发现**：单元测试用自己的 helper **伪造了带匹配 `mapRevision` 的文档**。于是"**在被检查的地方字段总是存在，在被生产的地方它从不出现**"。这是 fixture 替换真实生产者时的通用失效模式。

③ **修法 A（本项目采用）**：身份改由文档内部真正能有的字段（`mapId` + `calibrationRevision`）+ "**读的人是从这张地图目录里拿到的**"这一事实共同建立。**用获取路径作为身份的一部分。**

④ **修法 B（也正确）**：把物体层从 manifest 哈希的输入里**挪出去**，让它有一个独立的身份（比如 `objectsRevision`），并在 manifest 里只记录"引用了哪个 objectsRevision"。**分层身份，避免自指。**

⑤ **加分点**：指出这类 bug 的通用检测方法——**契约测试必须跑真实生产者**，而不是用手工 fixture。可以加一条测试：每次发布地图后，用真实产物重新加载一次。

</details>

### 学生最容易误解的四个点

| # | 误解 | 纠正 |
| --- | --- | --- |
| 1 | 以为 `resolve_targets` 在做"自然语言名词 → 实体"的解析 | 在生产路径上，实体 ID 是 **grounder 在计划生成之前**决定的，`resolve_targets` **只做确认**（`edge/agent/runner.go:846-855`） |
| 2 | 以为"**看不到**"等于"**不在那里**" | 在机器人里它只等于 `objects=0`——**不知道**。这是本章最重要的一条，也是三条 grounding 根因共同伪装成的那件事 |
| 3 | 以为 `mapRevision` 是一个可以随便塞进产物的字段 | 物体层是地图自己的产物，它的字节是 manifest 哈希的一部分，**它不可能包含自己的哈希** |
| 4 | 以为世界模型是一个数据库 | 它存的是"**某一次观测这么说过**"，每条事实都带 `EvidenceRef`。`Revision` 是"世界变过几次"，不是"收到几条消息" |

---

## 4.10 本章小结

1. **世界模型存的不是事实，是"某次观测的说法"。** 每条事实都带 `EvidenceRef`，每条判据都返回三值，`UNKNOWN` 永远不等于 `FALSE`。

2. **"房间"不是世界事实，是给一个已委托位姿起的名字。** 超过 4 米就拒绝认领，因为一个自信的错误计划比一个承认信息不足的计划更糟。

3. **一个错误的计划会以所有下游层都正确的方式失败。** 三条 grounding 根因（规划层、数据层、绑定层）全部表现为 `objects = 0`。

4. **安全检查的方向必须是"在正确的地方严格"。** 一个从非法状态出发、只允许"保持不动"的检查不是安全机制，是锁死机制——差 2 毫米就够了。

---

## 4.11 源码索引

| 内容 | 位置 |
| --- | --- |
| `EvidenceRef` | `core/worldmodel/types.go:20-27` |
| `RobotState`（含 `Held`） | `core/worldmodel/types.go:29-44` |
| `EntityState` | `core/worldmodel/types.go:46-57` |
| `ResourceState` / `SourceState` | `core/worldmodel/types.go:59-77` |
| `Snapshot` / `Delta` | `core/worldmodel/types.go:84-106` |
| 三值逻辑与谓词 | `core/worldmodel/predicate.go:5-11,42-140` |
| `Apply` 的六道闸 | `core/worldmodel/projector.go:66-95` |
| 恢复语义（旧证据降级） | `core/worldmodel/checkpoint.go:55-69`；`projector.go:197-241` |
| 房间归属与 `roomMatchRadiusM` | `orchestration/world.go:35,172-219` |
| 提示词约束的写法 | `orchestration/world.go:76-81,134-137` |
| 空值不猜 | `orchestration/world.go:148-153,206-214` |
| `resolve_targets` 原型 | `sim/mujoco/tangying_sim/tools.py:76-110` |
| 占位符重绑定 | `edge/agent/runner.go:846-855` |
| 自我指涉缺陷 | `sim/mujoco/tangying_sim/semantic_services.py:175-201` |
| `GOAL_NOT_CLEAR` 判定 | `robot/gateway/tangying_robot_gateway/grid_navigation.py:112-172` |
| 认证目标吸附 | `sim/mujoco/tangying_sim/rgbd_runtime.py:941-998` |
| 安全陷阱（2 mm） | `docs/experiments/2026-09-20-semantic-map-representations-and-nl-navigation.md:560-568` |
| 地图冲突只读 | `robot/.../robot_workflow.py:600-632` |
| 回忆新鲜度参数 | `edge/robotclient/recall_goal.go:24-46,68-163` |
| 物体物理属性成数据 | `robot/gateway/tangying_robot_gateway/physical_attributes.py` |
| 可扩展性成本模型 | `docs/development/2026-09-15-extensibility-review-tools-and-navigation.md` §一 |
| grounding 三条根因 | 提交 `6f87aca04` / `55ae5a5ca`；`docs/development/home-scene-expansion-plan.md` |

---

**上一章**：[第 3 章 闭环契约](../chapters/ch03-closed-loop-contract.md) · **下一章**：[第 5 章 工具层](../chapters/ch05-tool-layer.md) —— 28 个工具、5 级安全标注，模型能碰什么、绝对不能碰什么？
