# 第 17 章 云端大脑与边缘 Agent：统一能力目标的完整实现

本章以2026-09-27源码快照为准。当前主入口已经接入通用能力计划、同一任务权威、持久操作回执和云边委托。标定、SLAM、地图与导航可以由一次自然语言任务连续执行。指定 Gazebo 场景的实际闭环已通过；Orin NX、GPU 模型服务、实体机器人与目标规模机群仍待验证。

## 17.1 任务范围与权威放在哪里

| 形态 | 任务权威 | Agent 范围 | 执行位置 |
| --- | --- | --- | --- |
| Local 自治 | local-agent 与 SQLite 的 Task/Revision/审批/事件 | 连接的一台机器人 | 本机 Capability Executor → Runtime |
| Fleet 委托 | fleet-control-plane 与现有 Coordinator/任务存储 | 系统状态、草案、设备绑定目标与既有多机 Intent | 每台 edge-worker 运行同一 Executor，再调用本机 Runtime |
| 云端 System Agent | Fleet 的系统工具及未审批草案 | 设备、任务、世界摘要、编排诊断 | 不持有 Runtime 物理动作入口 |

Server 的系统 Harness 允许 fleet.read/fleet.draft；Edge 的角色 Harness 允许 robot.read/robot.local/robot.write。云端可以读取设备认证的目录并规划机器人任务，但真正的物理调用由有审批和设备租约的 Worker 执行。模型的算力和模型建议不能生成审批、claim、fencing token 或完成证据。

当前通用能力目标绑定一台机器人。Fleet 多设备时使用 `robotId: 完整目标` 明确选择；只有一台在线且有目录的设备时可自动选择。原多机器人 Intent 协调保留，任意自然语言目标的自动跨机器人分解尚未实现。

Local 和 Worker 对同一 Runtime 是互斥部署方式。同机控制锁限制并发入口；跨主机、地址别名与外部写者仍须部署方约束。同一 Runtime 的旧 Agent 失联不能被解释为已经停稳。

## 17.2 共享的规划与执行链

~~~text
Local 输入 / Fleet 输入
  → Runtime 服务目录（Fleet 为设备认证后的广告）
  → 完整离线语法或 GOAL 模型提出能力调用
  → 参数验证、机器人绑定、目录哈希、复合 Intent 冻结
  → 原 Task / Revision 草案
  → 显式审批
  → Local Executor / 获得 claim 的 Worker Executor
  → STARTED 与持久回执
  → Runtime 调用、稳定 operationId、进度与续租
  → 独立读回 / 既有导航抓放的动作后观测
  → CAPABILITY_VERIFIED → 步骤完成 → 任务完成
~~~

规划在 `internal/capabilityagent/planner.go`，执行在 `internal/capabilityagent/executor.go`；Local 与 Worker 装配同一包。`core/capability` 定义 Manifest/Contract/Call/Plan，`tasks/capability.go` 将能力计划接入原 Task，而不是另建 SLAM 任务队列。

`internal/agentharness.Profile` 继续装配角色工具和有界 `actionloop.Loop`；GOAL Planner、旧 Intent/Planning 服务、事件式 Task/Ops/Recovery Agent 各有职责。不能把所有入口描述成直接调用 Profile.Run，也不能把事件订阅权限解释为物理写权限。

完整离线句式优先，例如“运行标定，巡检建图，然后去厨房”。其他表达由独立 GOAL 模型处理。离线无法完整表示的否定、条件和额外动作需要澄清，不能只提取“建图”关键词。目标请求最多8000字节，单个能力计划最多16次调用；具体调用仍按对应Schema验证，参数序列化不超过64 KiB。

`robot.task` 是后端保留的复合工具名，Provider 不能注册同名服务。其导航、取物和抓放 Intent 在审批前解析、绑定机器人并冻结；执行时不重新解析目标。子步骤使用独立前缀，沿用原 Runner 的 Grounding、Runtime 准入和动作后验证。

## 17.3 工具目录必须提供可核验契约

Runtime ListServices 的 ServiceDefinition 同时提供 inputSchema 与 contract。目录名称与参数只是发现接口；自动执行资格还依赖效果、资源、操作控制和完成条件。

| 字段 | 作用 |
| --- | --- |
| version | 当前契约版本为1，未知版本不提供给 Planner |
| effects | READ、ARTIFACT_WRITE、CONFIG_CHANGE、PHYSICAL_MOTION、SAFETY_STOP；与 mutatesWorld 保持一致 |
| resources | 当前实现仅支持 robot 独占资源；不接受未实现的共享空间 Broker |
| verification | 独立只读服务、必需证据路径、与参数或回执的匹配关系 |
| operation | 长操作的稳定身份、状态服务、取消服务、互斥终态集合及租约能力 |
| outputSchema | 可选的有界输出约束 |

