# 系统评价与完善计划：多 Agent 分布式机器人 AgentOS

日期：2026-09-18 · 对象：HEAD `134ec1a`（v0.6.0）
范围：`agentruntime/`、`core/`、`orchestration/`、`training/`、`fleet/`、`edge/`、`console/`、`web/`

本文回答三件事：**这套系统现在真实处在什么水平**、**离"多 Agent 管理的分布式机器人 AgentOS"还差什么**、
**按什么顺序补、每一项怎么证明补上了**。

方法约定：**每一条结论都指得出文件、行号或可复现的查询。**
运行数据全部来自本机真实账本 `artifacts/sim-stack/furnished-home/local-agent/agent.db`
（356 MB、65 个任务、159,314 条事件）；代码结论全部由本人直接读过原文。
只做静态审计、没有实测的部分在第九节单独标明。

---

## 零、执行进度（2026-09-18 起）

阶段 0 已落地的部分，逐项对应下文的编号。每项都带"改回旧代码即红"的验证。

| # | 项 | 状态 | 落点 |
| --- | --- | --- | --- |
| 0.1 | 账本只记状态迁移 + 观察者补关闭边 | ✅ 完成 | `agentruntime/ledgerfilter.go`、`core/agentcontract/anomaly.go`、`opsagent.go` |
| 0.3a | Freshness 真算（按传感器声明的预算，不再默认 FRESH） | ✅ 完成 | `core/telemetry/freshness.go`、`edge/agent`、`internal/recoveryexec` |
| 0.3b | 闭环输入不信被治理方自述 | ✅ 完成 | `internal/recoveryexec`、`agentruntime/recoverycatalog.go`（新增 `MovesTools`） |
| 0.3c | 云侧不直写成功 | ✅ 完成 | `fleet/coordinator`（`VerificationBasis`、`ErrIntentUnverifiable`） |
| 0.4 | 控制台安全边界与批准证据 | ✅ 完成 | `console/guard.go`、`console/maps.go`、`console/server.go` |
| 0.4c | 非环回绑定默认拒绝 | ✅ 完成 | `cmd/local-agent/listen.go` |
| 0.5 | 云侧 claim 过期语义 + 对账入口 | ✅ 完成 | `fleet/coordinator`（`UnknownOutcome`、`ReconcileIntent`、修订携带） |
| 0.2 | 未知结果的清除路径（人是唯一的清除者） | ✅ 完成 | `middleware`、`console/reconcile.go`、`localapp` |
| 0.6 | 状态如实：批准记录带批准面与时间、E-Stop 三态、Fleet 文案 | ✅ 完成 | `tasks/service.go`、`middleware/sqlite`、`web/index.html`、`web/app.js` |
| 0.7 | 通电审计（声明式表，漂移即红） | ✅ 完成 | `tests/architecture/powered_test.go` |
| 0.8 | `task_events` 分级保留（可过期 = 别处存着同一件事） | ✅ 完成 | `middleware/sqlite/retention.go` |
| 0.9 | README 口径对齐 + 漂移守卫测试 | ✅ 完成 | `README.md`、`tests/docs/readme_claims_test.go` |

### 阶段 1 的实测结论（2026-09-18）

主线原本的诊断（"grounding 排在计划之前"）**不完整**。在干净家居栈上从零跑那条演示指令，
真实链路断在两处，且都在 grounding 之后：

1. **房间名进了物理工具**：LLM 计划写 `{"goalPose": "kitchen"}`，而导航只接受位姿。
   修在 runner 的参数解析：查得到就换成已 grounding 的位姿，查不到就留着让工具拒绝。
2. **计划可以静默省略物理前置步骤**：LLM 计划没有 `pre_position`，于是底盘站在地图从未认证过的
   地面上开出第一步，`LOCALIZATION_NOT_CLEAR`。修法是结构性的：
   `manipulation.MobilePreamble` 成为唯一定义，runner 给漏掉它的模板注入并让原入口等它。

**实测结果**：11 步操作序列全部 `COMPLETED`（12 条观测证据），**抓取与放置真的做完了**；
第 12 步返程导航止于 `GOAL_NOT_CLEAR`——那张地图上客厅返程点未认证，属场景覆盖。

### 1.2 的真正阻塞点（本轮实测定位）

接 CI 之前有一件必须先做的事，而这轮试跑把它暴露了出来：

**验收套件钉的是规划器的步骤名，不是任务的结果。**

`scripts/run_home_task_suite.py` 用 `route_steps(3) = ["observe","pre_position","navigate_00","verify_arrival_00",…]`
断言 `step_ids == expected_steps`。这套名字只在**确定性规划器**下成立。实测在一台配了模型的栈上，
同一个巡逻指令被 LLM 规划成 `["observe-start","navigate-bedroom","verify-bedroom",…]`——
任务在做正确的事，断言却以"unexpected confirmed steps"失败。

这与本仓库自己的评测原则正相反：`docs/architecture/orchestration-post-training.md` 写着
> 用例断言的是"机器人去了厨房"，不是"计划里有 `navigate_route`"。
> 这条规则是被一次真实失败教会的……**评测器自己踩了它在注释里警告的坑。**

所以 1.2 的第一步不是写 workflow，而是**把验收套件的断言从步骤名改成结果**。

**这一步已经做完**（2026-09-18）。现在的断言是：

| 断言 | 内容 |
| --- | --- |
| 任务结束 | `state == SUCCEEDED` |
| 工具齐备 | 场景要求的工具都**被调用过**（`navigation.navigate`、`manipulation.pick`、`verify_placement` …），名字与顺序不管 |
| 每一跳都有证据 | 每个确认步骤都有 `evidenceSource == command_observation` 与可查的采集明细 |
| 真的开到了 | 在**启用地图上**的导航跳数不少于场景要求 |
| 真的放对了 | `verify_placement` 的复验：3 个稳定几何样本、`inside:kitchen-tray`、`object_id == ceramic-mug` |
| 图片未被篡改 | 每张 RGB/depth 的 SHA-256 |

守卫它的是一条新测试：**同一件事换成模型式的步骤名（`observe-start`/`navigate-kitchen`/…）必须照样通过**。
把断言改回按名字比较，恰好这条测试失败——这是它该有的行为。

#### 第二个阻塞：验收依赖一份**刻意不入库**的地图

`build_sim_map.py` 走服务 API 巡检并发布不可变地图，但 `.gitignore` 把三样东西排除在仓库外，理由写在里面：

```
/artifacts/calibration/  # Guided calibration sessions: per-unit measurements made on a specific robot.
/artifacts/maps/         # Built maps: surveys of a specific building, often large binaries.
/artifacts/sim-assets/   # AWS Small House meshes
```

前两项**不该入库**——一台机器人的标定不是另一台的，一栋楼的扫描不是另一栋的。
所以"家居验收接进 CI"不是加一段 YAML，而是让这份依赖**在 CI 里被生产出来**：

```
install → prepare_home_world.py（按 pin 的 revision 克隆 AWS Small House 网格，需要网络）
        → furnished-home 栈起来
        → build_sim_map.py 巡检建图并激活（默认 timeout 600s）
        → run_home_task_suite.py
        → 拆除
```

这条链是可行的，而且**第 3 步已经在真栈上验证过**：`build_sim_map.py` 跑完 182 帧 / 28.47 m，
地图 `scan-3b9d237aaef8` 已保存并启用。代价是每次 CI 多约 10–15 分钟（现有 job 上限 45 分钟）。

**我仍然没有写这段 workflow**：第 4 步（跑套件）目前在一台真栈上还过不去。
写一个跑不通的 CI job 正是这份文档一路在记录的那类缺陷。下面是它的确切几何。

### `GOAL_NOT_CLEAR` 的确切几何（已诊断到格）

修复召回位姿的 z 之后，任务从"还没动就被拒"前移到第三步导航，停在：

```
navigate_01 navigation.navigate FAILED
GOAL_NOT_CLEAR 目标工作区尚未扫描，或底盘安全间距不足，请补扫或选择其他位置。
```

把导航栅格读出来逐格核对，结论是**覆盖缺口，不是几何太紧**：

| 房间目标 | 0.283 m 内的非自由格 | 0.30 m 内 |
| --- | --- | --- |
| `living_room` | 0 | **0**（通过） |
| `kitchen` | **2 个未知格** | 3 个未知格 |
| `bedroom` | — | 0.32 m 处才有 |
| `home_corridor` | — | 0.70 m 处才有 |

于是机器人自身的净空半径落在 **0.283–0.30 m** 之间，而厨房目标周围 0.21–0.28 m 处有两格
**205（未知）**——不是占用格。逐格画出来，它们在目标的**东南方向**，正是机器人站在该点时
自身遮挡、前向相机测不到的那块地面（`pre_position` 的注释早就写明这是"站立机器人脚下、
前向相机唯一测不到的一块"）。

补扫试过一次：`--mode explore --base-map-id …` 跑到"已探明 62% / no_reachable_frontier"停止，
未知格从 3 降到 2——**剩下的两格没有可达前沿**，也就是说靠继续探索到不了。

**三条修法，各有代价，我没有替项目做这个决定**：

