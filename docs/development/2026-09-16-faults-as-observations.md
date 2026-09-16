# 硬件故障发布成观测：从"报出来"到"摘能力、进世界状态"

**上一轮**（[模块故障与自愈设计](../architecture/module-health-and-faults.md)第 1 片）把故障**词汇表**建好了：`Fault` / `FaultLedger` / `capability_impact()`，但**没有任何真实故障走这条路**——运行时不会产生它们，Go 侧也不认识它们。这一轮做的是第 2 步：让运行时把自己**能自证**的硬件故障变成 `robot.faults.v1`，随观测发布；Go 侧用契约解码，放进 WorldHub，于是"哪个模块坏了"成为大脑能读到的一条事实，而不只是日志里的一句话。

---

## 一、为什么改

1. **大脑看不见故障，就会继续按"机器人是好的"规划。** 上一轮之后能力门禁已经存在（`available=false` + `blockers` → Go 计划期失败关闭），但运行时从不因故障把能力标不可用，这条门禁一直是空转的。
2. **跨语言契约不能靠"我以为你发的是这个"。** 故障表是能力门禁的输入：Python 发一份、Go 读一份，两边理解不一致时不会有异常，只会安静地放行一个本该被拒的计划。
3. **"不可用"必须说得出原因。** 实测（见 §四）暴露：`navigation.navigate`、`navigation.pre_position`、`recover_to_safe_pose` 三项是"不可用但 blockers 为空"——控制台只能告诉操作员"不能导航"，说不出"因为急停锁存"。
4. **计数单位错了会制造假警报。** 同样是实测暴露：运行时每次观测都重发它当前的故障，于是锁存急停 4 秒内 `occurrences` 涨到 10。而"同一故障重复 5 次就升级为需要人"这条规则会在两秒内把每个自愈故障判成"人手故障"——规则本身没错，计数单位错了。

## 二、改成什么

### ① 运行时只发布它能证明的故障（`sim/mujoco/tangying_sim/rgbd_runtime.py`）

| 故障 | 触发条件 | 严重度 | 处置 | 摘掉的能力 |
| --- | --- | --- | --- | --- |
| `estop:EMERGENCY_STOP_LATCHED` | 急停 RPC 锁存 | `safety` | `operator_assist` | **所有声明了依赖的能力**（5–7 项） |
| `workcell:WORKCELL_CALIBRATION_MISMATCH` | 实测支撑高度与标定 0.73 m 差 > 15 mm | `blocked` | `operator_assist` | `manipulation.*`、`plan_grasp` |
| `chassis:NAV_MAP_NOT_READY` | 移动场景、没有启用的地图、也没有外部导航 | `blocked` | `operator_assist` | `navigation.navigate`、`navigation.pre_position` |

**证不出来的不写进台账**：故障表短一点，好过喊狼来了。条件不再成立时运行时主动 `clear`，所以故障会自己消失，而不是留在台账里等人清。

**能力 → 模块的声明只有一处**：`CAPABILITY_MODULES`（`navigation.navigate` 依赖 `chassis`+`head`，`manipulation.pick` 依赖双臂双爪+工位……）。`emergency_stop` 声明**不依赖任何模块**——一个已经停住的机器人，仍然必须能被停住。

两处语义修正：

- **`safety` 级故障整体封锁**：它不是"某个模块坏了"，而是"什么都不能动"。规则实现在 `capability_impact()` 一处：`safety` 故障撤掉**所有声明了依赖的能力**，不按模块名匹配；唯一幸免的是声明"零依赖"的 `emergency_stop`。
- **封锁在最后一遍做**：`_fence_by_faults()` 作用在**最终**能力表上（基类 + 本运行时追加的 `navigation.*`）。此前只作用于基类列表，追加的两项因此成了"不可用但没原因"。

### ② 观测即载体，不新增总线

`observation.robot_state["faults"]` 带上整份 `robot.faults.v1`（含 `unavailableCapabilities` 与 `capabilityBlockers`）。**故障是观测不是异常**：世界状态里少了这条事实，大脑的任何推理都是错的。

### ③ Go 侧：契约解码 + 进世界快照（7 个文件）

| 位置 | 作用 |
| --- | --- |
| `core/robotcontract/faults.go`（新） | `Fault` / `FaultReport` + `DecodeFaults`，16 条测试用例 |
| `edge/robotclient/faults.go`（新） | 从观测里取出并校验；**不合规 → 拒绝整条快照** |
| `core/telemetry`、`fleet/telemetry` | 故障随遥测走（小，且正是运维要看的；原始几何仍不上低频通道） |
| `edge/worker` | `Sample` → `observation.RobotPayload.Faults` |
| `core/observation` | `RobotPayload.Faults`，并在 `Validate()` 里再验一次 |
| `core/worldmodel` | `world.snapshot.v1` 的 `robots[<id>].faults`（深拷贝，读者改不动投影状态） |