每个 Provider 写操作都必须声明资源及独立 verification。仅有 completed 终态不能建立地图或配置已生效的结论。内部 robot.task 的完成依据来自既有 Runner 已验证的子步骤。

下面是 mapping.build 的契约示例。业务输入 Schema 与这个 contract 分开传输：

~~~json
{
  "version": "1",
  "effects": ["PHYSICAL_MOTION", "ARTIFACT_WRITE"],
  "resources": ["robot"],
  "operation": {
    "leaseSupported": true,
    "statusService": "mapping.status", "cancelService": "mapping.cancel",
    "identityPath": "operationId", "statusIdentityPath": "operationId",
    "statePath": "state", "running": ["moving", "exploring", "finalizing"],
    "success": ["completed"], "failure": ["failed", "cancelled"]
  },
  "verification": {
    "service": "mapping.status",
    "required": ["activeMap.mapId", "activeMap.mapRevision", "activeMap.calibrationRevision"],
    "match": {"activeMap.mapId": "result.mapId"}
  }
}
~~~

输入支持对象、数组、字符串、数值、整数、布尔和 null，以及 required、enum、additionalProperties 和实现支持的上下限。未知 Schema 关键字、引用和组合约束拒绝，不能默默丢弃。Proto Struct 把整数运输为浮点数，验证接受有界的整数值，仍拒绝分数、布尔冒充整数和非有限值。

Planner 排除不可用、无有效写契约、控制回调缺失或资源不支持的服务。执行前重新核对机器人、目录指纹和参数；目录变化要求生成新计划并审批。当前目录上限64项，Provider 应发布本机器人真实可用的能力。

## 17.4 SLAM、标定和语义地图的工具闭环

| 工具 | 后端职责与完成条件 |
| --- | --- |
| calibration.run/save | Provider 执行自身算法；独立读回 calibration revision |
| mapping.build | 自动采集、移动、优化、保存、激活；核对全操作终态与 activeMap 身份 |
| mapping.activate | 核对实际启用 mapId 与目标一致 |
| mapping.ensure | 复用、探索或候选歧义；歧义进入 WAITING_USER |
| semantic.resolve | 名称/别名解析，返回语义来源、坐标、地图和标定版本及可导航性 |
| robot.task | 导航或抓放复合 Intent，沿用动作后新观测的验证 |

mapping.start/move/finish 是操作员手动调试接口，缺少自动目标完成契约，不提供给 GOAL。自动任务调用 mapping.build 后不再追加 mapping.finish。探索的 maxTravelM/maxLegs 是上限；巡检模式执行注册路线，当前同时指定这些预算会在接纳前拒绝。

Local 的 POST /v1/tasks 与“安排任务”是同一入口。旧 POST /v1/mapping/request 现在创建未批准 Task 并返回201；旧 environment/maxTravelM/maxLegs 字段非空时返回400 MAPPING_ENTRY_MIGRATED，应改为完整自然语言约束。POST /v1/robot/services 保留显式操作入口，不作为自然语言任务主入口。前端展示草案、批准计划、工具进度和证据，不自行循环调用建图服务。

一个建图操作可以有多个 session/mapId，但 operationId 保持不变。中间段保存为 FINALIZING/继续探索，只有整个操作结束才能完成父步骤。地图点云、轨迹和栅格留在制品中；任务事件投影保留身份、版本、制品引用和摘要。

传感器过期或标定变化向上传播为操作故障，不被当作道路障碍登记。前沿为空、预算到达或深度不足可能提前结束采集；有可保存的实测地图并通过读回也不代表全屋覆盖。

语义层将测量几何与命名来源分开：本家庭场景的房间用途来自 commissioned_workspace 标注，目标可通行性来自 measured_navigation_grid。不能仅按几何分区猜“厨房”，也不能因有名字就忽略地图未知或足迹净空。SLAM 地图包、Nav2 导航制品和 RTAB-Map 定位数据库各自有身份，不能相互冒充完成证据。

## 17.5 持久回执、失联和取消

Executor 使用原 StepRun 与 TaskEvent 留存 STARTED、调用指纹、回执、operationId、绝对 deadline、进度及核验证据。

| 恢复时的记录 | 行为 |
| --- | --- |
| 已完成步骤 | 跳过，不重放 |
| 写操作 STARTED 且有有效操作回执 | 跟踪原 operationId，并重新核验；保留原 deadline |
| 写操作 STARTED 且无回执 | 保持结果未知，等待对账 |
| Provider 明确 outcome=REJECTED | 接纳前拒绝；不从普通 ok=false 推断无副作用 |
| 状态身份/所有者不符 | 不取消他人的操作，不重新启动运动 |
| 地图候选歧义 | WAITING_USER；提交完整新目标修订并重新批准 |

