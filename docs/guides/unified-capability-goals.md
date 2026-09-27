# 统一能力目标：标定、SLAM 与机器人任务

## 一句话下发

Local Console 的「安排任务」和 POST /v1/tasks 使用同一任务权威。示例：

- 运行标定，探索建图最多行驶12米最多1轮，然后去厨房
- 运行标定，巡检建图，然后去厨房（驱动必须注册扫描路线）
- 检查标定，检查地图，检查语义地点

生成草案后，核对机器人、步骤和参数并批准。精确完整句式走离线语法，其他表达使用独立 GOAL 模型；无法完整理解的否定、条件或动作会要求澄清，不截取建图关键词启动运动。模型参数经过相同 Schema 校验。

标定算法由驱动注册。参考仿真的 calibration.run 检查其模型配置；不代表真实设备的手眼、相机、舵机已认证。需要人工准备的 Provider 应在接纳前拒绝并说明缺少什么。

探索距离和轮数是上限，提前停止可能因为没有可达前沿。任务成功表示工具完成和后续动作通过核验，**不等于全屋覆盖**。未观测区域保持未知，不可达目标不导航。

## 后端链路

~~~mermaid
flowchart TD
    U[完整自然语言目标] --> P[GOAL Planner / 完整离线语法]
    C[Runtime 目录与契约] --> P
    P --> T[Task 草案 / revision / 审批]
    T --> E[共享 Capability Executor]
    E --> J[持久 StepRun 与操作回执]
    E --> R[Runtime 工具]
    R --> V[稳定操作状态 / 地图标定版本 / 独立读回]
    V --> E
    E --> L[已有导航抓放执行器]
    L --> A[动作后观测与验证]
    A --> T
~~~

核心契约在 core/capability，规划执行在 internal/capabilityagent。前端展示任务、工具活动和证据，不循环推进 SLAM，关闭页面仍执行。

robot.task 是已有导航/抓放执行器的复合工具。意图在草案阶段解析、绑定机器人并冻结，执行时不重新调用模型改变目标。子步骤有唯一前缀，继续使用原感知、Grounding、Runtime 安全及到达/抓放验证。

## Provider 契约

ServiceDefinition.contract 声明版本1、效果、资源、输出 Schema、verification 与可选 operation。输入 Schema 来自 Registry，支持对象/数组/字符串/数值/整数/布尔/null、required、enum、上下限、additionalProperties。引用和组合 Schema 等不支持约束会拒绝，不因工具说明获得执行资格。

~~~json
{
  "version": "1",
  "effects": ["PHYSICAL_MOTION", "ARTIFACT_WRITE"],
  "resources": ["robot"],
  "operation": {
    "leaseSupported": true,
    "statusService": "mapping.status", "cancelService": "mapping.cancel",
    "identityPath": "operationId", "statusIdentityPath": "operationId",
    "statePath": "state", "running": ["moving", "exploring", "finalizing"],
    "success": ["completed"], "failure": ["failed", "cancelled"]
  },
  "verification": {
    "service": "mapping.status",
    "required": ["activeMap.mapId", "activeMap.mapRevision", "activeMap.calibrationRevision"],
    "match": {"activeMap.mapId": "result.mapId"}
  }
}
~~~

效果为 READ、ARTIFACT_WRITE、CONFIG_CHANGE、PHYSICAL_MOTION、SAFETY_STOP。目前自动执行只支持独占 robot 资源；共享空间、物体和地图写资源需扩展并认证 Broker，不能只增加名字。

长操作保持稳定 operationId，分段 session/mapId 可以变化。状态/取消是执行器控制回调，status 接受空业务参数；模型不能指定操作所有者、租约或命令身份。云端执行要求 Runtime 租约看门狗：执行器通过独立 RPC 字段申请30秒租约并轮询续约，参考 Runtime 过期后设置同一控制器停止事件，控制器退出才释放预约。收到取消请求不代表停稳。

calibration.run/save 读回 revision；mapping.build 自动采集、优化、保存、激活，核对操作终态和当前地图；mapping.activate 核对目标 mapId。mapping.ensure 返回复用、探索或候选歧义；歧义进入 WAITING_USER，应在同一编号下提交完整新目标并审批。

mapping.start/move/finish 保留为操作员手动扫描接口，没有自动目标执行契约，不提供给 GOAL 模型。Agent 使用 mapping.build，完成后不追加 mapping.finish。接入交互式工具前应定义中间状态、占用转交和完成条件。

