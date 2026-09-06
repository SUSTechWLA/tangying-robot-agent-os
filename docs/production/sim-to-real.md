# 从 RoboCasa 仿真迁移到机器人实机

首次购买与部署请先使用[购机后 Sim2Real 上手](../sim2real/README.md)，它提供配置 kit、阶段检查与逐次证据命令。本页面向系统集成开发者解释契约；当前没有实机生产验收结论。

## 1. 不变边界

迁移时保留 Task/Revision、Coordinator、Command、ObservationEnvelope、WorldSnapshot、custody/fencing、Harness 和 Console；只替换 Robot Runtime 的工具 Adapter、感知 provider、地图/变换和硬件安全实现。因此实机接入不是让 Agent 直接调用舵机，而是注册受约束工具与观测源。

## 2. 实现并完成工具注册

1. 在 Runtime Adapter 实现能力，当前 `observe_scene`、`resolve_targets`、`plan_grasp`、`manipulation.pick`、`manipulation.place` 与验证/恢复工具；新增导航或移动底盘需要单独安全设计，当前 XLeRobot 桌面配置禁用底盘。
2. 每个工具声明稳定 name、`display_name`、`purpose`、输入/输出、`safe_argument_names`、side-effect class、safety level、cancellable、recoverable、timeout。
3. Runtime 生成 catalog revision；Edge 通过 FleetGateway `Register` 上报 ToolDescriptor。
4. ExecuteSkill 必须校验 robot/task/revision/step/command、deadline、approval、catalog、world basis、resource/fencing、idempotency 和 safety profile。
5. 重复 idempotency key 返回已记录状态；旧 fencing、过期 deadline 或 catalog mismatch 不得驱动硬件。

用户端展示 `display_name` 和 `purpose`，例如“夹取方块——1号机器人正在抓住红色方块”，而不是 Python 类名或 gRPC 字段。专业详情再显示 tool name、command ID 和 evidence。

## 3. 注册观测源

Harness Agent 依赖环境状态，因此实机至少提供：

| 源 | 内容 | 建议频率/新鲜度 |
| --- | --- | --- |
| `{robot}/proprioception` | 关节、底盘 pose、夹爪、急停、activity、held | 20–100 Hz；100–500 ms |
| `{robot}/scene` | 目标实体 pose、关系、置信度 | 5–20 Hz；0.5–2 s |
| `map/localization` | map/world 变换、定位质量 | 5–20 Hz |
| `coordinator/resources/*` | owner、fencing token、lease | 事件驱动 |
| 安全源 | 限位、碰撞、电源、急停 | 硬实时本地；云端低频镜像 |

每个 ObservationSource 声明 source ID/type、schema revision、frame IDs、transform revision、update rate、freshness budget、payload kinds 和 adapter version。每条观测带单调 source sequence；相机图像可使用 FrameReference 的 URI/mime/SHA-256，不把大帧塞进低频状态流。

实体检测 provider 可通过 `ROBOT_ENTITY_PROVIDER=my_perception.providers:scene_entities` 注册，后置条件验证 provider 可用 `ROBOT_VERIFIER_PROVIDER=...:verify`。provider 错误、超时或低置信度必须产生 anomaly/STALE，而不是空集合冒充“场景安全”。

## 4. 地图坐标系与环境改变

先建立客户现场地图，再执行任务。定义 `world/map → odom → base_link → arm_base → tool0 → camera` 变换树；所有实体与机器人 pose 最终投影到 WorldModel 声明的 world frame。每次重定位、相机移动、地图重建或外参更新都生成新的 transform revision。旧 revision 的观测不能与新地图混用。

环境状态不仅包含机器人：工作台、禁入区、交接区、目标垫、障碍物、门、人和可移动物体都应有稳定 entity ID、category、pose/bounds、attributes、relations、freshness 和 evidence。地图静态对象可低频；人与动态障碍必须按安全预算更新。

## 5. XLeRobot 标定

