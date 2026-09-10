# ADR: 物理写工具闭环契约、语义地图层与恢复分类

状态：已接受（2026-09-10）。适用范围：`core/`、`edge/agent`、`skills/manipulation`、`robot/ros2_ws/src/tangying_navigation`。

## 背景：现有系统审计结论

升级前仓库已经具备下列能力，本 ADR 不替换它们：

| 能力 | 现有实现 | 审计结论 |
| --- | --- | --- |
| RTAB-Map / Nav2 几何与定位 | `sim/mujoco/tangying_sim/rtabmap_client.py`、`robot/ros2_ws/src/tangying_navigation`（`/map`、TF、位姿、建图/定位模式、速度租约） | 可用，保留为底座 |
| 物理命令边界 | `robot/gateway/tangying_robot_gateway/safety.py`（急停锁存、租约、profile、参数校验）、`robot/gateway/.../service.py`（执行锁、幂等、journal）、`RuntimeJournal` | 可用，保留 |
| 世界状态 | `core/worldmodel`（`Snapshot`、`EntityState`、`ResourceState`、`SourceState`、`EvidenceRef`、revision/freshness） | 已有证据链字段，保留 |
| 证据与新鲜度 | `core/observation/envelope.go`、`tasks/evidence.go`（同帧 RGB/depth + SHA-256） | 可用，保留 |
| 完成判定 | `core/harness/evaluator.go`（世界 revision 推进、命令后观测、来源新鲜、`inside` 关系、稳定观测、fencing） | 可用，保留 |
| 任务权威 | `tasks/service.go`（revision、aggregateVersion、事件）、`internal/localapp/app.go`（暂停/恢复/未知结果对账） | 可用，保留 |
| 外部 Agent 接入 | `robot/mcp`（8 个工具，`create_task` 保留人工审批） | 可用，本轮扩展只读语义工具 |

审计发现的**真实缺口**（本轮修复对象）：

1. **没有"读工具 / 写工具"的机器可判定标记。** `core/skills/manifest.go` 只有 `SafetyLevel`，它表达危险级别而不是"这个工具会改变物理世界"。写工具的闭环只能靠 `skills/manipulation/plugin.go` 里写死的步骤序列（`verify_grasp`/`verify_placement`/`verify_arrival`）实现；适配器新声明的写工具（例如 `open_door`）只要返回 `Success=true` 就会被 `edge/agent/runner.go` 记为完成，没有任何后置验证门禁。
2. **闭环只覆盖已知清单，不是通用契约。** `runner.go` 只在步骤名等于三个已知 verifier 时检查置信度，其他写工具没有"必须携带新鲜证据"的要求。
3. **没有语义地图层。** 全仓库没有 TagMap / 场景图 / 开放词汇地图。LLM 侧唯一可查询的是 `observe_world`（Fleet 融合快照）与 `get_robot_capabilities`，没有任何"物体在哪里、上次何时被看到、依据哪个观测"的接口。语义抽象缺失使"LLM 不消费原始传感器流"只能靠约定，不能靠接口保证。
4. **没有失败分类与有界恢复。** 失败后只有 `RECOVERABLE_FAILURE` + 人工 `resume`；重试由人决定，代码里没有"可重试 / 需重新感知 / 需重新规划 / 未知终态"的分类，也没有上限与退避。
5. **没有主动感知的搜索能力。** 目标不在视野时，系统只能整条任务失败；没有"再观察一次 / 旋转底盘搜索 / 在新视点重定位"的有界动作。

`robot/ros2_ws/src/tangying_safety_supervisor` 目前只是心跳看门狗（43 行），真正的安全否决在 Python `SafetySupervisor.evaluate()` 与 Go `core/guard`，这一点在 ADR-3 中明确，不重复造第二个否决层。

## ADR-1：把"会改变物理世界"变成契约字段，而不是步骤名约定

**决定。** 在 `core/skills.SkillManifest` 增加 `MutatesWorld bool`（JSON `mutatesWorld`），作为写工具的唯一机器可读标记；`MutatesWorld=true` 必须同时满足 `SideEffect=true`；`SafetyPhysical` 必须 `MutatesWorld=true`。`skills/manipulation.Catalog()` 为 5 个写工具（`navigation.navigate`、`manipulation.pick`、`manipulation.place`、`recover_to_safe_pose`、`emergency_stop`）显式标注。

**理由。** 闭环要求必须由能力契约驱动，不能依赖某个 adapter 恰好按参考顺序排列步骤。`SafetyLevel` 表达"危险级别"，`SideEffect` 表达"有副作用"，都不等于"改变可观测物理状态"，而后者才是"必须验证"的判据。

**被否决的方案。**

