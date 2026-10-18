# 边缘 AgentOS 通信、上下文与上线就绪审计

核验窗口：2026-10-18 至 2026-10-19。源码基线：`f930437ae7f8612cef41c0ca82be084cfa7d12af`，分支 `codex/heterogeneous-spatial-exploration`。本次由用户要求评价边缘异构 AgentOS、Agent 间通信、云边通信、上下文和核心 harness；完整实现导读见[专门说明](../architecture/edge-agent-communication-and-context.md)。

本次交付以源码审查、软件回归、真实回环 HTTP 多进程复验和说明文档为主。没有启动 Gazebo 或连接实体机器人；既有 Gazebo 与 DDS 实验按原源码和原场景引用。

## 1. 上线结论

**适合作为受控实机集成候选，尚不足以作为通用异构机群无人值守正式上线版本。** 当前最强的是受约束执行、消息与上下文身份绑定、持久恢复和可追溯性；最需补足的是具体型号物理适配、真实网络与长期规模验证，以及跨机器人空间协作的完整端到端验收。

| 关注点 | 本轮核对到的实现 | 判断与实际缺口 |
| --- | --- | --- |
| 单机器人边缘自治 | Local/Worker 共用 Host；粗目标合同、子计划、预算、检查点、未知动作隔离 | 具备软件基础；任意自然语言和实际长时间运行仍依赖具体模型、设备和现场验收 |
| 异构 Harness | robot/profile/commissioning、ROS 2 execute/state/stop、2D/地形/3D Provider | 可复用统一上层合同；DJI/Unitree/AGV 模板不是已完成的厂家驱动与物理停止认证 |
| Agent 之间的数据通信 | edge-exchange、身份/域、TTL、持久 inbox/outbox、feed、覆盖消费 | 回环多进程通过；无线时钟、RTT/丢包、长期负载、证书生命周期与实际故障域未在本轮验收 |
| 云边任务控制 | HTTP 任务/claim/续租/回执 + mTLS Link；持久通知与精确完成回执 | 故障语义合理；当前 cloud claim 失联停止，无离线继续委托；外部资源与云端成功事务之间仍有需对账窗口 |
| 上下文管理 | 不可变版本/Basis、权威账本、managed 投影、SHA 归档回查、Host 进度摘要 | 方向合理；不同路径有不同预算/装配，不能宣称统一全局上下文或所有模型阶段都自动共享完整历史 |
| 长历史处理成本 | 普通回归完成 32/48/64/96/128 步连续任务；race 到 96 步超时 | 模型输入有界不代表账本序列化/存储成本有界；正式长时间运行前需独立性能门禁 |
| 本机多 Agent 事件 | 注册发布口、topic 权限、绑定恢复、优先队列 | 队列可丢、sink 尽力持久；不构成所有观察消息的可靠审计出口，需要独立监控和持久消费升级 |
| 群体任务 / 空间一致性 | 受作用域 peer 建议、单机执行权威、集中协调研究与 Gazebo 测试 | 不等于多主共识、分区自治、自动跨机 SLAM 或任意同时运动冲突解决 |

这些结论不以主观百分数代替设备验收，也不把单元测试、记录式 DDS、Gazebo 和实机的成功数量合并。

## 2. 源码审查发现

本次未发现需要通过放宽准入、补造上下文或自动重试物理动作来解决的问题；保留现有执行合同。最容易产生对接误解的边界已在新专题逐条说明：

1. `agent.event.v1` 是本机观察/协作事件，`agent.data.v1` 是跨节点建议数据，`execution.context.v1` 是云边执行绑定。它们不能互换。
2. peer Receipt 只确认入库；coverage-view 只给建议；只有执行层、动作后观测与合同完成判据才能确立本机任务完成。
3. 设备 HTTP 是 HTTPS + 设备 token，Link 是 mTLS，exchange 是 TLS + 独立 peer bearer；不能全部称为 mTLS。
4. `context.completion.v2` 是 coordinator 保存的不可变完成回执，边缘 complete 请求发送原身份和 contextBasis。重传报告与重发动作严格分开。
5. EventBus 默认每订阅者 256 条、溢出可丢弃，持久 sink 不把失败返回执行者；恢复放行仍必须核对持久因果链。注册权限是受信任进程的组件边界，不是恶意插件沙箱。
6. managed 上下文从原始事件构建、保留 unknown guard、完整 JSON 外置并按 SHA/scope 回读；Host 的 8000 字节进度投影是另一层限制，不代表整个模型请求或全历史可见性。
7. coverage 快照超 1000 条时不完整并返回空建议，GC 缺口需要 resync；通用四类 payload 并不意味着已写好任意业务消息的自动消费适配。