**契约拒绝什么**（这是本轮的核心取舍）：未知字段、`null`、未知严重度/模块种类/处置类、`count` 与条数不符、**标题严重度不是最严重的那条**、同一 `module:code` 出现两次、`capabilityBlockers` 与 `unavailableCapabilities` 两个视图互相矛盾、blocker 指向报告里不存在的故障、缺 `detectedAtUnixMs`。理由：**这份文档是能力门禁的输入**，一份自相矛盾的故障表比没有故障表更危险——它会让大脑以为某个模块是好的。

**"更新版本的运行时"是未知，不是故障**：观测里根本没有 `faults` 键时，客户端返回 `nil`（未知），遥测照常流动；只有"发了但读不懂"才拒绝。

### ④ 计数单位：episodes，不是 samples

`FaultLedger.record()` 对**已在台账里的故障**只刷新 `lastSeen`（且不清空 blockers），`occurrences` 只在**故障消失后再次出现**时 +1；被清除的故障会把它的次数记在 `_episodes` 里（同样有界），这样"修好又坏"仍然会累积到升级阈值。`ageMs` 描述**这一段**故障存在了多久，`sinceLastSeenMs` 描述**最近一次还被看到**是多久前——两个数字合起来才能区分"一直坏着"和"闪了一下"。

## 三、量到了什么

### 跨语言契约（`tests/contract/test_fault_contract.py` + Go 探针，3 条）

用**真实 sim 运行时**（含真实 `EmergencyStop` RPC）产出的文档喂给**真实 Go 解码器**：

| 断言 | 结果 |
| --- | --- |
| 干净机器人 | 双方一致：`count=0`、`severity=info`、`safetyStopped=false`、`blocking=0` |
| 锁存急停 | 双方一致：`severity=safety`、`keys=["estop:EMERGENCY_STOP_LATCHED"]`、操作员指令逐字一致 |
| 能力影响 | Python `capability_impact()` 的结果与文档里的 `capabilityBlockers` 完全相同 |
| 改坏文档（6 种：count、严重度降级、编造 remedy、未知严重度、`ageMs=null`、多一个 `rootCause`） | Go **全部拒绝** |

### 真机栈实测（`xlerobot-mujoco-tabletop`，`/v1/world`、`/v1/runtime`）

| 时刻 | `/v1/world` 的 `robots["robot-local"].faults` |
| --- | --- |
| 干净 | `severity=info, count=0` |
| 锁存急停后 t+0.1 s | `severity=safety, count=1, occurrences=1, ageMs=125, sinceLastSeenMs=0, unavailableCapabilities=5 项` |
| t+8.1 s（连续 6 次观测） | `occurrences` **始终为 1**，`ageMs` 125 → 8124，`sinceLastSeenMs` 始终 0 |

`/v1/runtime` 同一时刻：**12 项能力 / 5 项可用 / 7 项不可用，且 7 项全部在 blockers 里指名 `estop:EMERGENCY_STOP_LATCHED`**（修复前是 4 项有名、3 项沉默）。`emergency_stop` 保持可用。

**修复前后对比（同一场景、同一操作）**：

| 指标 | 修复前 | 修复后 |
| --- | --- | --- |
| 锁存急停 4 秒后 `occurrences` | 10（= 观测次数） | 1（= 故障段数） |
| 不可用能力中"没说原因"的数量 | 3（navigation.navigate / navigation.pre_position / recover_to_safe_pose） | 0 |
| 自愈故障在两秒内被误升级为"需要人" | 会（阈值 5 次 ≈ 2 秒） | 不会（要靠真的修好又坏 5 次） |

### 回归

- 非 e2e 全量 Python：**1877 通过 / 35 跳过**（437 s；含本轮新增 3 条跨语言契约测试、2 条 sim 故障测试、3 条台账语义测试、9 条 Go 客户端测试、3 条世界模型测试、16 条 Go 契约测试用例）。
- Go 全包 `go test ./...`：通过；`make lint` 干净；Web 375 通过。
- `make test-python` 的结构未变：契约边界测试单独跑，其余全量。

## 四、边界（这一轮**没有**做的事）

| 没做 | 原因 / 下一步 |
| --- | --- |
| 真实机适配器（`xlerobot_backend`）的零散故障码（`SERIAL_PORTS_UNAVAILABLE`、`ARM_NOT_FOUND`…）统一成 `Fault` | 属第 2 步续做；sim 侧只发布它能自证的三种 |
| `robot.health` / `robot.self_test` 工具与控制台模块面板 | 第 3 步；本轮只保证"事实已经在世界状态里"，面板直接渲染即可 |
| 事故记录里带 `faults.v1` 快照、`diagnose_task.py` 的硬件故障族 | 第 4 步 |
| 周期 `doctor` 自检与 `dependsOn` 级联 | 第 5 步 |
| `resolve_targets` 这类纯查询能力的急停行为 | 目前仍可用；"急停时只读查询是否该一起摘"需要单独定，本轮不顺手改 |
| 云侧 fleet proto 携带 faults | 本地世界快照与遥测 API 已覆盖；跨云链路要动 proto，按需求再做 |
| 代按急停复位 / 代插拔线缆 / 代改标定 | 仍然不做：这三件的正确反应是**把人叫来** |

**这一轮唯一的行为变化**：能自证的硬件故障会**真的摘掉对应能力**（此前只是报出来）。门禁、状态机、审批、事故记录格式都没动。