- *按副作用强度分级来代替*：把"会改变世界"编码成 `SafetyLevel` 的一个新档位，会让"危险级别"和"是否需要后置证据"两个正交概念绑在一起；`emergency_stop` 是物理动作但不需要场景证据，正好证明二者不可合并。
- *每个 adapter 自己声明步骤序列*：参考场景可以，异构型号无法保证；且无法阻止遗漏。

**后果。** 新增写工具必须显式声明该标记，否则不会获得闭环门禁（保守：未声明即视为写工具？——不采用。见 ADR-2 的保守规则）。

## ADR-2：完成判定必须由新鲜证据支撑，缺失即失败关闭

**决定。** 在 `edge/agent/runner.go` 的通用执行路径上增加门禁：任何 `MutatesWorld` 步骤，只有同时满足下列条件才能记为 `COMPLETED`：

1. 返回结果 `Success=true`（保留原有语义：成功回执是触发器，不是证明）；
2. 该步骤关联到一条**新鲜的非空证据**（写工具自带的 `Evidence` 快照，或按 `publishTelemetry` 取得的工具后遥测）；
3. 该证据的时间戳晚于命令派发时刻。

不满足时：步骤保持 `STARTED`（物理结果可能未知），任务进入可恢复失败，且**不使用成功文案**。证据缺失与验证失败是两类不同错误：验证失败意味着"世界看起来不对"，证据缺失意味着"我们不知道世界是什么样"。

**理由。** 现有代码已经对 `verify_grasp/verify_placement/verify_arrival` 检查置信度，但那只覆盖参考清单。把门禁下沉到"写工具 + 新鲜证据"这一层，任何 adapter 的新写工具自动受约束。这直接满足"禁止仅凭 ExecuteTool 返回成功就认定完成"。

**为什么证据缺失也要失败关闭，而不是降级为"未验证但完成"。** 未验证的物理动作既不能重试（可能重复执行），也不能宣告成功（可能没做），只能进入"未知终态 + 人工对账"——这正是 `PHYSICAL_OUTCOME_UNKNOWN` 的既有语义。

**后果。** 参考场景的 14 步任务不受影响（每个物理步骤后本来就有验证步骤与遥测）。未接入证据的第三方 adapter 会出现新的失败，这是预期行为，并在 `docs/development/robot-adapters.md` 中说明。

## ADR-3：语义地图层建在导航桥侧，与 RTAB-Map 位姿、关键帧对齐

**决定。** 新增 `tangying_navigation` 的语义地图模块与 HTTP 只读接口：

- 输入：运行时观测（实体 + 采集时刻 + 来源 + 采集序号 + 置信度）与 RTAB-Map 地图位姿（`mapPose`、`poseObservedAtUnixMs`、`mapRevision`）。
- 表示：**TagMap**（`tag-map.v1`）。每个 tag = `entityId` + `category` + `attributes` + 最近一次观测的世界位姿 + `retired` 软删除 + 观测历史条数。每个 tag 记录 `mapRevision`、`keyframeId`（由采集序号与地图 revision 派生的稳定标识）、观测时刻、来源、置信度。
- 合并规则：同一实体被多次观测时按"最新观测覆盖、但保留首次观测时刻与观测计数"；实体在连续 N 次观测中消失才标记 `retired`，避免单帧漏检销毁记忆。
- 查询接口（只读）：`GET /v1/semantic/tags`（列出/按键过滤）、`GET /v1/semantic/tags/{entityId}`（单个）、`GET /v1/semantic/query?category=&attribute=`（按类别与属性查询）。
- 写入接口：`POST /v1/semantic/observations`，只接受已经过校验的观测（与现有导航观测同一时间戳/新鲜度规则），拒绝未来时刻与超预算载荷。

**理由。** 语义标签必须与几何/定位关联才有意义，而唯一同时掌握位姿与地图 revision 的进程就是导航桥；放在 Go 侧会引入第二份位姿来源，直接违反"RTAB-Map 是定位唯一来源"。TagMap 选型优先于完整 3D 场景图，因为当前观测只有单目级语义（类别 + 属性 + 位姿）与点云质心，场景图的多边形/关系推理没有数据支撑；接口按可扩展设计，后续可加边表示关系。

**被否决的方案。**

- *在 Go 侧另建语义库*：会与导航桥的地图 revision 产生双主，且需要把点云/TF 搬到 Go。
- *让 LLM 直接读 `/map` 或点云*：明确违反原则，且 LLM 无法保证数值一致性。
- *开放词汇检测器*：需要一个视觉语言模型与权重；当前工位只有颜色/几何检测器，硬上会把"未验证的识别"写成事实。接口预留 `label` 与 `confidence`，接入真实检测器只替换 producer，不改契约。