本次新增专题与本审计记录，并从 README、文档总导航、开发导航、Runtime 协议与长程协作入口建立链接。历史实验和两份原有营销文件保持原有工作区状态。

## 3. 本轮验证

所有命令在仓库根目录执行。机器可读结果与原始日志见 [evidence](../experiments/evidence/2026-10-18-communication-audit/summary.json)；Go 数量按 `go test -json` 的 pass/skip 事件统计，包含子测试，不是源码函数静态数量。

| 验证 | 命令 / 范围 | 结果 |
| --- | --- | --- |
| Go 协议/执行/存储/协调 | agentcontext、agentcontract、contextcontract、exchangecontract、objective、harness、agentharness、edgeagent、edge、agentruntime、tasks、fleet、SQLite、架构与文档 | 1170 个测试/子测试 pass，3 skip；34 package pass，1 package 无测试文件 |
| 通信与并发竞态，首次全组 | `go test -race -count=1`：exchangecontract/exchange/edge-exchange/SQLite/Worker/Host/agentruntime | 六个非 Host package 共 364 项 pass；Host 连续 campaign 在 96 步时触发默认 10 分钟超时，整条命令退出 1，不能计全组通过 |
| Host 定向竞态复核 | 同一包 `-race -count=1 -timeout=5m`，仅排除上述连续 campaign，显式记录 23 个顶层测试选择 | 49 个测试/子测试 pass；不替代被排除的长程 campaign |
| 额外模型/工具/恢复链 | `go test -count=1`：`internal/capabilityagent`、`internal/actionloop`、`internal/recoveryexec`、`agent/...` | 411 pass、1 skip；随后找到原始归档并单独复验该 skip 的实际上下文样本 |
| 原始 335 事件上下文回放 | 以 `TANGYING_RUN002_CONTEXT_FIXTURE` 绑定原 run-002 文件，单跑 `TestRun002ActualRecoveryContextReplay` | 3 个测试/子测试 pass；4 次决策投影 31572–32530 字节，低于 32768；每次外置 488 条记录，保留未决 guard 与原文可回查 |
| ROS 2 Profile / mobility / peer 消费 | `.venv/bin/python -m pytest -q robot/gateway/tests/test_ros2_profile.py robot/gateway/tests/test_mobility_contract.py robot/gateway/tests/test_spatial_peer_exchange.py` | 104 passed；包含真实本地 gRPC 与记录式/模拟驱动合同，不构成厂家设备或真实 DDS 网络验收 |
| 四进程通信与实际 Python 消费 | `.venv/bin/python scripts/run_edge_cooperation_harness.py --output artifacts/experiments/communication-readiness-20261018-001` | passed，4 个独立进程，15.307 秒；三类 robot 身份与一个云 peer |
| 跨语言固定线样例 | `.venv/bin/python scripts/verify_agent_data.py core/exchangecontract/testdata/agent-data-v1.json` | Go 规则兼容摘要匹配；含中文、特殊字符与大于 2^53 的字符串序号 |
| 文档与证据 | `go test ./tests/docs`、新文档相对链接、evidence manifest / 压缩 JSONL 校验、`git diff --check` | 通过；所有失败、选择清单和边界随文档保留 |

Go 的三个 skip 分别为 Redis 集成未配置 `EDGE_REDIS_TEST_ADDR`、MySQL 迁移未配置 `MYSQL_TEST_DSN`，以及只供父测试启动的 world checkpoint 子进程 helper。后者是测试结构的正常 skip，其父进程测试通过；前两项外部服务集成本轮确实未运行。`fleet/telemetry` 为无测试文件的 package，不记为测试通过。

额外 harness 套件中的 skip 是未设置原始上下文路径。随后核对实际 `final-task.json` SHA256 `b942bb7ca509d45402163e1b935eff80b795359586e6428a1ba7248e591eb3c7`、`revisions.json` SHA256 `b6e01cf922e18caf92ade7535a2186d9175f5be590d6f300497ec6e643007640` 后完成只读软件回放。该测试验证当前压缩/回查处理原始历史的能力，不重新执行 10-10 的物理任务，也不改变其原失败结论。

