# 目标委托的部署、续跑与升级

适用实现：`821659df`，核对日期：2026-10-16。先读[分层架构](../architecture/hierarchical-edge-objectives.md)与[API 接入](../guides/delegated-edge-goals.md)，再按本文核对部署与恢复。本文是现行操作参考，历史实验仍按原提交和二进制身份引用。

## 1. 选择一个控制入口

Local Agent 与 Fleet Worker 都装配 `internal/edgeagent.Host`。Local 接收本机目标并保存批准，Worker 接收云端已批准父目标并维护 claim；Host 根据本机能力生成子计划、执行、诊断和核验。一个 Runtime 只使用一个任务控制入口，同机控制锁不能替代跨主机的租约、fencing 与设备隔离。

| 部署选择 | 需要配置与核对 | 网络边界 |
| --- | --- | --- |
| Local | 原任务库、Runtime 准入文件、控制锁目录、本机目录和 GOAL 模型路由 | 模型端点在云端时仍需网络；没有云端 Fleet 不代表所有依赖均离线 |
| Fleet Worker | 原 execution SQLite、Runtime 准入文件、设备凭据、任务源、父 claim 和 `AGENT_GOAL_*` | 父目标执行全程续租；失去云端 claim 后停止，没有新增离线继续授权 |
| edge-exchange | 独立 inbox/outbox、收发身份、TLS 和引用数据通道 | ACK 只证明已持久接收；消息不产生批准、租约、地图真值或动作权限 |

模型在本机还是云端推理，与目标生命周期由边缘维护是两件事。复杂自然语言及按剩余进度重规划需要实际 GOAL 模型；确定性模式只覆盖已有离线语法。

## 2. 批准前核对合同

`POST /v1/objectives` 创建未批准的单个 `edge.goal` 父节点，使用原 `/v1/tasks/{id}/approve` 批准。现有工作台自然语言表单、旧 `/v1/tasks` 和旧系统工具仍使用冻结计划路径；不能依据页面输入框推断已启用目标委托。

核对真实机器人、目标目录摘要、允许工具与参数包络、效果、完成条件、预算和绝对期限。Fleet 的目标目录从 `GET /v1/devices/{robotId}/goal-catalog` 取得；Local 使用 `GET /v1/robot/services` 的 `goalCatalogRevision` 和按工具名索引的 `goalCapabilities`。低层 Runtime 目录摘要不能代替目标目录摘要。

v1 资源仅为绑定机器人的 `robot`。共享物品、区域预约和机群分工仍需云端另行协调。相同工具与业务参数的写调用默认一次；有意往返需要显式 `maxExecutions`。自然语言写“只去一次”不是可执行的权限约束。

完成条件是 1–16 个注册只读服务的精确值谓词，全部成立才完成。仅校验地图身份或定位状态不能证明巡检顺序，业务还需与子计划、动作后观测及独立路线验收核对。

| 预算 | 当前上限及计量单位 |
| --- | --- |
| `maxPlans` | 8 代子计划；失败、重启不恢复已消耗额度 |
| `maxSteps` | 128 次子能力调用；一次 `robot.task` 内仍可能有多个物理与验证步骤 |
| `maxReads` | 4,096 次 Host 业务读服务调用；规划查询、执行器状态/验证和完成条件均计入 |
| `timeoutSeconds` / `expiresAt` | 最长 86,400 秒，同时受绝对期限限制；不是小时级可靠性已验证的结论 |

Runtime 内部观测、控制周期、遥测、目录获取与父租约有各自规则；本表不是跨所有层的总动作/总读次数。实验应分开记录这些单位。

## 3. 按持久事实判断能否继续

