# 开发原则与代码地图

本页解释改动应放在哪一层，以及必须保持的语义。完整字段见[接口](../production/api-reference.md)与[数据契约](../production/data-contracts.md)；当前成熟度见[V1 状态](../production/v1-release-status.md)。

## 必须保持的原则

1. **模型只提出意图或候选动作。** LLM 不生成审批、期限、lease、幂等键、fencing 或 safety profile；策略 sidecar 不拥有数据库、资源或硬件设备。确定性代码重新校验边界。
2. **任务成功需要环境证据。** Tool 成功不等于物体到了目标。Harness 使用命令后的新鲜观测、正确 frame/transform、连续稳定状态和一致 custody；陈旧、冲突或缺失时等待或失败关闭。
3. **同一 Runtime 契约贯穿仿真与实机。** 仿真先验证任务、身份、停止、恢复与证据；实机更换 Adapter、感知、策略、标定和现场安全配置。仿真动作不能冒充已训练物理策略。

异构型号按[适配器开发指南](robot-adapters.md)增加 Profile、感知 provider 和规范工具 handler。机械关节键、单位与动作范围属于设备清单，不能在通用 Agent/Safety 层追加厂商名称分支。新感知必须先转换为带来源/采集时刻的 `scene.reconstruction.v1`，再由 Python 和 Go 双端校验；不要把图像、栅格或未转换坐标当成三维世界事实。JSON Schema 从 Python 合同导出，Go 对等校验与跨语言测试一起维护。
4. **核心依赖接口，适配器依赖 SDK。** 业务领域不导入 SQL、Redis、gRPC、protobuf 或机器人 SDK；在 `cmd/` / `internal/` 装配实现。`tests/architecture` 检查核心依赖方向。
5. **重试以物理结果为边界。** 队列可以至少一次交付，物理动作不能重复。先查 command fingerprint、Runtime journal 与世界事实；未知执行结果进入对账。重启不解除急停锁存。
6. **版本与身份不能倒退。** task/world revision、source sequence、catalog revision、fencing token 各有作用域；不通过重置数字或编辑历史解决冲突。持久化只覆盖已实现的单主范围。
7. **界面只呈现可信状态。** 常用任务流程保持简洁，诊断按需展开。隐藏开发按钮不是服务端权限控制；断线、未知提交结果、急停和视觉降级必须对用户可见。
8. **证据与结论绑定版本和环境。** 单元测试、仿真闭环、历史签名包、现场记录是不同证据。报告实际通过与跳过项；接口可调用、离线 READY 和默认参数都不是生产认证。

单机器人 RGB-D 入口与逐层阅读顺序见[闭环开发指南](single-robot-loop.md)；关键新增模块是通用 `rgbd.py`、参考 `rgbd_runtime.py`、Local 恢复、任务证据接口与 SQLite 存储。任务恢复是控制流程继续，历史重建/预览是可审计记录，不代表能凭深度预览无损重跑感知算法。

## 源码地图

