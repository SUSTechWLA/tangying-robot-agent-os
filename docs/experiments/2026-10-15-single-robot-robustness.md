# 单机器人长程执行、故障恢复与研究证据（2026-10-15）

类型：工程个例、软件故障矩阵与实际 Gazebo 仿真，分别报告。基线为 `0d36aa127ad2132857775241478ac7689478e783`。预定条件见[实验协议](2026-10-15-single-robot-robustness-protocol.md)，代码和维护方式见[实施记录](../development/2026-10-15-single-robot-robustness.md)。

保留目录为 `docs/experiments/evidence/2026-10-15-single-robot-robustness/`，其中源码变化导致无效的运行不用于最终版本结论。真实任务另保留在 `artifacts/experiments/single-robot-robustness-20261015-gazebo-002/`；未随 Git 分发的大型原始资料必须连同地图和图像 manifest 备份。

## 软件实验设计

同一合同设备、Host 和 SQLite 顺序执行 32、48、64、96、128 个动作的五个目标，不在目标之间重置设备状态。每代最多 16 次能力调用，48/64 动作目标各在三个主路站点明确拒绝；已完成前缀保留，只为剩余目标选择许可绕路。完成条件检查累计访问次数，测试再独立核对每个目标的唯一性与顺序。128 动作样本不注入拒绝，实际使用当前 8 代/128 步上限，没有扩大生产权限。

决策器是脚本而非真实模型，工具是 in-process 合同 fixture，不含定位、地形、碰撞或真实运动。目标、完整授权、各代有限进度投影、全量检查点和逐调用 UTC 时间线均保留。最近完成投影最多 8 项，完整已确认历史用于额度检查；不能据此证明任意模型能依靠摘要完成任务。

另测 3 类预算停止、24 个确认动作后的第 25 写响应丢失、32 动作任务关闭重开 SQLite，以及同一合同设备/边缘执行库连续 12 个 HTTP 云边任务。HTTP 矩阵每任务丢弃进度消息并分别在最终证明提交前/后丢失响应，重建 Worker 后只交付证明；另有安全检查点续跑再遇未知写的分类案例。

软件数据中的 `physicalStarts` 指 fixture 接收到的写启动数，不是实际机器人运动次数。`durationScope` 区分初次 Host.Run 与包含检查点/重建的计时范围；并行软件验证和 Gazebo 会改变负载，这些耗时不能用于硬件、模型延迟或 MTBF 结论。

## 最终软件结果

有效运行为 [final-campaign-04](evidence/2026-10-15-single-robot-robustness/final-campaign-04/summary.json)：源码运行前后未变，27 个顶层测试、含子测试 58 项通过；23 条 task、980 条 step 记录。这里的 step 包括设备只读调用和拒绝，不等于 980 次运动。

| 连续目标动作数 | 确认写 | 明确拒绝 | 步骤尝试 | 计划代数 | 业务读 | Host.Run 秒 | 重建后写/规划 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 32 | 32 | 0 | 32 | 2 | 34 | 2.090 | 0 / 0 |
| 48 | 48 | 3 | 51 | 6 | 51 | 6.892 | 0 / 0 |
| 64 | 64 | 3 | 67 | 7 | 68 | 14.564 | 0 / 0 |
| 96 | 96 | 0 | 96 | 6 | 102 | 46.564 | 0 / 0 |
| 128 | 128 | 0 | 128 | 8 | 136 | 133.659 | 0 / 0 |

五个目标共 368 次确认写、6 次拒绝；最长有限进度投影 2,634 字节，最近完成项保持 8 个，完整历史没有删除。耗时随目标增长明显增加，当前全量事件序列化/证明补传存在扩展成本；单次、并行负载下的数据不足以拟合复杂度或声称实时性能。

其余记录中，12 个 HTTP 目标共丢弃 120 条 advisory 进度消息、12 次最终结果响应（提交前/后各 6 次），每任务仅启动一次，重建后云端均 SUCCEEDED；故障由 Cloud client 边界装饰器注入，底层鉴权/协调请求走真实 HTTP，不是公网/TCP 丢包实验。一个关闭重开 SQLite 的 32 动作目标未重放。三个预算案例保留各 16 次确认写后停止；长前缀未知效果案例保留 24 确认和第 25 次待定写，重启不继续；Worker 安全续跑后再遇未知写也保持 UNKNOWN_OUTCOME。业务完成、安全停止、未知待核对不混成“全部成功”的分母。

