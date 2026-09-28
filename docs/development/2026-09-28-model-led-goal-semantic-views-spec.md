# GOAL Agent 的逐轮语义读取与复杂任务闭环规范（2026-09-28）

## 问题与边界

复杂目标中有“如果地图已就绪就复用，否则先建图”之类的条件。原 GOAL Planner 一次向模型索取整份计划，模型无法先读取状态再决定分支；完整离线句式还会绕过已配置的 GOAL 模型。另有家庭抓放子句丢失房间上下文、短暂 IMU 不就绪、视觉捕获过期和 Gazebo 张开指爪接近目标但超时的现场失败。失败样本和修复后的独立任务留在同日实验报告，不能把修复前失败删掉。

用户目标由模型选择能力、顺序和条件分支。确定性代码只承担能力契约、审批、安全准入、子技能物理算法及证据门；SLAM 的有界采集算法属于 `mapping.build` 的工具实现，不能作为用户目标的预写编排。配置 GOAL 模型时不执行 `literalCalls` 的捷径。没有 GOAL 路由时，保守离线语法仍可处理完全匹配的请求，不能从含否定或未知子句的句子中截取动作。

## 规划期 Agent 轮次

1. Runtime 发布服务目录、输入 Schema、效果、资源、操作与核验契约。Provider 对只读服务用 `contract.planningFields` 明确声明能进入模型上下文的顶层结构化字段；无声明则不提供规划期读权限。
2. GOAL 模型每轮只能选择一个声明的只读工具、提交完整 `propose_capability_plan`，或说明无法继续。最多 10 个决策、6 次只读查询、3 次无效提案。多工具输出或无效提案的原因回馈给下一轮，连续无效时拒绝创建任务。
3. 只读结果仅以字段投影进入上下文，单次最多 8 KiB；通用边界拒绝图像、RGB-D、IMU 样本、点云、原始栅格和密集数组。Gazebo `navigation.status` 返回地图身份、位姿、定位状态及融合来源，`navigation.map` 的栅格只供本地导航。规划期不提供物理写工具。
4. 最终提案校验目录、Schema、机器人身份、当前家庭路线明确目的地及复合 Intent；家庭抓放必须保持起点、操作房间、物品、容器、返回地点在同一个可解析子请求里。通过后冻结计划并等待审批。`plan.capabilities.planningTrace` 留存每轮只读结果、拒绝原因与最终提案。
5. 审批不赋予模型改变计划的权力。Executor 逐步执行冻结调用，Runtime 重新检查期限、资源、安全策略和新鲜证据；物理结果未知禁止重放。复合子任务失败写 `CAPABILITY_FAILED` 和工具活动记录，包含可诊断的有界错误消息。

`planningTrace` 是审批前决策依据，不等于物理完成证据。跨审批时间的状态可能变化，因此每个写操作仍须由 Runtime 与独立读回验证；新地图、定位和目标点都在执行时重新核查。

## 状态理解层与 IMU 融合

原始 IMU、RGB-D、关节反馈和点云留在机器人侧驱动、SLAM、避障与验证器。Gazebo ROS 接入层要求 IMU 与里程计时间相差不超过 200 ms，且 IMU 接收时间距当前不超过 500 ms；用 IMU 横滚/俯仰和里程计航向组合底盘姿态，再与同次相机采集配对。缺失或过期时不更新位姿。SLAM 对 RGB-D 与该位姿做配准、回环及地图定位。它不是完整的 IMU 惯导或 EKF，不能宣称以 IMU 修正了平面位置或航向漂移。

`navigation.status` 只把这一结果表示为地图版本、位姿、时间、新鲜度、定位状态和 `poseFusionSource=imu_roll_pitch_odom_yaw`；没有可证实来源时不填该标签。其他机器人可注册自己的融合器，但必须给出来源、时间和失效状态，不得把原始传感器数组塞进 `planningFields`。Agent 后台状态请求不订阅 RGB/深度；控制台 `/v1/telemetry` 默认去掉重建点阵和 `robotState` 中的原始传感器字段，只有显式 `detail=geometry` 的点云/三维诊断视图才返回点阵。操作员显式打开相机视图时才请求图像。内部闭环执行的重建证据仍可访问底层传感器，和模型的状态读取权限是不同边界。

## 感知和物理准入

- Runtime 物理准入遇到短暂 IMU、RGB-D、关节或吸附反馈过期时，在命令期限内最多等待 5 秒，每轮重新运行完整 SafetySupervisor；等待时不写入命令日志、不派发驱动动作。急停优先。
- Gazebo 家庭场景在结构化视觉重建前最多等待 3 秒取得真实的新帧，要求取帧时年龄不超过 450 毫秒，为重建与传输留出传感器有效期；旧帧不能重新打时间戳。超时返回明确失败。
- XLeRobot 仿真吸盘固定在 link 5，上游臂轴位置门限保持 0.04 弧度。张开指爪的 Gazebo 收敛门限为 0.06 弧度，仍需连续四次新反馈；吸附、物体抬升和容器放置分别由后续独立证据验证。此例外仅针对已知的 Gazebo 指爪轴，不改变实机驱动合同。

## 实施与验证

对应实现：`internal/capabilityagent/planner.go`、`core/capability/capability.go`、`internal/actionloop/llmdecider.go`、`robot/gateway/tangying_robot_gateway/{robot_workflow,service,safety,gazebo_backend,gazebo_actuation}.py`。单元和 Gazebo 现场任务记录见[复杂目标报告](../experiments/2026-09-28-complex-model-led-goals.md)。本规范保留设计决定；实验报告保留每次任务的原始结果和失败，不以书籍摘要覆盖。
