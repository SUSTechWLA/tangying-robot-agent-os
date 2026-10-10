# 实机接入 AgentOS：原理、异构机构与完整交付流程

2026-09-27 统一目标入口已升级：标定、自动 SLAM 与导航可通过同一 Task API 下发，自动建图使用 mapping.build；完整契约、审批/恢复、云边委托、逐阶段模型配置和旧入口迁移见[统一目标指南](unified-capability-goals.md)，实测范围见[本轮报告](../experiments/2026-09-27-capability-goal-closure.md)。原手动服务步骤继续用于明确调试操作。

初始核对日期：2026-09-27；2026-10-05 增补 [Local Policy 与真机就绪修补](../development/2026-10-05-physical-robot-integration.md)。历史接口核对基线：`7b34d52abbbd73c333ddd5566f85a6f3b17d59c6`。本页面向设备集成、算法与部署人员，是当前接入操作指南；核对依据和本轮验证见[修订记录](../development/2026-09-27-hardware-integration-guide-review.md)。

**系统支持不同机器人结构共享任务、Runtime、观测和安全契约。新型号仍须交付驱动、标定、实际技能与验收。** 当前 Gazebo 家庭闭环已有[实测证据](../experiments/2026-09-26-gazebo-xlerobot-home-closure.md)，Orin NX、GPU 大模型服务器与真实机器人尚未完成现场认证。

2026-10-11 增补：v0.7.0 的 Agent 通讯、上下文作用域、云边 checkpoint/完成回执与多机器人交接机制见[长程协作实现导读](../architecture/long-horizon-collaboration.md)。Profile 接入支持不同结构；当前单一 capability plan/Task Adapter 约束和缺少通用异构调度的边界也在该页列明。指定家庭单机器人长程已[实测完成](../experiments/2026-10-10-long-horizon-protocol.md)，不能作为新型号或异构多机的实机放行依据。

## 1. 原理：统一的是任务和能力，机构控制留在设备端

```text
自然语言 / 用户批准
        ↓
同一 Agent 决策内核 + 角色 Harness + 可配置模型
        ↓ 任务计划、能力约束、实体绑定、版本与授权
Local Agent 或 Fleet → Edge Worker（二选一作为任务权威）
        ↓ robot.v1 gRPC / mTLS
Robot Runtime：目录、参数、安全、租约、去重、停止与持久回执
        ↓ 本地可信 Backend / 型号适配器
厂商 SDK / 串口 / CAN / ROS 2 / 本机规划器与控制器
        ↓
真实关节、底盘、末端、传感器
        ↑ 原始测量 → 标定 / 重建 / 识别 → 规范观测与动作证据
Runtime → Agent 后置条件校验 → 任务状态与可回溯记录
```

Agent 发出“导航、抓取、放置、验证”等已有技能语义；适配器将其转换为本机运动学、控制器和传感器操作。电机闭环、轨迹插补、避障与速度失联停车必须留在设备端。云端模型的推理时延不应进入电机实时控制环。

系统有三种不同的“成功”：控制器接收命令、工具报告执行结果、任务取得动作后证据。前两项均不能替代最后一项。抓取要确认目标确实被持有；放置要确认已释放、位置关系正确且稳定；导航要确认实际定位到达。发生执行结果未知时先停车并对账，不能把超时当作可重发动作。

ROS 2 是可选接入方式。已有厂商 SDK 的机器人可直接实现 Backend；有 ROS 2 的设备可把话题、TF、Action 和控制器包装在本地适配器后面。Agent 不按 Gazebo、MuJoCo、ROS 或实机名称选择执行逻辑。

## 2. 异构机器人如何表达

`robot.profile.v1` 描述实际设备，`scene.reconstruction.v1` 描述统一世界观测，工具目录描述该设备此时真正能做什么。