取证入口：[任务 CSV](evidence/2026-10-15-single-robot-robustness/final-campaign-04/tasks.csv)、[结构化 task/step JSONL](evidence/2026-10-15-single-robot-robustness/final-campaign-04/records.jsonl)、[原始 Go 事件](evidence/2026-10-15-single-robot-robustness/final-campaign-04/test-events.jsonl)、[运行/源码身份](evidence/2026-10-15-single-robot-robustness/final-campaign-04/run.json)、[派生指标](evidence/2026-10-15-single-robot-robustness/software-metrics.json)和[SHA manifest](evidence/2026-10-15-single-robot-robustness/final-campaign-04/manifest.json)。CSV 空值表示未测量，不作 0。源码集合摘要为 `218cce6f735d42945e1199ac505b496dc291d35c472a94983531b550416d063a`。

## 发现、修复与首个失败

| 缺陷或实验问题 | 原始证据 | 修正与结论边界 |
| --- | --- | --- |
| 安全检查点重建后的未知写没有经过原云端失败上报 | [Worker 首次失败](evidence/2026-10-15-single-robot-robustness/worker-attempt02.jsonl) | 统一初次/续跑故障路径；仍只有一次启动，云端状态为 UNKNOWN_OUTCOME |
| 子动作持久完成、父确认提交丢失后一直对账 | [原 Host 回归失败](evidence/2026-10-15-single-robot-robustness/host-baseline-failure.jsonl)与[混合回归身份](evidence/2026-10-15-single-robot-robustness/host-baseline-metadata.json) | 仅完整绑定的 VERIFIED + StepCompleted 可补认；这是旧 Host 源码与新增测试的回归复现，不是旧版本整仓试验 |
| 最后只读完成观测暂不可达即失败 | [修复前](evidence/2026-10-15-single-robot-robustness/tangying-completion-before-fix.jsonl) | 有界持久只读恢复，不重新规划、不重复动作；权限/取消/截止不重试 |
| 恢复事件的写分类可被损坏；完成重试耗尽提交丢失可能多读 | [独立审查后的反例](evidence/2026-10-15-single-robot-robustness/tangying-host-review-before.jsonl)与[当时源码摘要](evidence/2026-10-15-single-robot-robustness/tangying-host-review-before-metadata.json) | 分类重新核对目标目录；每次完成读预留持久尝试；确认提交中断后要求复核，不取得额外读额度 |
| “去厨房巡检”不能确定性解析而落入不必要的 INTENT 模型 | [修复前](evidence/2026-10-15-single-robot-robustness/inspection-parser-before.jsonl)、[修复后](evidence/2026-10-15-single-robot-robustness/inspection-parser-after.jsonl) | 窄支持巡检/巡查后缀并保留命名返回；附加测温、开烤箱或关电仍拒绝。真实首次提案未保留中间 Intent，不能断言该语法问题是其唯一根因 |
| 新 Host 复合子执行重新分派父恢复，误将当前 Pending 认作重启未决 | [旧 Runner 回归](evidence/2026-10-15-single-robot-robustness/tangying-composite-runner-before.jsonl)、[修后](evidence/2026-10-15-single-robot-robustness/tangying-composite-runner-after.jsonl) | 实际 Host01 在运动前安全失败；已进入 legacy 执行路径只查物理步骤账本，外部父恢复、历史 STARTED、审批和撤销仍保持拦截；没有 SkipRecovery 开关 |

补强后的 Host 定向普通/race 均通过 21 个顶层、44 个含子用例，原始输出分别为[普通](evidence/2026-10-15-single-robot-robustness/tangying-host-final2-tests.jsonl)和[竞态](evidence/2026-10-15-single-robot-robustness/tangying-host-final2-race.jsonl)。缺失/错配证据、后续失败、目录分类损坏、撤销、未知效果、读/计划/步骤预算及截止均独立测试。

Runner 集成修复后全包通过 75 个顶层、146 个含子用例，关键 race 通过 6 个顶层、16 个含子用例。原始输出为[全包](evidence/2026-10-15-single-robot-robustness/tangying-composite-runner-package.jsonl)和[race](evidence/2026-10-15-single-robot-robustness/tangying-composite-runner-race.jsonl)。这也说明只做 Host 合同 fixture 不足以替代 Local 真实装配验收。

`final-campaign` 与 `final-campaign-02` 的 Go 测试通过，但执行期间分别追加了独立审查补强与语法修复，采集器检测源码变化并将整轮标为未通过；保留原数据，排除最终版本统计。早期 `campaign-attempt01/02` 为 120 秒合同探索运行；一轮误以 120 秒测试进程执行完整 campaign 而超时，保留 `tangying-host-robustness-fixed.jsonl`，属于实验运行配置问题。最终纯软件逻辑链使用 600 秒合同期限并单独测试严格期限，不宣称 600 秒是现场性能目标。`final-campaign-03` 源码稳定且通过，但位于随后真实 Local 复合执行修复之前；最终版本重新运行为 04，旧运行单独保留。