## ADR-4：恢复分类是确定性函数，重试有上限与退避

**决定。** 新增 `core/closedloop` 纯逻辑包：

- `Classify(code, ...)` 把失败码映射到 {`TRANSIENT`（可原地重试）, `PERCEPTION`（需重新观察/搜索）, `PLANNING`（需重新规划）, `RESOURCE`（资源或授权冲突）, `UNKNOWN`（物理终态未知）, `FATAL`（不可恢复）}。映射基于现有已定义失败码（`NAV_VELOCITY_STALE`、`NAV_MAP_NOT_READY`、`NAV_WORKSPACE_LIMIT`、`FENCING_TOKEN_STALE`、`EXECUTION_OUTCOME_UNKNOWN`、`VERIFICATION_*` 等），未知码一律保守归入 `UNKNOWN`，绝不默认重试。
- `Policy` 定义每类动作的最大尝试次数与退避序列（确定性、可注入时钟）。`UNKNOWN` 与 `FATAL` 的最大尝试为 0：不允许自动重试未知物理结果。
- `Track` 是单步闭环状态机：`PENDING → EXECUTING → AWAITING_EVIDENCE → VERIFIED | RETRYING | ESCALATED`，只由证据与分类驱动，不允许外部直接置为 `VERIFIED`。

**理由。** 需求明确要求"重试有上限和退避；未知物理终态进入安全状态并记录"。把分类做成纯函数使它可以被穷举测试，并且可以在不接触硬件的情况下证明"未知终态永不自动重试"。

**后果。** 本轮先把 `Track` 用于写工具门禁与失败分类的**判定**；自动重试循环仍由现有 Agent/人机流程承担（避免在没有真实策略的情况下自动重复物理动作）。自动重试的接线留作后续，但状态机与上限已就位并有测试。

## ADR-5：主动感知的边界——重新观测与有界搜索，不在本轮做视觉伺服

**决定。** 本轮提供：

1. `perception.verify_entity`（只读写入门禁的判定依据）：对指定实体要求"新鲜观测 + 位姿一致性"，返回是否可见、位姿、年龄、依据。写工具在验证阶段必须能给出该证据。
2. 语义层的 `retired` 与观测计数：目标在视野外时不会因为单帧缺失被当作"已消失"，也不会被当作"仍然在原位"。
3. 搜索动作**不新增物理工具**：底盘旋转搜索需要新的安全包络与现场验收（当前 `MAX_ANGULAR_SPEED_RAD_S` 与足迹检查只覆盖导航脉冲）。目标不可见时分类为 `PERCEPTION` 并进入"重新观察 → 人工/上层重新规划"路径，明确记录而不是假装搜索过。

**理由。** 加一个未做碰撞与速度验收的旋转搜索工具，会把"看起来闭环"变成真实风险，违反"Safety Supervisor 有否决权"与"不得绕过现场验收"。诚实的 `PERCEPTION` 升级比假搜索更符合可验证原则。

## ADR-6：`ground-truth` 调试运行时不能完成物理写，验收路径必须使用相机运行时

**决定。** 旧的真值调试运行时（`--perception ground-truth`）不为它返回的观测提供标识（`SkillEvent.observation_id` 为空，观察也没有 `reconstruction`），因此无法为物理写提供"新鲜、命令后、可命名"的证据。ADR-2 的门禁使其写入失败关闭，这是预期行为。所有执行物理写的自验收路径——`scripts/demo.sh`、`tests/e2e` 的任务用例——改为在相机工作台（`--perception rgbd --scene tabletop`）上运行；`--perception ground-truth` 仅保留只读调试用途。

**理由。** 三条出路在当前设计下都不可接受：放宽门禁接受无标识的遥测会破坏"证据链必须有来源与身份"；让门禁把客户端的抓取当作运行时证据会绕过来源校验；给真值运行时伪造观测身份会制造一条看起来通过、实际没有传感器证据的路径。改自验收路径是最小且诚实的选择，而且与仓库既定方向一致——真值模式本来就"不能作为相机感知验收"。

**后果。** `scripts/demo.sh` 现在显式传 `--perception rgbd --scene tabletop --robot-safety-profile simulation`，事件数略有变化。真值调试模式仍可用于观察与只读工具，但任何写工具都会返回 `CLOSURE_EVIDENCE_REQUIRED`。第三方 adapter 若不能标识自己的观测，同样无法完成写工具；这一要求写入了 `docs/development/robot-adapters.md` 的待办。

## ADR-7：语义地图与主动感知的本轮范围

