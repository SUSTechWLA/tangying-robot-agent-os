# 2026-10-05 真机接入的软件升级与验证边界

## 当前结论

可以开始受控真机集成；尚不能宣称真实机器人已完成长程任务或可无人值守上线。本轮没有获得机器人型号、设备地址、传感器与实际控制器信息，没有连接、使能或驱动真实设备。

前一轮 `28296d773d373b35d096117c93a9e8a5ab344214` 已保留 [Gazebo 长程成功记录](../experiments/2026-10-05-long-horizon-recovery.md)：自然语言规划、建图、8 次到达、抓放确认、暂停恢复、一次只读故障的 Ops/Recovery 协作，共 10 个能力、31 个子步骤，约 19 分钟。它证明限定仿真中的闭环；真实夹具、牵引、传感器和急停没有被这份记录认证。原始验收清单的源码 SHA 与任务证据保持原样，不改成当前版本身份。

| 路径 | 本轮后的状态 | 真实长程任务还缺什么 |
| --- | --- | --- |
| Local Agent + 外部 HTTP Policy | 已接入有边界的抓放策略准备、审计、派发和恢复绑定 | 该设备模型、真实 Profile/RGB-D、已标定执行器与独立 verifier |
| Direct XLeRobot 默认部署 | 保持底盘关闭、启动扭矩关闭；驱动故障可正确消退 | 完整实测 perception/actuation/verifier；旧 entity list 不满足新 Local 真机策略观测要求 |
| Legacy ROS Gateway | journal 持久化、独占运行；未实现的验证、抓放和回位明确拒绝 | 接入经过验收的 handler 与验证器，不能只改 available 标志 |
| 跨房间移动操作 | Gazebo 已实测；真机没有当前设备证据 | 实际底盘控制、定位/导航、地图/语义服务、停止距离与持物移动测试 |
| Task / Ops / Recovery | 保留原有只读故障诊断续跑；增加策略准备失败诊断 | 现场测试每个允许的恢复类别；未知物理结果和急停继续要求对账/人工复位 |

## 缺陷、修改与因果关系

1. **Local 没有动作 Policy 装配。** `cmd/local-agent/policy.go` 接入共享 Worker 准备边界，只有目录声明 action_chunk 的 pick/place 才请求 HTTP Provider。缺 Provider 时派发前失败，内部规划工具不注入轨迹。`LOCAL_POLICY_*` 从实际 local.env 读取，三个型号/变换/标定绑定不可省略，非回环端点要求 HTTPS。
2. **推理完成不等于仍可派发。** 推理后重验原始观测年龄，再读取当前 Runtime 的设备、adapter、目录与就绪状态；审计写入后还要检查有效期。接收端命令期限收紧到策略/传感器较短有效期，传输或持久化耗时不能延长旧动作块寿命。期限不足的动作会保守拒绝/停止，需要更短动作块或重新观测，不能人为放宽传感器预算。
3. **计数器与请求时间不能冒充真机测量。** Local 外部策略要求与启动身份一致的有效 Profile、真实来源 Reconstruction、原始采集时间及全部声明关节的有限、限内测量。HTTP deterministic manifest 被真实 adapter 拒绝；真实 manifest 必须明确型号、adapter、变换、标定和 scene/proprioception 来源。声明与配置相符仍不证明现场标定真实，现场需核查原件。
4. **动态动作会污染恢复参数。** 策略仅可附加 action_chunk 与 policy_execution；深复制阻止回调修改批准的语义参数。任务 TOOL_ACTIVITY 保留原始参数用于恢复；POLICY_PREPARED 另存模型/观测身份及有效期，不保存原始动作。完成步骤跳过推理和重复派发。准备失败发生于 MarkStepStarted 之前，不产生虚假的“已开始但结果未知”。
5. **ROS 能力误报与重启记录丢失。** 原 verify_* 宣称可用但始终返回未实现；现在目录和执行入口同时拒绝，缺 verifier 的抓放也不可用。ROS 服务使用 RuntimeJournal 与 exclusive_runtime；停止所有 RPC 后才释放锁，重启不清急停、不重放未知命令。Direct 驱动故障恢复后清除活动故障，同时保留再次发生计数。
6. **离线就绪检查证据过弱。** 两个不同字符设备替代 Path.exists；统一 calibration root；只读检查持久 journal，损坏/未决/锁存拒绝，尚未初始化如实标注。production-check 复用 Sim2Real kit，绑定设备、实际部署配置、标定哈希与安装源码，逐次 trial 去重，拒绝已声明的仿真/mock硬件证据。始终输出 physical_ready=false / live_verified=false；它无法鉴定伪造证据或证明正在运行的进程版本。
7. **前一轮 CI 的独立问题。** 长程验收脚本补入认证客户端约束并统一 helper；gRPC 无期限的超大有限哨兵转为 None，避免 C-core 纳秒整数转换溢出。零、常规有限与协议上限期限保持不变，原超时/大消息测试不放宽。

