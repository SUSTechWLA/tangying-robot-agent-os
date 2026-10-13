# 边缘 Agent ROS 2 接入与持久执行实验

日期：2026-10-13。起始代码 `a05e6cf6f0247c6a4a985178e36c4114fc15177d`，本报告随实施提交保存。配置、实现与迁移见[变更记录](../development/2026-10-13-edge-field-candidate.md)及[现场部署手册](../operations/edge-field-testing.md)。机器可读结果与制品摘要见[实验索引](2026-10-13-edge-field-candidate.json)。

这是工程集成和故障回归，**不是实体机器人实验，也不是统计可靠性评估**。实验在 macOS 主机与本地 Linux ARM64 Docker 中执行，ROS 2 Jazzy 经 DDS 通信。模型阶段使用确定性实现，没有新增在线 LLM 测量。不同场景的成功判据与代码阶段不同，不合并成“机器人成功率”。

## 1. 研究问题和判据

| 编号 | 问题 | 必须观察到的判据 |
| --- | --- | --- |
| L1 | 单机多步骤任务中断后能否继续？ | 原 task/revision，持久完成步骤不重复，最终物体关系有 RGBD 验证 |
| L2 | 动作结果丢失后会不会盲目继续？ | 恢复被拒绝，已尝试动作不重放，后续动作不派发 |
| F1 | 新 Worker 日志/契约是否兼容已有云边交接？ | 两个 Worker 的合同上下文闭环、交接不变量、实际仿真环境结果 |
| R1 | 三类 Profile 能否通过实际 ROS 2 接口？ | 精确身份、动作、独立验证、命令重放不重复、驱动失联拒绝与停止流程匹配 |
| D1 | Agent 数据能否跨进程和重启交换？ | 原信封恢复、序号连续、接收者去重、双向数据与正确回执 |
| N1 | 纯导航机器人能否直接执行自然语言路线？ | 经 Local Agent 审批/执行、实际 ROS 动作与每段验证，不要求不存在的抓取能力 |

R1/N1 的控制器是**记录式夹具**：它更新记录位姿并发布观测，没有轮地接触、飞行动力学、步态或真实环境传感器。L1/L2/F1 使用 MuJoCo 仿真；不能把这些类型互相替换，也不能计入本轮 Gazebo 或实体试验次数。

## 2. 单机持久任务

### L1：暂停、Agent 重启、继续

请求：`把红色杯子放进右侧收纳盒，然后把蓝色瓶子拿过来`。运行 `scripts/run_rgbd_acceptance.py --scenario pause-restart`，使用独立 Runtime/Agent 数据目录与端口。Runtime 持续运行，暂停后重启的是 Agent。

最终复验 task `task-227e016f7683792f2db4d2c8`、revision 1 通过：pick 2 次、place 2 次；红杯最终在 right-bin，蓝瓶在 front-tray。每次放置有多帧 RGBD 稳定关系验证，历史 capture 与执行记录保留在 `local-pause-restart-final/`。这是两个物体的有限任务恢复，不是小时级连续任务。

### L2：未知物理结果

运行同脚本 `--scenario unknown-outcome`。最终复验 task `task-271871cfa9a0c95077e18b0d`：pick 派发 1 次、place 派发 0 次，恢复被拒绝。验收通过表示**正确停在未知结果边界**，不表示完成了搬运目标。原始 Agent/Runtime 日志与 summary 在 `local-unknown-outcome-final/`。

先前开发阶段也分别执行了上述场景，保存在不带 `-final` 的目录中；最终验收不将它们当作同版本独立重复样本。

## 3. Fleet 兼容与可靠性

F1 使用原 `scripts/run_fleet_harness.py --scenario normal`，seed 7、Fleet memory 控制面、两个 Worker、MuJoCo shared-block Runtime，通过真实 HTTP、mTLS Link、gRPC 运行。逐工具准入修复后的 task `task-6ddd38ce0342adca7ca12a53` 为 `SUCCEEDED`，原验收 9 项不变量全部通过，4 次策略动作由环境结果确认。前一次 task `task-19156932a73eb85b635f2dda` 同样通过，保存在 `attempt-02-normal/`，不覆盖旧制品。

额外读取持久日志：两台设备各有一次 `CONTEXT_ACCEPTED → CONTEXT_EXECUTED → CONTEXT_COMPLETED`，outbox 为 0、通知已清空、SQLite `quick_check` 为 `ok`。第二台设备收到两次通知，但执行上下文只有一次闭环。完整证据在 `fleet-normal/`，包含任务、事件、世界状态、两套数据库、pin、进程日志与 SHA256；没有保存临时证书私钥。