1. **把厨房路点移动约 0.3 m** 到已测自由区。但该路点到操作台的距离由机械臂可达范围决定
   （`_commissioned_clearance_radius` 的注释记录过一次同类冲突），动它可能打断抓取。
2. **换一个方向补扫**同一片：让机器人在该点朝相反方向经过一次。巡检路线决定接近方向，
   所以这要改路线，而不是加预算。
3. **放宽 goal 的净空判据**：规划器已经会搜索"已认证连接点"（`connect(goal)` 的注释说明
   它刻意允许在不精确等于路点的位置停下），但 `point_clear(goal, radius)` 是额外的一道门。
   让 goal 也接受连接点，等于机器人停在路点附近而不是路点上——而路点位置是机械臂可达性定的，
   绕行可能让抓取失败。**这是导航语义的决定，不是机械修补。**

### 在真栈上找到并修掉的一个缺陷（2026-09-18）

`recalledGoal` 一路失败在 `goal exceeds robot workspace on navigation.z`。根因是两个生产端
对同一个量用了两套约定：物体层的 `observedFrom` 是 **2D 底盘位姿**（x, y, yaw，注释写明），
而抬升成 7 元组时 z 被写成 **0**（地图地面）；同一份数据里委任目标带的是**底盘高度 0.035**，
而 `withinWorkspace` 按宣告的 `navigation.z: [0.035, 0.035]` 严格比较。
于是**每一个走召回路径的任务都在 grounding 被拒**，而错误信息听起来像坐标问题。

修法：抬升用委任目标自己所在的平面（一个平面，两个生产端共用），有测试钉住。
实测把失败点从"还没动就被拒"前移到第三步导航。

**下一步**：0.7（通电审计）成本最低、能一次暴露所有"有实现无数据源"的能力；
0.9 决定这份文档之外的读者能不能相信 README；1.2 把这条已跑通的链路接进 CI，
否则它还会再坏一次而没人知道。

### 阶段 0 完成的判据

| 判据（文档原文） | 现状 |
| --- | --- |
| 单任务事件数 < 100 | ⚠️ **活栈实测 104 条**（旧栈 7,737，降 74 倍）——**差 4 条未达标**。剩下的全是每一步的执行事实（`TOOL_ACTIVITY` 及其投影），不是观察者噪声 |
| `ops.*` 占比 < 30% | ✅ 活栈实测 **23.1%**（旧栈 99.3%） |
| 未知结果有清除路径且留痕 | ✅ `POST /v1/tasks/{id}/reconcile`，写入一次 |
| 来源标 STALE 时 gate 必拒 | ✅ 计算式新鲜度，8 个测试 |
| 远端 `mutates_world=false` 不豁免 | ✅ `MovesTools` ∪ `PhysicalTool` ∪ 远端 |
| 云侧不直写成功 | ✅ `ErrIntentUnverifiable` + `VerificationBasis` |
| 跨站 POST 被拒 / 需可验证的人 | ✅ 统一守卫 + 会话令牌 |
| claim 过期不得回到可执行 | ✅ `UnknownOutcome`，只能对账 |
| 审批含操作员与时间 | ✅ `ApprovedBy` / `ApprovedAt` |
| 通电审计清单 | ⚠️ **5 项未通电**，已写成会红的表 |
| README 与代码一致 | ✅ 四项修正 + 守卫测试 |

**阶段 0 到此八项全部完成。** 剩下的 5 项未通电能力不是"没做"，是"做完了没插电"——
它们已经写进 `tests/architecture/powered_test.go` 的表里，有原因、会红，修好一项必须同时改表。

**已知的遗留缺口**（本轮如实记录，不掩饰）：
`ReconcileIntent` 只有 Go 入口，没有 HTTP 路由；`ops.anomaly_cleared` 改善了账本，
但控制台的 `Active` 仍由任务状态推导，尚未消费这个新事实；
`make lint` 的 ruff 一步在 17 处报错，全部是既有 Python 漂移，本轮未改。

---

## 一、结论

> **执行契约的设计是工业级的。但真正的问题不是"缺了哪个模块"，
> 而是多个已写完、注释清楚、API 也接好了的能力从未通电；
> 而三条最关键的契约在生产路径上没有接线。**
>
> **主线（自然语言 → 取杯子 → 放托盘）目前是断的——不是缺功能，是执行顺序上的一个循环依赖。**

三条最关键的实测数字：

| # | 数字 | 怎么得到的 |
| --- | --- | --- |
| 1 | 账本 **159,314 条事件里 158,193 条（99.3%）是观察者产生的**，执行事实只有 0.7% | `select case when type like 'ops.%' ...` |
| 2 | 单个任务产生 **7,737 条事件**，同一个异常 `ANOMALY_UNVERIFIED_MUTATION` 重复 **1,410 次**（约每 60 s 一次，跨 33 小时） | `group by task_id, code order by c desc` |
| 3 | 训练数据流水线的真实输出是 **"可用正样本 0 条"**（`sft 0 / refusals 6 / negative 0`） | 实跑 `bin/training-export` |

这三条是一条因果链：
**结果未知没有清除路径 → 观察者每 60 s 重报一次 → 只追加账本被灌满 →
而账本同时是回放源和训练语料 → 回溯、量化、数据集三件事一起失效。**

### "已实现但从未通电"——本轮审计最有价值的发现

我最初按"缺哪个模块"来查（缺鉴权、缺 trace、缺升级通道）。逐条读源码后，真实的模式是另一回事：

| 能力 | 状态 | 证据 |
| --- | --- | --- |
| **慢步骤告警** `ANOMALY_STEP_LATENCY` | 规则完整实现，**永远不会触发** | 规则在 `agentruntime/opsrules.go:449-456`，唯一的生产输入构造点 `agentruntime/opsagent.go:311-319` **从不填 `Latency` 字段**；全仓只有两个测试文件填过（`opsagent_test.go:362`、`faultmatrix_test.go:72`） |
| **四段耗时遥测** | 采集了、有 p50/p95/p99 handler，**前端零消费** | `latency/recorder.go:227`、`console/server.go:164`；`web/app.js` 无任何引用 |
| **事故诊断档案的计时** | 注释承诺"the console's latency report verbatim"，**永远 null** | `incidents/bundle.go:117-119` 的承诺 vs `internal/localapp/app.go:495-505` 从不设置 |
| **任务事件 WebSocket** | 名为推送，**实为 200 ms 轮询 SQLite** | `console/server.go:335` `time.NewTicker(200 * time.Millisecond)` |
| **世界事件 WebSocket** | 已实现，**前端绕过它改 3 s 轮询** | `console/server.go:162` vs `web/app.js:2018-2026` |
| **审批证据** | 端点把"有人调用了它"硬编码为人的同意 | `console/recovery_execute.go:114-117` |
| **跨进程因果字段** | 上了 protobuf 线，**生产端零赋值** | `core/observation/envelope.go:111` vs grep `.Causation =` 零命中 |

**这些不是"没做"，是"做完了没插电"。** 修它们的成本远低于新建模块，
而且它们比缺失的模块更危险——因为文档、注释、API 都在说它已经工作了。

---

## 二、已经真正建成的（强项，且有测试钉住）

先说强项，因为它们决定了后面**哪些东西不需要重做**。

### 2.1 失败分类表：全套系统里质量最高的一块

`core/closedloop` 把 113 个运行时故障码按"唯一安全的下一步"分成 **8 类**
（`closedloop.go:50-67`：Transient / Perception / Planning / Permission / Resource / Validation /
UnknownOutcome / Fatal），只有 Transient 与 Perception 可重试（`:329-336`），
**UnknownOutcome 永久禁止自动重试**（`:64`）。

两个细节说明这块代码的成色：

- **未识别的错误码归入 `UnknownOutcome`，而不是猜成可重试**（`:81-82`）——猜测的方向选的是安全那一侧；
- `core/closedloop/classification_coverage_test.go` **用 regexp 扫运行时代码抓失败码**，
  强制每一个新码必须被显式分类。这是**契约级测试**，不是实现细节测试。

### 2.2 `Track`（重试预算）被删除，而不是被接线

`closedloop.go:20-36` 记录了一次正确的自我否决：包里曾有重试预算、退避与升级阶梯，
但**没有任何东西驱动它**（`NewTrack` 只出现在本包测试里），而失败分类读起来像有界重试在生效。
删除的理由比大多数设计文档都清楚：

> 物理写的重试预算必须持久化才有意义。内存里的尝试计数在进程重启时归零，
> 于是崩溃过的 Agent 会永远重试——正是上限要防的那件事。

**"删掉不能兑现的承诺"** 是这套代码的默认动作。这个习惯第五节的阶段 7.1 还要用到。

### 2.3 多 Agent 运行时的骨架是对的，四处机制值得保护