## 实际 Gazebo 条件与执行

主机 Docker，真实 Gazebo、ROS 2/Nav2/RTAB-Map、RGB-D 与生产 Console；模型路由为已有配置的 deepseek-flash。没有实体机器人。场景为 home_furnished，只有一个机器人，任务间保留 Runtime 世界、地图、数据库；无关 MySQL/Redis 容器未改动。镜像、资源、二进制和实际源码分别记录身份，不能只用基线提交替代运行内容。

首次尝试复用旧地图时，资源版本不匹配被正式启动入口拒绝，零机器人动作；不改名地图绕过。第二 episode 冷启动执行正式标定与建图：任务 `task-160df157e20201affd7ee5d7`，493.295 秒、4 个能力调用，通过独立验收。mapping.build 报告累计轨迹 23.4715516 m、234 采集帧、220 注册，点云 72,329 点；这是该建图操作报告的实际累计轨迹，不能当作候选路径长度或 SLAM 绝对精度。

准备阶段数据来自 Provider 建图回执/地图会话，Agent observation index 为 0；这与后续巡检具有逐命令 RGB-D 的证据粒度不同。Runtime 自 14:48:48.106 UTC 启动后未复位或重启，期间仅在无活动任务的边界更新本轮拥有的 Agent 进程；各次二进制 SHA 和 911 项实际构建依赖/嵌入文件另存，不能把不同源码当作同一版本重复试验。

正式巡检原句为：

> 执行时先读取导航状态；从客厅出发，巡检卧室和卫生间，最后回到客厅；然后从客厅出发，去厨房巡检，再回到客厅；每次导航都确认抵达并取得新鲜RGB-D观测；最后读取并报告当前地图和导航定位状态。

第一次创建在 120 秒 HTTP 等待结束时失败，确认未创建任务、零运动、注入未消费；完整真实模型 attempt 保留。后续对同一原句使用独立 `--planning-timeout 600`，不延长机器人运动预算。已有覆盖守卫仍拒绝遗漏客厅的路线，未通过缩减原句让数据通过。

| 案例 | 路径与结果 | 时间与证据 |
| --- | --- | --- |
| 准备任务 | `/v1/tasks`，标定→建图→地图/定位；SUCCEEDED，独立验收通过 | Harness 总计 493.295 秒；Provider 内部 234 帧与地图产物 |
| inspection01 | 旧冻结计划通道的真实模型创建失败；没有 Task、没有运动 | 120 秒 HTTP 等待失败；9 轮规划 trace 原样保留 |
| inspection02 | 原句不变，`task-2e3c9aa3ef08e9fdefa41071`，SUCCEEDED，严格独立重验通过 | 15:10:30.775→15:17:59.505 UTC，执行 448.730 秒；Harness 528.093 秒；7 能力、18 子步骤、7 到位确认 |
| host-inspection01 | `/v1/objectives`，`task-goal-3f22762c2b7d41ec21d8f637931365a7`；RECOVERABLE_FAILURE | 17.179 秒，旧 Runner 递归恢复错误；0 物理子步骤、0 观测；原 Pending 检查点保留 |
| host-inspection02 | 同一精确巡检目标，新独立任务 `task-goal-e36db22fbb376a726a98a81ad3b45189`；SUCCEEDED，独立验证通过 | 15:25:58.599721→15:29:58.445870 UTC，执行 239.846 秒；Harness 250.158 秒；4 子能力、10 物理/验证子步骤、4 到位 |

inspection02 的两段精确路线为客厅→卧室→卫生间→客厅、客厅→厨房→客厅。7 到位确认包含两段起点确认，实际跨房移动为 5 次；20 个观测、36 张图像，独立重验覆盖 519 文件/433,521,317 字节。唯一注入为首个执行期 navigation.status 的 gRPC UNAVAILABLE，在转发前拒绝；Task/Ops/Recovery 调查 execution.read-history、确认只读、重试一次、验证并清除异常，原任务继续至完成，无人工 resume。最大到位观测残差 0.004604 m / 0.028505 rad，为相对目标的观测残差，不是 SLAM 绝对精度。