normal 场景没有注入未知动作。该分支另以真实 HTTP/SQLite 回归测试验证：能力服务启动后丢响应，云端暂时没有下一个节点时不能释放 `ACCEPTED`；Runtime 正常 RPC 中携带的未知结果码也必须阻断。真实 Redis 7 独立容器试验验证 pending entry 跨消费者重启恢复及 XACK 响应丢失，未使用或清空已有 Redis 数据库。

## 4. ROS 2 异构通信

R1 最终 run5 构建 `tangying_robot_msgs` 和 `tangying_ros_gateway` 两个真实 colcon 包；六个条件为 UGV/UAV/legged 各自的驱动失联、停止 ACK 两组。全部通过，共 18 次记录式导航、18 次独立到达检查、6 次不重复的命令重放；3 次驱动退出后拒绝导航、3 次停止流程身份匹配 ACK。无人机条件包含 `z=1` 的三维目标，每条命令额外注入八帧突发，验证确切证据 ID 不被后续帧代替。

六组中的 `driverLossDisablesMotion=false` 或 `safeStopProcedureAcknowledged=false` 分别表示该组**没有执行另一种互斥注入**，不是失败计数。逐条件、command/observation ID 和类型标签保留在 `artifacts/experiments/edge-field-20261013/ros2/run5/summary.json`。run1、run2、run4 属于修复过程中的其他成功批次，不累加为最终版本样本。

该实验说明类型、身份、lease、DDS 动作和观测接线可运行；不能说明机器人真正移动、已经落地、避障成功或步态稳定。停止服务 ACK 与实际物理停止是不同证据。

### N1：一条自然语言、十次访问

请求：`从客厅出发，去厨房确认一下环境，然后去卧室，再回到厨房，再去卧室，再回到厨房，再回到客厅，再去厨房，再去卧室，最后回到客厅。`

修复就绪门禁后的 nl-run2，task `task-006070873bc52f9798f382e3` 为 `SUCCEEDED`。顺序为客厅→厨房→卧室→厨房→卧室→厨房→客厅→厨房→卧室→客厅；记录 10 次导航、10 次独立 arrival、0 次 pre_position、0 次重复运动命令。到达验证携带原命令观测，而不是用后续任意帧代替。provider 为 deterministic，模型调用 0 次。

nl-run2 任务执行约 1.945 秒，整个脚本约 3.208 秒；这是记录器的执行时间，**不是机器人走完十站的耗时**。随后 nl-run3 在第六次导航后失去确切命令证据，任务进入未知结果并停止，没有重放。定位到观测 topic 深度 1 可能被新遥测覆盖，修复为双方可靠有界 64 队列、各订阅独立串行回调，仍保留 500 ms 时效和序号检查。

最终同版独立新命名空间复验三次：

| 目录 | task ID | 结果 | 导航 / 验证 / 重复 | 任务时间（记录器） |
| --- | --- | --- | --- | ---: |
| nl-run4 | `task-bef790fd8f5f5826ad512456` | SUCCEEDED | 10 / 10 / 0 | 1.982 s |
| nl-run5 | `task-9494c5f64ecb2c095784baf8` | SUCCEEDED | 10 / 10 / 0 | 1.933 s |
| nl-run6 | `task-38a7d8b4cf3d34a3341e3958` | SUCCEEDED | 10 / 10 / 0 | 2.053 s |

三次源码清单一致，运行期间没有变更；Linux ARM64 二进制 SHA256 为 `50b155857949f99796fd11123c3f11b001ab35b8fb6e631e861f4b687113c2e8`。每次都无 pre_position，保存原命令的确切观测。n=3 是软件链路复验，仍不估计真实任务可靠性，不证明长期运行、物理导航或多机器人协同效果。各 `nl-run*/` 保留请求、任务、事件、Profile/map 身份、源码/二进制摘要和控制器日志，失败任务不删除或重试。

## 5. Agent 间数据交换

D1 使用 `scripts/run_edge_exchange_harness.py` 启动两个实际 `edge-exchange` 进程。接收方离线时发送端持久排队 2 条；发送者重启后以原 requestId 恢复投递；接收者重启后第三条序号为 `"3"`；反向 cloud-b→edge-a 收到 1 条 advisory。原始 summary 计时 5.409 秒，所有判据通过。

本场景使用显式回环 HTTP 开发模式，没有真实云端业务和运动。HTTPS、peer 凭据、跳转拒绝、错误 ACK、冲突/乱序、过期清理与容量约束另有 Go 测试；不能把回环结果当公网吞吐、网络分区或证书运维测量。对等数据不会自动成为机器人执行命令。

## 6. 失败、修复和验证解释

保留失败输出，不删除失败目录来获得通过记录：

