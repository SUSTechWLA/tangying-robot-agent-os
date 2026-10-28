# 执行前范围核对

以下选取已经阅读测试及其 helper，仅使用生产模块配合进程内替身；这里没有通过测试名推断设备安全性。

| 测试 | 已读取的实现与替身 | 注入 / 预期行为 |
| --- | --- | --- |
| TestScopedDispatchSurvivesPeerReconfigurationAndRestart | edge/exchange/service_test.go：receiverTransport 直接调用 Service.Receive，t.TempDir SQLite | domain / robotId / agentId 变化后，新消息不受旧信封阻塞；关闭重开 SQLite；保留旧信封 |
| TestRejectCrossPeerIncomingCredentialDisclosureRegardlessOfOrder | cmd/edge-exchange/main_test.go：testConfig 写临时合成 token，只调用 loadConfig | 两种配置顺序都拒绝跨 peer 凭据复用；不监听端口、不创建状态目录 |
| TestSingleRobotCampaignSQLiteCloseReopen | internal/edgeagent/campaign_test.go、host_test.go：campaignRobot / missionRobot 的 CallService 直接改内存，scripted campaignDecider | 32 步确认后关闭重开 SQLite，恢复后不重复动作或规划 |
| TestSingleRobotCampaignUnknownAfterLongConfirmedPrefix | 同上，lostAt=25 返回合成 transport error | 24 步确认，第 25 次写响应未知；保留 pending，重启不重放、不假完成 |
| TestDelegatedCompletionReadFailureIsBoundedAcrossRestart | internal/edgeagent/completion_test.go：completionReadFault 包装 missionRobot | 重试配额、读取预算、权限拒绝分别安全停止；不会新增物理写 |
| TestPlanningReadBudgetLeavesArchiveThenTwoProposalDecisions | internal/capabilityagent/planning_convergence_test.go、planning_context_test.go：largePlanningProvider 返回合成字符串，contextPlanningDecider 直接调用 Go 函数 | 6 次业务读后仅回查冷档两页，再保留两轮提案；无外部模型调用 |

这些包没有 TestMain/init 运行外部服务。每轮固定 6 个顶层测试；子测试单独记为 leaf，避免父子重复计入分母。日志里的 physicalStarts/writes 是 fixture 写计数，不是机器人实际运动。32 步目标完成与未知结果待核对分别统计。exchange 测试使用固定 testNow 逻辑时钟，不将信封时间当作实际实验发生日期。

不执行 Gazebo、ROS 2 Runtime、Docker、真实模型或机器人接口；不复制临时 SQLite 数据库。没有对照组，也没有独立随机场景，重复次数只用于检测这组确定性回归的偶发失败。源码前后变化时保留日志并排除单版本验收结论。