原计划中的语义地图（TagMap）与搜索行为本轮**未实现**，这是刻意的范围裁剪，不是遗漏。现有实现已经提供了语义抽象的两个必要前提：运行时把 RGB-D 抽象为带类别、属性、世界位姿、来源、采集时刻与置信度的实体（LLM 与 MCP 从不接触原始像素或点云），Agent 的门禁要求这些实体必须新鲜且晚于命令。缺失的是"跨时间、跨关键帧、可按键查询"的持久语义层。

不在本轮实现的原因与后续接入点：

1. **键帧关联缺少数据支撑。** 仓库没有把 RTAB-Map 关键帧 ID 暴露到观测契约里；在没有真实关键帧标识的情况下建 TagMap，只能用采集序号冒充关键帧，会把"看起来有据可查"写成事实。correct 的做法是先扩展观测契约（新增可选 `keyframeId` 与地图 revision），再建图层。
2. **搜索需要新的安全包络。** 底盘旋转搜索会引入新的速度/足迹风险，必须像导航脉冲一样完成限幅与现场验收；本轮把"目标不可见"分类为 `PERCEPTION` 并进入重新观察/人工路径，而不是假装搜索过。
3. **接口已预留。** `core/closedloop` 的 `Class` 已包含 `Perception` 与 `Planning`，恢复策略可据此扩展"重新观察 → 新视点 → 重新规划"，无需改门禁语义。

## ADR-8：闭环身份使用运行时观测标识，缺失即失败关闭

**决定。** 门禁要求证据带**身份**（`ObservationID`）与**时刻**（`ObservedAt`），二者缺一不可。身份优先取持久化的采集 ID，其次取运行时在命令结果中返回的观测标识（`SkillEvent.observation_id`）。两者都没有时失败关闭，错误码 `CLOSURE_EVIDENCE_REQUIRED`。

**理由。** 只有时刻没有身份，证据无法被复核（"哪一次观测证明的"）；只有身份没有时刻，无法证明它晚于命令。相机运行时同时提供两者：工具结果携带 `evidence_observation`，其中包含 `observation_id`、`wall_time_unix_ms` 与规范重建。

**时间精度。** 运行时以整毫秒标记采集，亚毫秒的先后不可观测，因此 `NormalizeDispatchTime` 把派发时刻截断到毫秒，证据与派发在**同一毫秒**内视为命令后（先前一个毫秒仍被拒绝）。这条规则由 `DispatchPrecision` 单点定义，`Gate` 与 `Track` 共用，避免两处判据漂移。

## 验收映射

| 需求 | 本轮实现 | 证据 |
| --- | --- | --- |
| 读写工具区分 | `MutatesWorld` 字段贯穿 manifest / catalog / capability proto / 运行时声明 | `core/skills`、`skills/manipulation`、`core/closedloop`、`sim/mujoco/tests/test_world_mutation_contract.py` |
| 写工具闭环契约 | 门禁要求新鲜、命令后、带身份的证据；`Track` 状态机含重试上限与退避 | `edge/agent/closure_test.go`（缺证据必须失败关闭；只读工具不受影响；adapter 新写工具同样受约束） |
| LLM 不直连原始传感器 | 运行时已把感知抽象为实体；LLM/MCP 侧只读查询；语义持久层见 ADR-7 | 既有 `observe_world` / 能力查询接口 + 本轮实体契约 |
| WorldModel 一致性 | 证据缺失 → 步骤保持 STARTED、任务可恢复失败，不写成功状态 | `edge/agent/closure_test.go` 断言 `StepStarted` 与 `ErrPhysicalOutcomeUnknown` |
| 故障注入 | 断连/重复/过期租约/抓取失败/物体移动/遮挡既有测试全部保持通过 | `make test` |
| Safety 违规 0 | 保留 Python `SafetySupervisor` 为唯一否决点；本轮门禁只收紧完成条件，不新增旁路 | 既有 safety 测试 + ADR-3/ADR-6 |
| RTAB-Map 兼容 | 未改 ROS 图、话题或导航合同；语义持久层待接入（ADR-7） | 导航与 RTAB-Map 测试原样通过 |

## 本轮顺带修复的既有缺陷

1. **相机路径从未发布 `verification_confidence`。** `RgbdRuntimeService._verify_relation` 把结果写在服务实例上，而发布的 `robot_state` 来自 `RgbdTabletopWorld`，因此控制台与证据读取端在相机路径上一直看到 0.0 / 缺字段，而确定性路径是正常的。现在把判定写入 world 并在场景捕获时发布，并新增断言（`tests/e2e/test_observable_sequence.py`）。
2. **相机工作台的 `held` 只在物体被抬起期间出现，而遥测是 1 Hz。** 一次约 4 秒的两物品任务里，抽样可能完全错过该状态。测试改为断言确定性证据（任务事件中的证据 ID 与来源），不把"是否抽到某一帧"当作正确性判据。
