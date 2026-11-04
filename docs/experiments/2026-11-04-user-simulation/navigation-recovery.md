# 首次厨房导航失败与保留现场恢复

本记录只覆盖 `task-3b03351306d5b7df4835f7be6e69d577` 的首个导航故障、恢复准备与相关代码变更；不代表整个巡检任务成功。完整实验结论见同目录主报告。

## 故障证据

- 原子步骤 `rev-1-cap-02-navigate_00` 于 `2026-11-04T05:05:49.660432Z` 派发，`05:05:50.584096Z` 报 `NAV2_ACTION_ENDED`。
- Gazebo 日志显示：Nav2 从约 `(0,-1.25)` 向厨房 `(2.05,3)` 规划，`GridBased` 返回 `Failed to create plan with tolerance of: 0.010000`，随后 action abort。不是任务已经到达厨房。
- Runtime 命令追加记录与导航 SQLite 都为失败；前置 `navigation.pre_position` 成功。原任务、事件和数据库未改写。
- 启动时只复制了 workflow 的 DenseSLAM/语义地图，没有恢复 Nav2 使用的 RTAB-Map 库。两层地图是不同数据产品；语义路线在保存栅格准入，并不意味着新的 RTAB-Map 已观测过同一走廊。故障时 RTAB-Map 局部图/工作内存只有 3 个节点，后续只读快照为 12,172 个已知格。
- 独立 ROS 采样从故障后开始，不能据此证明失败期间没有运动。恢复时的只读 TF 检查显示里程计仍约 `(0,-1.25,0.035)`，距厨房约 4.72 m；这支持“未到达”，不能替代物理安全停止的全部证据。

原始证据位于本地 `artifacts/acceptance/user-sim-20261104-001/slam-recovery/`：`failure-gazebo.log`、`before.json`、`after-load.json`、`plan-probe.jsonl`。它们含各自采样时间，不能把后续快照回填成首次故障时刻的观测。

## 恢复步骤及边界

1. 通过 SQLite backup API 备份本轮 fresh RTAB-Map 库；另将 10-30 库复制到本轮 `maps/home_furnished/recovery/restore-from-20261030.db`。不复制旧导航命令、Runtime 命令或 Agent 任务库。
2. 经主 agent 授权，调用 `/rtabmap/rtabmap/set_mode_localization`，再调用 `load_database`，明确 `clear:false`。没有重置 Gazebo、修改机器人位姿或发送运动目标。
3. 一次设置 `Mem/InitWMWithAllNodes=true` 的 CLI 参数因 bool/string 类型不符被 ROS 拒绝；失败保留在 `restore-services.log`。读取实际参数确认 `Mem/IncrementalMemory=false`，`Mem/InitWMWithAllNodes=false`。加载后的图实际含 856 个工作节点，已知格为 96,045；不能把被拒绝的参数设置记作成功。
4. 使用只读 `ComputePathToPose`（没有 `FollowPath` 或 `NavigateToPose`）规划到厨房，返回 status 4 / errorCode 0，471 个路径点，路径长约 6.059 m。此结果只证明该时刻可规划，不证明机器人已执行路径。
5. 发现 HTTP 仍显示启动时的 `mapping`，尽管实际 ROS 已进入 localization。恢复后通过正式 `set_mode_mapping` 服务使运行模式与本次实验启动契约一致，并修复该状态误报。
6. 在无活动导航目标的边界，只重启 `navigation_http` 加载补丁；Gazebo 和 gateway 进程保留。`live-patch-manifest.json` 指明本轮为混合代码批次：导航 sidecar 已更新，gateway 的诊断透传变更当时尚未加载。补丁后 `ready=true`，实际和配置模式均为 mapping。

恢复不是把原任务标记成功，也没有绕过未知物理结果的对账限制。后续任务须通过正式对账、重新规划和用户界面批准。

## 代码变更

### 运行模式需要观测来源

