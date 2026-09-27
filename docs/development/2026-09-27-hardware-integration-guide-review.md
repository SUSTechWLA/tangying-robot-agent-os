# 实机与异构机器人接入指南：复核与修订记录

日期：2026-09-27。源码基线：`7b34d52abbbd73c333ddd5566f85a6f3b17d59c6`。本轮为文档补齐和契约解释，没有新增硬件驱动、使能设备或改变运行中的仿真。完整操作入口为[实机接入 AgentOS](../guides/hardware-agent-integration.md)。

## 需求与本轮改动

用户需要从实机到 Agent 的完整接入步骤和原理，并明确异构机构是否可接入。本轮新增一个顺序完整的指南，覆盖结构表达、厂商驱动、观测标定、动作生成、Runtime、身份/证书、云边 Docker、阶段门与回溯资料。更新文档入口和书籍增量链接，保留出版快照、历史升级规范和实验报告。

适配器手册原工具表把 action_chunk 描述为无例外要求。本轮按实际契约补充 `internallyPlannedTools`，同时明确通用 PluginBackend 仍展示 action_chunk、Worker 据目录要求 Policy，以及 Local 没有 Worker HTTP Policy 的装配，避免把字段存在解释为接入已经完成。

## 依据与边界

| 核对项 | 源码/配置依据 | 指南采用的结论 |
| --- | --- | --- |
| 机构、动作和观测契约 | `robot/gateway/tangying_robot_gateway/contracts.py`、`core/robotcontract/contract.go` | 支持六种 embodiment；关节、传感器与工具按本机声明，Profile 不等于完整动力学模型 |
| readiness 与默认动作目录 | `robot/gateway/tangying_robot_gateway/plugin_backend.py` | 未显式就绪不提供物理能力；通用目录仍有 action_chunk |
| 内部规划 | `contracts.py`、`gazebo_backend.py`、`robot/gateway/tests/test_gazebo_manipulation.py` | 显式契约与专用 Backend 实现，不由填写字段自动产生实际规划 |
| Policy 装配 | `cmd/edge-worker/main.go`、`edge/worker/policy.go`、`cmd/local-agent/main.go` | Worker 有 HTTP Policy；Local 的 LLM 不生成该动作策略 |
| 共享资源授权 | `service.py` 的 register_resource、`run_plugin.py` | 本地持久授权 API 已有；通用 CLI 未提供同步器或授权 RPC |
| 云边部署 | `Dockerfile.agent`、`deploy/edge-orin/compose.yaml`、`deploy/cloud/docker-compose.yml` | 共用 Go 角色镜像；不包含任意新型号 Runtime、厂商驱动和模型权重 |
| 现场资格 | 既有 Sim2Real、现场就绪审计及 Gazebo 验收报告 | 协议/仿真证明不代替 Orin、GPU、真实停止与负载认证 |

原始源码与既有报告中的型号、版本、失败、测试数量不覆盖。原 XLeRobot 专用 kit 不是其他型号的通用资格检查器；新的型号需要自己的资料、驱动故障测试和现场结论。

## 验证

| 实际运行检查 | 结果 |
| --- | --- |
| `python -m tangying_robot_gateway.run_plugin schema` | 成功导出当前 schema；未导入设备驱动 |
| `run_plugin check --profile examples/robots/arm.profile.json` 与 `mobile_sensor.profile.json` | 两个静态 Profile 均 valid=true；未采集实机观测 |
| `pytest -q tests/docs/test_production_docs.py tests/book/test_publication.py` | 10 passed，2.90 s；当前文档链接、make 目标与书籍出版合同通过 |
| `pytest -q robot/gateway/tests/test_plugin_contracts.py robot/gateway/tests/test_plugin_backend.py robot/gateway/tests/test_plugin_examples.py robot/gateway/tests/test_gazebo_manipulation.py tests/contract/test_heterogeneous_runtime_boundary.py` | 109 passed，4.84 s；含两类结构与真实 Go/Python gRPC 边界；设备运动为测试 fixture |
| `go test ./core/robotcontract ./edge/robotclient ./edge/worker ./cmd/edge-worker` | 四个包通过，缓存结果 |
| `make book-check` | 24 sections 的书籍源与生成 Markdown 一致，冻结正文未改 |
| `git diff --check` | 无空白错误 |

本轮仅修改文档，未重新执行全套发布门禁；上一次主线的软件门禁与 Gazebo 验收不改写为本轮实机结论。没有 Orin NX、GPU 模型服务器或真实执行器参与上述检查。
