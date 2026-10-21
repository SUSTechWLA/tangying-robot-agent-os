# 异构单机器人 Agent ROS 重复验收

日期：2026-10-18。本轮针对无人车、无人机、足式三个 ROS 2 Profile，检验同一边缘 LocalAgent 是否能完成自然语言长路线、保留命令与传感器来源证据，并在驱动失联和停止请求后正确关闭移动能力。基线提交为 `c1245771d56af351e997637c1b45263532a07e1c`，本轮未提交源码按字节冻结后构建测试二进制。

最终 **9/9 条自然语言任务、18/18 项 DDS 故障契约通过**；长路线包含 **99 次记录式移动和 99 次独立到达验证**。另一次完成的软件鲁棒性 campaign 保留 **23 条任务记录、1003 条总记录**，规定场景全部通过。三层证据分别统计，不能合成“真实机器人成功率”。机器索引见 [JSON](2026-10-18-edge-agent-ros-campaign.json)，逐条任务与指标见 [CSV](2026-10-18-edge-agent-ros-campaign.csv)。

## 实验边界与当前判断

链路为 `自然语言 → LocalAgent HTTP/审批/SQLite → gRPC Runtime → ROS 2 Jazzy DDS → ProfiledExecute → 记录式控制器 → 命令后观测 → 到达验证`。HTTP、gRPC、DDS、LocalAgent 和独立进程是真实执行；控制器通过更新记录位姿代替设备运动。自然语言使用确定性解析器，`modelCalls=0`。每次任务重新启动 LocalAgent、Runtime 和控制器，以隔离进程与任务数据库。

本轮证明三个机型可以复用单机器人边缘任务与 ROS 2 执行契约，也验证了新 `ros2.harness.health.v1` 诊断穿过实际链路。尚未证明飞行、轮式底盘动力学、步态、真实定位精度或小时级稳定性。足式使用平面支撑；UAV 使用不同目标高度和三维空间路径。示例 Profile 的标定版本仍为占位值，实际图像载荷为零，不能将这些样例直接视为设备调试参数。

这里的长路线、软件重启/unknown 故障，以及其他实验中的群体探索和消息交换，属于分别验证的组件链路。本轮没有将“开放式自然语言 → 多台实体异构机器人 → 陌生环境探索 → 群体联合避碰与故障恢复”作为一个完整端到端实验跑通。当前应定位为可进入逐设备受控调试的协议候选版本，不能据此承诺直接无人值守上实机。

## 预登记与固定输入

预登记文件为原始目录中的 `preparation/experiment-plan.json`。每种机型重复三次同一任务，独立任务 ID；它们是确定性工程复验，不是九个独立地图或九台机器人样本。每次一次提交：

> 从客厅出发，去厨房确认一下环境，然后去卧室，再回到厨房，再去卧室，再回到厨房，再回到客厅，再去厨房，再去卧室，最后回到客厅。最后回到出发点。

预定十次具名访问后返回单独观测的出发点，共 11 段移动。验收同时核对精确顺序、控制器目标位姿、独立到达事件、命令 ID 唯一性、空间路径 ID、地图部署版本，以及每次确认使用的命令后观测，不能只看 Task 的 `SUCCEEDED`。

地图为 `tests/contract/spatial_scenarios.py` 生成的同一合成隔墙场景，16×16×12 体素、0.25 m 分辨率；地面支撑是显式合成平面。无人车和足式绕行隔墙，UAV 路径保留高度。该任务没有从真实传感器重建陌生环境，不能用于报告 SLAM 的 ATE/RPE 或地图完整性。

原计划重复三轮 Go 软件 campaign；为避免重负载影响 DDS 时效，前三次并发开发尝试被人工中止并保留，正式 DDS 后完成一轮 Go campaign。变更在 `preparation/formal-dds-start.json` 留档，未将中止记录计为完成样本。

## 最终任务结果

仅 `container-evidence-attempt03/campaign` 计入本表。此前 attempt02 虽然通过，但初期与 Go 负载重叠，作为开发复验独立保留。

| 机型 | 模式 | 自然语言任务 | 移动 / 独立到达 | 每任务航点数 | 路径高度范围 |
| --- | --- | --- | --- | --- | --- |
| UGV | terrain25d | 3/3 | 33/33 | 48 | 0 m |
| UAV | volume3d | 3/3 | 33/33 | 41 | 0.5–1.5 m |
| legged | terrain25d | 3/3 | 33/33 | 48 | 0 m |

每条任务包含 24 条保留的 capture 元数据，共 **216 条**。额外审计逐一确认所有 99 个到达 receipt 指向任务保留的 capture，并包含来源、坐标系、序列、时间戳、变换版本和 snapshot SHA。九条任务结束时实际遥测中的 driver freshness、healthy、watchdog 以及传感器 freshness 均符合预期；unknown 命令和本地停止状态为空/未锁定。

`rgbBytes` 与 `depthBytes` 总计均为 **0**；这里的观测是控制器生成的 `sensor_fusion` 位姿，原始数据审计明确写为记录式数据。软件时长约数秒，不能解释为真实机器人行走或飞行速度。不同模式的目标高度不同，路径长度也不用于推导无人机优于无人车的结论。

## 故障、重启与回溯

DDS 每轮对三个机型分别执行驱动进程失联与声明的停止流程，共六项、三轮 18 项。每项先执行三次移动和三次独立到达，回放已完成命令不会产生新移动。失联场景真实终止控制器进程，确认 Runtime 不再发布可用导航能力；停止场景确认 Profile 声明的停止流程收到回执。回执表示进入声明流程，不等于测得设备物理停止或 UAV 已落地。

正式 Go 软件 campaign 为 `software-campaign-round-04`，场景分布如下：