`NavigationNode` 每 0.5 s 只读查询 RTAB-Map 的 `Mem/IncrementalMemory`，按实际 bool/string 值区分 mapping/localization。返回的 `mode` 是该实测值，`configuredMode` 保留启动配置，`modeEvidence` 指明来源和年龄。超过 3 s、服务失败、响应不合法时返回 unknown，并以 `RTABMAP_MODE_UNAVAILABLE` 阻止运动准入；不退回启动值冒充实际模式。

实际 localization 仍须新鲜定位观测与协方差合格；mapping 也仍须地图、视觉特征、TF、双相机、里程计和 Nav2 lifecycle 检查。这增加了对 ROS 参数服务的明确依赖；服务不可用时系统应报告未知而不是扩大运动权限。

### 失败保留底层原因

Nav2 action 的 status、error_code、error_msg 在终止回调中写入冻结 `failureObservation.nav2Action`。错误消息最多 2,048 字符，字段类型和范围受限。后续健康遥测不能覆盖终止时的原因。

gateway 导航客户端向调用方保留该冻结诊断与 goalId，技能结果提供 `navigationFailureJson`（goalId、commandId、observation）。保留原错误码和失败/未知结果处理，不以 action abort 推断可安全重试。对于历史失败没有补填或伪造新的 Nav2 结果字段。

排查透传链发现 Python `RobotRuntimeService` 与 Go `robotclient` 都只读状态报告，其他 payload 会丢失。现将失败诊断显式纳入 Runtime 命令日志和 `runtime.Result.FailureDiagnosticsJSON`：必须是与当前 commandId 一致的 JSON 对象，原始及规范化后都不超过 16 KiB；成功事件不携带该失败字段，畸形、超限或错绑字段丢弃，不改写终态，也不当作成功或重试授权。Go Agent 再将其写入失败工具事件，形成导航 sidecar → gateway journal → robotclient → Agent 持久事件的链路。

在无活动导航、Runtime 命令全部终态且无资源 grant 的边界，随后单独重启 gateway 加载三个 Python 文件。首次手工启动因缺少 launch 派生的标定环境变量而退出；第二次沿用仍存活 RTAB-Map 进程的原始 launch 环境后成功，未打印环境凭据。两个日志均保留。`gateway-patch-manifest.json` 记录文件哈希和 RuntimeJournal 前后哈希，命令日志字节完全一致；Gazebo、RTAB-Map、导航 sidecar 保持原进程。新读取的能力目录须用于重新规划，旧计划不能直接沿用。

## 验证

- Host：gateway workflow/backend 和导航 HTTP 回归共 **103 passed**。
- 诊断透传追加验证：`go test ./edge/runtime ./edge/robotclient` 通过；Python service **37 passed**，包含失败诊断的持久化/重启原样重放且不再次调用 backend，以及成功、畸形、超限、错绑拒绝。
- 独立 ROS_DOMAIN_ID=94 的容器软件测试：真实 ROS 类型、NavigationNode、SQLite 与 HTTP 共 **61 passed**。无驱动控制命令，不是物理巡检成功次数。
- 一次测试因临时目录层级不符合 conftest 的项目布局而失败；第二次因覆盖 PYTHONPATH 导致 ROS 模块跳过；调整目录并保留 ROS PYTHONPATH 后，第三次 61 项全部执行通过，日志保留为 `navigation-patch-tests*.log`。
- 实际 Gazebo：保留物理现场，恢复地图后只读路径规划成功；补丁加载后遥测 ready。巡检终态与每站到位指标由主实验报告单独记录。

## 第二轮：管理状态过期不能标成实际 inactive

第二轮 `task-a0fc09863e3a4be4f14ef61945b72877` 的第一段导航于 `05:30:31.558Z` 被 sidecar 冻结为 `NAVIGATION_OBSERVATION_LOST`。此时 RTAB-Map processing age 2,124 ms、双相机 age 600 ms、TF/里程计 age 37 ms，但模式和三个生命周期查询已失去有效期。Agent 事件 41/42 正确保留了新增诊断，表明透传链实际工作。