| 持久事实或现象 | 当前处理 | 操作人员应核对 |
| --- | --- | --- |
| 尚未开始的授权步骤或明确无在途动作的安全检查点 | 重新验证原批准、版本、目录、预算、期限和原父 claim 后继续 | 原 SQLite、Runtime journal 和准入身份是否完整 |
| 子步骤已持久完成，父确认未提交 | 只有匹配的 `StepCompleted` 和完整绑定的 `CAPABILITY_VERIFIED` 同时存在，才写 `EDGE_STEP_RECOVERED` 补认，不重发动作 | 子任务、步骤、命令、机器人、版本、目录及副作用分类，是否有后续失败 |
| 写调用明确 `REJECTED` 且未产生效果，或无副作用读失败 | Ops 诊断、Recovery 提议，Host 校验后在剩余额度内重规划 | 拒绝发生于准入前还是效果之后；不能仅按错误字符串认定无效果 |
| 写回执丢失、验证缺失、未完整确认的中断 | 停止并对账；首次运行和 Worker 续跑均上报原 claim 的未知结果 | Runtime 原命令记录、停止状态与动作后观测；不能换任务 ID 绕过 |
| 目标完成读取的瞬态 `Unavailable` / `DeadlineExceeded` | 整个目标共享最多两次持久只读重试；不重新规划或执行动作 | 原期限、剩余读预算、因果事件；权限拒绝、取消和总期限失效不重试 |
| 完成读取开始后，其确认/重试状态未能完整提交 | 保存待核对状态；重启不能凭丢失的内存状态再读一次 | `CompletionReadPending` 和检查点提交证据，不清零重试额度 |
| 已完成验证，但云端结果提交未确认 | `ErrReportPending` / `ErrCompletionPending` 路径补传原结果和证明，不重新执行 | 父 claim 与上下文绑定、已申报子计划、完成事件及 outbox 状态 |
| 目录、Profile、地图、授权或 claim 不一致 | 拒绝继续，按受影响边界重新核对 | 改动是否使旧批准失效；不要只改摘要或复制新地图到旧任务 |

`CanResumeGoal` 是本地只读预检查。实际运行还会验证当前权限与云端 claim，预检查可续跑不是新的动作授权。关闭 gRPC、取消 ACK 或云端终态也不单独证明机器人已停稳。

## 4. 升级和回滚保留同一因果链

1. 停止接收新目标，在受控边界暂停/停止并核对实机状态；记录未决命令、持物和当前 claim。不要在运动中切换 Local/Worker。
2. 对任务库/事件、Worker execution 库、Runtime journal、准入 pin、地图/标定/语义版本、控制器空间部署、配置及构建身份作一致性备份。SQLite 使用在线备份 API，或所有写者退出后的完整副本；活动库单独复制 `.db` 不构成一致性备份。
3. 备份覆盖整个执行库中的 `edge-context/*`、`edge-goal/*`、`edge-child/*`、通知和 outbox，不能只导出父任务。保留子计划的完整模型输入、回执与原始证据；最近 8 条调用的模型投影不是恢复备份。
4. 保持 robot ID、任务源 stream/group、数据目录和 Runtime 身份不变，构建并运行 `--check-config`。首次建立缺失 pin 仍按[设备准入流程](edge-field-testing.md#3-首次准入与升级)，不覆盖已有不匹配 pin。
5. 先做无动作连接、目录/坐标/传感器与控制权检查，再做受监护的有限任务。恢复原任务仅使用原批准和安全检查点；未知效果先对账。
6. 回滚同样保留所有新检查点和证据。旧程序未证明能读取新聚合/事件时，不让它接管未完成目标；不能用回滚旧数据库消除新版本可能已经执行的动作。

ROS 2 的车、足式和无人机复用能力与生命周期合同，各自仍需适配器、定位/坐标、局部控制器和设备停止验证。2D Nav2、地形与 SE3/体素通道按[空间导航](../development/spatial-navigation.md)配置；共享接口不等于共享动力学、飞行或步态认证。

## 5. 留存运行证据

按父 task/revision/claim → 子计划 generation/child ID → step/command → 验证事件与观测关联。父事件中的 `EDGE_PLAN_CREATED`、`EDGE_CHILD_EVENT`、`EDGE_OPS_FINDING`、`EDGE_RECOVERY_DECISION` 和最终 `CAPABILITY_VERIFIED` 是查询入口；完整边缘 SQLite 与 Runtime 原始记录仍应归档。

最新[鲁棒性报告](../experiments/2026-10-15-single-robot-robustness.md)分别保存软件长步骤链、回执故障和实际 Gazebo。新运行使用全新目录；历史 manifest、冻结检验器和失败尝试按原字节保留。[产物保留规则](artifact-retention.md)与[研究导航](../research/README.md)说明可公开材料和本地原始数据的区别。
