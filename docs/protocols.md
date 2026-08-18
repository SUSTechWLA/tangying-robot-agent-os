# Robot Runtime 协议不变量

笔记本始终主动建立 mTLS gRPC 连接；树莓派不反向连接，不需要消息代理。线协议位于 [`proto/robot/v1/robot.proto`](../proto/robot/v1/robot.proto)，Go 业务代码通过 `edge/runtime` 的语义接口使用它。

每个物理 `SkillCommand` 必须包含：

- 协议/模式版本、全局唯一 `command_id` 和本地 `task_id`；
- 白名单技能、目标引用和已验证参数；
- 绝对 deadline、短执行 lease、approval ID；
- 幂等键、确定性 command fingerprint 和 safety profile；
- 可选但有界的 `action_chunk`。

这些字段由 Local Agent 的确定性编译器生成。LLM 输出不能设置或覆盖安全字段。

Robot Runtime 拒绝未知版本或技能、缺失身份、过期命令、缺失/过长 lease、幂等冲突、无审批、非法安全配置，以及含未知键、底盘键、非有限值或越界值的动作块。

同一 command 的事件严格有序，且只有一个终态：成功、失败、取消或安全停止。相同身份的重复投递返回安全日志中的终态而不重复动作；同一幂等键对应不同 fingerprint 时失败关闭。

`Cancel` 只控制停止一个命令。`EmergencyStop` 立即停止并持久化锁存，远程接口不提供解除操作。断开连接后，活动命令必须在短 lease 到期内停止。

`RuntimeInfo`/能力描述是 Agent 可见的能力注册表；`Observation` 只传有界低频语义状态和显式请求的压缩观测。高频关节控制和原始传感器流留在树莓派驱动内部。
