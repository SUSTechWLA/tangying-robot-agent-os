# 从 RoboCasa 仿真迁移到机器人实机

## 1. 不变边界

迁移时保留 Task/Revision、Coordinator、Command、ObservationEnvelope、WorldSnapshot、custody/fencing、Harness 和 Console；只替换 Robot Runtime 的工具 Adapter、感知 provider、地图/变换和硬件安全实现。因此实机接入不是让 Agent 直接调用舵机，而是注册受约束工具与观测源。

## 2. 实现并完成工具注册

1. 在 Runtime Adapter 实现能力，例如 `move_base`、`approach_object`、`grasp_object`、`place_object`、`release_object`。
2. 每个工具声明稳定 name、`display_name`、`purpose`、输入/输出、`safe_argument_names`、side-effect class、safety level、cancellable、recoverable、timeout。
3. Runtime 生成 catalog revision；Edge 通过 FleetGateway `Register` 上报 ToolDescriptor。
4. ExecuteSkill 必须校验 robot/task/revision/step/command、deadline、approval、catalog、world basis、resource/fencing、idempotency 和 safety profile。
5. 重复 idempotency key 返回已记录状态；旧 fencing、过期 deadline 或 catalog mismatch 不得驱动硬件。

`simulation.reset_episode` 只属于仿真生命周期工具，不得注册到物理机器人 Adapter。真实环境的复位必须建模为经过审批的现场流程或独立安全工具，不能因为任务 revision 更新、Coordinator 重启或命令重试而重放。

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

XLeRobot 头部 RGB-D 必须按 `sensor.capture.v1` 原子发布：同一 capture 中的 RGB 与米制 depth 共享 robot、episode、source sequence、simulation/hardware step、时间戳、相机内外参与 world revision。实机 Adapter 可把 `simulationStep` 映射为单调的 hardware capture sequence，但不能省略该字段或拿浏览器截图代替传感器帧。Fleet 只暴露已完成哈希校验、身份校验和 world 关联的 capture；浏览器与 Harness 使用同一个元数据对象。工具活动必须记录动作开始/结束时的 capture 与 world revision，使“机器人在动、画面在变、传感器在更新、任务步骤在推进”可被同一条证据链证明。

Harness/VLA 消费的是版本化 observation bundle，而不是某一张截图：至少包含 proprioception、scene/world basis、RGB-D capture reference、transform/calibration revision、freshness 和 custody/fencing。任一关键源过期、错机器人、错 episode、倒序或与 world revision 不一致时，Harness 返回等待或异常，绝不把旧帧当作成功证据。

实体检测 provider 可通过 `ROBOT_ENTITY_PROVIDER=my_perception.providers:scene_entities` 注册，后置条件验证 provider 可用 `ROBOT_VERIFIER_PROVIDER=...:verify`。provider 错误、超时或低置信度必须产生 anomaly/STALE，而不是空集合冒充“场景安全”。

## 4. 地图坐标系与环境改变

先建立客户现场地图，再执行任务。定义 `world/map → odom → base_link → arm_base → tool0 → camera` 变换树；所有实体与机器人 pose 最终投影到 WorldModel 声明的 world frame。每次重定位、相机移动、地图重建或外参更新都生成新的 transform revision。旧 revision 的观测不能与新地图混用。

环境状态不仅包含机器人：工作台、禁入区、交接区、目标垫、障碍物、门、人和可移动物体都应有稳定 entity ID、category、pose/bounds、attributes、relations、freshness 和 evidence。地图静态对象可低频；人与动态障碍必须按安全预算更新。

## 5. XLeRobot 标定

1. 固定电源和实体急停，确认舵机 ID、方向、软硬限位与稳定串口 `/dev/tangying-left|right`。
2. 采集左右臂零点、关节比例/offset/direction、夹爪开闭、底盘尺度。
3. 固定两台头部 RGB-D 的设备序列号，标定 RGB/depth 内参、RGB-depth 对齐、米制 depth scale、相机到 arm/base 外参、工作台与 world/map 变换。
4. 用已知 AprilTag/标定块验证位置、yaw 和深度误差；记录 calibration、transform、相机固件和深度配置 revision。
5. 运行 `sudo bash scripts/robot-pi-preflight.sh`，再运行 `sudo robot-agent doctor robot-pi`。

