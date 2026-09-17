# 模块化机器人的故障上报与自愈设计（XLeRobot：底盘 / 机械臂 / 上位机 …）

**问题**：XLeRobot 由底盘、双臂、夹爪、头部、相机、总线、上位机等模块组成，任何一块都可能坏。现在这些故障到不了"大脑"——它们以零散字符串的形式散在驱动与运行时里（`ARM_FAILED`、`SERIAL_PORTS_UNAVAILABLE`、`HARDWARE_ERROR`），**没有模块身份、没有严重程度、没有处置方式**。于是大脑只能说"出错了"，人得自己猜是哪个部件。

**方案一句话**：把"哪个模块坏了、有多严重、能怎么办"做成**契约化的事实**，让它可以被观测、被门禁消费、被 LLM 提议处置、被人一键确认——而不是靠读日志猜。

---

## 一、今天的接口面（设计必须落在这些已有接缝上）

| 已有能力 | 位置 | 与本设计的关系 |
| --- | --- | --- |
| 能力清单 + `available` + `blockers` | 运行时 `CapabilityInfo`；Go `runtime.Snapshot` | **门禁已经存在**：能力不可用即计划期失败关闭（`ErrCapabilityUnavailable`），`PhysicalReady()` 要求无 blocker |
| 观测流（含 `anomalies`） | `Observation.semantic_state` → WorldHub → 世界快照 | **故障天然是观测**，不需要新总线 |
| 安全监督 | ROS 2 `tangying_safety_supervisor`：`emergency_stop` + `safety_stop_reason` | 安全级故障的最高优先级来源 |
| 标定/地图/协议修订 | `calibrationRevision`、`mapRevision`、`robot.profile.v1` | 模块级故障需要同样可追溯的"身份" |
| 失败分类 | `core/closedloop`（含 `EXECUTION_OUTCOME_UNKNOWN` 等） | 任务级后果的分类，故障要映射到它 |
| 事故记录 + 诊断 | 本轮之前的 `incidents/`、`diagnose_task.py` 故障族 | **通知与复盘那一半已经存在**，缺的是"模块级事实"这一半 |
| 审批策略 | `ApprovalPolicy`（物理动作需批准） | 处置动作沿用同一套信任模型 |
| 运行中自检 | `get_robot_status`（`HardwareHealth`：reachable/errors/available_tools/battery） | 粒度太粗，本设计把它升级为模块视图 |

## 二、五层设计

```
① 模块身份（profile 数据）       modules[]: moduleId / kind / criticality / repairClass
        │
② 故障事实（fault.v1）           moduleId + code + severity + remedy + 用户指令 + 证据
        │  ← 驱动、运行时、安全监督、自检都只产出这个
        ├──────────────► ③ 能力联动：fault → 能力 unavailable(blockers=[faultId])
        │                     （复用已有门禁；Go 无需知道故障词汇）
        ├──────────────► ④ 处置阶梯：self_recover / operator_assist / service_required
        │                     LLM 只能执行故障自己声明的 self_recover
        └──────────────► ⑤ 用户提醒：模块视图 + 当前故障 + 还能做什么 + 该做什么
                              （控制台面板 + 任务事件 + 事故记录）
```

### ① 模块身份：加数据，不加代码

在 `robot.profile.v1` 上增 `modules[]`：

```json
{
  "moduleId": "arm-left", "kind": "arm", "criticality": "mission",
  "capabilities": ["manipulation.pick", "manipulation.place", "verify_grasp"],
  "repairClass": "operator_assist",
  "dependsOn": ["bus-can", "power-12v"],
  "selfTest": "arm_self_test"
}
```

- `capabilities` 就是**能力 → 模块**的声明表，`capability_impact()` 直接消费它；
- `dependsOn` 让"总线坏了"自动牵连它上面的所有模块（级联故障不靠人推）；
- `selfTest` 指向一个**有界、无运动或微运动**的自检动作（见 §四）；
- 新增一个模块 = 加一条数据 + 一组故障码，**不改门禁代码**。

### ② 故障事实：`robot.faults.v1`