Host01 暴露了合同 fixture 没覆盖的真实 Local 装配问题；在只读 Runtime 命令账本和原始任务中确认没有该任务的物理派发后，保留其失败/待定事实，在受控更新 Agent 后创建新实验。没有重放旧目标、改写其检查点或复位世界。Host02 精确路线为客厅→卧室→卫生间→客厅，4 到位包含起点确认；1 代计划、4 步、8 次 Host 业务只读、3 个地图身份与定位状态完成谓词。独立验证父/子身份、grant、证明、命令后 RGB-D、到位与无重放，覆盖 287 文件/16,955,159 字节；到位残差最大 0.003145 m / 0.028505 rad，实际累计行驶里程未测量（null）。地图谓词仅证明地图一致性，路线完成依据完整子步骤和新鲜观测，不靠谓词常量单独宣布完成。

本轮没有抓放、实际飞行或步态实验。实际任务在不同二进制上发生，准备、失败请求、运动任务和环境拒绝分别登记，不按 3/5、3/6 或 100% 估计系统可靠率。

实际取证入口：[五项任务 CSV](evidence/2026-10-15-single-robot-robustness/gazebo/tasks.csv)、[逐步骤 CSV](evidence/2026-10-15-single-robot-robustness/gazebo/steps.csv)、[到位 CSV](evidence/2026-10-15-single-robot-robustness/gazebo/arrivals.csv)、[任务/版本摘要](evidence/2026-10-15-single-robot-robustness/gazebo/summary.json)、[唯一故障与恢复链](evidence/2026-10-15-single-robot-robustness/gazebo/fault-recovery-chain.json)、[原始包位置与摘要](evidence/2026-10-15-single-robot-robustness/gazebo/raw-evidence-index.json)、[选取证据 SHA](evidence/2026-10-15-single-robot-robustness/gazebo/manifest.json)。完整 terminal、原始模型 trace、父/子账本与独立验证报告已随本索引入库；大体积 RGB-D、地图、二进制和运行数据库保留本地，只有 Git 副本不能重做全部原始图像核验。

冻结依赖和全新 scratch 只读复验步骤见[Gazebo 证据说明](evidence/2026-10-15-single-robot-robustness/gazebo/README.md)。优化模式被验证器拒绝，原始包不覆盖；本轮证据的总体检索见[索引](evidence/2026-10-15-single-robot-robustness/evidence-index.json)与[总 SHA 清单](evidence/2026-10-15-single-robot-robustness/master-manifest.json)。交接时无活动任务，故障 arm 已撤除，8 次只读状态检查均就绪；[Console](http://127.0.0.1:8927/)保留供本机查看，不是持续运行保证。

CSV 采用显式未测量标签和空字段；JSON 中不同历史表的 `not_measured` 与报告的 null 都表示未取得实走里程，不能填成 0。旧任务 UTC 采集时间部分取原 run-config 的文件 mtime，来源已标记，不冒充当时未记录的精确开始事件；批准至完成的时间单独由原事件计算。

版本计数校正：五项 CSV 中有四个不同二进制 SHA（准备、037e580、760bffd、f8b150）。冻结 summary 的早期说明字符串写作“three implementation versions”；本报告以逐行 SHA 和构建记录为准，不按该说明字符串合并版本，也不改写已冻结输出。

## 最终验证

`make test-go` 的维护包清单全部通过；相关故障/交接回归的 race、独立 Runner 全包与 race 通过；Python 文档/书籍/两组 Harness 共 126 项通过。`go test ./tests/docs`、`make lint`、`make book-check`（31 节）、文档目录同步及 Local/Worker/Fleet Control Plane 编译通过。全仓 `go test ./...` 仍会收集 artifacts 中的局部 Go 源码归档，复现使用 Makefile 的维护包清单；本轮随 Git 入库的 Go 源码快照以 .txt 保存，不删除历史资料来制造通过。

## 复现与解释边界

```bash
.venv/bin/python scripts/run_edge_robustness_campaign.py --output artifacts/acceptance/edge-robustness-new-run
make test-go
go test -race ./internal/edgeagent ./edge/worker -run 'TestDelegated|TestObjective|TestCloudDelegates'
.venv/bin/pytest -q tests/install/test_edge_robustness_campaign.py tests/install/test_long_horizon_eval.py
```

实际 Gazebo 依赖原场景、地图/标定、模型配置和已有 episode。按[操作说明](long-horizon-harness.md)重新启动是新 episode，历史重放不增加新运动样本。目标委托 API 的样本与旧 `/v1/tasks` 冻结计划路径单独记录。地图身份完成谓词只证明地图一致性，返回房间、实际到位和新鲜 RGB-D 还须独立检查。

本轮属于受控工程验证，没有随机独立机器人抽样或策略对照，不能报泛化成功率、统计显著性、MTBF、实机认证、无人机/足式实测或无人值守生产可用性。未知动作安全停止与业务完成分别统计；raw 轨迹和失败是论文资料，不能只引用成功摘要。