云端 Coordinator 的资源 claim 与 Runtime 操作租约是两层控制。Worker 持续续租稳定 claim 身份和 fencing token；续租不改变原 Started 的证据基准。Runtime 通过独立 RPC 权威字段接收30秒操作租约，过期设置同一控制器停止事件，控制器退出后才释放预约。业务 arguments 不允许模型指定 owner、lease 或命令身份。

取消先确认原操作所有权，再请求取消、读回终态并记录 CAPABILITY_STOP_CONFIRMED。进度事件可能已经提交而HTTP回执因取消失败，此错误路径同样进入独立有界的停止核验，不能直接退出；[竞态修复报告](../../docs/development/2026-09-27-capability-cancel-ack-race.md)与确定性回归保留这个时序。云端保持 CANCEL_REQUESTED，直到停止证据可以建立 CANCELLED；停止不明保留 RECOVERABLE_FAILURE。云端验证设备、步骤、目录、调用指纹与当前资源租约后接受 CAPABILITY_VERIFIED，依据为 PROVIDER_CONTRACT_EDGE_VERIFIED，不能泛化为全局物理谓词认证。

暂停只在能力调用边界生效。当前长建图使用取消和核对，没有任意地点暂停后续扫的通用契约。数据库事务、租约和看门狗仍须在目标实体控制器上验证，不能据仿真推断实机停止距离。

## 17.6 每个模型调用点独立配置

| 阶段 | 用途 | 环境变量前缀 |
| --- | --- | --- |
| GOAL | 注册能力目标计划 | AGENT_GOAL_* |
| INTENT | 旧任务与 robot.task 复合 Intent | AGENT_INTENT_* |
| PLANNING | 旧执行图规划 | AGENT_PLANNING_* |
| RECOVERY | 恢复建议 | AGENT_RECOVERY_* |
| SYSTEM | 云端系统读取与草案 | AGENT_SYSTEM_* |

星号为 PROVIDER/BASE_URL/MODEL/API_KEY，未覆盖阶段继承 AGENT_*。openai 表示兼容协议适配器；deterministic 不调用语言模型，只支持已实现的规则。换阶段端点时须显式提供对应模型，密钥不跨地址继承。Environment 仅采集非空阶段变量，shell 空字符串不保证清除全局默认值；Resolve(map, stage) 支持显式空值清除。低层策略的 EDGE_POLICY_* 是独立 sidecar 契约。

Local 诊断页可以逐阶段热配置；PUT /v1/config/llm 的 stage 取 GOAL/INTENT/PLANNING/RECOVERY，空 stage 修改默认值。状态返回脱敏路由，密钥不返回浏览器。已有批准目标保留冻结 Intent；切换端点或模型仍应作为有记录的运维变更。

Orin 本机量化服务以 OpenAI 兼容接口接入。复杂阶段可使用认证的云端 `/v1/assist`，model 为 cloud-goal/cloud-intent/cloud-planning/cloud-recovery 或 cloud-assist。服务器通过 FLEET_ASSIST_GOAL_MODEL 等配置选择真实上游模型，设备只持有专属令牌和 CA；云 GPU 密钥留在服务器。

下面是待填写片段，须使用实际加载且验证通过的模型名，同时配置设备身份、Runtime mTLS、Assist URL/令牌和 CA：

~~~dotenv
AGENT_PROVIDER=deterministic
AGENT_GOAL_PROVIDER=openai
AGENT_GOAL_BASE_URL=http://127.0.0.1:8000/v1
AGENT_GOAL_MODEL=REPLACE_WITH_VALIDATED_QUANTIZED_MODEL
AGENT_INTENT_PROVIDER=openai
AGENT_INTENT_BASE_URL=http://127.0.0.1:8000/v1
AGENT_INTENT_MODEL=REPLACE_WITH_VALIDATED_QUANTIZED_MODEL
AGENT_PLANNING_PROVIDER=openai
AGENT_PLANNING_BASE_URL=https://fleet.example.invalid/v1/assist
AGENT_PLANNING_MODEL=cloud-planning
AGENT_RECOVERY_PROVIDER=deterministic
~~~

Assist 是推理服务，不能产生新的动作权限。默认并发、大小和 token 限额见 fleet/assist.go；限额不等同于容量实测，也不意味着自动故障转移已实现。

## 17.7 一套镜像，三种入口与持久状态

Dockerfile.agent 构建 fleet-control-plane/local-agent/edge-worker，由 scripts/agent-entrypoint.sh 的 server/edge/worker 选择入口。云端使用 scripts/fleet-up.sh 与 deploy/cloud/docker-compose.yml；Orin 使用 deploy/edge-orin/compose.yaml 的 edge 或 fleet profile。