```json
{
  "schemaVersion": "robot.faults.v1",
  "severity": "blocked",            // 最严重者：info < degraded < blocked < safety
  "count": 2,
  "faults": [{
    "moduleId": "chassis", "kind": "chassis", "code": "SERIAL_PORTS_UNAVAILABLE",
    "severity": "blocked", "detail": "no /dev/ttyACM* after 3 retries",
    "occurrences": 7, "remedy": "operator_assist",
    "userInstruction": "底盘串口未找到：重新插拔底盘 USB 并确认供电，然后点『重新自检』。",
    "detectedAtUnixMs": 1789500000000, "lastSeenUnixMs": 1789500600000,
    "ageMs": 600000, "sinceLastSeenMs": 0,
    "evidence": {"ports": [], "serviceLog": "…"}
  }],
  "operatorActions": ["底盘串口未找到：重新插拔底盘 USB 并确认供电，然后点『重新自检』。"],
  "unavailableCapabilities": ["navigation.navigate"],
  "capabilityBlockers": {"navigation.navigate": ["chassis:SERIAL_PORTS_UNAVAILABLE"]}
}
```

**已实现的第一片**（`robot/gateway/tangying_robot_gateway/module_faults.py`）：词汇表（模块种类 / 四种严重度 / 三类处置）、`Fault` 校验、`FaultLedger`（去重、有界、**首见与最近一次分别记录**、清除）、`capability_impact()`（把故障翻成能力封锁）、`snapshot()`（控制台与大脑读的那一份）。13 条测试覆盖：模块坏了正好封锁依赖它的能力、底盘故障不牵连机械臂、`info` 不封锁能力、多模块同时坏各自列名、**自愈重复到阈值自动升级为需要人**、重复故障保留首见时间、非自愈故障不允许自动处置、清除已修复模块、畸形数据被拒、台账满时明确报错而不是悄悄丢弃。

**已实现的第二片**（运行时发布 + Go 消费，见 §三之三）：
- 运行时把它**能自证**的三件事变成故障并随观测发布：急停锁存（`estop:EMERGENCY_STOP_LATCHED`，`safety`）、工位支撑高度与标定不符（`workcell:WORKCELL_CALIBRATION_MISMATCH`，`blocked`）、移动场景没有启用地图（`chassis:NAV_MAP_NOT_READY`，`blocked`）。**只发布能证明的**：证不出来的不写进台账，故障表短一点好过喊狼来了。
- 故障进观测的 `robot_state.faults`，Go 侧用契约解码（`core/robotcontract/faults.go`）后进 **WorldHub 的 `world.snapshot.v1`（按机器人挂 `faults`）**，控制台遥测 API 也带（`GET /v1/telemetry` 的 `latest.faults`）。
- **`safety` 级故障不是"某个模块坏了"**：它撤掉**所有声明了依赖的能力**；唯一必须活下来的是声明"不依赖任何模块"的 `emergency_stop`（急停时机器人仍然必须能被停）。`info` 仍然什么都不撤。

**关键取舍**：故障是**观测**（进 WorldHub、带 revision、可追溯），不是异常。理由与 `anomalies` 一致——世界状态里少了"某个模块坏了"这条事实，大脑的任何推理都是错的。

### ③ 能力联动：复用已有门禁，不在第二种语言里复制知识

运行时按 `capabilities` 声明把受影响的工具标记 `available=false, blockers=[faultKey]`；Go 侧**已经**在计划期失败关闭并在运行时拒绝（`ErrCapabilityUnavailable`，`PhysicalReady()`）。

两条规则（都有测试）：

1. **按模块精确封锁**：`chassis` 坏了只摘导航，机械臂能力不受影响——降级运行是常态，不是例外；
2. **`safety` 级故障整体封锁**：锁存的急停不是"底盘坏了"，它撤掉**所有声明了依赖的能力**；`emergency_stop` 声明自己**不依赖任何模块**，所以它永远可用（一个已经停住的机器人，仍然必须能被停住）。