### 长程 race 超时与性能观察

首次 race 命令运行到 Host 的 `TestSingleRobotLongHorizonCampaign/persistent_task_04_096_steps` 时触发默认总计 10 分钟 timeout，package 用时 602.148 秒；未出现 `WARNING: DATA RACE`，但未完成场景不能因此记为无竞态。失败原始 JSONL、堆栈和随后定向复核的选择清单均保留。

普通 Go 回归中，同一共享机器人/数据库连续执行 32、48、64、96、128 步任务，用时分别 **1.07 / 3.86 / 9.58 / 28.80 / 74.74 秒**，整个测试 118.05 秒；均成功并验证重建后没有新增写动作。race 仅完成前三个子任务，分别 **20.15 / 57.87 / 138.69 秒**，随后在第四个超时，未执行第五个。

这些是记录式软件 Provider、累计历史和当前机器上的测试时间，含持久读写，不含真实移动或模型服务耗时；不同步骤数与累积历史共同变化，不能由此推导单一算法复杂度。它提示长历史处理成本值得单独 profiling。正式长期上线应设立明确的规划/恢复延迟与存储增长阈值，优化前后以相同输入与权限、证据判据复验。本次不通过提高 timeout、缩短任务或减少步骤把失败记录改写为通过。

四进程复验使用显式 loopback HTTP 开发模式，记录源码包及依赖摘要和二进制 SHA。该时长包括构建和固定故障等待，不是网络延迟或系统吞吐指标。覆盖四组场景：

- UGV、UAV、legged 身份向其他机器人和云端发消息，持久收件后形成带作用域覆盖建议。
- Python explorer 真实消费建议并调整候选顺序，本机 coverage ledger 仍为零，不被 peer 声明强制完成。
- 云端离线与发送进程重启；恢复后分页续读，原任务与数据身份连续。
- 错误空间依据、任务版本、旧观测、越界目标、过期消息及截断快照拒绝/返回空建议。

进程脚本自动结束本次子进程并删除私有 token/配置/数据库临时目录；公开保留去密日志、事件、源码 manifest 与二进制摘要。完整本地源码包在上述 artifacts 目录，新提交仅收录核对需要的摘要与日志。

## 4. 正式上线前的优先工作

| 顺序 | 工作 | 可放行的证据 |
| --- | --- | --- |
| P0：单设备 | 按具体 SDK/固件/action/topic 绑定，逐传感器校时、坐标/标定，受限运动、到达、停稳/降落、失联与进程崩溃 | 设备版本、原始测量、命令后证据和 unknown/停止结果；通过后再扩大任务 |
| P0：云边对接 | 云端同事实现并核对目标/目录/claim/Basis/完成回执；消息丢失、版本推进和重启时只补原报告 | 两端同一任务和原命令的完整因果链、outbox 排空与没有重复运动 |
| P0：真实通信 | 无线/跨机/云网注入 RTT、丢包、乱序、长断连、时钟偏差，验证证书更新与隔离 | 分通道 RTT、ACK/消费延迟、outbox 最老年龄/容量、feed gap、拒绝原因、身份与时钟日志 |
| P1：上下文/观察可靠性 | 明确各模型路径的预算与回查；补可靠观察事件出口、sink 失败告警与消费水位 | 超长历史下 guard 完整、原文可回查、无跨任务/版本污染，消息缺失被发现 |
| P1：多机器人 | 建立真实共同坐标、分工资源和实际动态冲突策略；处理中央协调失效与资源跨存储窗口 | 一个完整自然语言目标下的多机、故障、恢复、空间测量与最终证据 |
| P1：长期试运行 | 逐步延长单机与多机运行，验证数据库增长、保留/归档、重启恢复、升级回滚 | 固定版本/配置的连续运行记录、指标与故障清单；预先声明验收阈值 |

系统健康至少应同时观察：模型预算拒绝与归档读取失败、未决命令数量、传感器原采集年龄、云 claim 续租错误、最老未送达消息年龄、peer 业务拒绝、feed 缺口、EventBus dropped 和持久写失败。收到心跳或 HTTP 200 不能代替这些指标。

本轮不给新正式版本号、不宣称已完成实机生产认证。后续升级仍按上述链路保留提交、配置、原始输入、失败、回执和测量。