| 机制 | 它防的是什么 | 位置 |
| --- | --- | --- |
| **发布通道由运行时注入**，不是组合根手工接线 | 一次真实事故：观察 Agent 没装 `Publish`，它的每个发现都进了告警存储却从未上总线——恢复 Agent 永远等不到触发，而控制台照常显示、每个 Agent 照常健康。**单元测试全绿，因为每个测试都自己注入了 `Publish`** | `orchestrator.go:127-150` |
| **权限门控在运行时，不在文档** | 门控刻意不重新推导安全，只回答"提出请求的 Agent 是不是那种可以做这件事的 Agent" | `permission.go:85-146` |
| **物理动作在飞时冻结恢复建议**（按计数不按布尔） | 两个动作重叠时布尔会在第一个完成时错误解除挂起；规则落在 Orchestrator 而不是 Agent 自己，否则"关掉一个观察者就会悄悄关掉保护移动机器人的那条规则" | `orchestrator.go:336-397` |
| **观察者没有任何执行端口** | 由反射测试钉住，不靠人记住 | `cmd/local-agent/recovery_wiring_test.go` |

再加一条架构级的：`tests/architecture/dependencies_test.go` 把"新增一个 Agent 不需要改核心"
从承诺变成机制。

### 2.4 事件账本只有一份

Agent 事件不走第二套存储，而是双向投影进既有任务账本（`agentruntime/projection.go` 开头写明了理由：
两份记录必然不一致，而回放只显示其中一份）。这条决定让"OpsAgent 的诊断出现在普通人已经在看的
任务回放里"零成本成立。

### 2.5 工具层的边界是**数据**声明的，可被 CI 校验

`tools.json`：28 个工具、**14 个 `mutates_world=true`**、安全等级分布 `{0:11, 1:2, 2:7, 3:6, 4:4}`、
`excluded_from_llm: ["move_arm_to_joints", "navigate_to_pose"]` 显式列出。
`internal/actionloop/loop.go:596-598` 的 `needsApproval` 由 SafetyLevel 派生而**不是询问工具**，方向是对的。

### 2.6 恢复动作目录是分级的，且分级不完整就装不起来

`agentruntime/recoverycatalog.go` 共 **16 个动作：6 个 `read_only`、7 个 `bounded_write`、3 个 `never_automatic`**。
自动通道只跑 `read_only`；`bounded_write` 需人批准；`never_automatic`（复位急停、插拔线缆、改标定参数）谁也跑不了。
`never_automatic` 且没有 `Refusal` 说明的动作会被**拒绝注册**（`:223`）——
这条"分级不完整就装不起来"的设计值得保留到后面每一个新动作。

### 2.7 延迟四段遥测的**实现**是合格的

`latency/recorder.go` 把每步拆成**排队 / 安全准入 / 执行 / 证据核验**四段，环形缓冲有界（4096）、
溢出计数不隐藏，已接入 `edge/agent/runner.go:132`。
**设计没问题，问题是没有消费者**（见第一节"从未通电"表）。

### 2.8 `AGENT_API_KEY` 的流向是干净的（正面确认）

env/flag → `internal/localconfig/settings.go:52,80-92` → 响应只回 `hasApiKey bool`（`console/server.go:36`），
**apiKey 从不回显**；输入框 `type="password"`（`index.html:302`），提交后清空（`app.js:420`）；
文件权限 0600（`settings.go:154`）。唯一落到浏览器存储的是云端 Fleet JWT（sessionStorage）。
**没有浏览器端泄漏路径。**

---

## 三、这些强项的边界（同一份代码的另一面）

### 3.1 闭环契约的 Gate 在**全仓只有 2 个生产调用点**

这是本节最重要的一条。设计是对的，接线不是：

| # | 绕过路径 | 后果 |
| --- | --- | --- |
| 1 | **云侧直写成功**：`fleet/coordinator/coordinator.go:571` 的 `if sharedHandoff && c.world != nil` 才做世界校验，否则 `:603` **无条件** `node.Status = StatusSucceeded`。而 `fleet/` **完全不 import `core/closedloop`** | 非 handoff 任务的成功完全靠 worker 自述。判定函数还硬编码 `EntityID:"red-block"`（`:596`）与 zone 名 |
| 2 | `internal/localapp/app.go:643` 在 runner 返回 nil 后**无条件** `StateSucceeded` | 没有二次证据校验 |
| 3 | **`Freshness` 走默认值**：`edge/agent/runner.go:700` 与 `internal/recoveryexec/tools.go:297` 起始写 `"FRESH"`，唯一降级路径是急停（`runner.go:717-726`） | gate 的 STALE 规则在本地执行路径上**只能由急停触发**。按来源新鲜度预算判定的逻辑存在于 `core/worldmodel/projector.go:259 freshnessAt()`，但**没有被用在这里** |
| 4 | **gate 的输入交给被治理方自述**：`internal/recoveryexec/tools.go:92` `MutatesWorld: service.GetMutatesWorld()`（来自 `robotv1.ServiceDefinition`），再经 `internal/actionloop/loop.go:569` 构造 Declaration | 远端适配器报 `mutates_world=false` 即可**自我豁免**闭环 |
| 5 | `core/taskgraph/runtime.go:115 MarkSucceeded` 不经任何证据检查 | grep 确认无生产调用者（死代码），但它是下一次误用的现成陷阱 |

对照之下 `edge/agent/runner.go:618-632` 是**唯一正确实现**：`Manifest || RuntimeMutatesWorld`——远端只能加严。
**修法是把那个模式复制到另外四处，不是重新设计。**

### 3.2 `AllowedSafetyProfiles` 声明了，但全仓没有任何比对逻辑

`core/skills/manifest.go:28` 声明、`:58` 校验非空、`plugin.go:38` 填充——
**没有任何地方拿它和实际 profile 比对**。profile 值由 `edge/robotclient/client.go:85,402,410`
单方面默认为 `"desktop_standard"`。**这是文档约定，不是强制。**

### 3.3 trace 是死代码，metrics 三套互不相通，logs 只有 stderr

- `core/trace` 全包 **13 行、0 个测试**，字段只有 `TaskID/Sequence/Type/StepID/Payload`，
  **没有 TraceID / SpanID / ParentID**；全仓唯一引用者是 `middleware/contracts.go:56-57` 的
  `TraceStore` 接口，**没有任何实现**；
- 因果只有单跳父指针 `agentcontract.Event.CausationID`（`event.go:37`）；
- **metrics 有三个互不相通的产出点**：步耗时环形缓冲（`latency/recorder.go`）、
  SQLite 任务表即时聚合（`orchestration/metrics.go:34`）、内存遥测 100 样本（`tasks/telemetry.go:16`）。**无导出端点**；
- **logs 仅 stderr**（`cmd/local-agent/main.go:558`），无结构化、无关联 ID；
- `core/telemetry` 同样 **0 测试**。

**结论：现在无法从一次任务回溯到每一步的工具调用、观测与决策；只有按 TaskID+Sequence 排序的扁平日志。**

### 3.4 投影层是干净的一致性来源，但有两个逃逸口

`Projector.entities`（`projector.go:34`）是 Go 侧唯一世界存储，`Apply`（`:66`）是唯一且确定的变更入口，
harness 做 revision + sequence + 时间三重"是否晚于命令"检查（`evaluator.go:71-120`）——**这块是好的**。

两个口子：

- `telemetry.Snapshot.RobotState map[string]any`（`telemetry.go:42`）是绕过 project 校验与新鲜度的
  **无类型平行真相**；
- `Checkpoint.RestoreProjector`（`:57-69`）刻意不信任全部恢复证据，而 `Clone`（`:44-55`）保留 freshness 边界——
  **两个入口语义不同**；且 freshness 由 `now()` 现算（`:259`）→ **重放依赖墙钟，不是严格可重放**。

### 3.5 可扩展性有硬编码枚举（与"换机器人不用重写"的声明有差距）

| 要做的事 | 必须改的地方 |
| --- | --- |
| 新增一个工具 | `skills/manipulation/plugin.go:21`、`core/robotcontract/contract.go:110` 与 `:120`（**两个手写白名单**）、新失败码还要加 `core/closedloop/closedloop.go:83` |
| 新增一个机器人本体 | `examples/robots/*.profile.json` + `core/robotcontract/contract.go:135`（`validSource`）、`:139`（`Embodiment` oneOf，硬编码枚举） |
| 新增一种传感器 | `core/observation/envelope.go:20-27` |

上一轮"可扩展性落地"（`b3e5729`）把物体物理属性做成了数据，但**白名单和枚举还是代码**。
这是"换一台机器人要花多少人工"的直接答案。

---

## 四、短板（按严重度排序）

分三组，因为它们的阻断对象不同：**能力、数据、安全**。

### 组一 · 能力阻断

#### P0-A 主线是断的：`grounding` 排在计划之前，计划里的导航永远执行不到

`edge/agent/runner.go:224-226`：

```go
for index, intent := range intents {
    grounded, err := r.grounder.Ground(ctx, intent)   // ← 先 grounding，失败即 return
    if err != nil { return result, fmt.Errorf("ground subtask %d: %w", index+1, err) }
    ...
    plan, err := r.planForIntent(task, index, grounded, intents)   // ← 后建计划
```

而 `planForIntent`（`runner.go:735`）使用 LLM 计划模板的条件是 `len(grounded.NavigationGoal) == 0`——
**根本走不到**，因为 grounding 在 `objects=0` 时已经 `return` 了。

这是一个**循环依赖，不是接线疏忽**：移动操作要去另一个房间拿东西时，正确顺序是
**先导航 → 再 grounding → 再规划**，而现在写的是"先 grounding → 再规划"。

