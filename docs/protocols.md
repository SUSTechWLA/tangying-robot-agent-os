# Robot Runtime 协议不变量

> 完整 HTTP、WebSocket、FleetGateway 与 RobotRuntime 调用规范见[生产接口参考](production/api-reference.md)，字段语义见[数据契约](production/data-contracts.md)。

RGB-D 原始流显式使用 `ObserveRequest.source_id` 和 `streams=["rgbd_raw"]`，返回同一采集的 RGB、米制深度、K、光学坐标到本体的变换，以及可选的机器人自身掩码。附加 `sensor_only` 可让支持它的 Runtime 跳过语义识别和预览压缩，保留规范身份／原采集时间与空的派生几何；旧适配器可以忽略这个优化标记。普通观测不附带这些大数组。`SkillEvent.evidence_observation` 保存工具实际用于验证的原始观测，失败终态同样可以附带；消费端核对命令、观测编号、时间与来源后归档，不能拿下一次相机采集冒充验证输入。详见 [观测证据](development/observation-evidence.md) 和 [导航合同](development/rtabmap-navigation.md)。

Local 形态由笔记本主动建立 Runtime mTLS gRPC 连接；Fleet 形态由机器人侧 Edge 主动连接云端 FleetGateway，并访问本机/受控 Runtime。Runtime 自身不需要业务消息代理。线协议位于 [`proto/robot/v1/robot.proto`](../proto/robot/v1/robot.proto)，Go 业务代码通过 `edge/runtime` 的语义接口使用它。

每个物理 `SkillCommand` 必须包含：

- 协议/模式版本、全局唯一 `command_id` 和本地 `task_id`；
- 白名单技能、目标引用和已验证参数；
- 绝对 deadline、短执行 lease、approval ID；
- 幂等键、确定性 command fingerprint 和 safety profile；
- 可选但有界的 `action_chunk`。

这些字段由 Local Agent 或 Fleet Edge 的确定性层生成。LLM 输出不能设置或覆盖安全字段。

Robot Runtime 拒绝未知版本或技能、缺失身份、过期命令、缺失/过长 lease、幂等冲突、无审批、非法安全配置，以及含未知键、非有限值或越界值的动作块。旧 XLeRobot 桌面配置继续禁止底盘键；新的 Profile 适配器仅接受其显式 `actionLimits` 范围内的动作，导航目标还受已声明世界工作区范围约束。

同一 command 的事件严格有序，且只有一个终态：成功、失败、取消或安全停止。相同身份的重复投递返回安全日志中的终态而不重复动作；同一幂等键对应不同 fingerprint 时失败关闭。

`Cancel` 只控制停止一个命令。`EmergencyStop` 立即停止并持久化锁存，远程接口不提供解除操作。断开连接后，活动命令必须在短 lease 到期内停止。

`RuntimeInfo`/能力描述是 Agent 可见的能力注册表；`Observation` 默认只传有界、低频的 Robot State、Semantic State 和 Scene Entity。Camera、LiDAR、IMU、Joint State 的原始流和高频关节控制留在树莓派内部完成处理与融合，不进入 Agent 任务协议。

Python Robot Runtime 内部使用传输无关的语义 `Command`、`RuntimeInfo` 和 `Observation`；只有 gRPC service 负责 protobuf 映射。ROS 2 Topic/Service/Action 与 Robot SDK 类型不得穿透这一边界。

异构接入增加版本化 `robot.profile.v1` 与 `scene.reconstruction.v1`；机械结构、来源、world/米坐标、wxyz 四元数与采集时刻成为可校验合同。每源重建序号与原始帧标识共同定位帧，World 的逐实体证据 ID 还包含原始序号，避免重复帧成为新证据，也避免新的序号被旧 opaque ID 吞掉。Python/Go 均会拒绝新 Profile 缺重建数据、原地变更帧或标定身份漂移，旧适配器明确保持 legacy。规范与接入命令见[适配器手册](development/robot-adapters.md)。

当前实机 systemd 服务会连接并保持扭矩关闭，但不自动 arm；连接会配置总线并禁用现有扭矩，启动前需支撑机械臂。明确现场授权与急停复位使用[Sim2Real 上手](sim2real/README.md)中的流程，不能把 capability READY 作为动作许可。
