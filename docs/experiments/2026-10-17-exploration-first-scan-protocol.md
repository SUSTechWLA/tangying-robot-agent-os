# 首次未知场景探索扫描：预登记与验收协议

协议日期：2026-10-17。状态：**预登记协议与验收口径，实际结果另行记录**。起始软件基线 `980e8b4d8`；本轮实际实现、镜像和运行参数在每次实验的预登记中固定。首轮执行后新增的扫描角度复核口径在下文明确标注；不得把新判据或新修复回填为旧运行已经通过。已有 2026-10-15 长程任务证据只作历史参照，不复用为本轮通过结果。

## 研究问题与范围

验证单台地面移动机器人在没有预建地图和预定房间路线的条件下，是否能够先完成有界的主动观察，依据新获得的在线地图选择 frontier 视点，并在明确预算内保存可用地图、停止和解释剩余未知区域。实验运行真实 Gazebo → ROS 2 → RGB-D / RTAB-Map → Runtime 的生产链路；若再经过自然语言任务审批，应单列为上层集成验收。

不把本实验称为实机验证、无人机探索、无定位探索、完全自主 SLAM 或完整房屋覆盖。本轮是二维地面探索；FUEL 与 EGO 均未集成。仿真世界、机器人初始位姿、传感器、里程计以及碰撞真值来源均应披露。规划器可获得机载传感器和合法本体约束；事后评分可使用仿真真值，规划阶段不可读取真值栅格或预定房间路线。

## 冻结实验初态

每次尝试创建全新且拒绝覆盖的目录，例如 `artifacts/experiments/exploration-first-scan-20261017-001/`。先写预登记，再启动实验动作。至少保存：

- 唯一 run ID、开始时间、Git HEAD、dirty patch、变更源文件 SHA-256、Python/Go/ROS/Gazebo 版本。
- 实际容器 image digest、Docker image ID、SDF/资源 manifest SHA-256、episode/seed、启动时间、命令和脱敏配置。
- 私有 token 仅记是否已配置，不保存其值；记录模型的 provider、model、角色路由、推理参数，不导出密钥。
- 机器人 ID、Runtime/能力目录版本、标定版本、工具可用性、运动与传感器限制。
- 地图库存、在用地图、RTAB-Map 数据库初态、导航模式、初始位姿、首帧观测时间；新建目录/卷与“没有导入旧地图”的独立证明。
- 请求原文、最终批准计划与预算，或者明确标为 Runtime 调试 RPC；两者分开统计。

旧 episode `1016` 已有地图和定位，不符合本协议的 map-cold 初态。保留旧实例和历史失败任务。新建隔离 namespace，不能删除旧数据库或把地图改名来伪造冷启动。冷启动验证允许第一批传感器已自然产生小范围局部地图；应保存其范围和形成时间，不能把传感器预热误写成全零地图。

宿主资源不足时先确认旧实例空闲，再可逆暂停它，结束新实验后停止新实例并恢复旧实例；保存暂停/恢复时间及前后健康状态。实验中不同时承载两套物理仿真以免资源竞争混淆结果。

## 两层试验

### A. Runtime 探索扫描

显式调用 `mapping.build` 的 `explore` 模式，不给 `baseMapId`，不用 `mapping.ensure`（它允许复用已有地图），不用 `survey`。首次尝试建议预算为 8 m、最多 1 段、操作外部时限 900 s；预算值必须在第一次动作前登记。若完成后覆盖不足，可以另开新尝试扩大预算，保留原记录。

基线调试工具可以按下列方式调用，但其记录缺口必须由外部记录器或升级后的 harness 补齐；以下是**建议命令，本文编写时未执行**。端口和输出目录须替换为新的隔离实例：

```bash
.venv/bin/python scripts/survey_run.py \
  --target 127.0.0.1:NEW_RUNTIME_PORT \
  --output artifacts/experiments/exploration-first-scan-20261017-001/runtime \
  --entry mapping.start --mode explore --name first-scene-exploration \
  --max-travel-m 8 --max-legs 1 --timeout 900
```

这里 `mapping.start` 是操作员调试入口。正式能力服务 `mapping.build` 包含长操作 lease，需要通过执行器维持租约或由专门 harness 按生产契约执行，不能把一次裸 RPC 当完整长操作验收。

