# 附录 C · 案例与实验的来源索引

## C.1 使用实验的规则

本附录选择与教学主线相关的原记录，不重新测量历史数据。软件契约测试验证规定输入/分支；模型测量验证指定决策集；仿真检验指定物理场景；真实设备记录只覆盖相应本体和现场。负责人放行是有范围的决定，不是另一种自动统计证据等级。

引用时写明日期、代码、模型、任务/数据身份、指标分母和限制。原制品无法取得时，只能检查公开报告及代码；不能声称独立重验完整像素或数据库。更细的旧统计、原表与失败记录可经 1.2.0 档案恢复。

## C.2 长程单自然语言任务，2026-10-10

来源：[完整报告](../../docs/experiments/2026-10-10-long-horizon-protocol.md)、[验收摘要](../../docs/experiments/2026-10-10-long-horizon-acceptance.json)、[Harness](../../docs/experiments/long-horizon-harness.md)。教学拆解见第 19 章。

| 身份/指标 | 原记录 |
| --- | --- |
| 任务/版本 | task-d65a1e20a528b66a4dc8e4bf，冻结 revision 1 |
| 环境/长度 | Gazebo 家庭场景；1159.459 秒，7 阶段、23 子步骤、6 抵达 |
| 地图 | 235 帧，227 配准，2 回环，72972 点，23.458 米 |
| 恢复 | 一次派发前只读 UNAVAILABLE；原失败→Ops→Recovery→独立复验→原命令一次重读 |
| 上下文 | 11 managed 记录，7 实际模型请求，4 压缩；冷分页实际 0 次 |
| 事件/制品 | 41 个 v1 事件，本次无身份冲突；1171 个原文件及 SHA 引用 |
| 干预/末尾 | 操作员暂停；原最后导航 ready=false；后采 ready=true 独立保存 |

这是单机器人仿真及真实规划模型调用，当前 Ops/Recovery 主要为确定性角色。已保留前轮失败；单次最终成功不能用于统计整个系统可靠性。分页覆盖来自另行软件测试，不能由“有归档”推出本次模型实际分页。

## C.3 统一能力目标闭环，2026-09-27

来源：[闭环报告](../../docs/experiments/2026-09-27-capability-goal-closure.md)、[验收摘要](../../docs/experiments/2026-09-27-capability-goal-acceptance.json)。Local + Gazebo，实际 GOAL 模型规划；task-77d1b15939d54145bedd36ef，约 11 分钟 34 秒。

主链 calibration.run → mapping.build(survey) → semantic.resolve(厨房) → robot.task；4 个 CAPABILITY_VERIFIED，到达判定 CONFIRMED。原 SLAM 记录为 230 帧、220 配准、2 回环、72382 点、23.492 米。标定、地图 revision 和观测身份以原摘要为准。

本例用于学习能力合同、制品读回与导航后置条件；它没有完成第 19 章的同杯抓放任务，也不能替代云边协议或实体测量。

## C.4 证据门禁与上下文研究，2026-09-21

| 原资料 | 可用于学习 | 解释限制 |
| --- | --- | --- |
| [Grounded verification](../../docs/experiments/2026-09-21-grounded-verification.md) | 回执、证据基线与独立判据的比较设计 | 指定场景和模型条件，不是所有验证器证明 |
| [Agent context evaluation](../../docs/experiments/2026-09-21-agent-context-evaluation.md) | 信息完整性、表示、阶段和统计单位 | 不把 JSON 与缺信息摘要的差异全归因于格式 |

上下文第一轮有 480 个标注案例，留出 test 240；另有 160 条行为轨迹。旧摘要和完整事实表达在该行为沙箱中都最终完成，差别主要在推进轮数等效率指标。不能写成“完整事实提高最终成功率”。第二轮按环节比较表达，不能从某一环节优势推出所有阶段都应采用同一格式。

这些研究不等同于当前 32768 字节 managed view 或实际冷分页的运行测量。第 12、18 章分别讨论评测方法和当前机制。

## C.5 多机、异构与部署证据

| 来源 | 覆盖对象 | 不支持的更强结论 |
| --- | --- | --- |
| [双机交接流程](../../docs/operations/robocasa-handoff.md) | 规定物料/交接区、共享资源与按序双机流程 | 通用异构最优匹配、任意并行任务 |
| `tests/contract/test_heterogeneous_runtime_boundary.py` | 两类 Profile、Python gRPC 与 Go 客户端的实际协议夹具 | 两类实机已合作抓放 |
| [云边验收](../../docs/production/cloud-edge-upgrade-acceptance.md) | 规定协议、存储、容器和故障分支 | 全局事务、生产 HA 或无限离线执行 |
| [现场准备](../../docs/production/field-readiness-2026-09-26.md) | 设备接入与待验证项 | 当前全部实体现场已经放行 |

## C.6 新实验登记模板

记录：问题/假设；代码与书籍版次；设备/场景/模型/种子；配置/数据 SHA；统计单位与分母；独立判据；故障点；完整原输出；介入和排除；结果与不确定性；覆盖范围；原制品及摘要索引。新记录另存，并和旧实验比较，不覆盖旧报告。