所以：**故障词汇表只存在于 Python 运行时一处**，Go 只消费 `available`/`blockers`（以及 `robot.faults.v1` 这份人类可读的事实）✓ 这与"故障族知识只在 `diagnose_task.py` 一处"是同一条原则。

### ④ 处置阶梯：谁能动手，由故障自己声明

| 处置类 | 谁执行 | 例子 | 门禁 |
| --- | --- | --- | --- |
| `self_recover` | 机器人（LLM 可提议） | 重试命令、机械臂回零、重连设备、重新观测、清空队列 | 必须是有界工具；**仍要过审批策略**；每次自愈写事件与计数 |
| `operator_assist` | 人 | 复位急停、插拔线缆、重启服务、换电池、挪开障碍 | 只能提示 + 等待，**软件不代替** |
| `service_required` | 维修 | 换电机、换相机、修结构件 | 生成故障单（事故记录 + 证据） |

**两条硬规则**（已在第一片里实现并有测试）：

1. **LLM 只能执行故障自己声明的 `self_recover`**：`ledger.remedy_allowed(key, "self_recover")` 对非自愈故障返回 `False`；"请人处理"永远是允许的（那是消息，不是运动）。
2. **自愈不得掩盖故障**：同一故障重复到 `ESCALATION_THRESHOLD=5` 次，`self_recover` **自动升级为 `operator_assist`**——每十分钟需要复位一次的模块，即使还能动也是坏的。

### ⑤ 用户提醒：说清三件事

控制台"我的机器人"面板（已有页面，扩展即可）显示：

1. **现状**：每个模块一行（绿/黄/红）+ 最严重故障的 `userInstruction`；
2. **还能做什么 / 不能做什么**：直接用 `unavailableCapabilities` 与"仍然可用"的差集，不让用户猜；
3. **该做什么**：按 `remedy` 分组——"点这里重试"（self_recover，按钮即调用有界工具）/ "请人工处理：…"（operator_assist）/ "需要维修：…"（service_required，附事故记录链接）。
   **今天所有故障都是 `operator_assist`**，所以这一栏只会出现"请人工处理"；上面的状态说明解释了为什么。

任务侧同样有出口：受影响能力不可用 ⇒ 任务在**计划期**失败并给出原因（现状已如此），失败终态自动产出事故记录（`artifacts/incidents/*.bundle.json`）⇒ `diagnose_task.py --sweep` 归类到故障族。**"提醒用户"与"自动复盘"共用同一条证据链。**

## 三、检测：四层，从便宜到昂贵

| 层 | 检测什么 | 频率/时机 | 现状 |
| --- | --- | --- | --- |
| 驱动层 | 串口/CAN 心跳、电机错误标志、电流/温度、编码器读数合理性、夹爪行程 | 每命令 + 周期心跳 | 部分有（`SERIAL_PORTS_UNAVAILABLE`、`MOTOR_LAYOUT_MISMATCH`），需统一成 `Fault` |
| 运行时层 | 命令超时、能力自检、标定/地图修订不一致、观测新鲜度、相机丢帧 | 每命令 + 空闲时 | 部分有（`NAV_COMMAND_STALE`、`CALIBRATION_CHANGED`） |
| 任务层 | 同一能力反复失败、验证连续失败、恢复次数 | 每步 | 有（闭环门 + 恢复视图），需产出模块级故障 |
| 周期自检（`doctor`） | 无运动自检：读状态、试探总线、相机取帧、关节限位读取、电池 | 空闲时 / 用户点击 / 启动后 | `get_robot_status` 雏形，需升级为模块级 `selfTest` |

**原则**：自检**默认不带运动**；任何带运动的检测（例如回零）都是 `self_recover` 动作，要过审批、要有界、要留证据。

## 三之二、自动定位 / 自动恢复 / 自动解决（本轮新增）

前三节讲的是"故障能被大脑看见"。这一节讲"看见之后自己动手"，规矩与系统其它部分一致：**证据说话，自己说成功不算**。

### 自动定位：随时能问"现在哪儿有问题"

三层出口，同一份事实：