本轮专用记录器已实现，可优先使用下列模板。`--source-root` 必须指向实际挂载进容器、保留仓库相对目录结构的冻结源码；`--map-root` 为新 runtime 工作流地图目录的宿主机挂载。环境 JSON 必须事先脱敏，不得传入私密 `.env` 或含凭据的完整 `docker inspect`。模板中的路径与端口需按实际隔离实例替换：

```bash
.venv/bin/python scripts/validate_exploration_first_scan.py \
  --runtime 127.0.0.1:50571 \
  --output artifacts/experiments/exploration-first-scan-20261017-001/evidence/attempt-001 \
  --map-root artifacts/experiments/exploration-first-scan-20261017-001/gazebo/maps/home_furnished/workflow \
  --source-root artifacts/experiments/exploration-first-scan-20261017-001/source \
  --environment-json artifacts/experiments/exploration-first-scan-20261017-001/environment-preregistration.json
```

该记录器默认 8 m / 1 段 / 900 s，并保留轨迹。单段成功要求初始扫描端口已明确尝试且返回；这不等于已完成 360° 转动或全部场景扫描，实际动作与观测仍需独立核验。对已加载但未 ready 的导航不会判通过。辅助回归 `tests/scripts/test_validate_exploration_first_scan.py` 使用 fixture 测试记录与停止语义，没有启动仿真或真实运动。

### B. 单自然语言到地图结果

建议请求：

> 第一次进入这个环境，请先主动环视并采集 RGB-D，依据实时观测选择可安全到达的未知区域继续扫描，不按预定房间路线巡检；总行程不超过 8 米、最多 1 段，完成后保存并启用地图，报告停止原因和仍然未知的区域。

批准前核对至少存在 `mapping.build(mode=explore,maxTravelM=8,maxLegs=1)`；禁止计划中出现 `mapping.build(mode=survey)`、`mapping.ensure` 复用、`robot.task` 房间路线及未请求的机械臂动作。模型不能完整表达目标时，在动作前拒绝，保留规划失败原文和所有轮次，不通过删改请求隐藏失败。

`scripts/evaluate_capability_goal.py` 可要求工具与参数，并留存冻结计划、审批和完成证据；它属于 `/v1/tasks` 计划入口。若改为 `/v1/objectives`，另记 Host 子计划和授权，不可混称相同任务协议。

## 在线记录与异常处理

从操作提交前开始留存，至少包括完整初态、每次服务请求与响应、首轮扫描状态、逐次选择视点、测量栅格尺寸/分辨率、前沿数、拒绝码/位置、操作和会话 ID、累计行程、帧数、轨迹、段切换、状态及停止原因。JSONL 每条带 UTC 时间和单调时钟耗时，持续落盘。

当前实现的 `scanEvidence.looks` 只保留最近 12 次扫描、`scanEvidence.legs` 只保留最近 6 段；独立 JSONL 和各段地图包承担完整历史。记录扫描的 `phase`、`status`、实际接受关键帧增量、命令数、目标角度、有符号实测进度、绝对角路程、剩余角度、实测平移偏移和耗时；`initialScanStatus=returned` 只证明端口返回。SLAM 来源中的 `wholeSceneCoverage: not_measured` 和 `coverageScope: observed_grid_nonblind_frontiers` 必须保留。

深度不足的连续 streak 与整操作累计拒绝数分别记录；有效关键帧、新操作和续段的重置行为应在测试中核对。续段应保留原取消 `Event` 与租约状态，不能借分段恢复运动授权。`visitedViewpoints` 是退役观察目标，`refused` 是驱动实际拒绝，两个计数和处理原因分开归档。

在独立观察流或 trace 中记录机器人的真实位姿及新鲜观测时间。地图包的 SLAM trajectory 与 Runtime bounded-step journal 需互相核对；记录路径长度定义和采样频率，不能把目标直线距离或框架服务调用数称为真实行程。

异常、超时、用户取消、定位不可信、无传感器帧、零进展、安全拒绝均保留完整尝试。先取消当前操作，再有界等待与复读终态；停止未确认则记 `stopUnconfirmed`，不能报告成功。保存原始错误和必要日志后再修改代码；下一次使用新目录、绑定新源码 hash。不同源码版本/不同终止类型不得合并成简单成功率。

只有已知操作身份且状态仍匹配时才取消。启动响应丢失导致身份未知时，不盲目取消或续约；只读等待原 30 秒 provider lease 到期，再复核是否停止。未确认停止必须保留不确定结论。