Nav2 日志在该时刻仍显示 controller 接收路径和运行；`05:30:31.762Z` 明确记录取消成功、停止机器人。管理查询随后恢复，sidecar 只剩 cancellation pending。因此旧 `NAV2_*_INACTIVE` 文案并不能证明节点真的被关闭。原实现把 stale 与 inactive 合并；模式/lifecycle clients 又与地图和 action 处理共享默认互斥 callback group。共享组会阻塞查询完成，这是代码和并发测试证实的调度风险；现有日志没有单独测量每个 callback 的执行时长，不能给出该次阻塞的完整 CPU 时间归因。

修复后，管理 clients 与轮询 timers 使用独立 callback group；freshness 仍按请求开始时间算 3 s。真实 inactive 仍立即阻止运动；过期观测改报 `NAV2_*_STATE_STALE`。短暂管理查询过期加入现有真实零输出的有界 observation hold，默认仍为 2 s，只有原命令和授权尚有效、观测恢复且未超过期限才可继续同一命令。终态 FAILED 永不复活或自动重发。

新增并发测试实际堵住默认 callback group，验证另一个 ROS 服务组仍能更新模式；其余测试验证零输出等待、恢复只启动一次、超时终止、真实 inactive 不等待。最终 `navigation-patch-tests-005.log` 为 **66 passed**（ROS_DOMAIN_ID=95），未提高 freshness 或 hold 阈值。

主 agent 完成第二轮正式 ABANDONED 对账并取消后，仅替换导航 sidecar PID `9631 → 18470`，保留世界、位置、RTAB-Map、gateway 和失败历史。`navigation-patch-002-manifest.json` 与 `live-final-file-verification-002.json` 记录本次源文件/运行文件哈希，五个 Python 文件均一致；`after-navigation-patch-002.json` 记录加载后的 ready 状态。后续执行期间不再运行编译或测试来增加仿真负载。

## 第三轮只读导航监控

第三轮 `task-583c94bad756b62ba0b238c678605c39` 最终任务状态 SUCCEEDED。执行窗口 `1793770883109–1793771076521 ms`（UTC Unix ms）共 193.412 s；这不包括规划与批准前的耗时。

`navigation-watch-003-summary.json` 汇总该窗口内 96 个两秒采样，最大采样间隔 2,020 ms；not-ready 样本 0。最大 mode 查询年龄 1,599.6 ms，RTAB processing age 1,137 ms，TF/里程计 age 100 ms，base/head 相机 age 255/377 ms。四次 pre_position 和四次 navigate API 目标均 SUCCEEDED，现存目标记录中的 observationHolds 总数 0；只读目标结果另存 `navigation-goal-results-003.json`。

该采样不是连续健康证明，也不是外部物理真值。每站到位、原始图像、独立 verify_arrival 与完整任务收尾由主实验 audit-003 核验。第三轮 Go 二进制为 cohort-003，SHA256 `0f88a03822f28dae1280a241fa2bf721bd64e069d29505f2f01476ee787358c6`；早前 live-final-file-verification-002 中的二进制哈希是 cohort-002 时点，不应覆盖成第三轮身份。

## 第四轮只读导航监控

同 cohort-003/world 的 `task-86a1f1438b266353b5a9e4965ed6c1fa` 也达到 SUCCEEDED，执行窗口 239.178 s。`navigation-goal-results-004.json` 中四次 pre_position 与四次 navigate 均成功，observationHolds 总数 0。

额外 health watch-004 首样本比进入 EXECUTING 晚 186.567 s，只覆盖末段 27 个执行中样本，not-ready 为 0。这不是完整执行健康率，完整范围使用 campaign 的持续 ROS 流和 audit-004。任务结束后仅停止该只读采样进程，未停止仿真服务；范围和限制写入 `navigation-watch-004-summary.json`。