| 出口 | 内容 | 现状 |
| --- | --- | --- |
| 遥测/观测 | `robot.faults.v1` 快照（模块、严重度、首见/最近、次数、用户指令、还能做什么） | **已实现**（见 §三之三） |
| 事故记录 | 异常终态自动写 `artifacts/incidents/<taskId>.bundle.json` | 已实现 |
| 诊断 | `diagnose_task.py --task/--bundle/--sweep` 归类故障族并给出该跑哪些回归测试 | 已实现 |

### 自动恢复：`FaultRemedyEngine`

> **状态：已接线，但当前无事可做——这是事实，不是遗漏。**
>
> 2026-09-17 复核发现：`FaultLedger` 全仓库只在**仿真**里被实例化过一次，真实机器人**根本不发布 `robot.faults.v1`**。
> 后果不只是引擎空转：Agent 的 `ANOMALY_COMPONENT_FAULT` 规则读的就是这份文档，所以在真机上它**永远不会触发**——
> 新业主最可能遇到的那条故障（"这台机器人还没调试完"）恰好是 Agent 看不见的那条。
>
> 本轮做的两件事：
> 1. **给它输入**：真实后端（`xlerobot_backend.py`）现在把**它本来就算出来、却只当能力 blocker 发布**的那些条件写成故障台账并发布
>    （`ROBOT_NOT_ARMED` / `ENTITY_PROVIDER_REQUIRED` / `VERIFIER_REQUIRED` / 驱动自己的 blocker）。
>    规则与仿真一致：**只记驱动能证明的东西**；模块归属不猜——驱动报的一律记在 `driver` 模块下。
> 2. **驱动引擎**：每次观测都以 `robot_can_move=False` 跑一遍 `resolve_all`，结果作为 `remedyOutcomes` 与故障文档**并列**发布。
>
> **为什么 `robot_can_move` 恒为 False**：这个系统**没有无人使能路径**——`--arm` 要求本地交互终端。所以
> "会动机器人的自愈动作"在无人值守进程里**永远不该被选中**，这不是配置问题而是设计。
>
> **为什么现在还没有任何 `self_recover` 故障**：真实后端能证明的四条全是人要做的事（使能、配感知、配验证、装依赖），
> 它们**本来就该**是 `operator_assist`。所以引擎今天只会升级、不会修复——**为了让它"有活干"而发明一条自愈故障，就是在为了机制而制造自主性**。
>
> **接缝已有守卫**：`robot/gateway/tests/test_fault_ledger_contract.py` 断言"每一个声明 `self_recover` 的故障，都必须有已注册的 remedy 覆盖它"。
> 今天这条断言在空集上通过——它必须在**第一条** `self_recover` 出现之前就存在，而不是之后。

规则只有一条，但它是关键：**恢复是否成功，由"故障消失"判定，不由恢复动作自述**。

```
故障进入台账 → 引擎按声明的 remedy（有界动作）依次尝试
              → 每次尝试记录：动作、耗时、结果、证据
              → 成功判据 = 复查后该故障从台账消失（不是"动作返回 True"）
              → 全部失败 → 升级：把 userInstruction 交给用户，并留下完整尝试记录
```

四条约束（都已实现并有测试）：

1. **只跑故障自己声明的 `self_recover`**：`operator_assist` / `service_required` 的故障，引擎直接升级给人，绝不自作主张；
2. **每次尝试都留证据**：动作、耗时、成败、原因，随事故记录与遥测一起走——"它自己修过什么"必须可查；
3. **有界**：单故障最多 `max_attempts` 次（默认 2）；台账层面的升级阈值（同一故障重复 5 次）另算，两者叠加保证不会无限重试；
4. **恢复动作自己抛异常不算成功**：异常记为一次失败尝试，继续下一个候选动作。

### 自动解决：闭环

```
① 定位   台账 + 事故记录 + 诊断
② 恢复   FaultRemedyEngine（只对 self_recover；验证靠故障消失）
③ 复测   恢复后跑该故障族的回归测试（diagnose_task 给出清单）
④ 升级   恢复不了 → 用户指令 + 尝试记录；代码级缺陷 → 交给受审的 coding agent（见 AI 诊断文档）
⑤ 归档   事故记录 + 尝试记录留档，作为"这次为什么这么处理"的证据
```

