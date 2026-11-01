# 2026-11-01 自动边缘会话：软件验证记录

基线为 `47f300fb67ca7e83c145f5c3cc00d984bccc1c82`，工作分支 `codex/edge-long-horizon-scalability`。测试针对本次未提交工作树；最终源码及回归结果随本报告同一提交保存。设计、来源与范围见[自动会话实现](../development/2026-11-01-automatic-edge-sessions.md)，教学解释见[教材补充](../../book/updates/2026-11-01-automatic-edge-sessions.md)。

本次验证自动接收会话、任务关联、模型上下文和重启恢复。使用实际 Go 服务、SQLite、HTTP 序列化与模型请求构建；模型端为本地 fixture，机器人端为测试 Provider。未调用公网模型、未重启现有 Gazebo、未下发实机或仿真运动，不产生新的导航成功率、token 节省或物理安全结论。

## 1. 验证目标与实际过程

| 场景 | 输入或故障 | 核对的结果 |
| --- | --- | --- |
| 无需选择 session | 向真实 console handler 提交两次明确标定查询，中间关闭并重开 SQLite | 相同 session、递增 turn、新 task；第二次模型 HTTP 请求包含第一轮请求与任务关联 |
| 原句与权限 | 本轮新任务带上旧历史 | request 不变，历史是独立 reference，不自动批准，Provider 副作用调用为零 |
| 创建中断 | 接收持久化后无 task；或 task 已存而会话未收尾 | 前者记 interrupted，后者按预分配 task ID 恢复关联；均不重新执行任务 |
| 故障与并发 | 提交失败、取消等待、取消后收尾、重复 finalizer、并发 Begin | 未取得接收权不规划；清理有界；不同 turn 不覆盖；非持有者不能释放别人的锁 |
| 作用域 | 不同机器人、adapter、损坏摘要、错误历史来源 | 隔离会话，拒绝错误绑定和来源，不将外域历史送给模型 |
| 长历史 | 多轮输入、中文长原文、30 分钟空闲、32 轮轮换 | 最多八条近期记录、6000 字节投影；原句另存，受预算外置并经 context_read 精确分页读取 |
| 任务经验 | 同一句目标有自动会话参考 | recipe 可给提示，不能仅按目标字符串跳过模型 |
| 含糊续跑 | 继续、重试等 | 不新建 turn/task，返回澄清，使用确切任务的恢复入口 |
| 目录所有权 | 同目录及符号链接别名、符号链接锁文件 | 第二个本地所有者被拒；释放后可重开；不改写符号链接目标 |

HTTP 集成测试为 `cmd/local-agent/session_test.go`。它验证实际序列化与重启数据流，但没有启动完整部署的监听进程，也没有模拟断电或操作系统强杀。崩溃窗口通过保留 pending、关闭/重开数据库及注入提交错误构造。

## 2. 审查中发现并修复的问题

1. **用户输入错误被当成服务器错误。** 超长或非法 UTF-8 输入的普通 error 会被 HTTP 映射为 500。现在于启用会话的入口先校验，返回可澄清错误；存储失败仍按内部故障处理，无会话路径维持兼容。边界测试验证 8000 字节原文保留、非法输入不进入 Begin。
2. **旧规则解析跨机器人草案。** 审查用“让二号机器人把红色杯子放进右侧收纳盒”复现：本机 session 为 robot-1，解析出的 task 为 robot-2，旧入口仍保存 READY 草案。执行层随后会拒绝，未观察到跨机动作；但会污染本地会话并创建不能执行的任务。修复要求显式机器人身份在规划和保存前匹配本机会话，覆盖 Sequence 子项；未指定机器人保留现有本机语义。
3. **文档生成顺序。** 第一轮 Python 文档检查为 22 passed、1 failed，因为导航已链接本报告而报告尚未写入。报告与目录补齐后重测；这不是机器人功能故障。

## 3. 测试命令与证据

主要回归：

```bash
go test -race -json ./core/agentsession ./internal/agentsession ./tasks \
  ./internal/capabilityagent ./internal/controllease ./cmd/local-agent \
  ./console ./middleware/sqlite ./internal/edgeagent ./edge/worker -count=1
```

首轮完整回归的 10 个包全部通过：539 个顶层测试、387 个子测试记录，无失败或跳过；父子测试不能相加解释为独立任务样本。`internal/edgeagent` 为 319.425 秒，包含原有最长 128 步的长任务软件 campaign；该 campaign 本身未开启本地接收会话，作用是检查共享任务与 Host/Worker 兼容性，不是新会话的长程机器人运动验收。

输入/身份修复后，`tasks`、`internal/agentsession`、`cmd/local-agent`、`console` 四包再次通过 race 回归：340 个顶层测试、235 个子测试记录，无失败或跳过。最终命令、逐包统计和源码摘要见[验证清单](2026-11-01-automatic-edge-sessions/verification.json)，文档检查见[检查结果](2026-11-01-automatic-edge-sessions/checks-final.json)。日志区分首次整组和后续定向，重复执行不能累加为更多独立实验。

原始完整日志保存在本机 `artifacts/acceptance/automatic-sessions-20261101-001/verification/`；本报告旁的 `2026-11-01-automatic-edge-sessions/` 保存压缩副本、命令时间、退出码、逐包统计、校验和与源文件摘要。解压日志可逐条读取 Go test JSON，复核 package/Test/Action/Elapsed；测试耗时不是机器人响应延迟。

## 4. 已知边界与下一轮实验

- 本地单操作员范围，按 robot/adapter 分区；没有用户、租户或跨渠道权限模型。
- 恢复 pending 不等于外部 HTTP 重试去重。响应丢失后再次 POST 相同全文仍可能创建新任务；先查看已有任务，后续用明确 request ID 实现幂等。
- 近期召回有界；旧 session 和更久历史留在审计事件中，但不会自动语义召回。模型历史有界不代表磁盘事件无限增长无成本。
- 历史不继承批准，不能覆盖 UNKNOWN 命令或当前传感器事实；暂停、继续、对账仍使用现有任务协议。离线 legacy grammar 只解析当前原句，会话记账不赋予它跨轮指代理解能力。
- 本轮未测真实模型缓存命中、费用、跨渠道会话识别准确率、现场断网或硬件停机恢复。后续对照需固定任务与故障注入，分别测 token、澄清率、延迟、接收恢复和真实执行成功率。

软件测试支持“系统自动管理会话、关联可追溯、恢复不自动重放”的当前判断，不支持“已完整实现 DeepSeek Super-agent”或“机器人已可无人值守上线”的结论。