**后果**：每个任务失败在 `grounding absent: objects=0`。
README 开头"12 步全部有证据"的演示**在当前 HEAD 上无法复现**。
账本佐证——23 个 `SUCCEEDED` 全是导航任务：`step_runs` 247 行里
`navigation.navigate` 80 次、`verify_arrival` 62 次、`observe_scene` 53 次，
而 `pick` / `place` / `verify_grasp` / `verify_placement` **各只有 8 次**，
30 个任务停在 `RECOVERABLE_FAILURE`。

**这一条不修，后面所有工作都是在一台不会干活的机器人上加仪表盘。**

### 组二 · 数据阻断

#### P0-B 观察者风暴：账本 99.3% 是噪声，而账本同时是训练语料

| 指标 | 实测 |
| --- | --- |
| 总事件 | 159,314 |
| `ops.*`（观察者） | **158,193（99.3%）** |
| 执行/任务事件 | 1,162（0.7%） |
| 9/15 事件量 | 181 |
| 9/17（Agent 运行时上线当天） | **75,717** |
| 9/18 | **82,538** |
| 单任务最大事件数 | 7,737 |
| 单任务同一异常最多重复 | 1,732 次（跨 33 小时） |
| 数据库体积 | 356 MB（`task_events` 254 MB + `observation_evidence` 86 MB） |

**约 170 倍放大**，发生在 `88cf9f7`（Agent 层升级为多 Agent 运行时）之后。
按当前速率约 128 MB/天、47 GB/年。

根因三层，必须一起看：

1. **当前状态存在内存里，重复观测写进只追加账本**：`agentruntime/runnermemory.go:51` 的 `AlertStore`
   是进程内 map + TTL，重启即丢。**持久化的那一半接错了对象。**
2. **`UNKNOWN_OUTCOME` 没有清除路径**：`docs/architecture/readiness.md:93-107` 自己记录了这件事。
   条件永远不解除，`opsagent.go:416` 的 60 s 冷却就变成"每 60 s 永久重报"。
3. **没有保留策略**：而控制台**已经把 279 条报告压成 7 个问题**（`tasks/alert_groups.go`）——
   **投影层是对的，账本层没跟上。**

顺带一个被它放大的风险：`agentruntime/bus.go:29` 的每订阅者队列是 256 且有界，
溢出时**丢弃最不重要的事件**并计数。也就是说，**在事件最密集、最需要完整记录的时刻，
账本恰恰不是完整的**——而"可回溯"的承诺依赖它是完整的。

#### P0-C 没有端到端回归门禁：所以 P0-A 能坏好几天没人发现

CI（`.github/workflows/ci.yml`）跑的是 `scripts/run_simulation_acceptance.py --episodes 30`，而它：

- 用 `TabletopWorld`（桌面场景），**不是家居五房间场景**；
- 用 `service.execute_for_test(command)` **直接注入命令**，绕过 Agent 层、规划器、审批与证据门。

同一份 CI 里**没有任何一步会跑 `scripts/run_home_task_suite.py`**；
`make nl-eval` 也不在 CI 里（grep `nl-eval|training-export|eval` 零命中）。

**结论：README 的头号声明没有回归门禁。**
测试数量在涨（Go **975** 个测试函数、Python **1,362** 个、Web 25 个测试文件），
**但没有一个在守护那句最重要的话**。

### 组三 · 安全阻断

#### P0-D 批准证据在协议层不可证伪

这是审批（P0-D）与安全（本组）相交处的**同一个问题**，不是两件事。

| 事实 | 位置 |
| --- | --- |
| `POST /v1/recovery/execute` 把"有人调用了这个端点"**硬编码**为批准证据 | `console/recovery_execute.go:117` `OperatorApproved: true`，紧邻注释写着 "A person called this endpoint about this action. That is the approval"（`:114-116`） |
| **控制台没有认证** | `Handler()` 只挂 CSP 头（`console/server.go:118`）。代码注释自己承认："The local console has no authentication… anything that can reach it can call this."（`recovery_execute.go:31-33`） |
| **写接口不校验 Content-Type，也不查 Origin** | `console/server.go:245,261` 等直接用 `json.NewDecoder(r.Body)`。**控制台的 HTTP 写路由里只有 `POST /v1/robot/services` 一条**做了完整的 Origin + `Sec-Fetch-Site` + Content-Type 三重检查（`robot_services.go:46-47`）；两个 WebSocket 走 gorilla 默认同源校验（`server.go:194` 手写、`server.go:329` 零值默认），但 `POST /v1/tasks`、`/approve`、`/recovery/execute`、`PUT /v1/config/llm` 都没有 |
| 后果：浏览器会以 `text/plain` 这类"简单请求"形态投递 JSON 体且**不触发预检** → 操作员访问的任意网页可跨站触发 `POST /v1/tasks`、`/approve`、`/recovery/execute`、`PUT /v1/config/llm` | — |
| 而 `recovery/execute` **只认 `actionId`**，取值来自固定目录（如 `execution.read-history`），**可猜** | `console/recovery_execute.go` |
| **listen 完全可配置，代码不做 loopback 校验** | `--listen` / `LOCAL_LISTEN`（`cmd/local-agent/main.go:78`），而包注释仍称 "loopback-only"（`console/server.go:1`）；`docs/production/configuration-and-security.md:76` 把鉴权**委托给一个未随仓库交付的反向代理** |
| 审批本身不记录操作员 | `tasks/service.go:600,602`：只有一个布尔位 + 事件。**无批准人、无时间、无理由、无超时、无默认拒绝** |

**所以准确的表述是：这套系统把"有人调用了这个端点"当成人的同意，
但没有任何机制能证明调用来自人或来自本页面。**
可追溯性缺失与暴露面在这里不是两个问题，是同一个问题的两半。

另外两处较小的暴露：`console/maps.go` 的符号链接围栏只覆盖 SLAM 分支（`:230-242` 有 `EvalSymlinks`），
非 SLAM 分支走 `:266 serveFileAt` **不做约束**；`console/server.go:534,578` 把标定文件的
**绝对路径回显**给任何读者。

#### P0-E 云侧有一个 2 分钟的定时器，正好在做闭环契约禁止的事

这是本轮最锋利的一处**内部自相矛盾**：

- `core/closedloop:63-65` 的立场是：**物理动作结果未知时，永远禁止自动重试**；
- 而 `fleet/coordinator/coordinator.go:237-258` 的 `reclaimStaleLocked` 会：
  任何 `RUNNING` 且 `Started` 超过 `claimLease` 的节点 → **改回 `READY`**、清空 claim、
  写 `Error = "claim lease expired"`——**完全不检查物理动作是否仍在飞、或是否已经完成**；
- `claimLease` 默认 **2 分钟**（`cmd/fleet-control-plane/main.go:143` `FLEET_INTENT_LEASE=2m`）；
- 而**任务节点的资源租约从不续期**：`Coordinator.RenewLeadership` 只续 *leadership*（`main.go:188`，
  且失败仅打日志 `:188-190`），没有任何地方为任务节点续期。

**于是一次超过 2 分钟的物理动作，账目会被回滚成"可执行"，而机器人可能已经动过了。**
这正是 `core/closedloop` 花 369 行要防的事，在 `fleet/` 里被一个定时器放回来了。

配套的分布式问题（静态审计，未做双机实测）：

| 问题 | 证据 |
| --- | --- |
| **fence 校验与业务提交跨存储非原子**：校验在 Redis，提交在 MySQL，中间可停顿超 TTL → 旧 leader 幽灵写 | `fleet/mysql/coordination.go:15-53`（无 fence 列）、`coordinator.go:1084 → :1059` |
| **资源模型只有一个硬编码物料**：地图 / 位姿 / 持物 / 充电位**无锁** | `coordinator.go:788` `"block:red-block"` |
| **无 `REDIS_ADDR` 时 leader 退化为进程内 map**，多副本会各自自认 leader | `cmd/fleet-control-plane/main.go:145-151` |
| **分区无安全停机**：设备租约只翻 `Online`，无停机回调；链路断开时**远程急停直接失败**；worker **不检查链路、上报错误被丢弃仍继续物理动作** | `fleet/registry/registry.go:126,139`、`fleet/gateway/gateway.go:413-417`、`edge/worker/worker.go:276,314-317` |
| **Fleet 侧没有对账（reconcile）**：只有 Local Brain 有 | `internal/localapp/recovery.go:13-40` |
| leader 无故障切换：启动时一次 Acquire，失败即退出 | `main.go:171-173` |
| 脑裂测试只有内存模型；**没有任何测试启动真实 Redis/MySQL** | `fleet/redis/lease_test.go:10` 只测 nil client；MySQL 仅 schema 断言 |

**诚实说明**：mTLS / gRPC / 独立 main 是真的存在的（`fleet/gateway/gateway.go:151-155`
TLS1.3 + `RequireAndVerifyClientCert`，`:179-181` 强校验 CN==robotID）。
所以这是"单主控制面 + 真加密的分布式**部署**"，不是"分布式**一致性**"——
一致性仍停在应用层校验 + 跨存储非原子 + 租约不续期。

#### P0-F 状态与口径不实（三处）