| 结构 | `embodiment` | 典型能力子集 | 该型号必须补齐的实现 |
| --- | --- | --- | --- |
| 固定单臂 | `arm` | 观察、抓放、抓放验证、停止 | 原生关节、TCP、IK/轨迹、夹具与反馈、固定工位标定 |
| 双臂 | `dual_arm` | 单臂能力及已实现的协作技能 | 两条运动链、末端区分、互碰约束、持物与协作规划 |
| 移动底盘 | `mobile_base` | 观察、导航、到达验证、停止 | 差速/全向/阿克曼等本机控制、定位、足迹、避障与制动 |
| 移动操作机器人 | `mobile_manipulator` | 导航 + 抓放 | 底盘到手臂坐标链、停车操作、收臂/负载包络与运动所有权 |
| 传感器平台 | `sensor_rig` | 观察、停止接口 | 真实数据、时间、坐标与语义转换；不声明未实现的运动 |
| 其他结构 | `custom` | 已有语义可表达的能力 | 本体驱动与任务专用技能；新工具语义需跨层扩展 |

以上是契约可表达的结构，**不是六类真实机器人均已通过认证**。`dual_arm` 标签不会自动产生双臂协作规划；`custom` 不会自动获得足式行走、无人机飞行或灵巧手技能。

Profile 要点：

- `robotId` 是设备实例；`adapterId` / `adapterVersion` 是驱动身份；`modelId` 是实际型号及配置。设备凭据、证书、Runtime 与任务路由必须对应同一实例。
- `joints` 使用本机原生名称、类型、单位和实际限位；旋转用 rad、直线用 m。不同关节数无需冒充 XLeRobot 的左右臂字段。
- `endEffectors` 区分夹爪、吸盘、工具等，并只引用已声明关节。TCP、FK/IK、碰撞几何、动力学、负载和制动参数仍由型号驱动/控制器与现场资料维护；Profile 不是完整 URDF 或动力学模型。
- `sensors` 声明实际来源、原始 frame、变换版本和允许年龄。工具只声明已实现的 canonical tools，至少包括 `observe_scene` 和 `emergency_stop`。无底盘的手臂不声明导航，无夹具的底盘不声明抓取。
- `actionLimits` 列出每个允许的动作键及上下限；导航另需 `navigation.x/y/z` 的米制工作区。工作区边界不等于避障、碰撞或动力学认证。
- `available` 是运行时就绪状态，不是型号的永久承诺。缺 handler、未使能或故障应报告不可用；`physical_ready` 缺失、异常或返回非 True 都不会放行物理工具。
- 同一 Runtime 生命周期内 Profile 不可变。更换机构、工具集或标定版本后，停车、核对旧任务，重启并重新注册，不在旧任务中热换身份。

已有结构例子：[单臂 Profile](../../examples/robots/arm.profile.json)、[移动传感器 Profile](../../examples/robots/mobile_sensor.profile.json)。两者对应内存仿真，仅用于理解契约与测试，不能改一个 ID 就当作实机驱动。

## 3. 先选部署形态和动作生成路线

### 3.1 两种任务权威

| 形态 | 机器人端 | 系统端 | 适合的开始方式 |
| --- | --- | --- | --- |
| 单机自治 | Local Agent + 一个 Runtime + 本地驱动 | 可选云端 Assist | 一台设备的观察、已验收内部规划技能或配置 HTTP Policy 的抓放 |
| 云端机群 | Edge Worker + 一个 Runtime + 本地驱动/策略 | Fleet + Server Harness + MySQL/Redis | 云端任务管理，或使用现有 Worker HTTP 动作策略链 |

两者共享 Agent 内核和 Runtime 协议，任务存储及控制权不同。**同一机器人只运行一个任务权威**。共享文件锁只保护同机同一 Runtime 地址；跨主机或地址别名还需运维控制唯一写者。Local Agent 调用云端 Assist 仍是单机自治，Assist 不获得该机器人的派单权限。

### 3.2 抓放动作有两条路线，当前装配范围不同