**仍然不自动做的三件事**：代按急停复位、代插拔线缆、代改标定。这三件的正确反应是**把人叫来**，而不是把软件写得更勇敢。

> **落地状态（后补）**：本节描述的设计**尚未全部接线**。运行中的"发现与定位"已在 Go 的
> Review Agent 里实现并实测；但 **第 ④ 段"执行恢复"没有接**——`FaultRemedyEngine`
> 有完整实现与测试，生产路径里**0 个调用者**。当前能力边界、以及第 ④⑤ 段接线的前置条件，
> 见 [Review Agent 运行原理](review-agent.md) 第六节。

## 三之三、故障怎么走到大脑（本轮实现：发布路径与契约）

一份故障从驱动器到 LLM 面前要过四道门，每道门都有测试：

```
运行时（Python）                      Go 侧
FaultLedger ──► observation.robot_state["faults"]     观测即载体，不新增总线
                     │
                     ├─► edge/robotclient 用契约解码：robot.faults.v1 不合规 → 拒绝整条快照
                     │        （故障表决定能力是否可用，"读不懂"不等于"大概没事"）
                     ├─► fleet telemetry（小、且正是运维要看的，走普通遥测 JSON）
                     └─► worldmodel：world.snapshot.v1 的 Robots[<id>].faults
                              └─► 大脑/控制台：与 pose/held 同级的一条事实
```

**契约为先**（`core/robotcontract/faults.go`，12 条测试）。`DecodeFaults` 拒绝：未知字段、`null`、未知严重度/模块种类/处置类、`count` 与条数不符、**标题严重度不是最严重的那条**、同一 `module:code` 出现两次、`capabilityBlockers` 与 `unavailableCapabilities` 两个视图互相矛盾、blocker 指向报告里不存在的故障、没有 `detectedAtUnixMs`。理由：**这份文档是能力门禁的输入**，一份自相矛盾的故障表比没有故障表更危险——它会让大脑以为某个模块是好的。

**跨语言一致性由测试钉住**（`tests/contract/test_fault_contract.py`，3 条 + Go 探针）：用**真实 sim 运行时**（`RgbdRuntimeService`，含真实 `EmergencyStop` RPC）产出的文档喂给**真实 Go 解码器**，断言两边对 `severity` / `count` / `keys` / `safetyStopped` / 操作员指令 / 不可用能力集合的理解一致；同一测试再把文档改坏（count 对不上、严重度降级、`remedy` 编造、`ageMs=null`、多一个 `rootCause` 字段）逐条确认 **Go 拒绝**。两个语言各写各的测试永远发现不了"我以为你发的是这个"。

**为什么 `ageMs` 没算出来时是"没有这个键"而不是 `null`**：Go 契约禁止 `null`（历史上 `null` 解码成数字 0 造成过静默错误），所以 Python 侧只发布能回答的字段——"没算"与"是 0 毫秒"是两件事。

## 四、LLM 的工具面（新增三个，全部只读或受控）

| 工具 | 安全级 | 作用 |
| --- | --- | --- |
| `robot.health` | QUERY | 返回模块级 `robot.faults.v1` 快照（含年龄、次数、可做/不可做） |
| `robot.self_test(module_id)` | QUERY（无运动自检）/ LOW_SPEED_MOTION（带微动） | 跑该模块声明的 `selfTest`，成功即 `ledger.clear(module)` |
| `robot.remedy(fault, action)` | 由动作决定 | 只对 `self_recover` 故障开放；走既有审批与安全监督；结果写事件 |

**模型边界不变**：模型看到的是"模块名 + 故障码 + 严重度 + 允许的动作"，看不到寄存器地址，也不能自己发明动作——动作来自故障声明或工具目录。

## 五、与任务/闭环的关系（为什么这样就够用）

