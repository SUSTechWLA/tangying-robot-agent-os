# 第 9 章 单机自治、持久状态与开发入口

**学习目标**：快速启动理解系统；分清持久和实时状态；分析单机重启与控制端切换。

**先修知识**：第2、3、8章；文件与数据库操作。

**关键术语**：Local自治、SQLite、liveness、readiness、journal、回放

## 9.1 单机是完整闭环的最小载体

Local Agent只控制一台Runtime，用SQLite保存目标、版本、事件、决策与执行信息，在同进程装配Task/Ops/Recovery。简化机群协调不应简化未知结果、审批或证据。

```text
用户目标 → Task/Revision/批准 → 能力执行器 → Runtime
              │ SQLite                │ 观测与journal
              └─ EventBus → Ops/Recovery → 调查/复验
```

初学者先从这一形态观察系统全貌，再研究Fleet。没有云中心不等于所有状态都可放内存。

## 9.2 哪些内容必须持久化

目标与批准内容、命令身份/状态、unknown/冲突、恢复预算、完整因果链和实际模型请求，是跨重启判断需要的事实。临时UI缓存或一次模型摘要不具备同等权威。

SQLite提供数据库事务/隔离语义；它们不延伸到外部执行器。WAL下的读快照与写串行仍应按具体连接/事务理解，而非“单机无并发问题”。[SQLite隔离说明](https://www.sqlite.org/isolation.html)

## 9.3 进入运行中的系统

```bash
make setup
make home-furnished
curl --fail --silent http://127.0.0.1:8897/healthz
curl --fail --silent http://127.0.0.1:8897/v1/runtime
curl --fail --silent http://127.0.0.1:8897/v1/readiness
```

这是开发仿真入口，需要对应依赖及Docker。已有服务承载任务时先核对并复用，不重复启动。healthz是进程存活；Runtime ready与Console readiness涉及不同范围；批准和现场使能又是另外的条件。

先查看设备目录、地图/标定与历史，再提交适用目标，在工作台核对草案和审批。敏感模型配置保存在私有文件，不能通过日志展示key。

## 9.4 重启、备份与回放

进程退出时数据库记录可能完整，物理动作却仍未知；Runtime journal和新鲜观测须共同对账。历史SUCCEEDED不能自动证明当前仍满足条件。

活跃数据库应使用一致性备份接口，保留地图/配置绑定和制品哈希。普通复制正在写入的文件可能不是完整快照。恢复后的来源仍需重新采集，不能将旧帧标为新鲜。

## 9.5 切换到机群

停止旧派发→核对命令和持物→建立Fleet设备身份/资源→启用worker，是受控切换流程。Local和worker不能同时控制同一Runtime。接口共享不提供自动双主接管或无限离线授权。

## 9.6 开发调试的入口选择

组合根用于看实际装配；Task API用于看冻结定义；事件回放用于因果；Runtime journal用于实际命令；观测用于当前物理结果。应先定位哪类状态矛盾，再选择读取工具，避免用删数据或重启替代诊断。

### 开发推演：从只读页面追一个 taskId

打开现有任务回放，先记录目标、active revision 与终态；选择一个步骤，查 command、Runtime journal 和验证来源；再查 Agent 事件与恢复计划；最后对齐模型 trace 和冷制品。每一步记录“来自哪个权威对象”。

若页面说完成而 journal 是 unknown，先核对是否同一个 revision/command，而不是立即重启。若 healthz 正常但 readiness=false，读取具体阻塞原因。该阅读练习不需要新增运动；新仿真任务另按草案、批准和独立 episode 执行。

## 9.7 持久化对象与回放的组织

单机执行至少涉及任务内容、不可变版本、事件、具体命令和外部制品。它们不能都化成一列 task.status。

| 对象 | 更新性质 | 恢复关注 |
| --- | --- | --- |
| 任务聚合 | 当前状态与 active revision | 预期版本/批准/未知 |
| 不可变版本 | 执行定义的历史 | 原目标/目录/计划，禁止倒写 |
| 事件账本 | 已记录事实序列 | 原事件身份、原因和失败 |
| journal/checkpoint | 具体执行阶段和结果 | 是否派发、是否只能重传完成 |
| 地图/图像/trace | 外部制品与索引 | 文件存在、SHA、身份与访问范围 |

回放从权威记录构造读视图，不等于重执行。外部文件与数据库的一致快照需保持索引和内容对应；备份完成后应在隔离只读环境核对，不能把恢复演练连接到旧设备后自动派发。

Local 简化了机群协调和跨主机运维，但仍保留批准、日志、证据和未知处理。它是完整闭环的最小部署方式，适合先验证任务与设备责任，再引入分布式机制。

## 9.8 小结与阅读

源码见[Local入口](../../cmd/local-agent/main.go)、[SQLite适配器](../../middleware/sqlite/)与[单机恢复](../../cmd/local-agent/read_recovery.go)。快速实践在附录F。

## 9.9 分层习题

基础题 B：为什么healthz正常但readiness为false可能是正确状态？

工程题 E：设计包含任务库、命令journal、地图和配置身份的一致备份清单。

研究题 R：比较单机与机群控制端切换的故障假设，设计不会重复派发的验证方法。

作答应写明假设、判据与证据。评价要点见[附录 I](../appendix/I-exercise-guide.md)，实践路线见[附录 F](../appendix/F-learning-labs.md)。