| 软件契约场景 | 任务记录 |
| --- | ---: |
| 同一机器人连续五个长任务 | 5 |
| 顺序云端回执丢失 | 12 |
| 长已确认前缀后的 unknown outcome | 1 |
| SQLite 关闭再打开、已验证目标恢复 | 1 |
| 重启后遇到 unknown 写操作 | 1 |
| 规划预算、步骤预算、保留最终检查的读取预算 | 3 |

unknown、数据库恢复和预算测试属于 Go 软件契约，不冒充真实 DDS 进程在运动中崩溃后自动恢复同一任务。所有 23 条任务记录保留原始预期 outcome；预期拒绝、预算结束或 unknown 场景通过，不能改写成 23 条任务全部运动完成。

## Harness 修复与失败记录

新增 `scripts/run_ros2_edge_campaign.py` 将重复任务、DDS 故障测试、完整源码字节归档、二进制与环境、原始日志、逐条 CSV 和全文件 SHA 绑定起来；离线 `--verify` 重新检查源包、文件哈希、每条任务和 campaign 汇总的一致性，不重新执行机器人。超时或中断时清理整个子进程组，避免遗留控制器。

`run_ros2_edge_acceptance.py` 修复了一个清理顺序问题：旧流程在 `finally` 中先读取仍可能写入的 controller JSONL，半截行解析异常会跳过所有进程清理。现在先结束写入进程，再解析并保留异常行号，将不完整证据标为失败；同时检查运行中源码、二进制和 Profile 的变动。软件 campaign 的源码摘要补齐 `cmd`、`incidents`、`latency`。

| 尝试 | 结果与处理 |
| --- | --- |
| 准备命令第一次 | cwd 尚未创建，shell 未执行；拆分为先创建快照再构建 |
| 第一次源码包构建 | 缺少 incidents/latency，Go 编译失败；补齐源集，保留原包和失败日志 |
| 第一次软件脚本启动 | snapshot 目录的默认 python3 为 3.9，缺少 datetime.UTC；改用项目 Python 3.11 绝对路径 |
| 容器 attempt01 | 只选择两个 ROS 包，漏安全监督依赖；改用 `colcon --packages-up-to tangying_ros_gateway`，无任务派发 |
| DDS attempt02 | 9/9 任务与 18/18 故障通过；因早期负载重叠不计入最终九条 |
| Go software01/02/03 | 人工停止自有测试进程以隔离 DDS；全部保留失败摘要和中止说明，视为未完成 |
| DDS attempt03、Go software04 | 分阶段执行；最终通过，源字节稳定，原始验证通过 |

Harness 单元回归 **17 passed**，覆盖证据篡改拒绝、缺失验收条件、源码变动中止、原目录拒绝覆盖、半截 JSONL 保留和真实超时子进程清理；相关脚本 lint 通过。它们与上述任务实验单位不同，不叠加成更多机器人样本。

## 环境、源码与容器恢复

在原有唯一容器 `tangying-gazebo-4d6962a6c3d7-gazebo-house-1` 中测试，没有新建容器。为避免启动旧 Gazebo 并写入历史地图，先按原字节备份入口文件，临时使用 idle 入口；所有构建和任务写入独立 `/tmp/tangying-edge-agent-20261018-003`。DDS 使用 domain 117、`ROS_LOCALHOST_ONLY=1`，服务绑定 loopback。未启动 Gazebo，没有挂接实体机器人。

结束后容器状态为 `exited`，容器 ID 保持 `ebc94e6c3e3d7b8cf4d496fa1e10f9686dd1ca807ca75dfcb71fb89ef5a1b75e`，入口文件恢复 SHA256 为 `c2711c239e25e9738b40d0c85668d9a25a308e22b89438c1d5494ada84a53466`，与备份一致。恢复证明见 `preparation/container-attempt03/container-restoration.json`。

Linux ARM64 LocalAgent 由冻结的 **1105 个源文件**交叉编译，二进制 SHA256 为 `897cd2baeb0e9aeb35612e219c17cf7ce03f334e812bceaa113f77cf1e733b1b`；Go 1.26.2、CGO disabled。构建源文件字节集合与最终 DDS campaign 的源包逐文件一致。DDS 完成时的初次审计仅发现通信侧测试文件后续变更；最终集成又补充了设备族绑定预检和 Ops 在控制器状态缺失时的告警修复，另行运行回归并保留来源。这些增量不属于上述旧二进制，不能将其描述为已经执行过这九条 DDS 任务；以[总报告](2026-10-18-edge-swarm-readiness.md)的补充验证与来源索引为准。

## 复现与原始证据

完整本地目录为 `artifacts/experiments/edge-agent-ros-campaign-20261018-001/`，`experiment-index.json` 和 `attempts.json` 列出每次尝试，公开打包索引由本轮总报告统一维护。原始证据包括 controller/runtime/agent 日志、任务 JSON、capture 索引、SQLite、源包、二进制摘要和环境；公开包应排除 `local-agent/console-session`，并明确这种脱敏副本与原始完整 manifest 的区别。原始完整证据继续保留在本地。

在已加载对应 ROS 2 构建环境、隔离 DDS 域和冻结源码中执行：

```bash
python3 scripts/run_ros2_edge_campaign.py --binary /path/to/linux/local-agent \
  --output /new/evidence/ros-campaign --rounds 3
python3 scripts/run_ros2_edge_campaign.py --output /new/evidence/ros-campaign --verify
python3.11 scripts/run_edge_robustness_campaign.py --output /new/evidence/software-campaign
```

所有输出目录必须不存在。重放验证用于核对已记录证据；实机准入仍需逐台填写真实 Profile/标定/定位来源、接入厂家控制器，在受控场地分别测量急停、失联、定位退化、实际到达误差及长时间运行。