Orin 用 host 网络连接本机 Runtime 和模型服务，非 root 运行、只读根文件系统、证书组与 agent-state 持久卷。Worker 的 EDGE_EXECUTION_DB 默认 Docker 路径为 `/var/lib/tangying-agent/fleet-execution.db`；这份 journal 记录设备执行回执，Fleet 的 Task 仍是云端业务权威。宿主机开发默认在 artifacts/edge-worker 下按机器人分目录，SQLite 路径先转为绝对路径并建目录。

升级 Runtime/Agent/Worker 时同步 Proto 生成代码，保留任务数据库、Worker journal、证书、地图与标定。不得删除卷来清除未知动作。仅修改镜像不能回滚物理世界、数据库版本或 fencing。

先按[Orin 部署指南](../../docs/install/edge-orin.md)准备私有 env、证书组和设备，再选择一个 profile：

~~~bash
docker compose -f deploy/edge-orin/compose.yaml --profile edge up -d --build
# Fleet 模式使用 --profile fleet；切换前处理在途任务并停旧入口
~~~

Local 启动会验证 Runtime 身份，开发占位 robot-local 解析为真实 ID 后再构造 Grounder/Runtime/Telemetry/模型路由。显式配置 ID 与 Runtime 不符仍拒绝启动，不能用别名逃避绑定。

## 17.8 异构机器人和仿真独立性

Agent 面向服务目录、动作/观测协议、资源和证据，机器人驱动提供机构、关节布局、IK、足迹、传感器坐标、运动、停止和标定算法。更换 Gazebo、MuJoCo 或实机，不应在 Planner 加仿真器分支；Provider 可以提供不同工具，实现统一的权威与核验要求。

同一个工具名不证明能力相同：抓放需要该机器人真实可达、感知和持物判据，导航需要合适的足迹与定位来源。没有契约的动作不进入自动目录。接入过程和逐项验收见[实机与异构机器人指南](../../docs/guides/hardware-agent-integration.md)。

用户目标可以表达新的需求，但系统只能调度已接入、获授权且可核验的能力。新能力通过 Manifest、Provider、模型可见目录与后置条件扩展；不能把“任意自然语言”写成所有物理任务均可执行。

## 17.9 当前实测与现场边界

实际请求“请先运行机器人标定，然后调用自动建图工具按已注册的巡检路线重新建立并启用家庭地图，最后前往厨房并确认到位。”由配置的 GOAL 模型生成四步，Task task-77d1b15939d54145bedd36ef 最终 SUCCEEDED。

| 检查 | 2026-09-27 实际结果 |
| --- | --- |
| 场景与本体 | Gazebo，XLeRobot 同源原型，home_furnished 多房间家庭 |
| SLAM | 23.492米、230帧、220配准、2回环，保存启用 scan-e23523f77362 |
| 语义与导航 | 五个房间目标 navigationReady；厨房解析绑定同一地图，Nav2 到达后 verify_arrival=CONFIRMED |
| 未观测区域 | 保存栅格未知28.512%；分母是矩形图幅，不是全屋覆盖率 |
| 云边测试 | 真实 HTTP + Runtime fixture 验证目录认证、审批、claim 续租、持久回执、完成与 owned cancel |
| 软件门禁 | Go 全量；Python2431+隔离2通过、40跳过；前端486通过；实施提交的 CI release-gate 通过 |

[闭环报告](../../docs/experiments/2026-09-27-capability-goal-closure.md)保留五次失败、代码修复、成功地图版本与观测 ID，[验收摘要](../../docs/experiments/2026-09-27-capability-goal-acceptance.json)保存原始文件 SHA256。指定场景一次成功不建立统计成功率。HTTP fixture 不能写成云端服务器连接实体机器人已通过。

Orin 需要测量模型加载、峰值内存、KV/context、并发、p50/p95/p99、功耗温度和控制争用。以70亿参数4 bit为估算例，原始权重约3.5 GB，不含比例因子、未量化层、KV、工作区和系统余量。云端须验证真实大模型协议、排队和目标规模机群；本仓库不捆绑权重、不承诺某个模型能装入设备。

## 17.10 教学练习：怎样加入新能力

**题目**：为轮式或腿式机器人加入“检查并标定相机，然后去工作区”。Provider 只有成功布尔值，没有稳定操作身份或独立配置读回，工程师又让模型直接填 lease 和 owner。哪些设计必须修正？

**答案要点**：驱动定义实际算法、前置准备、Schema、效果、资源与独立标定 revision。长操作提供稳定 operationId、状态、owned cancel 和租约看门狗；权威字段由执行器通过独立 RPC 字段填充。地点解析绑定语义来源、地图/标定版本及该本体足迹。任务草案进入同一 Task/Revision，显式批准后执行，未知结果不重放。先做契约与恢复测试，再在目标机器人验证运动和停止。

[术语与时钟假设](../appendix/D-glossary-and-assumptions.md) · [参考资料](../appendix/E-references.md)