配置参考 `deploy/config/robot-pi.env.example`。`XLEROBOT_MAX_RELATIVE_TARGET` 和 `XLEROBOT_MAX_ACTION_CHUNK_LENGTH` 先使用保守值。

## 6. mTLS 与身份

使用 `scripts/fleet-certs.sh` 或生产 PKI 为每台机器人签发独立客户端证书；FleetGateway 不接受匿名或共享证书。Runtime 同样要求控制端客户端证书。私钥 mode 0600、不可进入镜像/仓库/日志。注册的 robot ID、证书身份、HTTP `X-Robot-ID`、命令 `robot_id` 必须一致。轮换时允许短暂双 CA 信任，验证新链后撤销旧证书。

## 7. 安全、fencing 与幂等

- 实体急停与驱动限位在本地闭环，网络断开不影响停止。
- Edge lease 过期停止领取新任务；Runtime command lease/deadline 过期停止或进入安全姿态。
- 持有物体时任务更新只在工具边界、安全放置点或可证明取消点切换。
- 资源 token 不匹配时拒绝动作；机器人离线后新 owner 必须获得更大 token。
- `ExecuteSkill` 重试不能重复抓取/放置；Runtime journal 记录 command/idempotency/终态。
- 急停解除必须由现场人员检查并显式复位，不能因进程重启自动解除。

## 8. 分阶段验收

| 阶段 | 允许动作 | 通过标准 |
| --- | --- | --- |
| dry-run | 不使能执行器 | 注册目录、观测源、地图、mTLS、UI 全正确 |
| bench | 空载、低速、单关节/夹爪 | 限位、取消、急停、幂等、日志正确 |
| limited workspace | 单机器人、软围栏、轻物体 | 观测与 Harness 后置条件稳定 |
| handoff rehearsal | 两机器人但人工监护 | custody token 1/2/3、无冲突、可安全暂停 |
| production pilot | 限定任务/时间/操作员 | 长稳、恢复、审计、备份、告警达标 |

实机必须独立验证显示 rAF、网络延迟、观测 freshness、抓取成功率、碰撞/急停响应和恢复；RoboCasa 的签名证据不能替代这些结果。

## 9. 回滚

保留上一个 Runtime/Adapter、tool catalog、observation catalog、calibration 和 transform revision。若新版本出现工具/观测 mismatch、定位漂移或异常：停止派发；安全释放资源；锁存必要急停；将设备标记 MAINTENANCE；恢复上一镜像和配置；以新的 adapter/catalog revision 重注册；从 dry-run 重新验收。不要手工修改 Task 历史、fencing token、World revision 或 Harness evidence 来“跳过”失败。

## 10. VLA/模仿学习/强化学习工具

学习型抓取/放置仍表现为普通注册工具，例如 `grasp_object`、`place_object`，Task/Coordinator 不直接依赖 VLA、模仿学习或强化学习框架。工具内部的 Policy Provider 接收版本化 observation bundle，返回有限长度 action chunk；Edge 校验模型 manifest、输入 schema、相机顺序、归一化、地图与标定 revision，Runtime 再执行限位、速度、碰撞、deadline 和 fencing 检查，Harness 最后用新的环境与 RGB-D 证据验证后置条件。这样同一个 ToolDescriptor 可以在仿真 Provider、shadow Provider 和真实机器人 Provider 之间替换，而不改自然语言编排与任务状态机。

策略证据只属于需要连续运动输出的 learned physical tool。仿真 reset、环境观察、目标解析、规划和结果验证不得伪造 `policy_execution`；真实 Adapter 也应按 ToolDescriptor 的输入 schema 明确哪些能力需要 `action_chunk`。发布门禁按 logical command/command ID 核验最终环境确认，而不是依赖事件总数：安全重试可以保留多个 inference，但每个最终 pick/place 都必须能追溯到独立 observation、manifest revision 与受控策略制品。

发布必须逐级通过 `SIMULATION_GO`（MuJoCo 中任务与故障矩阵）、`SHADOW_GO`（真实观测输入但不驱动执行器）和 `PHYSICAL_GO`（限定工作区、低速、人工监护）。模型制品、动作 schema、训练数据版本、相机顺序、归一化、地图、标定和安全包必须一起签名；任一不匹配都应拒绝执行，而不是自动降级到未知模型。实现范例、HTTP 协议和故障恢复见[学习型策略工具](policy-tools.md)。
