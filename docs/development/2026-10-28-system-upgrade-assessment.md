# 2026-10-28 边缘系统能力与实机接入评估

本次核对工作树中的代码、部署文档及已有实验记录，没有新增实机、ROS 2、Gazebo、GPU 或无线网络测量。结论是：**当前可以开展受监护的设备集成与现场验收；现有证据不足以认定 DJI、宇树、AGV 混合机群已能无人值守运行。** 这两个阶段的差距主要在设备接线、独立观测与故障验收，不能由增加 Agent 角色或通过软件单测代替。

## 1. 当前模块边界

| 模块 | 当前已实现的职责 | 不能由此推导的保证 |
| --- | --- | --- |
| Local Agent / Fleet Worker | 两个入口共用 `internal/edgeagent.Host`；目标合同、有限规划、执行、核验、持久恢复 | Worker 不是 Local Agent 的下级；同一机器人不能由两个无协调入口同时控制 |
| 跨 Agent 交换 | 持久 Outbox/Inbox、身份过滤、TTL、幂等与流顺序；覆盖建议影响探索候选排序 | 通用消息接收不等于自动融合地图；邻机建议不授予动作权限，也不等于全局共识 |
| 云边任务 | Fleet claim、资源 fence、动作租约及目标版本约束；失去权威后停止 | 文件锁只覆盖同一主机；令牌只在实际检查它的接收边界生效，不能独自排除物理空间冲突 |
| 长程上下文 / 模型 | 原始证据、受保护状态、有限投影分层；模型请求可回溯，缓存提示按域隔离 | 摘要不是权威状态；缓存提示不是命中证明；非流式总耗时不是 TTFT；未知结果不能靠模型猜测后重放 |
| 异构移动 Harness | 按机器人能力、移动域与执行包络约束工具；ROS 2 ProfiledExecute 接口 | 通用 DJI / 宇树 / AGV binding 是接入模板，不是厂商控制器和具体机型的认证 |
| SLAM / 空间导航 | 二维 SLAM/Nav2；外部注册 SE3 点云的有界融合、几何路径、地面支撑与体素约束 | `RegisteredSpatialSlamProvider` 不估计位姿；真实 LIO/VIO/SLAM、时间同步、标定、地图注册与运动控制仍由 Provider/设备侧落实 |
| Ops 与回溯 | 任务/命令持久记录；Task/Ops/Recovery 因果事件及健康计数 | `agentruntime` 非阻塞总线允许丢事件，观察者不能假设收到每一事件；它不是任务权威日志 |

源码入口：[Host](../../internal/edgeagent/host.go)、[Worker](../../edge/worker/worker.go)、[模型调用](../../internal/actionloop/llmdecider.go)、[空间建图](../../robot/gateway/tangying_robot_gateway/spatial_mapping.py)、[事件总线](../../agentruntime/bus.go)。完整消息边界见[通信与上下文](../architecture/edge-agent-communication-and-context.md)。

## 2. 实机放行前仍需补齐的证据