1. 固定电源和实体急停，确认舵机 ID、方向、软硬限位与稳定串口 `/dev/tangying-left|right`。
2. 按固定驱动要求记录左右臂/头部/夹爪与总线电机名、ID、零位和范围；若硬件与校准 schema 不符先停止集成。移动底盘不在当前桌面动作范围。
3. 标定相机内参、相机到 arm/base 外参、工作台与 world/map 变换。
4. 用已知 AprilTag/标定块验证位置和 yaw；记录 calibration 与 transform revision。
5. 运行 `sudo bash scripts/robot-pi-preflight.sh`，再运行 `sudo robot-agent doctor robot-pi`。

配置参考 `deploy/config/robot-pi.env.example`。`XLEROBOT_MAX_RELATIVE_TARGET` 和 `XLEROBOT_MAX_ACTION_CHUNK_LENGTH` 是软件默认限制，不构成安全认证；由现场负责人为具体设备选择并验证。

## 6. mTLS 与身份

使用 `scripts/fleet-certs.sh` 或生产 PKI 为每台机器人签发独立客户端证书；FleetGateway 不接受匿名或共享证书。Runtime 同样要求控制端客户端证书。私钥 mode 0600、不可进入镜像/仓库/日志。注册的 robot ID、证书身份、HTTP `X-Robot-ID`、命令 `robot_id` 必须一致。轮换时允许短暂双 CA 信任，验证新链后撤销旧证书。

## 7. 安全、fencing 与幂等

- 实体急停与驱动限位在本地闭环，网络断开不影响停止。
- Edge lease 过期停止领取新任务；Runtime command lease/deadline 过期停止或进入安全姿态。
- 持有物体时任务更新只在工具边界、安全放置点或可证明取消点切换。
- 资源 token 不匹配时拒绝动作；机器人离线后新 owner 必须获得更大 token。
- `ExecuteSkill` 重试不能重复抓取/放置；Runtime journal 记录 command/idempotency/终态。
- 急停解除必须由现场人员检查并显式复位，不能因进程重启自动解除。

默认 systemd 服务使用 `--connect` 但不自动 arm。连接禁用现有扭矩并配置寄存器，必须预先支撑机械臂；`arm` 要求本次现场操作员授权；具体启动、端口互斥和急停复位限制见[Sim2Real 上手](../sim2real/README.md)。

## 8. 分阶段验收

| 阶段 | 允许动作 | 通过标准 |
| --- | --- | --- |
| dry-run | 不连接/不 arm，不使能执行器 | 注册目录、观测源、地图、mTLS、UI 全正确 |
| bench | 空载、低速、单关节/夹爪 | 限位、取消、急停、幂等、日志正确 |
| limited workspace | 单机器人、软围栏、轻物体 | 观测与 Harness 后置条件稳定 |
| handoff rehearsal | 两机器人但人工监护 | custody token 1/2/3、无冲突、可安全暂停 |
| production pilot | 限定任务/时间/操作员 | 长稳、恢复、审计、备份、告警达标 |

实机必须独立验证显示 rAF、网络延迟、观测 freshness、抓取成功率、碰撞/急停响应和恢复；RoboCasa 的签名证据不能替代这些结果。

## 9. 回滚

保留上一个 Runtime/Adapter、tool catalog、observation catalog、calibration 和 transform revision。若新版本出现工具/观测 mismatch、定位漂移或异常：停止派发；安全释放资源；锁存必要急停；将设备标记 MAINTENANCE；恢复上一镜像和配置；以新的 adapter/catalog revision 重注册；从 dry-run 重新验收。不要手工修改 Task 历史、fencing token、World revision 或 Harness evidence 来“跳过”失败。

## 10. VLA/模仿学习/强化学习工具

学习型抓取/放置保持相同 ToolDescriptor，但 Runtime 在 capability input 声明需要 action chunk，Edge 因此必须配置匹配的策略 Provider。模型只输出候选命名关节动作，Edge 校验 manifest 与观测，Runtime 校验硬件边界，Harness Agent 再确认环境结果。模型制品、动作 schema、相机顺序、归一化、地图和标定 revision 必须一同版本化。实现范例、HTTP 协议、故障恢复和 SIMULATION_GO/SHADOW_GO/PHYSICAL_GO 见[学习型策略工具](policy-tools.md)。