| 事实 | 位置 |
| --- | --- |
| **Fleet 创建任务后自动批准，而同一段代码的文案写"等待审批"** | `web/app.js:5985` 写 `已创建 …，等待审批`，紧接着 `:5987` `await fleetTaskAction("approve", task)` |
| `E-Stop 安全` 是 **HTML 硬编码文本** | `web/index.html:351` |
| README 四项声明与代码不符 | 见下 |

README 口径：

| 位置 | 文档说 | 实测 |
| --- | --- | --- |
| `README.md:27` | 305 个 Go 测试 + 165 个 Python 测试 | Go 测试函数 **975**、Python **1,362** |
| `README.md:39,96` | "七类失败分类" | `core/closedloop` 里是 **8 类**（含 `FATAL`） |
| `README.md:139` | 家居自然语言拆解"✅ 已验证，12 步全部有证据" | 当前 HEAD 无法复现（P0-A） |
| `console/server.go:1` | "loopback-only" | listen 可配置且无 loopback 校验（P0-D） |

最后一条正是这个仓库自己在 CHANGELOG 里反复认定为最不能产生的那个结果：
**"什么都没有"和"系统健康"长得一模一样。**
README 说"已验证"而实际不能跑，与告警页面说"没问题"而其实渲染失败，是同一类缺陷。

### 组四 · 能力缺口（P1）

#### P1-G 两个半边不接：多 Agent 运行时是单进程的，分布式控制面里没有 Agent

实测导入关系：

```
agentruntime 的导入方 = cmd/local-agent/*、internal/{localapp,autorecovery,recoveryexec}
cmd/edge-worker/main.go     → 无 agentruntime / Orchestrator / OpsAgent / RecoveryAgent
cmd/fleet-control-plane     → 无 agentruntime / Orchestrator / OpsAgent
```

三个 Agent 在 `cmd/local-agent` 一个进程里共用进程内 `EventBus`，管一台机器人；
而 `fleet/`（10,030 行）+ `edge/`（10,636 行）有租约、fencing、Outbox、WorldHub，**但不托管任何 Agent**。
另外 `cmd/local-agent` 还同进程内嵌了 worker + worldhub（`main.go:270-271`），
所以 Local Brain 与 Fleet 是**两套部署画像，不是双活**。

#### P1-H "自动修复小问题"目前实际能力 ≈ 6 个只读诊断步骤

- 自动通道只执行 `read_only`（6 个：`observe.re-read`、`nav.read-map`、`map.read-conflicts`、
  `map.read-status`、`calibration.read`、`execution.read-history`）；
- 7 个 `bounded_write`（含 `task.retry-step`、`arm.home`、`nav.re-localize`、`map.activate`）**全部需人批准**；
- **没有任何自动重试**——`closedloop.Track` 已删除，因为持久化尝试计数还不存在。

系统今天能自动做的是"**自己去看清楚**"，不是"**自己修好**"。
要落地"自动优化修复小问题"，缺的不是策略，是**持久化的尝试记账**。

#### P1-I 人处理闭环是单向的：有"提示"，没有"处理"

- `tasks/alert_groups.go:209-232` 的 `Handling`（`automatic`/`approval`/`human`）
  **是从恢复计划推导出来的分类**（"需要人"= 计划没法自动执行），不是操作员的意图；
- 控制台 31 条路由里**没有一条是"认领/处理中/已解决/忽略"**；
- **`stage: escalated` 服务端算出来了（`alert_groups.go:48-51`），前端从不渲染**；
- 没有通知通道（无 webhook / 邮件 / 工单）、没有指派、没有 SLA、没有 ack。

**这套升级机制目前只在"人正盯着屏幕"时成立。**

#### P1-J 急停：软件侧存在，但控制台够不到

- 仿真侧有 `EmergencyStop` RPC（`sim/mujoco/tangying_sim/server.py:249,518-527`），**`console/` 零调用**；
- 通用代理 `POST /v1/robot/services`（`console/server.go:153`）虽接受任意服务名，
  但机器人服务目录只有 `calibration.*` / `mapping.*` / `navigation.map`（`robot_workflow.py:164-178`），
  **所以也调不到急停**；
- 唯一可用的软件急停链路在云端：`fleet/server.go:124,597-609` → `edge/worker/worker.go:153-166`
  → `edge/robotclient/client.go:495`，**前端没有按钮**；
- 本地最近似的动作是 `cancel` / `pause`，而 `internal/localapp/app.go:309` 自己注明"不是急停"。

与设计一致（`docs/architecture/supervision-verification.md:199` "软件不代按实体急停"），
但**软件层唯一能做的正确动作没有入口**，且 `E-Stop 安全` 是硬编码文本。

#### P1-K eval 只有 10 道题，而且题目本身在训练集里

- `orchestration/eval/cases.go:30 DefaultCases` 共 **10 条**：4 条可执行 + 6 条拒绝。
  实测 `planner=deterministic 6/10，executable 0/4，refusals 6/6`。
  **这是"10 个用例的通过率"，不是系统性度量**：4 条正向用例里翻 1 条就是 25%；
- **训练集与评测集同源**：`cmd/training-export/main.go:134-139` 无条件把
  `eval.DefaultCases` 里那 6 条拒绝用例追加进训练语料（注释说明是为了"安全关键样本不会因为有人忘了写文件而缺席"）。
  意图是好的，**但同一个集合既当训练数据又当门禁**，于是"refusals 不许下降"这条门禁测的是背过的答案；
- **无 holdout**，`Report` 也不含用例集哈希（`eval.go:107-123`），
  把用例改小后两份报告一起重跑就能通过 `gate.go:246-259` 的用例集校验；
- **`train/` 没有 CLI**：`Compare` / `Gate.Admit` 全仓只被它自己的测试引用，**流水线里调不到**；
- **CI 完全不跑 `nl-eval`**。

#### P1-L 过程证据是空壳：36/36 事故包没有步骤、没有证据、没有计时

| 事实 | 证据 |
| --- | --- |
| **36/36 个 `incident.bundle.json` 的 `stepRuns=[]`、`evidence=[]`、`timing=null`**，而同一部署的 DB 里有 247 条 `step_runs` | 唯一写入点 `internal/localapp/app.go:478-535` 只填 Task/Recovery/Timeline；`incidents/bundle.go:150-160` 把它们默认成空数组 |
| timeline 里带 `error` 的事件只有 **27/711**，且不含失败分类 | — |
| `training.Record.FailureClass`（`quantify.go:104`）、`Step.Action/Postcondition`（`:64-71`）**全仓无赋值** → 死字段 | 真实 DB 里 failed=0，`FailureClass` 永远为空 |
| **latency 不落盘** | `latency/recorder.go:106-128` 仅内存环，重启即丢 |
| 事故包**没有任何 HTTP 路由暴露** | 诊断只能靠离线 `scripts/diagnose_task.py` |

#### P1-M 回放是前端拼装，失败步骤只能看不能重放

`web/task_trace.js`（1,023 行）自行抓 4 个端点（任务 + 事件、`/experience`、`/observations`、`/recovery`）
在浏览器里对齐；后端只给结构化记录。客户端自建 `RECONCILIATION_REQUIRED` 检查，
物理步骤终态未知时禁止自动重放——**这条判断是对的**，但它活在浏览器里，换个客户端就没有了。

#### P1-N 数据集离"能交付"还差一整套元数据

| 项 | 判定 |
| --- | --- |
| 数据版本 | ❌ manifest 只有 counts/excluded，无 git sha / 时间 / 用例集指纹 |
| 场景分布统计 | ⚠️ 有 ByVerdict/ByPlanSource/DistinctRequests，无技能/意图/场景维度 |
| 负样本 | ⚠️ 机制有，实测 `negative.jsonl` **0 行** |
| 去重 | ✅ `Fingerprint` + 按请求择优（65 任务 → 15 请求） |
| **脱敏** | ❌ 中文请求原文进 DB / bundle / jsonl，无 redact |
| **许可与隐私** | ❌ 无 license / provenance 字段 |
| **train/val/test 切分** | ❌ `sft/refusals/negative` 是**类型切分**，不是数据集切分 |

**实测导出：`sft 0 / refusals 6 / negative 0`，排除 `verified-but-no-plan 4`、`unknown-outcome 3`、`unfinished 8`。
不能直接用于 SFT/DPO/RL。**

一个必须说清楚的澄清：`training/` 与 `train/` **不训练任何模型**，只做数据准备与判卷；
仓库里唯一真会"训"的是 `sim/mujoco/tangying_sim/training/` 的表格 Q 学习
（`scripts/train_semantic_policy.py train`，实测 `successRate 1.0`），**与 LLM 后训练无关**。

#### P1-O 多机器人：只跑通一条硬编码物料的顺序交接

可跑通：单任务顺序双机交接、绑定/未绑定认领、revision CAS + 安全点、token 移交、harness 后置条件。
**未完成**：① 无跨任务调度器（仅 per-task 顺序）；② 资源模型只有 1 个硬编码物料；
③ leader 无故障切换；④ 世界不跨主机；⑤ MySQL/Redis 单实例无 HA；⑥ 实机未接 grant
（`sim/mujoco/tangying_sim/fleet_server.py:37-41` 是唯一来源，实机 RPi 网关无调用者）；⑦ 无碰撞规避。

---

## 五、对照目标的差距矩阵