1. **外部策略输出动作块。** 通用 `PluginBackend` 默认声明 `action_chunk` 参数。Local Agent 与 Fleet Worker 可请求已验收的 HTTP Policy，检查模型、观测、标定、动作范围，再交给 Runtime 和实际控制器。Local 在配置文件中启用 `LOCAL_POLICY_MODE=http`，并固定型号、变换和标定版本，详见 [Policy 配置](../production/policy-tools.md#local-agent-单机配置)。Local 真机外部策略要求稳定 Profile、真实来源 Reconstruction、原始采集时间及全部声明关节的测量值；旧的实体列表接口不能满足该门禁。
2. **Backend 自己规划动作。** 契约的 `internallyPlannedTools` 可明确声明 `manipulation.pick`、`manipulation.place`、`recover_to_safe_pose` 由驱动拥有规划。它必须实现本机 IK/规划、限位、取消、停止与结果取证，并让实际 capability 目录与行为一致。Gazebo Backend 是现有参考实现，其仿真夹具不能用于证明真夹爪能力。

当前通用 `PluginBackend.capabilities()` 从公共 schema 生成参数目录，即使 Profile 填写 `internallyPlannedTools` 也仍展示 `action_chunk`；Worker 会因此要求策略。**仅填写该字段不能完成第二条路线的接入。** 应在受信任 Backend 中实现匹配的目录/执行行为及边界测试，不伪造参数目录来跳过一个仍依赖外部轨迹的工具。内部规划的 pick/place 不接受调用者关节轨迹；`arm.move` 仍要求动作块。

策略接口、清单和模型晋级见[Policy 手册](../production/policy-tools.md)。没有仓库随附的通用实机抓取模型，LLM endpoint 不能替代动作 Policy。

## 4. 第一步：建立该设备的接入清单

先限定首批任务、物体、负载、工作区、成功条件和允许失败类型。每台设备保留：

| 资料 | 必填内容 |
| --- | --- |
| 设备清单 | robotId、型号、机构、执行器/传感器序列号、稳定设备映射、固件版本 |
| 软件身份 | Git commit、构建与镜像 digest、驱动版本、依赖锁定、模型/量化权重 SHA-256 |
| 机构资料 | 本机模型、关节方向/零点/限位、TCP、末端类型、底盘运动学与足迹 |
| 标定与世界 | 内外参、原始 frame、world ID、transformRevision、标定文件及摘要 |
| 部署与安全 | 单机或 Fleet、证书身份、控制权、物理急停、失联停止、恢复与备份责任 |
| 验收标准 | 目标误差、停止时间/距离、模型延迟、成功率、负载、观察时长与签字人 |

XLeRobot 的专用接入包可用：

```bash
.venv/bin/python scripts/sim2real.py init --robot-id robot-1 --output site/robot-1
.venv/bin/python scripts/sim2real.py check --kit site/robot-1 --stage inventory
```

该包检查锁定 XLeRobot 双臂硬件、电机标定与策略，不是任意型号的通用验收工具。其他型号应保留独立资料包，不沿用另一台机器的通过记录。`site/` 为私有忽略目录；密钥、token、原始采集与运行 journal 不进入 Git。

## 5. 第二步：完成本机驱动和安全，不依赖 Agent 才能停车

先在厂商工具/本机程序中验证设备连接、反馈与停止，再接 Runtime：

1. 按序列号固定串口/CAN/网络设备身份，核对每个关节方向、量纲和零位；避免用易变化的 `ttyACM0/1` 判断左右臂。
2. 初始状态不使能动作。连接、断开与撤销扭矩可能导致位移，按设备特性支撑机械臂；构造 factory 和读取 readiness 不得顺便解锁。
3. 本地控制器拒绝越界、不可达、碰撞、过载和过期输入。速度、加速度、力、轨迹插补与真实负载约束不能只依赖公共参数 schema。
4. 实现可并发打断正在执行的 `stop(reason)`；长时间 SDK 调用不能阻塞停止线程。导航、扫描、手动控制共享单一运动所有权，底盘速度出口另有短周期看门狗。
5. 物理急停独立于模型、网络和 Agent 进程。软件停止 ACK 与物理停止反馈分别记录，测量真实停止时间与距离。

已有 XLeRobot 路线见[安装](../install/robot-pi.md)和[购机后接入](../sim2real/README.md)：锁定上游与 LeRobot 版本、串口映射和全部电机标定；默认 `run_direct_edge --connect` 不使能，现场交互 `--arm --operator-present` 才请求使能。**这组 CLI 不适用于新厂商插件**；新型号需要自己的本地授权/使能流程。默认 XLeRobot 桌面实机路线关闭底盘。

## 6. 第三步：把真实传感器变成规范观测

固定工位先校准 `world → camera / arm_base → tool`；移动机器人通常有 `world/map → odom → base_link → arm_base → tool` 和相机分支。使用真实内外参和采集时刻的机器人位姿，不把启动时或当前位姿贴到旧图像上。

RGB-D 的基本链路为：

```text
同步 RGB + 米制对齐深度 + K + 原始采集时间
→ 检测/分割得到目标像素
→ K 反投影相应深度得到相机三维点
→ 采集时刻 world_from_camera 变换
→ world 中的点、实体、置信度与 on:/inside: 关系
```

接入 LiDAR、双目、融合感知或厂商场景服务也必须最终遵守同一契约。公共 SDK 校验数据，不提供通用 SLAM 或现场物体识别模型。

- `frameId=world`、`units=m`；pose 顺序 **`[x,y,z,qw,qx,qy,qz]`**，四元数须归一化。ROS 常见 xyzw 字段要转换。
- `robotId`、`sourceId`、`sourceType`、`sourceFrameId`、`transformRevision` 与 Profile 一致；原始 frame 保留来源，点和实体已经转换到 world。
- `observedAtUnixMs` 是原始采集时间；`observationId`/sequence 不倒退，不用轮询时间给旧缓存续命。校验超过来源的 maxAgeMs 或明显未来时钟时拒绝。
- 单帧最多 2048 个实体、4096 个点；原始稠密地图由本地地图服务保存，任务观测提供有界场景。实体 ID 稳定且唯一，只有已测事实才写入关系。
- 图片需要 `ReconstructionCapture` 对应的同次采集图像字段；只返回实体字典不会自动获得 RGB-D 画面。编码器与图像配对，缺测值保持缺失，不能填 0。
- `state_provider` 返回实际原生关节、底盘位姿、持物等可 JSON 化的有限数值。配置 `EDGE_WORLD_POSE` 时核对本地位姿偏置；已在 world 的 reconstruction 不重复平移。

验证已知尺寸/位置、轴方向、左右相机、遮挡、传感器掉线、缓存过期与时间漂移。固定工位可不装 SLAM；移动导航另需地图、重定位与地图/标定版本一致。参考 [RGB-D](../development/single-robot-loop.md)、[ROS 2 RGB-D](../development/ros2-rgbd.md)、[RTAB-Map/Nav2](../development/rtabmap-navigation.md)。

平面室内设备可按 [SLAM 与语义导航](slam-semantic-navigation.md) 复用参考 `RobotWorkflow`：提供配对采集、运动、预约和语义标注回调；保存地图的地点通过 `semantic.locations` / `semantic.resolve` 查询。房间功能仍需显式标注或已验收的 provider，工作区需验证本机停靠和操作可达性；自动几何分区不能直接证明功能房间或机械臂可达。

## 7. 第四步：实现受信任的 Backend，并检查契约

新增型号通常增加本地 Python 包、Profile、观测转换器、工具 handler、依赖、驱动测试和部署配置。已有工具足够表达任务时，MCP/Fleet/gRPC 不需要按厂商重写。超出已有语义的新技能需同步扩展 Python/Go 契约、技能计划器、Guard、目录、验证与 UI 展示。

以下是**结构示意**，`deployment.local_driver` 是需要集成人员实现的模块，不是现成驱动：

```python
from tangying_robot_gateway.plugin_backend import PluginBackend
from deployment.local_driver import load_commissioned_driver

def build():
    driver = load_commissioned_driver()  # 不自动使能；初始 locally_armed=False
    return PluginBackend(
        driver.profile,
        observation_provider=driver.canonical_capture,
        state_provider=driver.robot_state,
        handlers=driver.canonical_handlers,  # Command -> Result
        stop=driver.stop,
        disconnect=driver.disconnect,
        physical_ready=lambda: (
            driver.commissioning_complete
            and driver.locally_armed
            and not driver.has_fault
        ),
    )
```

观察与急停使用专用 callback，不放进 handlers。每个 handler 与 Profile.tools 对应；必须返回合法 `Result`。实际运动进入后发生异常或结果非法，公共服务会以 `EXECUTION_OUTCOME_UNKNOWN` 停止并锁存。必须保留真实控制器反馈，不能在异常处理中固定返回已成功或未执行。

先导出当前 schema、检查静态 Profile：

```bash
.venv/bin/python -m tangying_robot_gateway.run_plugin schema > /tmp/robot-contracts.json
.venv/bin/python -m tangying_robot_gateway.run_plugin check --profile examples/robots/arm.profile.json
# 用该设备真实 Profile 替换下面路径
.venv/bin/python -m tangying_robot_gateway.run_plugin check --profile /absolute/path/site/profile.json
# 在设备现场、未使能状态验证已安装的可信 factory
.venv/bin/python -m tangying_robot_gateway.run_plugin check --factory deployment.my_robot:build
```

`check --profile` 不导入驱动；`check --factory` 会构造 Backend、采集一帧，结束时调用 stop/disconnect，虽不派发动作 handler，仍可能产生驱动连接/退出效果。先审查 factory 生命周期，不把它视作任意 SDK 的无物理影响探针。

Runtime 对 `mutates_world` 工具保存固定的动作后观测。Backend 可实现 `wait_command_capture(command, completed_ns, completed_wall_ms)` 等待实际完成后的新采集；原图、重建与 observation ID 一起冻结到终止回执，不能用之后某次实时画面替代历史证据。

## 8. 第五步：启动 Runtime，并建立两条信任链

为 Runtime 准备具有正确 server name/SAN 的服务证书及 Edge 客户端证书；客户端与服务端互信。运行用户可写持久状态目录。示意命令中的 factory/证书/路径须替换为现场已安装内容：

```bash
.venv/bin/python -m tangying_robot_gateway.run_plugin serve \
  --factory deployment.my_robot:build \
  --listen 127.0.0.1:50051 \
  --journal /var/lib/tangying/my-robot/commands.json \
  --server-key /etc/tangying/runtime/server.key \
  --server-cert /etc/tangying/runtime/server.crt \
  --client-ca /etc/tangying/runtime/client-ca.crt
```

Runtime 同机连接优先 loopback；若跨主机，配置受控监听地址、网络与 mTLS。实机部署不使用 `--allow-insecure`。一个 journal 仅由一个 Runtime 持有，备份同时保留 `commands.json` 与 `commands.json.events/`；索引缺失事件或摘要错误会锁存停止。不能删状态文件解除未知结果/急停。

| 链路 | Local 配置 | Fleet Worker 配置 |
| --- | --- | --- |
| Agent/Worker → Runtime | `ROBOT_ADDRESS`、`ROBOT_CA/CERT/KEY/SERVER_NAME` | `EDGE_RUNTIME_ADDR`、`EDGE_RUNTIME_CA/CERT/KEY/SERVER_NAME` |
| Worker → Fleet HTTPS | 不需要；Assist 用独立配置 | `EDGE_FLEET_URL`、`EDGE_FLEET_CA`、设备独立 `EDGE_DEVICE_TOKEN` |
| Worker → Fleet Link | 不需要 | `EDGE_FLEET_GRPC`、`EDGE_MTLS_CA/CERT/KEY/SERVER_NAME` |

严格 Profile 的 Worker 自动读取设备、驱动和型号身份；显式配置冲突会拒绝启动。Fleet 仍需事先设置 `FLEET_ROBOTS`、`FLEET_DEVICE_CREDENTIALS` 及证书身份。Runtime CA、Fleet HTTPS CA 和 Fleet Link CA 是不同信任配置，不能混填。仅有 HTTP 遥测不等于 Link 心跳、租约与服务器停止通道已验收。

**共享物料另有必要集成。** `RobotRuntimeService.register_resource` 是本地持久授权方法，`run_plugin serve` 没有自动 Fleet grant 同步器或授权 RPC。携带 resourceId 的任务在无本地 grant 时返回 `RESOURCE_GRANT_REQUIRED`。需在自有 Runtime 宿主中，通过独立可信控制通道同步 owner/fencing token、撤销与重启恢复；不能从收到的动作命令给它自己授权。无共享资源的单机试验不能算跨机器人交接通过。

详细凭据与部署见[适配器手册](../development/robot-adapters.md)、[配置安全](../production/configuration-and-security.md)和[Fleet](../architecture/fleet-cloud.md)。

## 9. 第六步：装配模型与 Docker Harness

### 模型按职责选择

| 调用位置 | 配置/扩展方式 | 边界 |
| --- | --- | --- |
| 意图理解 | `AGENT_INTENT_PROVIDER/BASE_URL/API_KEY/MODEL` | 把自然语言转为限定任务语义 |
| 任务规划 | `AGENT_PLANNING_*` | 只选择已注册且适用的技能 |
| 单机恢复选择 | `AGENT_RECOVERY_*` | 受审批范围、工具权限和证据约束 |
| 云端系统任务 | `AGENT_SYSTEM_*` | Server 读取机群与创建待审批草案 |
| 边缘调用云端推理 | `AGENT_CLOUD_ASSIST_*`；云端 `FLEET_ASSIST_*` | 独立 Assist 凭据，只读推理 |
| 抓放动作策略 | Worker `EDGE_POLICY_MODE=http`、`EDGE_POLICY_ENDPOINT` + PolicyManifest | 匹配本机 action schema、观测和标定 |
| 感知/SLAM/本机规划 | 型号 Backend 的 provider/factory | 由驱动集成模型/算法，非统一 AGENT_* 配置 |

意图、规划、恢复、系统模型可独立配置 provider/base URL/key/model；更换 URL 时需显式填写新模型名及适用密钥。边缘可部署已验证的量化模型服务，云端可部署大模型服务；本仓库调用 OpenAI 兼容接口，不自动下载权重、量化或部署 GPU 推理引擎。量化格式、JetPack/CUDA 兼容、显存/内存和工具调用协议必须在目标设备实测。

例如 Orin 私有 `edge.env`：

```text
LOCAL_ROBOT_ID=robot-7
AGENT_PROVIDER=deterministic
AGENT_INTENT_PROVIDER=openai
AGENT_INTENT_BASE_URL=http://127.0.0.1:8000/v1
AGENT_INTENT_MODEL=commissioned-small-quantized
AGENT_PLANNING_PROVIDER=openai
AGENT_PLANNING_BASE_URL=http://127.0.0.1:8000/v1
AGENT_PLANNING_MODEL=commissioned-small-quantized
AGENT_RECOVERY_PROVIDER=deterministic
```

模型名称为部署占位，不是随仓库安装的模型。复杂规划可把 PLANNING_BASE_URL 改为与 `AGENT_CLOUD_ASSIST_URL` 完全相同的 `https://fleet.example/v1/assist`，MODEL 改为 `cloud-planning`，配置独立 `AGENT_CLOUD_ASSIST_DEVICE_TOKEN` 和 CA；云端必须配置 `FLEET_ASSIST_PLANNING_MODEL` 等对应映射。模型返回建议仍走同一执行边界。

### Docker 启动

`Dockerfile.agent` 构建 Go Agent/Worker/Server 入口。**该镜像不包含新型号 Python Runtime、厂商驱动或模型权重服务。** Runtime 可先作为宿主机服务运行；若容器化，需要为本型号单独封装依赖、选择性传入设备、安装校准与证书、挂载 journal 卷并验证停止/退出行为，不能通过给 Agent 容器全部设备权限代替驱动集成。

在目标 Linux/Orin 上，根据[Orin 安装](../install/edge-orin.md)先安装实际证书、私有 env、`EDGE_CERT_GID` 与卷权限，再选择一种 profile：

```bash
# 单机自治
sudo docker compose -f deploy/edge-orin/compose.yaml --profile edge config -q
sudo docker compose -f deploy/edge-orin/compose.yaml --profile edge build local-agent
sudo docker compose -f deploy/edge-orin/compose.yaml --profile edge run --rm --no-deps \
  --entrypoint /usr/local/bin/local-agent local-agent \
  --config /etc/tangying/edge.env --check-config
sudo docker compose -f deploy/edge-orin/compose.yaml --profile edge up -d local-agent

# 或云端机群的机器人端；需先核对并停止上一形态
sudo docker compose -f deploy/edge-orin/compose.yaml --profile fleet config -q
sudo docker compose -f deploy/edge-orin/compose.yaml --profile fleet build edge-worker
sudo docker compose -f deploy/edge-orin/compose.yaml --profile fleet run --rm --no-deps \
  --entrypoint /usr/local/bin/edge-worker edge-worker --check-config
sudo docker compose -f deploy/edge-orin/compose.yaml --profile fleet up -d edge-worker
```

只执行选定形态的一组命令。配置预检不连接 Runtime/模型，也不证明联机协议正确。镜像非 root UID 65534，模板的 host 网络访问本机 Runtime/模型，Local 控制台默认 loopback。角色切换前冻结任务、检查持物与未知终态，停止旧服务后启新服务，保留 `agent-state`，不执行 `down -v`。

云端使用 `deploy/cloud/docker-compose.yml`，在云端主机按[部署指南](../install/alicloud-cloud.md)设置生产配置、数据库、证书和访问策略，运行 `bash scripts/fleet-up.sh up --build`。Server 的 `AGENT_SYSTEM_PROVIDER=openai` 与独立 SYSTEM endpoint/model 启用系统任务模型；其目录只允许机群读取和创建草案，不能批准、直接调 Runtime 或复位急停。数百/数千台需要目标规模容量及故障压测，现有软件配置不构成容量认证。

## 10. 第七步：通过现场阶段门，再开始自然语言闭环

| 阶段 | 做什么 | 通过依据 |
| --- | --- | --- |
| 只读 | 未使能，读取原始反馈、规范观测、目录、身份和证书 | 实物与页面对应；时间/坐标/标定正确，持续新鲜 |
| 最小动作 | 有现场人员和物理急停，低幅空载单轴/短程 | 方向、限位、反馈正确；停止可中断执行 |
| 单技能 | 单次导航或拿放固定物体 | 实际结果、控制器反馈与固定动作后证据一致 |
| 自然语言 | 范围内任务，经目标核对与批准后逐步执行 | 每个写步骤都有有效证据，缺能力/不可达明确失败 |
| 故障 | 无审批、过期观测、断传感器、取消、断网、租约丢失、重复命令、进程重启 | 未授权不动；异常停车；不重放未知动作；重启保留锁存 |
| 持物恢复 | 故意中断抓放，在现场核对持物/目标位置 | 不自动重抓/放置，人工授权恢复并留审计 |
| 长稳与负载 | 当前构建、模型、标定下连续任务，测延迟/温度/内存/失败 | 达到本型号现场制定标准，保留失败与签字 |
| 多机（如需要） | 相同资源竞争、旧 owner/token、撤销、单机掉线和控制切换 | 本地 grant 同步完成；旧授权不能执行，物料归属可信 |

开始任务前取得 capability catalogue 和当前场景，核对对象、起点/目标区域、可达性与实际就绪。**任务文本必须能被已有意图和技能计划器表达**；只注册导航接口不会自动支持任意楼宇巡检或所有自然语言。

批准行为有区别：Local 查看计划后批准；Fleet 用户工作台“创建并开始”会创建并立即批准，操作前核对现场；MCP `create_task` 创建未审批提案，需操作员在控制台批准。MCP 不提供解锁、批准或任意厂商 SDK 工具。参见[MCP](../../robot/mcp/README.md)。

软件检查示例（没有真实设备运动）：

```bash
.venv/bin/python -m pytest -q \
  robot/gateway/tests/test_plugin_contracts.py \
  robot/gateway/tests/test_plugin_backend.py \
  robot/gateway/tests/test_plugin_examples.py \
  tests/contract/test_heterogeneous_runtime_boundary.py
go test ./core/robotcontract ./edge/robotclient ./edge/worker ./cmd/edge-worker
```

这些检查验证协议、示例与跨语言边界；厂商驱动 fixture、断流、取消中断和真实停止测量须另外补齐。XLeRobot 专用接入包要求当前配置的 30 个不同实机任务、至少 1 小时观察及故障类别记录，这是试点资料最低门槛，不是通用可靠性或安全认证。

## 11. 当前接入边界与问题回溯

| 当前边界 | 接入方的必要行动 |
| --- | --- |
| 各类结构 Profile、Plugin SDK、Go/Python 协议已实现 | 为每个型号提供可信驱动、标定与技能，不复用内存仿真或别机实机证据 |
| 默认 Plugin 为外部 action_chunk；Local 未装配 Worker HTTP Policy | 选择适合的执行路线，或实现并测试内部规划 Backend/Local 策略集成 |
| Profile 不包含完整运动学、速度/力/碰撞保护 | 在本机控制器与型号资料中落实并现场验证 |
| 通用插件 CLI 无 Fleet 资源授权同步器 | 有资源任务增加可信 Runtime 宿主授权通道，验证 fencing/revoke/restart |
| proto 有 task_revision、aggregate_version、step_id；Python Command 尚未映射并独立验证这三个字段 | 依靠当前 Fleet/Edge 上层版本门禁，不能声称 Runtime 已独立完成所有版本授权；需要该保证时补接口实现及测试 |
| 既有 XLeRobot `ROBOT_ENTITY_PROVIDER` 是 legacy 列表接口，没有原始帧时间字段 | provider 自行拒绝旧帧；新严格插件使用帧级契约，不能把两个配置接口混用 |
| 软件急停/取消回执不等于实物停止已确认 | 现场验证独立急停、真实反馈与停止测量 |

为每次上线保存同一资料身份：robotId、Git commit、镜像/模型 hash、驱动/Profile/目录版本、标定、world/map 版本、任务/步骤/command ID、策略清单与推理 ID、捕获 ID、原始时间与回执。失败也保存，不重新标时间、不删 journal、不重复试验 ID 凑数量。

重启或回滚前冻结派单，确认运动停止、持物和未知终态；同时保留 Local SQLite 或 Fleet 数据库/事件/World 快照，以及 Runtime journal 索引和事件目录。更换模型、标定、负载或夹具后重做相关验收。最终签署结论只能覆盖本设备、本版本与限定任务。

推荐阅读顺序：本页 → [适配器 SDK](../development/robot-adapters.md) → [Policy](../production/policy-tools.md) / [导航接入](../development/rtabmap-navigation.md) → [Orin Docker](../install/edge-orin.md) → [现场就绪审计](../production/field-readiness-2026-09-26.md)。XLeRobot 首次上电另按[专用交付指南](../sim2real/README.md)执行。
