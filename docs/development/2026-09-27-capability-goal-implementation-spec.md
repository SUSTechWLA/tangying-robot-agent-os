# 统一能力目标实施规格（2026-09-27）

状态：本轮实施规格；原 [P0 ADR](../superpowers/specs/2026-09-27-unified-capability-agent-architecture-adr.md) 与审计保留原范围，本文记录随后实现，不覆盖历史结论。

## 目标与不变量

同一 Task/Revision/审批/事件权威接入标定、SLAM、地图激活、语义读工具和旧导航抓放执行器。Local 管单机器人；Fleet 规划并委托认证 Worker，同一 Executor 在边缘完成工具调用。UI 仅输入、批准、显示、诊断。驱动负责机构差异、运动、安全、传感器、定位和语义来源。

模型只提议工具与业务参数，无法生成审批、operation owner、lease、command ID、fencing token 或成功证据。写操作未知不重新派发；旧图/旧标定/旧目录不偷偷沿用。不能从一张新图像推断地图或标定完成。

## 交付

| 层 | 实现 | 约束 |
| --- | --- | --- |
| 契约 | core/capability Manifest/Contract/Operation/Verification | 版本1；严格 Schema 子集；未知约束拒绝；robot 独占资源 |
| 规划 | internal/capabilityagent Planner、fleet CapabilityPlanner | 完整离线语法或 GOAL 模型；目录与机器人绑定；最多16调用；审批前解析复合 Intent |
| 持久执行 | 同包 Executor、原 StepRun 与 TaskEvent | 固定身份；回执、deadline、状态和证据；不新建第二任务数据库权威 |
| 复合调用 | robot.task → edge/agent Runner | 子步骤独立前缀；沿用 Grounding、安全和动作后观测 |
| 云委托 | Coordinator claim/renew/complete、Worker Executor | 未批准不可领；稳定 claim 身份；资源租约和 fencing；只接受设备绑定证据 |
| Runtime | Registry 契约、mapping.build、operation 看门狗 | 自动建图终态包含保存和激活；操作 ID 跨分段稳定；30秒失联停机监督 |
| 模型 | GOAL/INTENT/PLANNING/RECOVERY/SYSTEM | 按阶段配置；边缘云咨询；Local 可热应用；跨地址不继承密钥 |
| 兼容入口 | mapping/request → 同一 Task 草案 | 201未批准；旧结构约束非空拒绝迁移；不按关键词独立启动 |
| 部署 | 云/Orin Docker、Worker SQLite journal 卷 | edge/fleet Runtime 权威互斥；保留状态；主机开发默认独立机器人 journal |

## 操作完成与恢复

校准写入按独立读回 revision 完成；地图写入按操作终态及启用地图身份完成；物理导航/抓放按原闭环确认。写操作先存 STARTED，再调用并保存回执、参数指纹、deadline；已完成不重放，有回执只跟踪原操作，无回执等待核对。

取消读回同一操作并确认终态；不能取消外来操作。租约过期不可复活。未知停止保持可恢复失败。候选地图歧义进入 WAITING_USER，通过完整新目标修订后审批。暂停在能力边界，不宣称长扫描可随时暂停续扫。

Runtime 服务失败只有明确接纳前拒绝才返回 outcome=REJECTED；handler 故障默认未知。探索的传感器过期不是道路障碍，不得因此黑名单目标或声称地图已覆盖。可选原地环视被 NAV_ENVELOPE 拒绝时停止该环视，再选择安全前沿；不扩大安全包络。

## 协议与迁移

- robot ServiceDefinition 增加可选 contract Struct；ServiceRequest 增加 operation_lease_ms/operation_id/operation_owner_id，业务 Schema 不增加权威字段。
- fleet RegisterRequest 增加 service_catalog；设备认证之后登记，Telemetry 心跳不覆盖已认证目录。
- Runtime/Agent/Worker 同步升级生成代码；旧无效果和完成契约的修改服务不提供给 GOAL 模型。
- mapping.start/move/finish 保留手动操作接口；自动目标仅提供 mapping.build 和其他已定义闭环的工具。
- 新 POST intents/{index}/renew 验证完整 claim 身份，维护原 Started 证据基准，过期状态持久化。generic SUCCEEDED 禁止由普通 state 接口伪造。
- 分阶段模型沿用 AGENT_* 继承规则，cloud-goal 对应 FLEET_ASSIST_GOAL_MODEL；设备只使用别名，云模型名称与密钥由服务器控制。

## 验收与边界

必须通过全仓 build/lint/generate-check/test、文档与 book-check；共享 Executor 测试覆盖无回执不重放、原操作恢复、身份漂移、缺证据、取消停稳、完整 Schema/数值约束、否定与预算。HTTP 云边集成测试覆盖身份认证、未批准不可领、持久 journal、租约续期和绑定证据完成。实际 Gazebo 使用同源 XLeRobot 家庭，在统一 Task API 下运行标定→建图→语义导航→到达确认，失败样本保留。

本轮不包括任意自然语言都能执行、未知房间自动功能命名、通用物理能力包认证、共享空间资源 Broker、任意跨机器人目标自动分解、Orin NX/GPU 性能或千台机群压测。Provider 契约验证是可部署的软件协议基础，不等于真实执行器、安全停止和传感器已认证。最终证据与未通过项见[闭环报告](../experiments/2026-09-27-capability-goal-closure.md)。
