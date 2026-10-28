# 2026-10-28 边缘任务复测与故障修复

本轮基线：`a98c0681d524a6db7036a7a5bbe121df6f8e239d`。接续[持续运行与成本升级](../2026-10-28-edge-scalability/README.md)，用户要求实际执行多组任务、检验功能、修复问题并继续优化。范围在执行前登记；下面分别保留规划、通信、设备合同与物理实验结果。实现变化见[故障修复记录](../../development/2026-10-28-task-validation-fixes.md)。

## 预定任务与证据边界

| 组别 | 输入与异常 | 核验内容 | 证据层级 |
| --- | --- | --- | --- |
| 重复规划与模型成本 | 相同目标，off/hint/validated；状态变化、SQLite 重开、取消、计量失败 | 模型请求与真实返回 usage、当前查询、规划 trace；复用不绕过预算和授权 | 真实 HTTP/SQLite，受控模型与机器人 fixture |
| 机器人间与云边通信 | 丢 ACK、重复/冲突消息、peer 失效恢复、身份变更、重启 | 收件与当前视图、持久序号、unknown 不重放、完成回执 | HTTP/SQLite 与云边软件 fixture |
| 异构设备 Harness | ROS 2 profile、命令/lease/停止/回执边界 | 异构能力与身份隔离、未知结果和传感器证据 | Python 合同回归 |
| Gazebo 三机器人任务 | 地面车、足式代理、无人机代理执行完整中文路线，注入 stale-peer | 实际导航与独立到达测量、任务回溯、故障处理；按已有判据执行 | Gazebo 经典刚体控制、ROS 2、gRPC、Local Agent |

Gazebo 沿用原实验的真值定位和集中式 hub，不能推导在线 SLAM、无线分布式自治、厂商飞控/步态或实体机器人已验收。软件模型 fixture 不能用作商业 token 费用测量。未知结果和预算场景的预期停止不算目标完成。

## 执行约束

- 每次使用新输出目录，保留原始失败和重测，不重写旧实验。
- 不放宽到达、停止、身份、授权和预算判据；不把缩短任务后的通过替代原任务结果。
- 原始传感器/任务账本留在本地产物，公开记录排除会话凭据；保存源码、二进制、命令和摘要以关联结果。
- 保留本轮之前两份营销文稿的未提交修改。临时仿真进程在结束后退出，原容器恢复停止状态。

## 实际结果概览

| 分组 | 实际执行 | 结果与不能推出的结论 |
| --- | --- | --- |
| [规划与成本](cost/README.md) | 10 个连续规划情景，另测 Recovery/System 公用 Loop 的账本故障 | 修复后通过；未批准计划，物理写入 0；模型为 HTTP fixture，不能计为商业费用实测 |
| [通信与云边恢复](peer/README.md) | 新增故障场景 ×10、通信 4 包 race、云边 8 类场景 ×10 | 最终三个阶段 185 次顶层测试执行通过；包含重复和重叠用例，不是 185 个独立机器人任务 |
| [异构 Harness](harness/summary.json) | Runtime、ROS 2 Profile、移动合同、journal 与 Go→Python gRPC 合同 | 111 passed；真实 gRPC + 夹具传感器/执行器，不是厂商控制器验收 |
| [Gazebo 路线](gazebo/summaries/evidence/run-002/analysis/metrics.json) | 三机器人各一条十目标完整中文任务，注入过期 peer | 3/3 SUCCEEDED，30 次导航 + 30 次独立到达核验，180.651 墙钟秒；严格碰撞证据覆盖仍未通过 |
| [独立物理停止](gazebo/summaries/evidence/run-003-stops/stops-001/summary.json) | 每台机器人断流 watchdog 和显式停止各一次 | 6/6 passed；UAV 只验证悬停，未验证降落/关推力 |
| [最终 Go 集成](verification/go-final/result.json) | actionloop、planner、recovery、Host、通信、SQLite、worker 与 4 个入口，共 11 包 race | 446 passed / 1 skipped，17.558 墙钟秒，执行前后源码一致 |
| [最终 Python 集成](verification/python-final/output.txt) | hub、停止、ROS controller、物理分析器、公开证据导出 | 113 passed，执行前后源码一致 |

Go 跳过项是 `TestRun002ActualRecoveryContextReplay`：未设置历史外部文件参数 `TANGYING_RUN002_CONTEXT_FIXTURE`。四个 `TestSingleRobot.*Campaign` 长链测试按命令显式排除，使用[同日上一轮长链证据](../2026-10-28-edge-scalability/README.md)作为该部分参考，不冒称本轮重跑。新的 `TestCostCampaignSequentialHTTPAndSQLite` 已包含在最终回归中。不同组的测试有重叠，不将这些数字相加充当成功样本量。

