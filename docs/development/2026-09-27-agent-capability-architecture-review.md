# 2026-09-27 Agent 能力架构审计与修订记录

基线：`main@126db7d6487c7a463d7d192eec673d7460cdb7f6`。范围：主任务 Agent、角色 Harness、Runtime 服务、SLAM/标定入口与 Coding Agent 架构参考。目标规范见[统一能力 ADR](../superpowers/specs/2026-09-27-unified-capability-agent-architecture-adr.md)。

## 结论

Runtime/Adapter、安全监督、任务版本和云边角色的基础分层合理。SLAM 和标定已是 Runtime 服务，但“主 Agent 可统一编排所有已注册能力”尚未实现；当前恢复循环和 Server 系统 Agent 的通用工具机制不能代表主自然语言任务入口也已通用。

本轮完成架构审计、分阶段设计以及参数契约修复。没有执行新的 SLAM、标定、导航或实体运动，不把单元测试写成 Gazebo 或实机验收。

## 源码证据与问题

| 问题 | 可复核的源码路径/标识符 | 影响 |
| --- | --- | --- |
| 主任务固定 Intent | `agent/agent.go:Parser.Parse`、`llmPlanner.Plan`、`manipulationTools`；`skills/manipulation/` | 模型只得到 `pick_and_place/fetch/navigate_route`。新增标定工具不会自动进入主任务 |
| 建图独立入口 | `console/mapping_request.go:mappingRequest`、`isMappingRequest`；`console/server.go` 路由 | 关键词触发 `mapping.ensure`，没有通用目标图；仅靠词表不能正确理解否定、复合任务与依赖 |
| 丰富能力已在后端 | `proto/robot/v1/robot.proto:ListServices/CallService/ServiceDefinition`；`robot/gateway/tangying_robot_gateway/robot_workflow.py` 的服务注册 | calibration.get/run/save、mapping.status/start/ensure/finish/activate 等可被后端调用；UI 不应承担流程权威 |
| 完整输入 Schema 丢失 | `internal/recoveryexec/tools.go:parametersOf`、`Executor.resolve`；`internal/actionloop/llmdecider.go:parametersSchema` | 属性都降为 string，required/enum/范围/嵌套结构丢失 |
| 通用循环接线范围有限 | `cmd/local-agent/main.go` 的 `recoveryexec.Executor`；`internal/agentharness/profile.go`；`cmd/fleet-control-plane/main.go` | Edge 主要用于恢复；Server 当前工具范围是机群读/草案。主任务整合仍需单独迁移 |
| 完成契约不足以承接全部服务 | `ServiceDefinition.mutates_world`；`actionloop` 物理证据 gate；`recoveryexec.RobotServices` | 配置/产物/物理效果需分别验证；新帧本身不证明建图或标定通过 |
| Python 名称不一致 | `tools/mapping.py:build_map` 的 schema `mapId`，handler 原参数 `map_id`；`tool_layer.py:RobotTool.execute` 直接展开参数 | 按模型公开 Schema 指定地图会抛 unexpected keyword，转为工具错误 |

源码按路径与标识符记录；行号会随修订变化。旧映射入口的历史说明保留在[Agent 主动建图记录](2026-09-19-agent-initiated-mapping.md)，不能把其入口级能力解读为已经支持通用复合目标。

## 本轮修复

1. `actionloop.Tool`、`recoveryexec.Tool` 增加可选完整 `InputSchema`，优先于旧参数名列表。Runtime Protobuf Struct 到恢复执行器再到模型请求，完整保留类型、required、enum、上下限与嵌套结构。
2. 完整 Schema 工具严格保持业务参数，不添加决策元数据、不删除业务 `reason`。旧工具与完成/阻塞控制工具保持既有 reason 行为。完整 Schema 调用的独立模型理由目前为空，这是明确的迁移限制。
3. 无模型单工具决策器检查完整 Schema；仅明确简单空对象允许无参数调用。带参数、引用、未知根约束时不猜测参数。
4. `build_map` 接收公开 Schema 的 `mapId`；复用、探索和歧义结果保持原语义。未变更导出的工具目录内容（29 项模型工具）。

本轮的参数保留不会自动验证模型参数；工具执行端仍承担验证职责。也不会使 `surveyStarted` 自动变为完成，或让标定自动越过人工准备。

## 验证记录

环境：macOS 本地 Go 1.26 工具链和已有 Python 虚拟环境；使用假模型 HTTP 服务、假的 ServiceCaller/Adapter 与服务工作流单元测试。当前规范中的 P1–P4 是待实现和待验收内容，不纳入本轮通过数量。

| 检查 | 命令/范围 | 实际结果 |
| --- | --- | --- |
| Go 决策与接线 | `go test -json ./internal/actionloop ./internal/recoveryexec ./internal/agentharness ./cmd/local-agent ./cmd/fleet-control-plane ./fleet -count=1` | 6 个包通过；190 项测试/子用例通过 |
| Python 工具与服务 | `.venv/bin/pytest -q tests/tool_layer robot/gateway/tests/test_map_catalog.py robot/gateway/tests/test_robot_services.py` | 309 passed |
| 参数目录 | `.venv/bin/python -m tangying_robot_gateway.llm_tools --check` | 29 个模型工具，导出目录无需变化 |
| Python 静态检查 | `.venv/bin/ruff check robot/gateway/tangying_robot_gateway/tools/mapping.py tests/tool_layer/test_mapping_contract.py` | 通过 |
| 书籍同步 | `make book-check` | 24 sections verified；正文和合订本保持同步 |
| 文档契约与链接 | `go test ./tests/docs -count=1` | 通过 |
| Go 编译接线 | `go build ./...` | 全部 Go 包编译通过 |
| 补丁格式 | `git diff --check` | 通过 |

新增断言覆盖：完整数值/enum/required/嵌套约束真实到达假模型 HTTP 请求；注册 Schema 不被改写；业务 reason 原样返回；Runtime Schema 经恢复执行器到 Decider；无模型调用不会忽略完整 Schema；公开 mapId 和预算参数真实进入建图 Provider，复用/探索/歧义三分支保持原行为。

调试记录：第一次 Go 测试的新增 fixture 使用 `structpb.NewStruct` 不支持的 `[]string`，修正为 protobuf JSON 表示 `[]any` 后通过；新增 Python 测试的导入顺序经 Ruff 修正。没有改变运行服务、仿真场景、已有地图或机器人状态。本轮没有运行全仓库 Python/Web 回归或实机验证，合并仍须通过仓库 CI 的完整 `release-gate`。

## 后续落实与回溯

以 ADR 的 P1–P4 顺序推进，每阶段留下规范、契约变化、迁移方式、验证和未认证范围。目标是关闭前端也能通过统一目标完成建图/标定/导航，并可在同一任务编号下恢复。无需重新建立一个可绕过 Runtime 的驱动系统。

参考 dsh 的可组合能力、事件投影与工具流水线，参考 Pi 的通用决策循环、工具/模型注册和上下文投影；其来源与机器人领域调整已在 ADR 第3节逐项链接。本文不将借鉴视为直接采用其运行时，也未新增相关 npm/Python 依赖。

## 2026-09-27 后续实施记录

上述“本轮”与待实现状态保留原 P0 快照。随后目录契约、持久操作、统一主任务、云边委托和逐阶段模型配置已实施；实际交付范围见[后续实施规格](2026-09-27-capability-goal-implementation-spec.md)，实际验收见[闭环报告](../experiments/2026-09-27-capability-goal-closure.md)。P4 实机、目标机群规模和任意物理能力认证仍未完成。历史记录不作为后续代码通过证据。