| 目录/入口 | 职责与常见改动 |
| --- | --- |
| `cmd/robot-agent`、`internal/robotagent` | CLI、角色生命周期、预检、仿真/Sim2Real 工具分派 |
| `cmd/local-agent`、`internal/localapp` | Local Brain 装配、依赖注入、恢复与工作队列 |
| `agent`、`orchestration`、`skills/manipulation` | 意图、计划守卫、能力模板 |
| `tasks`、`core/taskgraph` | Task、Revision、更新 CAS、安全点和事件投影 |
| `core/compiler`、`core/guard` | 编译与计划校验 |
| `edge/runtime`、`edge/robotclient` | Go 语义 Runtime 与 gRPC 映射 |
| `edge/agent`、`edge/worker` | Local 执行与 Fleet Worker、grounding、policy、恢复和遥测 |
| `edge/policy`、`edge/recovery` | Manifest、Observation、Inference、动作校验与恢复分类 |
| `policy/sidecar` | Python VLA/IL/RL HTTP 包装；不随附已训练实机模型 |
| `fleet/server.go`、`fleet/auth`、`fleet/gateway` | HTTP/WS、设备/操作员身份与 mTLS 注册 |
| `fleet/coordinator`、`fleet/lease` | 单写协调、交接、租约与 fencing |
| `fleet/mysql`、`fleet/redis`、`fleet/eventlog` | 存储、迁移、队列、Outbox 与重启 |
| `core/observation`、`core/worldmodel`、`fleet/worldhub`、`core/harness` | 观测、世界投影与后置条件 |
| `middleware` | Local 的 SQLite/内存实现和基础设施端口 |
| `robot/gateway/tangying_robot_gateway` | Python service、Safety、journal、direct backend |
| `robot/ros2_ws/src/xlerobot_adapter` | 共享 XLeRobot 驱动与可选 ROS 2 包；驱动在此不意味着运行需要 ROS 2 |
| `sim/mujoco`、`sim/robocasa` | 自有仿真适配、场景、共享世界、观测与可视化 |
| `web`、`console` | 工作台、同源静态资产与 Local API |
| `proto`、`gen/go`、`python/tangying_robot_proto` | 线协议与生成代码 |
| `scripts`、`deploy`、`tests` | 安装、部署、故障注入、验收和文档契约 |

`XLeRobot/`、`datasets/`、`vendor/` 与 `tangying-ai-operation-system/` 包含上游或参考代码。先确认所属仓库和版本，不在其中绕过系统接口，也不要把上游工作区变更当成本项目修复。

## 扩展一项能力

先定义语义输入、后置条件、所需观测与失败含义，再决定 Runtime Adapter 和策略 Provider。工具和观测目录分别注册；manifest/catalog/地图/标定需版本匹配。实现仿真行为后，用跨语言契约验证实机分支的校验、停止和未知终态处理，最后进入[Sim2Real 验收](../sim2real/README.md)。

本地配置 UI 的 LLM 与 Fleet Edge 的 `EDGE_POLICY_MODE=http` 是不同通道：前者理解自然语言，后者提供低层候选动作。配置一个 LLM Key 不能补齐感知、动作策略或抓取结果验证。

## 文档与变更一起更新

界面改动更新[前端说明](../frontend/console-v1.md)，HTTP/协议更新[API](../production/api-reference.md)和[数据契约](../production/data-contracts.md)，环境变量更新[配置](../production/configuration-and-security.md)及示例，部署命令更新相应安装指南。发布验证写[V1 状态](../production/v1-release-status.md)；历史评估、签名包和设计计划保留原始身份。

| 改动入口 | 对应文档与验证入口 |
| --- | --- |
| `agent/agent.go`、`agent/intent/parser.go` | [Agent 契约](../agent-v1.md)、[语言评测](natural-language-evaluation.md)；`agent/intent/natural_language_test.go` |
| `tasks/service.go`、`tasks/experience.go` | [API](../production/api-reference.md)、[数据契约](../production/data-contracts.md)；`tasks/service_revision_test.go`、`tests/e2e/test_robocasa_task_updates.py` |
| `edge/robotclient/client.go` | 实体与起点关系契约、[Sim2Real provider 要求](../sim2real/README.md)；`edge/robotclient/grounding_test.go` |
| `sim/robocasa/tangying_robocasa/world.py` | [RoboCasa 方向/回合边界](../robocasa-handoff.md)、语言正反例；不能只增加解析别称就宣告动作已支持 |
| `web/app.js`、`web/console_ui.js`、`scripts/preview-console.cjs` | [用户指南](../user-console.md)、[前端说明](../frontend/console-v1.md)、[异常运维](../production/operations-and-failures.md)；`make test-web` |
| 测试结果与发布范围 | [V1 状态](../production/v1-release-status.md)、[Changelog](../../CHANGELOG.md)；标明日期、命令、报告位置和未覆盖项 |

文档命令以当前脚本参数为准，区分 Compose、独立测试夹具和前端预览的账号/端口。新开发克隆不到忽略的本机报告，需要按文档复现；已存在的测试结果保留原始采集日期，文档更新时间不能伪装成重跑测试或完成实机验收。