| 你的目标 | 现状 | 差距 |
| --- | --- | --- |
| 自然语言任务 → 自动拆解执行 | 分解链路通（`source=llm` 实测 4/4 可规划） | **执行链路断**（P0-A） |
| 执行中启动 Runner / Ops / Recovery Agent | 三个 Agent 已实现、已托管、有仲裁与权限门控 | 架构完好，**但被 P0-B 噪声淹没** |
| 监控任务执行过程中的问题 | 规则化诊断 + `robot.faults.v1` 台账 + 113 故障码分类 | 能力完整，**但慢步骤告警从未通电** |
| 出现问题自动优化修复小问题 | 6 个只读步骤可自动；7 个改动型需批准；0 个自动重试 | **缺持久化尝试记账**（P1-H） |
| 提示人处理复杂问题 | 升级事件 + 问题页 + `handling` 分类 | **单向**：无认领/SLA/超时；`escalated` 算了不显示（P1-I） |
| 每个阶段、每个任务可回溯 | 单一账本 + 任务回放 | **账本 99.3% 噪声**；trace 死代码；事故包 36/36 空壳（P0-B / 3.3 / P1-L） |
| Eval 体系、过程量化 | 编排 eval 两半打印 + 四段耗时实现 | **只有 10 道题、题目在训练集里、无 holdout、不在 CI**（P1-K） |
| 留存高质量数据集 | 确定性导出 + Quantify + 拒绝样本采集 | **0 正样本；无版本/切分/脱敏/许可**（P1-N） |
| 安全 | 8 类分类、`never_automatic` 分级、模型不碰底层 | **批准不可证伪 + 云侧 2 分钟定时器违反闭环 + 三条契约未接线**（P0-D/E、3.1） |
| 稳定 | 有界队列 + 丢弃计数 + 就绪自检 | **账本无界增长**；云侧租约不续期；无资源遥测 |
| 高效 | 四段耗时已采集 | **无消费者、无汇总对比**（"从未通电"） |
| 可扩展 | 新增 Agent 不改核心（有架构测试） | **新增工具/本体/传感器要改硬编码白名单与枚举**（3.5）；Agent 运行时与分布式控制面不接（P1-G） |

---

## 六、完善计划

排序原则只有一条：**每一项都必须先能回答"不做它，哪一个具体任务会失败或无法衡量"。**
不做框架级重构；每一阶段都有**可复现的数字判据**，达不到就停在这一阶段。

### 阶段 0：止血（1 周）——让账本可信，让安全边界真的是边界

**为什么第一。** 在 99.3% 噪声的账本上做回溯、量化、数据集，等于在涂改过的卷宗上断案；
而 P0-D / P0-E 是**当前就存在的真实暴露面**，不修就等于把"安全"这条卖点挂在未经强制的假设上。
这一阶段**不触碰执行语义**，风险最低。

| # | 做什么 | 落点 |
| --- | --- | --- |
| 0.1 | **账本只记状态迁移**：一个条件只在 `opened` / `changed`（计数或严重度变化）/ `cleared` 时写一行；高频重观测留在内存投影 | `agentruntime/projection.go` 落盘策略 + `opsagent.go:416` 冷却语义 |
| 0.2 | **给 `UNKNOWN_OUTCOME` 一条清除路径**：区分"阻塞重试该步骤"与"阻塞整机接新任务"，后者需要一个操作员可用的对账入口（`readiness.md` §7 缺的就是它） | `core/closedloop` + `tasks/` + `console/readiness.go` |
| 0.3 | **闭环契约补齐三处接线**：Freshness 用来源新鲜度预算真算（复用 `core/worldmodel/projector.go:259`）；gate 的 `MutatesWorld` 只信清单不信自述（复制 `edge/agent/runner.go:618-632` 的模式）；云侧 `coordinator.go:603` 不再直写成功 | 4 处，逐一补反例测试 |
| 0.4 | **控制台安全边界**：所有写接口加统一的 Origin + `Sec-Fetch-Site` + Content-Type 校验（抄 `robot_services.go:47`）；`/v1/recovery/execute` 的批准证据必须来自**可验证的人**（会话令牌或本地握手），不能是"有人调用了它"；默认强制 loopback，非环回启动要么拒绝、要么强制鉴权；修 `maps.go` 非 SLAM 分支的符号链接围栏与绝对路径回显 | `console/` |
| 0.5 | **云侧删掉那个 2 分钟定时器**，或改成与闭环契约一致的语义：claim 过期**不得**把节点改回可执行，只能转人工对账 | `fleet/coordinator/coordinator.go:237-258` |
| 0.6 | **状态如实**：Fleet 的自动批准要么改成真等待、要么文案改成如实说明；`E-Stop` 状态改成真实读数；审批记录加操作员与时间 | `web/app.js:5985-5987`、`web/index.html:351`、`tasks/service.go:600` |
| 0.7 | **"通电审计"**：为每一条"已实现的能力"补一个"有没有生产数据源 / 有没有消费者"的检查项，把 `ANOMALY_STEP_LATENCY`、bundle `Timing`、`/v1/telemetry/latency`、`observation.Causation` 纳入 | 新增检查脚本 + CI |
| 0.8 | **`task_events` 分级保留 + 体积自检** | `middleware/sqlite` |
| 0.9 | **修正口径**：README 四项声明与代码对齐；无法复现的声明降级并写明条件 | `README.md`、`console/server.go:1` |

**判据（可测）**：
- 同一场景跑 10 个任务，**单任务事件数 < 100**（现状 7,737）；账本 `ops.*` 占比 **< 30%**（现状 99.3%）；
- 卡在未知结果的任务，**24 小时内重复事件 ≤ 3 条**（现状 1,440）；
- `Freshness` 三条路径各有一个反例测试：**把来源标成 STALE 后 gate 必须拒绝**（现状会放行）；
- **远端报 `mutates_world=false` 时 gate 仍然生效**（有测试）；
- **跨站 `text/plain` POST 到 `/v1/recovery/execute` 与 `/v1/tasks` 必须被拒**（有测试）；
- **没有可验证的人的身份时，`OperatorApproved` 不得为真**（有测试）；
- **claim 过期后节点不得回到可执行状态**（有测试）；
- "通电审计"能列出所有"有实现无数据源"的能力；修完后该清单为空；
- 清除路径可让 `readiness.ready` 变真，且清除动作本身落一条不可改写记录。

**风险与对策**：降采样可能丢掉"状况恶化"信号 → 保留 `changed` 分支，
并补"计数从 1 涨到 5 必须重报"的用例（`core/agentcontract/anomaly.go:35-43` 已定义这条语义）。

### 阶段 1：让主线真的跑通（1–2 周）

| # | 做什么 | 落点 |
| --- | --- | --- |
| 1.1 | **执行顺序改成"导航 → grounding → 规划"的外层循环**：grounding 在目标不在当前房间时返回"尚未定位"这一**可规划结果**，而不是硬失败 | `edge/agent/runner.go:224` 附近的 `RunControlled` |
| 1.2 | **家居任务套件接进 CI**：真场景、真 Agent、真审批，三场景 `patrol` / `inspect-kitchen` / `mug-transfer` | `.github/workflows/ci.yml` + `scripts/run_home_task_suite.py` |
| 1.3 | 修好后**如实更新** README 验收表与 `docs/releases/` | `README.md` |

**判据**：
- 三场景各 10 轮，**端到端成功率 ≥ 90%**，`mug-transfer` 的 12 步**每一步都有新鲜证据**；
- CI 里任一场景失败即红；
- 失败输出**结构化失败分类**（沿用 8 类），不是一行错误字符串；
- 在 `tests/architecture/` 加一条"grounding 不得先于计划执行"的结构约束。

**风险**：1.1 动的是执行主路径。对策：先用**只读重放**验证顺序（`step_runs` + `observation_evidence` 已有数据），再上真场景。

### 阶段 2：装上刻度——EvalAgent + 端到端指标 + holdout（2 周）

| # | 做什么 | 落点 |
| --- | --- | --- |
| 2.1 | **实现 EvalAgent**（`contract.go:59-61` 已预留 `completion.verification`）：只能读证据、只能否决完成声明，**不能改任务状态、不能驱动硬件**——与 OpsAgent 同规格，由权限门控与反射测试钉住 | 新增 `agentruntime/evalagent.go` |
| 2.2 | **端到端指标集**（全部从账本算，不新增埋点）：任务成功率、首次到达即见目标率、无效行驶距离、恢复次数与成功率、四段耗时 p50/p95、未知结果率 | `orchestration/eval` 扩展 + `training/quantify.go` |
| 2.3 | **把训练集与评测集切开**：`DefaultCases` 保留为公开集，另建一份**不外泄的 holdout**；导出的拒绝样本**不得**来自 holdout | `cmd/training-export/main.go:134-139`、`orchestration/eval/cases.go` |
| 2.4 | **给 `train/` 一个 CLI 并挂进 CI**：`Gate.Admit` 现在是调不到的库 | 新增 `cmd/train-gate` |
| 2.5 | **Report 带上用例集指纹**，用例集变化时拒绝与旧基线比较 | `eval.go:107-123`、`train/gate.go:246-259` |
| 2.6 | **把 `latency` 的四段耗时接进 OpsAgent 的 `Latency` 事实**，让 `ANOMALY_STEP_LATENCY` 真的会响 | `agentruntime/opsagent.go:311-319` |