1. **具体设备接线与物理停止。** 三类 [binding 模板](../../examples/robots/bindings/) 仍要求填写机型、固件、SDK、桥接版本及兼容性证据。binding 预检不会自动配置 Runtime 或厂商 DDS 域，`--template` 也不会授权移动。应分别验证底盘制动、足式稳定停止、无人机悬停/降落/失联策略；由独立传感器证明结果，不能回显目标当作抵达。[接线说明](../operations/ros2-device-bindings.md)
2. **真实定位及共同坐标系。** SE3 测量入口明确声明不估计 SLAM 位姿。注册点云中的变换、时间戳、协方差及标定版本需要真实 Provider 提供；重定位或回环改变地图后需按新版本重新接纳任务。跨机共享 `frame_id` 字符串不构成空间对齐证据。[SLAM Provider](slam-providers.md)、[空间导航](spatial-navigation.md)
3. **Fleet 部署的通知与离线策略。** Worker 收到通知后才持久化；可确认来源在持久化后 ACK。HTTP ready/claim 通知尚缺可回放投递回执，崩溃窗口仍须由云边协议补齐。Local 模式不依赖 Fleet claim，但远程模型不可达时仍需明确退化行为；不能声称任意断网后继续所有任务。[现场手册](../operations/edge-field-testing.md)、[持久 Inbox](../../edge/worker/inbox.go)
4. **目标硬件上的资源与长期运行。** ARM64 交叉编译证明可构建，不证明 Orin 上的实时性、内存峰值、磁盘增长和热负载。历史 96 步 race campaign 超过默认 10 分钟；本轮审计优化后又发现完整组在 180 秒超时，进一步修复 child 写入路径后，同规模完整组在相同上限内通过。历史和新失败均保留，[持续运行实验](../experiments/2026-10-28-edge-scalability/README.md)分别给出配对小样本和完整组结果。仍需测量目标设备的资源与耗时，不能从局部加速推导无限历史容量。
5. **异常可观测性与安全覆盖。** 软件事件总线有丢弃计数，必须接入现场监控；关键安全动作由 Runtime/控制器独立落实。应验证日志写入失败、传感器失效、动作结果 unknown、节点重启、证书过期和碰撞反馈缺失均能被识别。不能以“没有观测到碰撞”代替“碰撞检测链已验证”。

## 3. 已有实验分别证明什么

- [10-28 软件可靠性 campaign](../experiments/2026-10-27-edge-brain-reliability/summary.json)：10 轮、60 次顶层测试、110 次叶测试执行，无失败；每轮各一个 SUCCEEDED 和 RECONCILIATION。32 步成功样本与计划 64 步但第 25 次写入 unknown 后停止的样本应分开。它使用软件 fixture，无外部模型、Gazebo、ROS 2 或真实机器人，不可作为硬件成功率。
- [10-18 Gazebo 场景](../experiments/2026-10-18-gazebo-field-validation.md)：指定场景中独立 Agent/Runtime 经实验协调器控制刚体机器人；使用真值位姿，地面模型不含宇树步态，无人机不代表 DJI 飞控。UAV 停止验证为悬停，`landingVerified=false`、`rotorsOffVerified=false`；碰撞检测存在未完全验证项。
- 同次 Gazebo 跨机点云直接注册因误差与支持率不足被拒绝，随后接受的是已知 CAD 锚点离线注册。此结果支持“拒绝不可靠对齐”，不能改写为“陌生环境在线 SLAM 自动跨机对齐成功”。

## 4. README 与部署文档编辑建议

README 本轮已开始修正模型服务与 API Key 的区别、缩小一致性承诺并增加最新模型成本入口。后续应持续保留以下结构：

- 开头给出“当前可用能力 / 证据后端 / 未验证边界”表；将历史升级流水账移到开发索引，避免把日期最新等同于全系统已现场验收。
- 使用 Local 与 Worker 共用 Host 的架构图；把 Task/Ops/Recovery 解释为职责与事件角色，避免读者理解成多层机器人控制权。
- 介绍 3D 能力时直接写“外部位姿估计 + 注册点云融合 + 有界几何导航”，与自主 SLAM、动力学轨迹和设备跟踪能力分别列出。
- 缓存与经验只描述已接线入口；模型 token 统计分别报告已知值、缺失次数、失败/未完成调用与使用样本分母，不以缓存命中或复用次数推算未经测量的费用。
- “已验证”后标明软件 fixture、记录式 ROS 2、Gazebo 或具体实机；发布流程中的路线次数是场景验收样本要求，不是跨机型通用安全证明。

本评估不修改历史实验的分母、失败结果或实现基线。后续现场记录应冻结设备/软件版本、输入任务、地图与标定、控制授权、异常注入时刻、原始传感器引用、状态转移及最终判据，再决定哪些能力可以扩大发布范围。