[最终资料检查](checks-final/summary.json)全部通过：文档 Python 23 项、Go 文档检查、书稿 31 节、目录同步、Ruff/gofmt 和两个公开归档校验。首次资料检查曾把英文说明中的普通短语误识别为 Make target；仅调整说明措辞，原失败留在 `checks-initial/`（原始 `python-docs.txt` 逐字节 gzip 保存，摘要中的 SHA256 对应解压内容），未修改测试规则或实验数值。全目录文件指纹见 `archive-sha256.json`。

## 规划复用、取消与 token 记录

每个情景使用同一生产 Planner/LLMDecider、回环 HTTP 服务及可重开的 SQLite。`off` 和 `hint` 两种策略的重复目标各收到 2 次模型 HTTP 请求；`validated` 首次也为 2 次，数据库重开后的相同目标为 **0 次模型请求、1 次新鲜 Provider 读取**。地图条件变化时，旧经验被判为 `RECIPE_REVALIDATION_MISS`，使用刷新后的读取结果重新请求模型一次。新鲜查询、授权与实际执行验证仍不可省略。

发现并修复两个错误边界：取消后普通规划仍制造额外 ModelCall 尝试；usage 结果无法落盘被误当成模型语法错误继续请求。十情景前后真实 HTTP 总数均为 **13**，客户端 ModelCall 轨迹由 **18 降至 13**，不能说减少了 5 次付费请求。单独的 Loop 账本故障由保留两轮拒绝且返回 nil，改为保留首轮、返回 `ErrModelUsageLog`，工具执行为 0。

最终 ledger 为 13 个 start、12 个 result、1 个刻意制造的未完成记录。已返回的 fixture usage 在规划 trace 中保留；独立 ledger 缺结果仍标未知，不能填 0。原始 [before](cost/before/summary.json)、[after](cost/after/summary.json)、完整 cases、usage JSONL 和命令同时保留。该实验没有真实大模型调用，不能证明语义泛化、KV cache 命中率、收费额度或真实响应时延收益。

## 通信故障与恢复

三 peer、每 peer 两条消息、`Dispatch(limit=1)`、其中两个 peer 暂时离线：旧实现连续六次都选中第一个离线 peer；新实现尝试分布为 `[2,2,2]`，健康 peer 的两条及时交付。两端数据库重开并恢复网络后，其余四条完成交付，保持原 ID、FIFO 和收件去重。**当前 CLI 使用 limit=1000**，故障针对公开 Service 的较小批次调用，不应推断默认 CLI 已发生该问题。

重复字段、大小写字段别名、超长空白、隐藏第二个 JSON、未知字段和错误类型 ACK 均不得退役发送账本；发送端与接收端重启后，以同一信封重试，接收端返回 duplicate 后再推进下一条。调度游标是进程内提示，重启重新开始轮转；原有不支持 PendingFor 的 Journal 保持旧契约。网络仍为至少一次交付，不能声称 exactly-once。

云边 8 类故障各重复 10 次，覆盖竞争 claim、跨域/旧 Basis 拒绝、物理动作结果丢失、完成回执丢失、双端重启、旧任务版本、65 次控制面失败后的收敛。`UNKNOWN_OUTCOME` 表示必须对账且不重放动作，是正确停留状态，**不是机器人完成任务**。见 [peer 报告](peer/README.md)的逐场景断言及所有失败原件。

## Gazebo 完整任务与停止

三台分别使用 UGV、legged、UAV Profile，输入同一句任务：

> 从客厅出发，去厨房确认一下环境，然后去卧室，再回到厨房，再去卧室，再回到厨房，再回到客厅，再去厨房，再去卧室，最后回到客厅。

路线包括起点，共 10 个导航目标与 10 次独立确认/机器人。标签 seed=3 只决定 stale-peer 注入时机，不代表随机地图。保留原任务、速度/位置/姿态判据；中途没有重启世界、位姿重置或缩短路线。实际链路为 LocalAgent HTTP → gRPC Runtime → ROS 2 → 经典力/推力控制 → Gazebo 刚体 → 新测量回执。

| 机器人 | 完成状态 | 测得路径 m | 最大到达位置误差 m | 最大到达回执速度 m/s |
| --- | --- | ---: | ---: | ---: |
| ground-a / UGV 代理 | SUCCEEDED | 35.7830 | 0.09285 | 0.06871 |
| ground-b / 足式类别代理 | SUCCEEDED | 35.8399 | 0.09249 | 0.06894 |
| uav-a / UAV 代理 | SUCCEEDED | 34.3819 | 0.03114 | 0.06960 |

