# 异构边缘 Agent 与分布式协作分层验收

日期：2026-10-18。基线 `c1245771d`；实现见[本轮升级](../development/2026-10-18-edge-swarm-readiness.md)。本报告区分软件合同、真实 ROS 2 DDS 记录式控制器、回环 HTTP 数据交换与空间算法 fixture；这些证据不能拼接成真实异构机群的端到端物理成功。

## 评价

当前可以作为逐型号实机接入和受控调试的候选。已具备单机任务/命令/观测回溯、三形态 ROS 2 harness、按任务和地图校验的 peer 数据消费以及故障观察基础。尚不足以直接放行“任意机器人在陌生环境中自然语言驱动、无人值守自主协作”。具体厂商控制器、真实传感器和标定、跨机器人地图对齐、动态协同避碰与现场停止验证仍未完成。

## 本轮实验口径

所有测试先固定输入和实现来源，再保存结果；原始失败目录不覆盖。开发调试、同一场景重复运行和真正不同地图分别标注。研究者可用于论文方法与工程验收章节；不能据这些样本计算实机部署成功率或全场景覆盖保证。

| 层级 | 回答的问题 | 不支持的结论 |
| --- | --- | --- |
| ROS bridge/Runtime 软件合同 | 未知命令是否阻断，停止是否关联，传感器诊断是否有界 | 实际停止距离、飞行稳定或步态稳定 |
| LocalAgent→gRPC→DDS | 自然语言固定句式是否形成经独立到达验证的长任务 | LLM 泛化、真实定位精度、实际电机运动 |
| 四进程 loopback HTTP | 消息是否持久传递并按任务/坐标范围消费 | 无线丢包、DDS 多机 QoS、多主执行一致性 |
| 三形态未知环境 fixture | 生产 Runner 是否协调动作、测量、未知结果和有界覆盖 | 多机器人真实 SLAM、并行动力学、任意全场景完成 |
| Host/SQLite 软件长任务 | 检查点、预算、上下文和重启是否重复动作 | 数小时实机耐久度或断电安全认证 |

## 实际发现与修复

- ROS bridge 异常后曾提前清空 active command，停止请求无法指明未知动作；现保留 unknown 身份并阻断新命令。
- Ops 曾接受过期/未来时间为健康、用收件时间刷新缺失采集时间、遥测丢失后关闭历史异常；现按权威来源保留未解决状态。
- 初始化历史读取失败曾永久跳过恢复；现完整读取成功后才确认初始化，失败重试。
- 原 inbox 最新 100 条接口没有可可靠恢复的消费游标；新增事务游标、保留缺口和明确作用域的覆盖视图。
- 新测试发现不完整 harness 也可能误清未知动作；独立复核后补齐时间、字段与逐来源关闭条件。
- 最终独立复核发现控制器缺失时的合法空值会掩盖未知命令告警，以及桥重启后的空 unknown 字段会误清旧命令告警；新增失败复现和修复后回归单独归档。两项修复均在原 DDS 轮次之后，不能算入旧二进制的运行证据。

原 Ops 缺陷通过新增回归先复现，`ops-before-fix.log` 保留失败；后续修复过程中测试时钟夹具和提示词断言失配分别保存。一次包含长 campaign 的 `go test -race` 触发 10 分钟默认超时，堆栈停在长步骤 JSON/检查点处理，不能记作通过；随后将有并发影响的包单独 race 测试，并将长任务作为独立软件 campaign 执行。

## ROS 2、长任务与通信实际结果

正式 ROS 2 轮次为 attempt03：三种机型各 3 条固定自然语言长路线，共 **9/9 任务成功**、**99 次导航和 99 次独立到达验证**，每条均返回最初观测到的起点，未发现重复运动命令；另有 **18/18** 驱动故障合同检查通过。真实传输为 LocalAgent HTTP → gRPC Runtime → ROS 2 DDS；使用 deterministic 解析，LLM 调用数为零。开发 attempt02 也通过，但它与正式轮次使用相同场景，不能合并成独立场地样本。

harnessHealth 实际经过整条遥测链路，能查到每个 source/frame/sequence/observation/calibration/年龄。此实验的图像字节数为 **0**，控制器使用记录式位置与合成 sensor_fusion，Profile 标定仍为示例值；既不是相机/激光雷达成像试验，也不是无人机试飞或足式运动实验。

Host/SQLite 正式软件 campaign round04 通过，**23 条场景记录**覆盖 32、48、64、96、128 步任务链、确认后重启不重复、云端回执丢失、未决动作禁止重放与预算边界。通过表示各场景符合其完成或拒绝判据，不能写成 23 个物理长任务全部完成。前三轮因负载隔离被人工中止，保留中止原因与未完成日志，不计成功。