- 新 pin 调用与 Worker、新停止上下文与 Client 字段分批落盘时，两次跨语言/进程测试在编译阶段失败。没有派发动作；属于并行代码装配问题。后续相同编译/测试入口已通过。
- Worker 未知结果测试首先发现“云端无下一节点”可错误释放通知；修复持久 `ACCEPTED` 检查并保留首次失败日志。
- 独立审查发现同毫秒旧帧可能被当作命令后观测；增加 source 序号和接收单调时间门槛，再运行 R1 run2。
- N1 首次 task `task-12db741a95e65d1be95dc5a5` 在任何 ROS 动作前报未就绪。纯导航 Profile 正确没有 pick/place，旧代码却把 `manipulation_ready` 当作通用运动开关。修复为依据严格 Profile 的实际运动能力判断共享就绪，仍检查全部计划工具、全局/单工具 blockers；只读/急停能力不能启用运动。
- DDS run3 第四组 UAV-stop 中途就绪拒绝，没有得到可确定的根因；不能将它记为已修复的具体缺陷。后续加持久就绪诊断并隔离命名空间，run4、最终 run5 通过；时效门槛未放宽。
- nl-run3 task `task-f465b8ef64a7a88c5feb61d5` 在第六次记录式移动后丢失精确证据；保持未知并停止。队列和回调修复后同版三次新任务完成，未继续或重试原未知动作。
- 文档门禁检出新 Console 脚本未归类和尚未生成的 JSON 索引链接，补齐共享会话 helper 接线、客户端清单和实验索引后复验。

首次失败后修改了实现，因此各次运行是工程迭代，不能直接作为同版本 A/B 或成功率分母。40 站导航编译测试只证明计划依赖、三维坐标和每站验证结构，不是 40 次在线执行。

## 7. 复现与论文归档

最终软件回归：`make test-go` 中 65 个有测试的包通过；改动核心、边缘、执行器和存储另有 race 回归；Python Gateway/工具层/架构/异构边界 1552 passed、4 skipped；文档/安装 38 passed，Go 文档检查、Ruff、分类目录及 diff 检查通过。不同批次有重叠，不相加统计测试总数。Linux ARM64 三个边缘程序交叉编译、Agent 容器镜像构建及三个 CLI 启动检查通过，Compose 配置解析通过；这些构建结果不代表已在 Orin 实机运行。

容器检查另外发现旧 Local Agent `--help` 正常打印后却以退出码 1 结束；已处理 `flag.ErrHelp`，保持不启动网络/任务，重新构建并验证退出码 0。该 CLI 修复发生在上述 ROS 自然语言样本之后，不改变动作路径；各运行仍以自己的二进制摘要为身份。

软件测试入口：

```bash
go test -race ./core/... ./edge/... ./cmd/local-agent ./cmd/edge-worker ./cmd/edge-exchange \
  ./middleware/sqlite ./internal/localapp ./internal/capabilityagent ./skills/manipulation ./tasks/... ./tests/architecture
.venv/bin/python -m pytest -q robot/gateway/tests tests/tool_layer tests/architecture \
  tests/install/test_edge_exchange_contract.py tests/contract/test_heterogeneous_runtime_boundary.py
go build -o /tmp/edge-local-agent ./cmd/local-agent
.venv/bin/python scripts/run_rgbd_acceptance.py --agent /tmp/edge-local-agent \
  --scenario pause-restart --output artifacts/my-run/pause-restart
.venv/bin/python scripts/run_rgbd_acceptance.py --agent /tmp/edge-local-agent \
  --scenario unknown-outcome --output artifacts/my-run/unknown-outcome
.venv/bin/python scripts/run_edge_exchange_harness.py --output artifacts/my-run/exchange
make edge-orin-build
```

每次使用新的输出目录，保留请求、随机种子、代码/二进制摘要、配置/地图/Profile、task/revision、command/observation ID、原始事件、失败与人工干预。ROS 2 的具体启动与脚本参数见[接入指南](../development/heterogeneous-mobility.md)，所需 ROS/DDS 依赖不由普通 Python pytest 提供。

本地制品多数被 Git 忽略；仓库提交摘要和 SHA256 索引，不默认发布包含现场身份、任务历史或凭据的完整目录。仅取得公开报告不能声称已独立复验原始数据。最终源码摘要用于定位交付树；早期运行属于各自开发阶段，不能把最终摘要倒填为早期运行版本。

尚未验证实体 UGV/UAV/足式设备、真实传感器误差、飞控降落、碰撞/越障、实网抖动、长时间断电与小时级任务稳定性；未估计可靠性置信区间，未证明多机器人实地协作成功率。下一轮按实际型号建立独立控制器与现场试验协议。