**判据**：
- EvalAgent 的反射测试证明它**没有执行端口、不能改任务状态**；
- 端到端指标与手工核算**偏差 < 5%**；
- 人为注入一次退化（例如把 grounding 顺序改回去）**CI 必须红**；
- **慢步骤告警能被真实数据触发**（有测试，且有一次真实触发记录）。

### 阶段 3：把过程变成可交付的数据集（2 周）

| # | 做什么 | 落点 |
| --- | --- | --- |
| 3.1 | **事故包填实**：`stepRuns` / `evidence` / `timing` 从账本读出来（现在 36/36 是空的），并暴露 HTTP 路由 | `internal/localapp/app.go:478-535`、`incidents/bundle.go`、`console/` |
| 3.2 | **latency 落盘**，并让前端消费 `GET /v1/telemetry/latency` | `latency/recorder.go`、`web/app.js` |
| 3.3 | **数据集版本化 + manifest**：git sha、导出时间、用例集指纹、场景分布、train/val/test 切分、正/负/拒绝计数、**与上一版的 diff** | `training/export.go` + `cmd/training-export` |
| 3.4 | **拒绝样本采集**：被拒请求现在**根本不落盘**（服务在创建时就拒了）→ 补一条"拒绝也留痕"的最小路径 | `tasks/service.go` |
| 3.5 | **脱敏与来源**：请求文本 redact 策略 + license / provenance 字段 | `training/` |
| 3.6 | **`Ledger` / `Beads` 持久化**（现在只有 `Execution` 是真实现）——这是 ExperienceAgent 的消费者 | `core/agentcontract/memory.go` |
| 3.7 | 填掉死字段 `FailureClass` / `Step.Action` / `Step.Postcondition`，或删掉它们 | `training/quantify.go` |

**判据**：
- 一次导出给出：正样本 N 条 + 分布表 + **与上一版 diff**，退出码反映数据是否够用；
- 每个 `incident.bundle.json` 的 `stepRuns` / `evidence` / `timing` **非空且与账本一致**；
- `unknown-outcome` 在正负两半都不出现（有测试，`quantify.go` 的文档与实现曾矛盾、被测试抓到过）；
- latency 重启后仍可查。

### 阶段 4：把"提示人"变成"人到场"（2 周）

| # | 做什么 | 落点 |
| --- | --- | --- |
| 4.1 | **实现 EscalationAgent**（`contract.go:65-67` 已预留 `escalation`）：升级队列状态机 `open → claimed → handling → resolved / wontfix`，带 owner、SLA、**超时默认拒绝** | 新增 `agentruntime/escalationagent.go` |
| 4.2 | **把 `stage: escalated` 真的显示出来** | `web/problems.js`、`web/app.js` |
| 4.3 | 控制台入口：认领 / 移交 / 解决 / 忽略（忽略必须写理由且进账本） | `console/` + `web/` |
| 4.4 | 审批升级：从"整任务一个布尔位"到**按步骤 + 带身份与时间 + 超时拒绝** | `tasks/service.go`、`console/server.go` |
| 4.5 | **补急停入口**：本地控制台加一个真正调用机器人急停的按钮（走 `robot/services` 或新增路由），Fleet 的 `estop` 路由接上 UI | `console/`、`web/` |

**判据**：
- 需人的发现在 **1 个 tick 内**进队列，有 owner 与截止时间；
- SLA 到期默认动作是**拒绝**，有测试；
- 操作员的每个动作都可在回放里看到，且**不可被 Agent 改写**；
- **每一条审批记录都能回答"谁、何时、依据什么"**，有测试；
- **急停按钮从 UI 到机器人是一条可用链路**（有测试或验收记录）。

### 阶段 5：统一 trace 与可重放回放（2 周）

| # | 做什么 |
| --- | --- |
| 5.1 | **给 `core/trace` 补上 TraceID / SpanID / ParentID**（现在是 13 行死代码，`TraceStore` 无实现），跨进程传播——**复用现有 `CausationID`，不发明第三套 id** |
| 5.2 | **让 `observation.Causation` 真的被写入**（字段上线了，生产端零赋值） |
| 5.3 | **单文件轨迹导出**：一次任务导出自包含 trace（因果链 + 每步证据引用 + 失败分类 + 恢复历史），人和训练共用同一份 |
| 5.4 | **回放从浏览器搬回后端**；把客户端那个 `RECONCILIATION_REQUIRED` 判断变成服务端规则 |
| 5.5 | 统一 metrics 的三个产出点，加一个可导出端点；logs 结构化并带关联 ID |
| 5.6 | 消除 `telemetry.RobotState map[string]any` 这个绕过 project 的逃逸口 |
| 5.7 | 任务事件 WS 改成真事件驱动（现在是 200 ms 轮询 SQLite）；前端改用已实现的世界 WS（现在绕过它轮询） |

**判据**：
- 任意历史任务可导出单文件 trace，**不需要访问运行中的进程**；
- 从 trace 能回答"哪个 Agent 在哪一步因为什么失败"，且可被 `step_runs` 交叉验证；
- **重放一个失败任务不产生任何世界写入**（有测试）；
- 一次任务的延迟在 WS 上是推送到达（有测试测时延，不再是 200 ms 粒度）。

### 阶段 6：把两个半边接起来（3–4 周）

**为什么排在后面。** 把一个还没跑通、还没有刻度、还没有 trace 的系统分布化，只会让问题更难定位。

| # | 做什么 |
| --- | --- |
| 6.1 | **解耦 `agentruntime` 与 `cmd/local-agent`**：EventBus 后端可替换（进程内 → fleet EventLog），Orchestrator 可作为服务托管。目标是"**同一套 Agent 代码，既能在单机跑，也能在控制面托管**" |
| 6.2 | **边缘侧观察者**：让 `edge-worker` 至少跑 OpsAgent 的采集半边（快环异常检测不能等一次跨网往返） |
| 6.3 | **租约与 fencing 补齐**：为任务节点做**真正的续期**；把 fence 校验与提交做成原子（或把 fence 放到与业务同一存储）；资源模型从 1 个硬编码物料扩到地图/位姿/持物/充电位 |
| 6.4 | **分区安全停机与对账**：设备租约掉线时的停机回调；worker 检查链路后再做物理动作；Fleet 侧实现 reconcile |
| 6.5 | **可扩展性去硬编码**：白名单与枚举改成数据（`core/robotcontract/contract.go:110,120,135,139`、`core/observation/envelope.go:20-27`） |
| 6.6 | **故障注入矩阵**：分区、脑裂、Outbox 重复投递、旧持有者写入，逐项有测试**并接真实 Redis/MySQL**（现在全是内存模型） |
| 6.7 | leader 故障切换；`REDIS_ADDR` 缺失时不得静默退化为进程内 leader |

**判据**：
- 两台机器人 + 一台控制面跑同一套 Agent 代码；**杀掉控制面，机器人的实时环与安全监督不受影响**（有测试）；
- 旧 fencing token 的写入被**数据层**拒绝（不是应用层 if）；
- **超过 claim lease 的物理动作不会被回滚成可执行**（反例测试）；
- 脑裂/分区/崩溃恢复的测试**跑在真实 Redis/MySQL 上**，不再是内存模型；
- **新增一个工具 / 一个本体 / 一种传感器，各只需新增数据文件，零代码改动**（有测试证明）。

### 阶段 7：有界自动修复（2–3 周，可与阶段 6 并行）

**为什么最后。** 自动改动物理世界是这套系统里**唯一不可逆**的动作，
必须在"有刻度、有 trace、有人兜底"之后才允许扩大。

| # | 做什么 |
| --- | --- |
| 7.1 | **持久化尝试记账**——这正是 `closedloop.Track` 被删除时写下的前提条件（谁批第二次、跨重启怎么算、如何串进证据门） |
| 7.2 | **失败分类 → 恢复动作覆盖矩阵**：8 类失败 × 16 个动作，逐格标注"自动 / 需批准 / 禁止"，**空格即缺口** |
| 7.3 | 逐格放行：每放行一格，必须同时有一条**反例测试**（越界被拒）和一条**回滚路径** |

**判据**：
- 矩阵**无空格**；
- 每条自动路径都有：反例测试、回滚路径、一次真实成功的证据；
- 尝试预算跨进程重启**仍然有界**（测试：杀进程后重建，计数不归零）。

---

## 七、明确不做

- **不做框架级重构。** 阶段 6.1 的解耦是唯一结构性改动，判据是"同一套代码两种托管"，不是"架构更漂亮"。
- **不在 grounding 修好之前做 RL。** 环境是坏的，reward curve 看起来会像学习，实际在拟合噪声。
- **不让 EvalAgent 或任何 Agent 拥有移动判据的能力。** 判卷的人不能改判据。
- **不用软件路径"解除"急停。** 保持不变——但阶段 4.5 必须补上"软件层唯一能做的正确动作"的入口。
- **不把观察者的高频重观测写进任何"事实"存储。** 这正是阶段 0.1 要修的，不要在别处重犯。
- **不承诺跨地域生产级高可用。** 那属于 Fleet 的待验证清单。
- **不在没有真实 Redis/MySQL 的测试之前，把 Fleet 用于任何真机。** 见 P0-E。

