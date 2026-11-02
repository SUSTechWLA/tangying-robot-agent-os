# 自动接收会话契约

核对实现：`27cbbbaf96e2d76e1a17a1ea1befc8a8d8db14ce`，文档同步于 2026-11-02。

`core/agentsession` 只定义 `Binding`、`HistoryRecord` 与进程内 context 传递函数；持久化编排位于 [`internal/agentsession`](../../internal/agentsession/manager.go)，Local 入口装配位于 [`cmd/local-agent`](../../cmd/local-agent/main.go)。它让用户直接提交任务而无需管理 session，同时保留输入与任务的可回溯关系。

## 接收、执行与模型计算的三个生命周期

| 生命周期 | 事实来源 | 结束意味着什么 |
| --- | --- | --- |
| session / turn | 原始接收事件与 session 状态 | 当前输入已关联一个 Task，或接收/创建失败；不代表机器人完成 |
| Task / revision / command | 任务事务、批准、执行库、Runtime journal 与动作后证据 | 按各层状态机确认完成、失败或结果未知 |
| 模型请求 / KV cache | 本轮实际输入、响应、usage 与供应商缓存 | 一次推理结束或缓存失效；不会删除持久会话或改变执行权 |

控制台访问 token 又是独立的访问控制机制，不提供用户/租户身份。本机 manager 以已核验的 robot ID 与规范化 adapter 分区；同一本地部署内的使用者会共享该分区历史，不能将它直接当作多用户服务。

## Binding 的精确字段

| JSON 字段 | 含义 |
| --- | --- |
| `sessionId` | 系统分配的活动接收会话身份，轮换时更改 |
| `turnId` | 一次被接收输入的身份 |
| `taskId` | 接收时预分配的候选任务 ID，用于任务写入和中断后精确查账 |
| `robotId` | 来自已核验 Local Runtime 的绑定身份 |
| `adapter` | 经 `tasks.NormalizeAdapter` 规范化的执行 adapter |
| `sequence` | 当前 session 内的接收轮次，从 1 开始；失败轮次也计入 |
| `context` | 本轮捕获的近期历史 JSON 投影，参考数据，最多 6000 UTF-8 字节 |
| `contextSHA256` | `SHA256(context)` 的十六进制摘要 |

`Binding.Validate` 核对非空身份、长度、UTF-8、轮次和摘要。该校验只发现损坏和接线错误；SHA 不是签名，持有 Binding 不能取得批准或运动 lease。能力规划还须匹配目录 robot，legacy 解析根 Intent 和 Sequence 子项的显式机器人也须匹配。

`HistoryRecord` 独立携带 `turnId/taskId/request/requestSHA256`。manager 按保存的 source event 版本读取原文，检查事件类型、session/turn、Binding 和原文 SHA；失败就停止接收，不通过相似文本猜测历史。原文不能追加到本次 goal 中，否则会把过去要求混为当前授权。

## 持久协议与中断窗口

1. Local 启动前独占数据目录；同一 manager 的 gate 串行化接收/规划，等待可取消。
2. `Begin` 在调用规划器前，以 eventstore CAS 原子保存 pending、原始输入与 `SESSION_TURN_RECEIVED`。分区键为 `intake-session/SHA256(robotId + NUL + adapter)`。
3. 任务服务使用候选 task ID，随 Task/Revision 的创建事务写入 `TASK_CREATED.payload.session`。会话接收与 Task 创建是两个事务，不能宣称全局原子提交。
4. `Complete` 用有 5 秒期限的脱离请求取消上下文收尾，记录 `task_created` 或 `intake_failed`。已存 Task 不因会话收尾失败而被返回为可重试的创建失败；pending 留待下一次 Begin 核对。
5. 遇到遗留 pending，按确切 task ID 和 `TASK_CREATED` Binding 查账。存在且一致则补记关联；不存在则记录 `interrupted`；冲突则拒绝。`SESSION_TURN_RECOVERED` 不触发模型或机器人动作。

内部事件使用 turn 身份去重；外部 `POST /v1/tasks` 尚无请求幂等键。网络响应丢失后重发相同原句仍可能创建第二个 Task，不能用文本相同判断用户是否希望再次巡检。当前应先查询既有任务，未来需要认证主体、客户端 request ID 和原响应留存协议。

## 投影预算与模型接线

| 限制 | 当前固定值 | 影响 |
| --- | ---: | --- |
| 空闲轮换 / 轮次数轮换 | 30 分钟 / 32 轮 | 新 session 不自动召回旧 session；不取消运行中的 Task |
| 近期记录 | 最多 8 轮 | 任务状态从库现读；已省略历史明确计数 |
| 单条请求/错误预览 | 最多 512 UTF-8 字节 | 原始请求另保存在源事件 |
| 近期摘要投影 | 最多 6000 UTF-8 字节 | 过长逐条移除最旧摘要，不把半截 JSON 传给模型 |
| 一条原始输入 | 最多 8000 UTF-8 字节 | 非空、合法 UTF-8；任务入口先以澄清错误拒绝非法输入 |

至多八条近期原文作为 `planning:session-input:<turnId>` reference，摘要作为 `planning:session` reference 进入 GOAL 的 `ProjectManaged`。长记录可外置到本轮捕获的归档，再经 `context_read` 分页读取；不能传文件路径查询另一机器人或任意任务库。预审批规划 scope 仍只有实际 robot，候选 task ID 是 reference，不能伪装成已有任务批准。

以上是字节与记录数预算，不是精确 token 预算；既有整请求/热上下文上限仍生效。原始审计事件继续增长，需另外管理磁盘保留。会话存在时 recipe 只作 hint，避免同一句目标忽略不同历史而跳过模型；因此不能保证本轮会话升级一定节省 token，实际用量由模型计量记录。

没有 GOAL 模型时，legacy grammar 继续只解释当前原句。纯“继续／重试／再来一次”等在新建任务入口要求澄清；操作员使用确切 Task 的 resume/reconcile，不依靠历史重放 UNKNOWN 动作。Worker 和 `/v1/objectives` 继续使用独立的[执行上下文合同](../contextcontract/README.md)与[目标包络](../../docs/architecture/hierarchical-edge-objectives.md)。

## 阅读与验证入口

- [完整消息与上下文数据流](../../docs/architecture/edge-agent-communication-and-context.md)、[自动会话运维](../../docs/operations/automatic-edge-sessions.md)。
- [设计来源与实施记录](../../docs/development/2026-11-01-automatic-edge-sessions.md)、[实际软件验证及压缩日志](../../docs/experiments/2026-11-01-automatic-edge-sessions.md)。
- [Binding 测试](context_test.go)、[manager 重开/并发/来源校验](../../internal/agentsession/manager_test.go)、[任务写入与授权边界](../../tasks/creation_session_test.go)、[全文外置与回查](../../internal/capabilityagent/session_context_test.go)、[HTTP/SQLite 重启接线](../../cmd/local-agent/session_test.go)。

这些测试使用模型/机器人 fixture，不能替代真实模型缓存性能、Gazebo 或实机运动验收。