```
模块故障 ──► 能力 unavailable ──► 计划期失败（原因=哪个模块）
    │                                   │
    │                                   ├─ 若有等价能力：换策略（左臂坏→用右臂；底盘坏→原地作业）
    │                                   └─ 否则：任务进入可恢复失败 + 事故记录
    └──► 用户面板提示 + LLM 提议 self_recover / 请人处理
```

- **安全级故障**（急停、碰撞、失控）→ 任务 `SAFETY_STOPPED`，**不能由任务动作解除**（既有语义），只能人复位；
- **结果未知**（命令发出后模块失联）→ 步骤保持 `STARTED`、**永不重放**（既有语义），由人或对账流程处理；
- **降级运行**（例：右臂可用）→ 属于"任务改版/换策略"，不是新机制：`ActionParameters` 已能表达用哪只臂，`policy_execution` 已能表达用哪个策略。

## 六、可扩展性：新增一个模块要改什么（清单）

1. `robot.profile.v1` 加一条 `modules[]`（含 `capabilities`/`dependsOn`/`selfTest`/`repairClass`）；
2. 该模块的故障码表（码 → 严重度 + 处置类 + 用户指令），放运行时侧；
3. `diagnose_task.py` 的 `FAMILIES` 里加一族（或复用已有族），并配一条回归测试；
4. 若引入新故障码，CI 的"每个故障族至少一条测试"提示补族。

**不需要改**：Go 门禁、任务状态机、事故记录格式、控制台协议（面板按 `faults.v1` 渲染，字段可选即兼容）。

## 七、明确不做

| 不做 | 原因 |
| --- | --- |
| 让 LLM 直接写寄存器 / 改标定 / 复位急停 | 绕过审批与安全监督；且出错无人可追 |
| 用自愈掩盖故障（反复重试到"看起来正常"） | 会掩盖真实劣化：所以有升级阈值与计数 |
| 在严重度未知时"降级继续跑" | 未知等于不可信；宁可停下报人 |
| 把故障只写日志不写世界状态 | 大脑的推理依据会缺一条事实 |
| 为每种模块新增一套专属上报路径 | 一条 `robot.faults.v1` + profile 声明即可扩展 |

## 八、落地顺序（每步都可独立验收）

| 步骤 | 内容 | 验收 |
| --- | --- | --- |
| 1（**已完成**） | 词汇表 + 台账 + 能力联动 + 快照（`module_faults.py`，13 条测试） | 单元测试；`info` 不封锁、`blocked` 精确封锁、自愈升级 |
| 2（**已完成**） | 运行时把现有硬件故障统一成 `Fault`、进台账、随观测发布（`robot.faults.v1`），Go 侧契约解码并进世界快照 | **实测**：锁存急停后观测里出现 `estop:EMERGENCY_STOP_LATCHED`（`safety`），`navigation.navigate` / `manipulation.pick` / `observe_scene` 变 `available=false` 且 blockers 指向该故障，`emergency_stop` 仍可用；没有启用地图的移动场景报 `chassis:NAV_MAP_NOT_READY` 且只摘导航；跨语言契约测试 3 条通过 |
| 3 | `robot.health` / `robot.self_test` 工具 + 控制台模块面板（含"还能做什么"） | 面板显示当前故障、年龄、次数、用户指令 |
| 4 | 事故记录带上 `faults.v1` 快照与能力影响；`diagnose_task.py` 加硬件故障族 | `--sweep` 能把"底盘串口丢失"归到硬件族并给出处置 |
| 5 | 周期性 `doctor` 自检 + 级联（`dependsOn`：总线坏 → 牵连模块一并标记） | 总线故障注入：受牵连模块全部标红，且不误伤无关模块 |

**第 2 步落地后行为的唯一变化**：能自证的硬件故障会**真的摘掉对应能力**（此前只是报出来）。其余的零散硬件码（`SERIAL_PORTS_UNAVAILABLE`、`ARM_NOT_FOUND` 等）在**真实机适配器**里统一，属第 2 步的续做；门禁、状态机、审批与事故记录格式仍不动。