peer 通信 campaign 002、003、004 各启动三个机器人身份和一个云身份，共四个真实进程，使用显式开发回环 HTTP。三次均验证发送/收件重启、离线后恢复、分页消费、无效作用域/坐标/年龄拒绝和 Python explorer 实际建议排序；本地测量账本不会因 peer 全覆盖报告而变成完成。001 因短 TTL 排队和负例时效干扰失败，修正的是实验注入顺序，不放宽线上认证或动作准入。005 的新增源码归档预检失败发生在启动通信场景之前，后续单独修复验证，不能计作完成任务。

综合回归：机器人 Gateway、安装、三机型 fixture 和仓库合同 **2330 passed / 6 skipped**；Ops/Local/Worker 与通信 SQLite 的 race 检查、跨模块 Go 回归、构建与 lint 通过。完整命令与日志纳入证据，不将未运行的全仓 Web 或动力学测试描述为通过。

## 三形态陌生环境任务

固定场景意图为三个机器人协作扫描未知区域。脚本不调用 LLM；实际自然语言解析由另一层 DDS 路线实验验证。使用同一全 ROI、三个独立 seed，各运行正常/故障两种条件，使用生产 `SpatialMissionRunner`、`TaskCoordinator` 和 `CoverageLedger`，每次 18 轮预算。

六个任务均为 **partial**，没有删除不可见区域或缩小分母。正常平均覆盖率 **81.70%**，故障组 **78.54%**，0 软件异常。总计 **179** 条软件命令：159 成功、17 已知拒绝、3 unknown。每个任务的 UAV、UGV、legged 都有成功移动与观测贡献。

| Seed | 正常覆盖 | 故障覆盖 |
| --- | ---: | ---: |
| 3 | 75.00% | 70.19% |
| 7 | 93.53% | 94.51% |
| 19 | 76.57% | 70.93% |

9 项预定故障实际应用 8 项。seed 19 足式拒绝注入后没有符合条件的命令，记为 skipped；没有修改调度强行补出成功注入。3 次 unknown 均在绑定命令的正停止回执后恢复。地图消息缓存重连投递 58 条，最终无积压。这里的断连只影响占据图转发，共享 ledger/coordinator 仍然可用，不能当作真正分区自治或分布式共识测试。

原始传感器采用理想体素射线，控制器采用受约束的几何位置更新。覆盖指标是已分类体素比例，包含占据和空闲，并非点云表面重建质量或语义识别准确率。未执行动态物理、飞行/步态或真实传感器误差模型。

## 可复现入口

```bash
# 无硬件的三形态探索 fixture；新目录，拒绝覆盖旧证据
.venv/bin/python scripts/run_heterogeneous_exploration_fixture.py \
  --config examples/experiments/heterogeneous_trio_fixture.json \
  --output artifacts/experiments/trio-new
.venv/bin/python scripts/run_heterogeneous_exploration_fixture.py \
  --verify artifacts/experiments/trio-new

# 独立进程的回环通信与实际建议消费
.venv/bin/python scripts/run_edge_cooperation_harness.py \
  --output artifacts/experiments/peer-new

# 在已构建同版ROS2消息/网关的隔离Linux环境中运行
python3 scripts/run_ros2_edge_campaign.py --binary /path/to/local-agent \
  --rounds 3 --output /path/to/new-evidence
```

软件源码、实际命令、测试结果、来源摘要和原始证据见[公开归档与字节校验](evidence/2026-10-18-edge-swarm-readiness/README.md)及[机器可读索引](2026-10-18-edge-swarm-readiness.json)。公开归档移除了临时会话凭据，保留原 manifest 和脱敏说明；完整本地原件保持不变。各层详细记录继续见[ROS 2 重复验收](2026-10-18-edge-agent-ros-campaign.md)和[通信消费实现与实验](../development/2026-10-18-scoped-peer-cooperation.md)。

## 已选实机设备族与后续验证

用户确定首轮使用大疆无人机、宇树足式、通用 AGV 的 ROS 2 控制。新增[型号绑定与预检入口](../operations/ros2-device-bindings.md)属于这次实验之后的集成增量，不在既有 DDS 二进制的冻结来源内。它不能把记录式 UAV/legged/UGV 控制器结果转换为 DJI/Unitree/AGV 实机通过；后续应按真实型号另建实验目录，记录固件、SDK、配置摘要和实际传感器数据。

补充验证：设备绑定的 **36 项离线测试通过**；三个模板模式预检通过，正式模式均按设备参数未填写的预期返回 2。最终两项 Ops 复核修复通过 `agentruntime / internal/localapp / edge/worker` 的非缓存 race 回归；本机和 Linux ARM64 边缘构建、最终 lint、52 项文档/教材检查与 Go 文档检查通过。新增源码、失败复现、预检输出和构建摘要在公开包 `post-review.tar.gz`，与原始 `raw-evidence.tar.gz` 分开校验；没有重新执行 DDS、Gazebo 或实机。最后查询 Docker 时守护进程已不可达，未为追加验证重新启动它；此前测试退出时的容器恢复证明保留。