---

## 八、一页纸验收清单

按顺序打勾；**任何一项没有数字就不算完成**。

| # | 判据 | 现状 |
| --- | --- | --- |
| 0.1 | 单任务事件数 < 100；`ops.*` 占比 < 30% | 7,737 / 99.3% |
| 0.2 | 未知结果有清除路径，清除后可 `ready`，且留痕 | 无路径（`readiness.md` §7 自认） |
| 0.3 | 来源标 STALE 时 gate 必拒；远端 `mutates_world=false` 不豁免；云侧不直写成功 | 三处均放行 |
| 0.4 | 跨站 POST 被拒；`OperatorApproved` 需可验证的人；非环回启动必须鉴权 | 均无 |
| 0.5 | claim 过期不得把节点改回可执行 | 2 分钟后会（`FLEET_INTENT_LEASE=2m`） |
| 0.6 | 审批记录含操作员与时间；无"自动批准却显示等待"；E-Stop 状态真实 | 布尔位；`app.js:5985/5987` 自相矛盾 |
| 0.7 | "通电审计"清单为空 | 至少 7 项（见第一节表） |
| 0.8 | `task_events` 有分级保留与体积自检 | 无 |
| 0.9 | README 四项声明与代码一致 | 4 处不符 |
| 1.1 | 移动操作任务端到端成功率 ≥ 90%（3 场景 × 10 轮） | **单条任务已跑通 11 步操作序列**（抓取+放置有证据）；返程止于 `GOAL_NOT_CLEAR` |
| 1.2 | 家居场景在 CI 里跑，失败即红 | ⬜ 未接。**本轮定位到真正的阻塞点**，见下 |
| 2.1 | EvalAgent 有反射测试证明无执行端口 | 未实现 |
| 2.2 | 端到端指标与手工核算偏差 < 5% | 无 |
| 2.3 | 训练集与评测集不重叠，holdout 不外泄 | 6/6 拒绝用例逐字同源 |
| 2.4 | `train/` 门禁有 CLI 且挂进 CI | 无 CLI |
| 2.6 | 慢步骤告警能被真实数据触发 | 字段从不填，永不触发 |
| 3.1 | 事故包 `stepRuns/evidence/timing` 非空且与账本一致 | 36/36 空 |
| 3.2 | latency 重启后仍可查，且前端消费 | 内存环，前端零引用 |
| 3.3 | 一次导出给出样本数 + 分布 + 与上版 diff | 0 正样本 |
| 3.4 | 被拒请求留痕 | 不落盘 |
| 4.1 | 需人的发现 1 tick 内进队列，有 owner 与 SLA | 无队列 |
| 4.2 | `stage: escalated` 在 UI 上可见 | 服务端算了，前端零渲染 |
| 4.5 | 急停从 UI 到机器人是可用链路 | 本地无路由，云端有路由无按钮 |
| 5.1 | 任意历史任务可离线导出单文件 trace | `core/trace` 13 行死代码 |
| 5.2 | `observation.Causation` 在生产端被写入 | 零赋值 |
| 5.7 | 任务事件真正推送，世界 WS 被前端使用 | 200 ms 轮询；前端绕过 WS |
| 6.1 | 同一套 Agent 代码在单机与控制面都能托管 | 仅 `cmd/local-agent` |
| 6.3 | 任务节点租约真续期；fence 与提交原子 | 均无 |
| 6.5 | 新增工具/本体/传感器零代码改动 | 需改 3–4 处硬编码 |
| 6.6 | 脑裂/分区测试跑在真实 Redis/MySQL 上 | 全内存模型 |
| 7.1 | 尝试预算跨重启有界（有测试） | 无（`Track` 已删） |
| 7.2 | 8 类失败 × 16 动作矩阵无空格 | 部分 |

---

## 九、这份文档的边界

- 本文数字来自**本机真实账本**（65 个任务、159,314 条事件、356 MB）与代码静态审计。
  这是**一台开发机上的单机器人仿真**，**不是实机结论**；按本仓库自己的口径，
  软件发布与实机放行是两项独立结论。
- **分布式部分（P0-E、P1-O、阶段 6）只做了静态审计**：读了导入关系、main 入口、schema 与代码路径，
  **没有跑过双机故障注入，也没有启动过真实 Redis/MySQL**。
  所以那里的结论是"代码看起来怎样"，不是"实测怎样"——但它足以支持"上真机前必须先补这些测试"。
- **我本轮审计中撤回了一处自己的错误**：曾判断任务事件 WS"连 Origin 都不查"，
  实际 `websocket.Upgrader{}` 零值走 gorilla 默认 `checkSameOrigin`，与手写检查等价。
  该结论已作废，不影响 P0-D 的其余部分。
- 阶段 0.1 的降噪方案会改变**回放看到的内容**。它必须与"操作员需要持续被告知"这个正确诉求一起设计，
  而不是简单地把事件砍掉——`opsagent.go:372-374` 的意图是对的，错的是**它落到了只追加账本上**。
- 本次为**只读审计**，没有修改仓库任何代码，也没有改动数据。
  审计过程构建的 `bin/nl-eval`、`bin/training-export` 与导出的样例数据留在
  `artifacts/scratch/audit-dataset/`（已被 gitignore）。


---

## 十、转向：先要一个稳定的可用版本（2026-09-19）

用户改变了优先级：**先调通一个生产稳定的版本**，再一个模块一个模块地提升功能完成度；
数据库可以清空、数据可以从头制造。这一节记录这条路线上的实测与阻塞。

### 10.1 已做的一处真实改进：控制台会话的"你连的是哪一个"

一台机器上同时跑两个控制台是常态，而令牌发现为此错了两次：

| 规则 | 什么时候错 |
| --- | --- |
| "固定目录清单里第一个命中的" | 第二个部署一出现，就返回旧部署的令牌 |
| "最新的那个文件胜出" | 旧部署比被测的那个后重启，就返回错的 |

两种错法的表现**一模一样**：对着一个健康的控制台拿到 `CONSOLE_SESSION_REQUIRED`，
读起来像"门禁坏了"，实际是"你拿着别人的钥匙"。

修法：**agent 把 `console-address` 写在令牌旁边**（`cmd/local-agent`），
调用方带上自己的 `base_url`，按地址取对应控制台的令牌；不带地址时回落到"最新"。
6 个 Python 客户端全部改成传 `base_url`。

这是"生产稳定"该有的形态：**答案由"你在跟谁说话"决定，而不是由"谁的文件更新"决定。**

### 10.2 阻塞：这台机器的负载让"稳定"无法测量

清空数据库、起全新栈（sim 50361 / agent 8908 / 独立地图目录）之后，文档流程在巡检建图处失败：

```
RGB-D capture is stale or future dated (age 2461 ms)
```

`max_age_ms` 是 2000。**超预算 461 ms**——而这个检查在 `robot/gateway/.../rgbd.py`，
与本轮任何改动无关（先核实再归因）。同一台机器上还出现 `DeadlineExceeded`（遥测 RPC 超时）。

原因是机器负载：

| 时刻 | load average |
| --- | --- |
| 巡检首次失败 | 17.3 |
| 停掉我起的全部进程之后 | 16.1 → 19.7 → **22.6** |

而 `lsof` 只看到 **2 个 python 进程**——它们在满核空转（MuJoCo 的 OSMesa 软件渲染很吃 CPU）。
`ps` / `top` 被沙箱拒绝，所以我**无法确定地把负载归因到某个进程**，也就没有资格去杀它。

**结论：在负载 20+ 的机器上，任何"稳定版"的结论都不成立**——它会把资源竞争
读成代码缺陷（这一轮就发生了两次：`stale capture` 与 `DeadlineExceeded`）。
测稳定版之前需要一台安静的机器，或者先确认那两个进程是谁的。

### 10.3 模块清单（下一步"一个模块一个模块"的入口）

按代码量与测试面排出待优化模块，每个模块的"优化"都要能给出一条可测的判据：

| 模块 | 代码行 | 测试文件 | 当前最该看的一件事 |
| --- | --- | --- | --- |
| `agentruntime` | 5,317 | 11 | 三个 Agent 的分工与协作是否覆盖了全部失败路径 |
| `robot/gateway` | 23,492 | 36 | 传感器新鲜度预算（2 s）与实际管线延迟是否一致 |
| `sim/mujoco` | 16,879 | 23 | 遥测/采集延迟；测试抖动 |
| `fleet` + `edge` | 12,633 | 59 | 分布式语义（租约续期、原子 fence、真实 Redis/MySQL 测试） |
| `console` | 3,668 | 17 | 操作员闭环（认领/SLA/急停入口） |
| `tasks` | 3,650 | 18 | 任务状态机与修订 |
| `edge/agent` | 1,482 | 10 | 执行顺序与物理前置步骤 |
| `orchestration` | 1,338 | 4 | 规划器；评测刻度 |
| `middleware/sqlite` | 1,542 | 6 | 账本保留与体积 |
| `core/*` | 1,551 | 8 | 闭环、世界模型、新鲜度 |
