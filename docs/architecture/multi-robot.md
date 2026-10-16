# 多机器人协调

当前联网主形态是 Fleet，Local Brain 为独立离线形态。双机器人共享红色方块的实际入口见[RoboCasa 交接](../operations/robocasa-handoff.md)；早期双杯/瓶实验保留在[论文闭环档案](fleet-paper-loop.md)。

## 已实现的边界

- `edge/runtime.Router` 与 Fleet 注册按 robot ID 路由；mTLS 设备身份与命令身份绑定。
- `tasks` / `core/taskgraph` 表达版本化任务与依赖；`fleet/coordinator` 领取意图、寻找安全点并刷新后续工作。
- `edge/worker` 负责每台机器人的云端接入、claim 续租和回执；批准目标经共享 Edge Host 在本机编排和验证，旧批准步骤仍经原执行器运行。目录、任务/命令/资源身份保持固定。
- `fleet/worldhub` / `core/worldmodel` 合并注册观测，`core/harness` 根据后置条件确认任务。
- 共享资源使用 owner、lease 与 fencing token；held/entity/resource 三源冲突时停止后续动作。

## 共享资源交接

```text
robot-1 领取资源 token 1
  → 抓取、放到交接区
  → 新鲜环境观测与 Harness 确认
  → robot-2 以更大 token 2 领取资源
  → 抓取、放到最终目标
  → environment owner / token 3，全部后置条件满足
```

工具返回成功不能跳过环境确认。队列允许重复投递，Runtime journal 和命令身份阻止重复物理动作；状态未知时对账，不能直接重放。任务更新通过 revision CAS 和安全点生效。

## 单机器人目标与机群任务的关系

`robot.objective.v1` 只绑定一个 robotId，资源仅允许 `robot`。一个边缘目标不能自行认领共享物体、区域或第二台机器人；跨机分工与交接仍由 Fleet 协调器拥有权威。可为每台设备分别批准目标，但这并不自动组成支持任意依赖、碰撞规避和共享物料的异构任务图。边缘间的 advisory 数据可经独立 edge-exchange 传递，接收消息本身不授予动作权。详见[目标边界](hierarchical-edge-objectives.md)和[数据交换](../development/edge-data-exchange.md)。

## 当前范围与扩展

当前可复现任务是明确有序的双机器人限定场景，不是任意任务的并行调度器或通用碰撞规避系统。新并行能力需要证明资源互斥、独立工作区、观测质量和停止语义。世界单主持久化、数据库/队列与 HA 的范围见[完整架构](../production/architecture.md)、[分布式成熟度](distributed-agentos.md)和[部署限制](../production/deployment-and-capacity.md)。

真实双机交接必须先完成单机感知、策略、地图标定、停止与持物恢复，再按[Sim2Real](../sim2real/README.md)验证；仿真场景不能替代实体机器人试验。
