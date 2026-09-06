# Heterogeneous Robot Integration Implementation Plan

**Goal:** 支持异构机器人通过统一工具、严格三维感知合同和 MCP 接入现有 Agent。

**Architecture:** 可插拔 Python RobotBackend 承接厂商 SDK，版本化 Profile/Reconstruction 跨 gRPC 传递，Go 在消费前校验；MCP 使用 Fleet 任务与查询 API。

**Tech Stack:** Go、Python/Pydantic、protobuf/gRPC、官方 MCP Python SDK。

**Spec:** [架构设计](../specs/2026-09-06-heterogeneous-robots-design.md)

## 约束

不自动使能实机、不跳过审批/急停/租约/幂等、不声称自带三维重建算法；保留旧适配器兼容，严格模式必有新合同。

## 1. SDK 与适配器

- [x] 在 `robot/gateway/tangying_robot_gateway/contracts.py` 定义 Profile/Reconstruction 严格类型与导出 schema。
- [x] 在 `plugin_backend.py` 绑定规范工具及可信本地 handler/provider，`run_plugin.py` 复用 TLS Runtime。
- [x] 扩展 runtime/service/safety：新profile显式动作键及范围、统一感知映射，保留legacy行为。
- [x] 在 gateway 测试和 `examples/robots` 验证两类不同结构以及所有拒绝路径。

## 2. 跨语言消费链

- [x] 为 RuntimeInfo/Observation 添加可选 profile/reconstruction 字段，重新生成代码。
- [x] 新建 `core/robotcontract` 的严格解码/身份/新鲜度/坐标验证及恶意数据测试。
- [x] `edge/robotclient` 在 Info/Telemetry/Ground 验证合同，保持原始采集时间；`edge/worker` 保留真实来源。
- [x] 测试新profile观测必需、错误数据拒绝、world投影与旧适配器兼容。

## 3. MCP 与旧回归

- [x] 新建 `robot/mcp`：官方SDK stdio服务、Fleet鉴权、跨型号一致工具、只创建待审批任务。
- [x] 新建 `tests/mcp`：真MCP初始化/发现/调用与本地HTTP服务契约测试。
- [x] 从 MuJoCo 观测源修复 inside/on 关系丢失，恢复三项交接回归。

## 4. 交付

- [x] 运行相关测试、全量 `make test`、`make lint` 与生成代码一致性检查。
- [x] 更新用户/开发接入手册、schema、实例、架构/API/状态/测试文档；记录通过项、跳过项和实机边界。
- [x] 审阅差异，排除临时制品和敏感配置，commit并push当前工作分支，确认远端hash一致。

验证结果与边界见 [V1 当前交付状态](../../production/v1-release-status.md)。Git 发布结果以同一任务的提交回执及远端分支为准。
