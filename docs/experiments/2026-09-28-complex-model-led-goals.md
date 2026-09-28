# 复杂自然语言目标、语义状态与 Gazebo 闭环诊断（2026-09-28）

## 范围与证据

同一开发机的 Local Agent、Gazebo `home_furnished`、同源 XLeRobot 原型，以真实 `deepseek-flash` GOAL 模型经 `/v1/tasks` 创建自然语言任务，审阅冻结计划后批准。模型决定只读调用、写操作和顺序；`robot.task` 的导航与抓放仍由受契约约束的闭环执行器完成。审批和执行未由实验脚本选择工具。13 条样本是按故障逐次修复的诊断序列，不能用其中 5 个通过任务计算成功率。

[机器清单](2026-09-28-complex-model-led-goals.json)列出所有 13 条请求的结果、任务 ID、模型可见读次数和原始 JSON 的 SHA-256。原始 `draft-plan.json`、`approved-plan.json`、`task.json`、`report.json` 或创建错误保存在本机 `artifacts/acceptance/complex-goals-20260928/<case>/`，未把巨量任务事件复制进 Git。清单使用原项目的绝对路径定位这批现场样本；换机器后需要复制该目录才能复核文件哈希。所有已创建并通过的任务均为 `source=llm`、冻结计划与批准计划一致。

| 诊断目标 | 首次结果 | 修复后独立任务 |
| --- | --- | --- |
| 检查标定/地图，再去厨房工作区并返回客厅 | `case01` 在 `IMU_NOT_READY` 前拒绝物理动作 | `case01-v2` `task-2810167dd05dda37c9ac70ea`，3/3 能力核验，两处到位确认 |
| 条件性核对地图并去卧室 | 初次计划遗漏卧室；第二次模型多工具输出拒绝创建 | `case03-v3` `task-a074598d2fb8b48e2a96246b`，2/2 核验，一处到位确认 |
| 从客厅到厨房取杯放进收纳盘再返回 | 两次缺完整房间路线而拒绝创建；两次因旧重建拒绝；一次走到厨房后指爪收敛超时 | `case06` `task-242a5081749f9a716ec19b4e`，4/4 核验，厨房/客厅各一次到位，抓取与放置各一次独立确认 |
| 重新巡检建图、解析卧室、前往并确认定位 | — | `case07` `task-62e0c7e11385ae4d11c76461`，5/5 核验，一处到位确认 |
| 只读核对地图、定位和卧室，不移动 | — | `case08` `task-5ed171b1571b1c70abfc6c2a`，4/4 只读核验，计划无物理写 |

其他保留样本包括 `case02-v3`、`case04` 的旧视觉重建拒绝，`case05` 的 `JOINT_TARGET_TIMEOUT`，以及未批准的 `case03` 旧草案。`case04` 的 `CAPABILITY_FAILED` 事件给出 `ground subtask 1: reconstruction rejected: reconstruction is stale or future dated`；由此修复等待新帧和失败追溯。指爪门限只对 Gazebo XLeRobot 的已知视觉指爪轴由 0.04 调至 0.06 弧度，臂轴仍为 0.04，吸附、抓取和放置的后续证据没有放宽。未批准草案从未执行。

`case07` 的 `mapping.build(mode=survey)` 操作 ID 为 `2c72804716ad482284956634e932a964`；实走 23.496115 米，采 233 帧，220 次配准、2 次回环、72,289 个点。新地图 `scan-2c72804716ad`，版本 `ea419bbf785ac6c19834243c0d1d00463758c73654ad9dd6cedbca87caf31f5f`。其后卧室 `semantic.resolve` 的 `navigationReady=true`，`navigation.status` 在同版地图上返回 `localized/ready=true`。这是本次仿真里的记录，不能外推到实机定位精度。

## 状态理解层复核

修复后的模型目录只公开 Provider 声明 `planningFields` 的只读投影；`navigation.map` 的栅格未进入规划期目录，模型改读 `navigation.status`。投影经字段、递归原始信号键、密集数组和 8 KiB 大小门限校验。机器清单对 13 条草案的模型可见 `planningTrace.result` 检查，原始图像、RGB-D、IMU 样本、点云和栅格字段数为 0。`case08` 在真实 Gazebo 重启后主动选择 `navigation.status` → `mapping.inventory` → `semantic.locations` → `semantic.resolve(卧室)`，读到地图身份、融合来源 `imu_roll_pitch_odom_yaw` 和定位状态，随后冻结同样四个只读步骤并完成。后台常规状态不订阅 RGB/深度；普通控制台遥测默认隐去点阵和原始传感器字段，显式诊断才读取几何或图像。后一句由接口测试核验，`case08` 本身只核验模型规划边界。底层执行器仍可处理原始测量作 SLAM、避障与物理核验，这不属于模型状态读取。

Gazebo ROS 接入层将新鲜 IMU 横滚/俯仰与里程计航向组合，过期 IMU 时不更新位姿；再由 RGB-D SLAM 得到地图定位。这不是完整 IMU 惯导或 EKF，不表示 IMU 已修正平面位置和航向漂移。其他机器人必须声明自己的融合来源和失效条件，不能直接复用这个仿真来源标签。

## 修复与验证

修复涵盖模型逐轮读取与无效提案重试、完整家庭路线冻结校验、语义状态投影、短暂传感器未就绪的限时准入、新鲜视觉采集、Gazebo 指爪容差和失败事件记录。实现契约见[保留的设计规范](../development/2026-09-28-model-led-goal-semantic-views-spec.md)。本轮 `go test ./...`、`make lint`、相关 137 项 Gateway 回归及上述现场任务通过。Orin、GPU 大模型服务、实体机器人、机群并发与量化模型延迟未在此报告认证。
