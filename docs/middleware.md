# Middleware 端口与适配器指南

Middleware 为 Local Agent 提供可替换的基础设施能力。核心规则是：业务包依赖稳定端口，具体 SDK 只存在于适配器和启动装配层。

## 当前端口

| 端口 | 用途 | 当前实现 |
| --- | --- | --- |
| `tasks.Repository` | 任务、审批、状态和审计事件 | `middleware/sqlite` |
| `middleware.ExecutionStore` | 物理步骤开始/完成与重启恢复 | `middleware/sqlite` |
| `middleware.Queue[T]` | 有界任务交付与背压 | `middleware/memory` |
| `Publisher[T]` / `Subscription[T]` | 进程内瞬时事件分发 | `middleware/memory` |
| `Cache` | 可选性能优化 | 暂无，正确性不得依赖缓存命中 |
| `Locker` / `Lease` | 可选协调与 fencing token | 暂无，单机默认不需要 |
| `TraceStore` | 结构化 Trace 追加与查询 | 暂无独立适配器 |

默认安装不需要 PostgreSQL、Redis、Kafka、Docker 或消息代理。接口存在是为了控制依赖方向，不代表必须部署对应产品。

## 适配器映射

```text
Agent / Application / Robot Runtime
            -> stable ports
                 -> middleware/sqlite (current)
                 -> middleware/memory (current)
                 -> middleware/postgres (future)
                 -> middleware/redis (future)
                 -> middleware/kafka (future)
                 -> other adapters
```

- PostgreSQL 适合实现 `tasks.Repository`、`ExecutionStore`、`TraceStore`；事务必须继续保证任务状态与审计事件原子更新。
- Redis 适合实现 `Cache`、`Locker` 或可丢失后重建的队列；锁必须提供 fencing token，不能只依赖过期时间。
- Kafka 适合实现发布/订阅或事件流；不能仅凭 Kafka 消息推断物理步骤已经安全完成。
- 新基础设施不应改变 Agent、任务、Runtime 或 Safety 的领域类型。

## 新增适配器的步骤

1. 选择最小现有端口；只有语义确实缺失时才扩展契约。
2. 在 `middleware/<name>` 中实现，所有厂商 SDK、连接配置、重试和序列化细节留在该目录。
3. 添加编译期接口断言、故障/重启测试和并发测试。
4. 仅在 `cmd/local-agent` 等 composition root 中根据配置选择实现。
5. 运行架构测试，确认核心包没有导入新适配器或 SDK。
6. 更新本文件和新的架构决策记录；保留旧设计资产以便追溯。

禁止在 Agent 或业务逻辑中直接出现 `redis.xxx()`、`kafka.xxx()`、SQL 查询或 PostgreSQL 客户端调用。缓存失败不得改变业务正确性；事件发布失败不得回滚已经提交的权威状态；在物理动作重放语义不明确时必须失败关闭。