## 本轮验证

所有新增真机相关测试使用 HTTP 测试服务、内存 Runtime、临时 journal、离线 commissioning fixture 和 ROS 桩件。它们验证软件拒绝与状态恢复行为，不构成真实试验次数；没有将这些合成结果登记为现场 trial。

验证完成于 2026-10-06（工作始于 2026-10-05）：

| 检查 | 结果 |
| --- | --- |
| `go test ./...` | 全部通过 |
| `go test -race ./edge/agent ./edge/worker ./edge/policy ./cmd/local-agent ./agentruntime ./tasks ./core/agentcontract` | 7 个包通过 |
| `.venv/bin/python -m pytest -q robot/gateway/tests tests/install tests/docs` | 1278 passed、6 skipped；199.02 秒 |
| `make lint` | gofmt、全仓配置范围 Ruff、tools.json 同步检查通过 |
| `make build` | robot-agent 与 local-agent 构建通过 |
| ROS gateway/journal 专项桩件回归 | 137 项通过，已被上面的广泛 Python 回归覆盖 |
| Local HTTP 测试服务与 Runner 恢复 | 13 个 Local 顶层用例及 Runner/Worker/Ops 回归通过；覆盖超时、清单漂移、过期、目录变化、观测/关节缺失、持久化失败和不重复已完成动作 |

新增准备诊断使用 `action.preparation_failed`：持久提交后才发布，Ops 实时与重启回放都能定位原任务/步骤/能力；同一 eventId 不重复计数。它不冒充 `action.executed`，不把未知物理结果降级成可重试；持久事件只保存稳定错误码，原异常留在调用者错误链。

原始日志保存在本地忽略目录 `artifacts/acceptance/physical-integration-20261005/`，摘要、文件哈希和软件范围见 [机器可读验证记录](2026-10-05-physical-robot-validation.json)。这些 Go/HTTP/ROS 桩件测试未重新运行前轮 19 分钟 Gazebo 全流程，也没有进行实际 ROS 节点启动、机械臂动作或真实长期 soak；6 项跳过不记作通过。

## 拿到设备后的执行顺序

1. 确定 robotId、型号、控制主机地址、实际机构/传感器/执行器、首批任务工作区和轻物负载。默认 XLeRobot 无底盘路线先做固定工位任务，不能直接提交仿真跨房间原句。
2. 按 [实机接入指南](../guides/hardware-agent-integration.md) 和 [Sim2Real kit](../sim2real/README.md) 固定部署、驱动、标定、动作限制、模型 hash、证书与 journal；执行不打开串口的预检。使用真实 ROS RGB-D header/TF 时可接现有 RosRgbdSource/create_rgbd_backend，再接经过验收的 actuator/verifier。
3. 配置 [Local HTTP Policy](../production/policy-tools.md#local-agent-单机配置)。只读核查 Runtime profile/catalog、采集序列、原始时间、坐标版本和关节测量；缺项时先修适配器，不把模拟字段移植成真机证据。
4. 现场有人、机械臂受支撑、实体急停有效的条件下，按设备 runbook 显式连接/使能。先单关节、空载夹具和低速停止，再抓取一个轻物并独立确认持有，随后放置并确认稳定。软件测试无法代替这些步骤。
5. 冻结现场任务计划后提交长程请求，例如固定工位的“依次检查已登记工作区，将指定轻物归入各自收纳区，每次抓取和放置都确认，最后报告漏项”。仅使用已实现并现场验证的能力；首次经批准执行，再分别验证暂停、断网、重启、重复请求和观测过期。
6. 保存每次 task/command/receipt、观测源、版本、失败和恢复结果。只读故障可按已验证预算诊断续跑；可能已执行的动作不能自动重发，急停不能自动解除。完成绑定的逐次试验与持续运行后，由现场负责人决定受限任务放行。

回退时停任务并保留数据目录、Runtime journal 与所有失败证据。不能通过删除 journal、换 robotId、改 available 或放宽新鲜度阈值绕过未解决的现场问题。