## 故障与恢复

- 绑定 task/revision/step、机器人、目录哈希、参数指纹；目录变化要求新计划与审批。
- 已完成步骤不重放；启动后有持久回执的长操作只查询原 operationId。
- 写操作无回执、通信中断或操作身份变化时保持结果未知，不自动重启运动。
- ServiceResponse.result.outcome=REJECTED 只用于明确接纳前拒绝。普通 handler 故障仍可能有副作用，不能仅看 ok=false 推断未运动。
- 取消核对所有权、调用取消、读回终态，并记录 CAPABILITY_STOP_CONFIRMED；停止不明保持可恢复失败。
- 暂停在能力调用边界生效；长建图目前使用取消和核对，尚无任意位置暂停/续扫契约。

事件保留摘要，大量点云/轨迹/栅格在现有地图制品中。Provider 完成证据应使用版本、身份、制品引用，不能依赖摘要投影剔除的批量几何字段。

## 云边与异构机器人

Local 管一台机器人。Fleet 使用设备认证的服务目录规划；Worker 领取批准步骤、持有资源租约和 fencing token，再运行同一 Executor。连续续租不改变命令身份或证据新鲜度基准。云端核对当前所有者的证据后提交完成，来源为 PROVIDER_CONTRACT_EDGE_VERIFIED。

通用目标按单机器人绑定，多设备使用 robotId: 完整目标。原多机器人 Intent 协调仍保留，本轮没有实现任意目标自动跨设备分解或千台压测。机构控制、传感器、IK、足迹、语义来源与物理安全留在 Provider，Agent 不按仿真器选择实现。

Orin Docker 使用 deploy/edge-orin/compose.yaml 的 edge 或 fleet profile，互斥连接同一 Runtime；云端使用 scripts/fleet-up.sh。先按[部署指南](../install/edge-orin.md)配置证书、服务组和私有 env，再启动。Worker 的 EDGE_EXECUTION_DB=/var/lib/tangying-agent/fleet-execution.db 在持久 agent-state 卷。升级保留数据库、证书、地图/标定和原操作，不删除卷清除未知结果；Runtime/Agent/Worker 一起更新协议代码。

## 模型调用点

| 路由 | 用途 | 配置 |
| --- | --- | --- |
| GOAL | 注册能力计划 | AGENT_GOAL_PROVIDER/BASE_URL/MODEL/API_KEY |
| INTENT | 旧任务或复合工具意图 | AGENT_INTENT_* |
| PLANNING | 旧执行图规划 | AGENT_PLANNING_* |
| RECOVERY | 恢复建议 | AGENT_RECOVERY_* |
| SYSTEM | 云端系统草案 | AGENT_SYSTEM_* |

未覆盖阶段继承 AGENT_*。切换地址须提供对应模型，密钥不自动跨地址继承。Local 诊断页可逐阶段配置并热应用；PUT /v1/config/llm 的 stage=GOAL 指定能力目标，空 stage 改默认值，密钥不返回浏览器。

边缘可接本地 OpenAI 兼容量化模型。复杂阶段可将 Base URL 指向已配置认证和 CA 的 AGENT_CLOUD_ASSIST_URL（云端 /v1/assist），GOAL 使用 cloud-goal 别名、云端 FLEET_ASSIST_GOAL_MODEL；其他别名为 cloud-intent/planning/recovery。该服务只推理，动作权限仍来自任务审批与设备租约。Orin NX/GPU 模型部署和性能尚待实机认证。

## 迁移与验收

旧 POST /v1/mapping/request 改为创建未批准 Task，返回201，须显式批准。旧 environment/maxTravelM/maxLegs 字段非空返回400 MAPPING_ENTRY_MIGRATED，迁移为完整请求约束，避免丢失含义。POST /v1/robot/services 保留明确操作入口，不作为自然语言主入口。

~~~bash
.venv/bin/python scripts/evaluate_capability_goal.py \
  --request '运行标定，巡检建图，然后去厨房' \
  --output artifacts/acceptance/my-capability-goal --timeout 1800
~~~

脚本批准后会运动；目录必须不存在。保留目录、草案、批准计划、模型路由、任务历史和报告。成功覆盖全部步骤，导航必须 verify_arrival=CONFIRMED。见[实施规格](../development/2026-09-27-capability-goal-implementation-spec.md)、[闭环报告](../experiments/2026-09-27-capability-goal-closure.md)、[实机接入](hardware-agent-integration.md)。