970 次状态采样、96.9 仿真秒；全部 60 条导航/确认回执关联到物理帧。保守包围球最小采样间隙 **0.0959004m**。地面接触通道计数递增，未观察到意外接触；UAV 缺接触通道覆盖证明，因此 `safetyMetricsPassed=false / not_fully_verified`。采样间隙也不能证明连续时刻无碰撞。

失败与复测必须一起读取：

| 轮次 | 观察与处置 |
| --- | --- |
| 初始编排 | 编排器错读 snapshot 的 `robots` 键，实际为 `states`，未派发自然语言任务。保留失败编排日志，修正后使用新目录 |
| run-002/campaign-001 | 上述三条完整任务全部成功；严格安全覆盖未完成 |
| run-002/stops-001 | 控制器退出时已锁存停止，随后停止脚本误认状态推进就等于运动就绪；三次 goal 均被 HTTP 400 拒绝，另三项跳过。原汇总 0/6 与 `trial_failure` 原样保留，不当作六次物理制动失败 |
| run-003-stops/stops-001 | 独立新世界先检查停止锁存与测量推进，再按原判据执行；6/6 通过，不清锁，不在试验中重置位姿 |

停止专项是 hub 直接触发，不能写成自然语言 Agent 自动完成全部恢复：

| 机器人 | 触发 | 最大位移 m | 含持续停稳窗口的仿真时间 s |
| --- | --- | ---: | ---: |
| ground-a | 控制器命令断流 | 0.256076 | 1.210 |
| ground-a | 显式停止 | 0.115872 | 0.906 |
| ground-b | 控制器命令断流 | 0.207591 | 1.190 |
| ground-b | 显式停止 | 0.057860 | 0.810 |
| uav-a | 控制器命令断流 | 0.350692 | 1.790 |
| uav-a | 显式停止 / hover | 0.205067 | 1.596 |

停止判据：触发前达到至少 0.18m/s，最终线速度≤0.03m/s、角速度≤0.05rad/s 并持续至少 0.4 仿真秒；地面位移上限 0.65m、UAV 0.9m，时间上限均为 4 仿真秒。此时间包含停稳确认窗口，不解释为纯控制器延迟。

## 源码版本、原始材料与复现

物理主任务使用冻结的 `a98c0681d`，LocalAgent Linux arm64 二进制 SHA256 为 `efae33716278d6385ed5961aeb095d3528893ea895e13a626c555a447eaf5186`；停止复测为同一基线加本轮 Python 就绪/诊断补丁，见 [patch-manifest](gazebo/patch-manifest.json)。**本轮 Go 模型/通信修复通过软件集成验证，未进入这次物理主任务二进制**。物理任务采用 `/v1/tasks` 的固定句式 deterministic 入口，未物理验证新 GOAL、recipe、usage 或分层 Host。不同组不能拼成一条不存在的全栈实验。

| 目录 | 资料与用途 |
| --- | --- |
| [cost](cost/README.md) | 前后对照、完整规划轨迹、usage、源码副本 `.go.txt`、命令/哈希；SQLite 原件路径与摘要在 MANIFEST 中 |
| [peer](peer/README.md) | 原始失败、最终重复试验、JSON 测试事件、源码身份清单、精确命令和补丁的完整压缩包 |
| [harness](harness/summary.json) | 跨语言合同的命令、源码摘要、pytest/JUnit 原始输出 |
| [gazebo](gazebo/README.md) | 任务/传感器/控制器/SQLite 原始公开包、各轮摘要、runner、源码和环境恢复记录 |
| [verification](verification/go-final/metadata.json) | 最终聚焦 Go/Python 命令、未过滤输出、执行前后源码身份；通用记录器为 `verification/run.py` |

在仓库根运行下列只读校验可验证公开物理归档：

```sh
python3 docs/experiments/2026-10-28-edge-task-validation/gazebo/public/verify.py
```

SHA256 仅用来核对文件身份，不能认证实验真实性。文中命令与本机绝对路径保留为执行记录，换机复现需调整目录和构建依赖。所有临时仿真进程退出，原容器入口字节 SHA256 与 0755 权限原样恢复，容器返回停止状态，见 [restoration](gazebo/restoration.json)。

## 对后续论文和实机的含义

本轮支持“给定场景下任务链可完成、停止可测、故障可回溯、异常 ACK 不丢消息、模型计量失败不继续执行”的工程结论。单个场景的三台机器人、重复同一软件 fixture 和固定句式任务不足以估计泛化成功率。

下一步应单独登记真实模型及缓存/费用对照、GOAL→物理闭环、UAV 接触通道健康证明、独立场景/在线 SLAM、真实网络分区与厂商驱动试验。保持当前判据，不把未知证据补成成功，也不把已有地图/集中式 hub 的结果泛化为陌生环境分布式群体自主探索已完成。