## 首轮观测后的追加复核：扫描角度闭环

首轮运行揭示：发出合计一圈的目标，不代表控制器实际转满一圈；各子目标的到达容差会积累成缺口。该发现不改变首轮原始状态、指标、预算停止和地图校验结果；它也不能让首轮被解释为全覆盖。修改后的版本使用有符号 yaw 增量累计并跨子目标保留残差，需要新的冻结版本和新尝试验证。

对后续尝试增加独立核对：

1. 从实测轨迹按相邻 yaw 的最短角差展开，分别累计有符号进度与绝对角路程；只有前者能区分正向扫描和来回摆动。
2. 比较 `rotationTargetRad`、`measuredYawAdvanceRad` 与 `rotationResidualRad`，对照冻结版本的容差。未达到目标但因帧数、时间、安全或无进展而停止时，保留该真实状态，不改称整圈完成。
3. 比较首次/末次新鲜关键帧与 `acceptedFrameDelta`；转动的角度证明不等于深度质量或所有表面覆盖证明。
4. 独立记录每次扫描的状态。`sufficient_local_evidence` 可以提前结束有界扫描，但不能据此推断已观测整个场景。框架端口返回、预算内地图发布、实测扫描完成、全环境覆盖四项分开评定。

## 成功、部分结果与覆盖的定义

| 结果维度 | 验收要求 |
| --- | --- |
| 真实执行链 | 实际适配器为 Gazebo，传感器、在线地图与运动不是录制回放或 fixture |
| 初始扫描 | 有有界主动观察的状态/动作/新鲜观测记录；安全层拒绝时能解释而非无限转动 |
| 自主选点 | 后续目标由观测地图产生；无预定房间目标和离线真值输入 |
| 安全与预算 | 未知空间不作为无证据自由通道；累计 operation 行程不超过批准上限，取消/停止得到确认 |
| 地图发布 | 不可变地图包身份、标定、revision 一致，独立加载与导航状态检查通过 |
| 探索停止 | `complete`、`no_reachable_frontier`、`travel_budget`、`frame_budget`、`leg_timeout`、无进展或故障按原始码报告 |
| 任务完成 | 仅在约定动作与产物证据齐全时判定；地图保存完成和全环境探索完成是两个字段 |

`1 - unknownFraction` 的分母是当前测量栅格全部单元，会受地图扩张影响，包含墙内和场景外部，**不能称作整个环境覆盖率**。建议报告初始/终末 free、occupied、unknown 计数、已知面积、栅格几何变化与可达未完成 frontier 数。

有独立真值、冻结了场景/传感器/可达性定义和坐标配准时，才计算 `coverableMapped / coverable`。可参考 `scripts/score_live_map.py`，同时报告自由格误落真值障碍及障碍误落可达自由区；旧缓存必须校验与当前 SDF/机器人包一致。缺少这些条件时 `coverageGroundTruth` 记为 `not_measured`，不补估计数字。单次“无可达前沿”只能说明当前地图和约束下规划不到合适视点，不证明未知空间不存在。

## 归档与复核

每次尝试保留 `preregistration.json`、`initial-state.json`、`request.json`、`calls.jsonl`、`progress.jsonl`、`trajectory.jsonl`、`final-state.json`、`report.json`、地图包 manifest 和原始失败日志；存在才列出，缺失须明确。最终生成包含文件大小和 SHA-256 的 manifest。原始二进制图像/数据库可以仅本地保存，但报告应列路径、大小、hash 与导出方法，tracked 脱敏证据可独立复核。

只有实际运行完成后，另写结果报告，逐尝试列源码版本、执行链、初态、预算、终态、停止原因、地图身份、实际测量与限制。本协议中没有预填任何通过率、覆盖率、时长或真实扫描成功结论。

## 003 后追加：实际位置与动作分辨率

路线存在与下一步可执行分别判断。记录 `exploration.nextWaypoint`、`waypointDistanceM` 及内部 `minStepM`；仅选择从实际 `robot_xy` 通过同一通行 mask 的整段直线检查、且达到最小动作距离的候选。粗网格可以插值安全前缀，但不能以减少格心误差为由跳过碰撞/拐角检查。没有候选时为 `no_executable_waypoint` 的部分结果。该内部参数不是新增建图服务或 Harness CLI 业务参数，001–003不存在的原字段保持缺失；后续版本另开 map-cold episode 验证。
