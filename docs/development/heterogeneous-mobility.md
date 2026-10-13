# 无人车、无人机与足式机器人的边缘接入合同

本指南对应 `robot.profile.v1` 的可选 `mobility` 扩展与 `robot.mobility.v1`。首批无人车、无人机、足式统一走 ROS2 接口。平台提供统一准入、日志、幂等恢复和数据合同；每种实机仍需要厂商 ROS2 适配器、底层控制器与现场调试。仓库中的三个 commissioning profile 是接入模板，不是已经通过实机验收的设备配置。

## 职责与支持范围

| 层次 | 负责什么 | 本轮支持与边界 |
| --- | --- | --- |
| Edge Agent | 长程任务分步执行、审批与目录版本绑定、恢复、结果和证据回传 | 所有接入设备复用同一条执行链 |
| Runtime / PluginBackend | 验证 Profile、目标范围、感知新鲜度、驱动就绪、调用幂等日志和停止回调 | 可验证无人车、无人机、足式 Profile；不因加载 Profile 自动解锁设备 |
| 驱动 / 运动控制器 | 路径跟踪、速度限制、姿态稳定、碰撞与地形处理、独立失联保护、实际停止策略 | 由设备集成者实现；LLM 不负责周期性控制 |
| 地图 / 导航提供者 | 提供当前设备可执行的目标和路径 | 现有栅格导航、SLAM Provider 的 SE2 约束保持不变；声明 SE3 不会把二维导航自动升级为三维飞行规划 |

`navigation.navigate` 仍使用 `goalPose = [x,y,z,qw,qx,qy,qz]`。导航路径、飞行起降或足式步态由本地控制器实现。未增加可以远程绕过审批的起飞、解锁或电机指令，也没有宣称仓库已实现 PX4、MAVSDK、Unitree 或任意厂商的量产驱动。当前 air 合同限定 multirotor；固定翼、倾转旋翼等需要后续扩展合同和验证。

## Profile 中的新字段

历史 `arm`、`mobile_base` 等 Profile 可继续省略 `mobility`。新的 `aerial` 与 `legged` embodiment 必须提供它；显式 `null` 无效。

| 字段 | 取值 / 语义 |
| --- | --- |
| `schemaVersion` | `robot.mobility.v1` |
| `domain` | `ground` 或 `air` |
| `motionModel` | `differential_drive`、`ackermann`、`omnidirectional`、`legged`、`multirotor` |
| `poseModel` | `se2` 或 `se3`；轮式为 SE2，multirotor 为 SE3，足式可声明二者 |
| `navigationPreparation` | `controller` 由设备控制器完成出发准备；`agent_pre_position` 必须声明 `navigation.pre_position`；无人机必须为 `controller` |
| `commandFrame` / `worldConvention` | `world` / `right_handed_z_up` |
| `linearUnit` / `angularUnit` | `m` / `rad` |
| `maxLinearSpeedMps` / `maxAngularSpeedRadps` | 有限且大于零；驱动必须在控制器侧落实，Agent 不能覆盖 |
| `safeStop.strategy` | `brake`、`hold_position`、`controlled_land`、`vendor_procedure` |
| `safeStop.procedureId` | 已调试停止流程的固定版本标识 |
| `safeStop.controllerOwned` | 必须为 `true`；停止流程和独立失联保护归本地控制器所有 |
| `safeStop.commandLossTimeoutMs` | 1–60000 毫秒；声明底层控制器的失联超时，Profile 自身不创建硬件看门狗 |

所有移动目标仍必须位于 `actionLimits.navigation.x/y/z` 的米制范围内。SE2 目标拒绝带 roll/pitch 的四元数。全量 Profile 验证拒绝领域、形态、运动模型和停止策略矛盾：例如无人机使用 `brake`，足式使用 `brake`，或无人机宣称平面 `navigation.pre_position`。

世界坐标采用右手系、Z 向上；世界原点和方向由标定决定，不能仅凭 `world` 名称推断地理东/北。厂商 NED、FRD、经纬度等坐标必须在适配器边界转换并绑定 `transformRevision`。这与 [ROS REP 103 的 SI、右手系和特殊坐标约定](https://github.com/ros-infrastructure/rep/blob/master/rep-0103.rst)一致。

## 安全停止在不同形态中的含义

轮式底盘可以声明经过调试的制动或位置保持；足式必须使用控制器保持平衡的 `hold_position` 或 `vendor_procedure`。多旋翼必须声明 `controlled_land` 或包含完整故障分支的 `vendor_procedure`，不能把持续悬停当作没有电量、定位等后续条件的完整失联策略。合同没有通用 `motor_cut` 选项。

Runtime 的 emergency stop、租约到期、取消与服务退出均沿现有 `stop(reason)` 路径请求停止。这里的“停止”表示停止 Agent 当前任务并将设备交给已注册的安全流程；对飞行器不等同于立即断电，也不等同于已经落地。现有 `EStopResult.latched` 仅确认 Runtime 锁定状态，不能作为着陆完成或实机稳定的证据。驱动应另行提供真实状态和新观测。

PX4 的 Offboard 模式要求持续的控制存活信号，并在超时后执行配置的 offboard-loss 行为。此类控制循环和硬件失联保护必须留在飞控/本地驱动中，不能依赖云端或 Agent 的逐步任务通信。[PX4 Offboard 官方文档](https://docs.px4.io/main/en/flight_modes/offboard)

```python
backend = PluginBackend(
    commissioned_profile,
    observation_provider=driver.observe_world,
    handlers={
        "navigation.navigate": driver.navigate,
        "verify_arrival": driver.verify_arrival,
    },
    stop=driver.enter_safe_procedure,
    stop_policy_id=commissioned_profile["mobility"]["safeStop"]["procedureId"],
    physical_ready=driver.commissioned_and_armed_and_healthy,
    state_provider=driver.state,
    disconnect=driver.disconnect,
)
```

这段代码只是接线方式：`driver` 由集成者实现。构造时 `stop_policy_id` 必须与 Profile 完全相同，否则拒绝加载。字符串匹配证明配置一致，不能证明厂商回调已经正确实现，现场验收仍必须测量真实行为。`physical_ready` 缺失、异常或不是 `True`，物理工具都不能进入驱动。

## 模板、验证和升级

- [无人车模板](../../examples/robots/ugv.commissioning.profile.json)：差速、SE2、控制器制动。
- [无人机模板](../../examples/robots/uav.commissioning.profile.json)：多旋翼、SE3、控制器受控降落。
- [足式模板](../../examples/robots/legged.commissioning.profile.json)：足式、SE3、控制器保持平衡。

模板里的型号、传感器标定、区域、速度、停止标识与超时必须替换为实机配置。其小范围和低速度仅为示例数值，不能视为任何硬件的安全推荐。使用仓库环境执行只读合同验证：

```bash
PYTHONPATH=robot/gateway:python:. .venv/bin/python -m tangying_robot_gateway.run_plugin check --profile examples/robots/uav.commissioning.profile.json
PYTHONPATH=robot/gateway:python:. .venv/bin/python -m tangying_robot_gateway.run_plugin schema
```

Profile 改动现在参与 Runtime `catalog_revision`：工具描述、完整 Profile、传感器标定与时效、执行限制、mobility 和停止流程共同计算 `robot.execution-catalog.v2` 内容 SHA-256。此标签是散列输入的版本，不是新 RPC 方法。临时未就绪、estop 和 availability 不进入散列，仍在每次准入时检查。

因此，运行时重启后即使工具名称相同，旧审批命令也不能在改变的硬件配置上执行。升级后所有带 Profile 的 Runtime 会获得新目录版本，旧的待执行审批需要重新观测、规划并审批；未带 Profile 的历史 Runtime 保留旧的工具目录散列。Go 与 Python 应一起升级，旧 Go 的严格解码器会拒绝新 mobility 字段。

## 可复现实验与不足

`robot/gateway/tests/test_mobility_contract.py` 覆盖三种模板的真实本机 gRPC 传输、默认未解锁阻止运动、停止回调、使用相同日志跨 Runtime 重启后不重复执行，以及修改限制/标定/停止流程后拒绝旧命令。这里的移动回调是记录器，观测为空场景；不是动力学仿真、真实飞行、步态控制或导航到达实验。

`core/robotcontract/mobility_test.go` 读取同三份 JSON 模板，验证 Go/Python 一致的合法形态与拒绝条件。已有 PluginBackend 测试继续覆盖参数越界、感知无效、RPC 合同和不同执行设备结构。最终部署前仍须完成设备专用感知、定位、路径执行、动作中断、真实到达验证、飞控或底盘失联保护与现场恢复测试。

## 已实现的 ROS2 接线

新的 `tangying_ros_gateway.profiled_node:create_backend` 工厂通过 `PluginBackend` 和统一 Runtime 对外提供 gRPC。它运行独立 ROS2 context / 四线程 executor，从默认未就绪开始；旧的固定机械臂 `gateway` 节点保留原行为。`profiled_gateway` 使用与 `run_plugin` 一样的 `check` / `serve` / `schema` CLI，因此 mTLS、持久 journal、进程独占和退出停止流程均复用已有实现。

示例配置（本地可信 JSON，不由云端任务提供）：

```json
{
  "profilePath": "/etc/tangying/ugv.commissioning.profile.json",
  "commissioningId": "site-a-ugv-commission-v1",
  "namespace": "/ugv_1",
  "stateMaxAgeMs": 500,
  "stopTimeoutMs": 1000
}
```

部署时设置 `TANGYING_ROS_PROFILE_CONFIG` 为此文件路径。执行 `ros2 run tangying_ros_gateway profiled_gateway serve --factory tangying_ros_gateway.profiled_node:create_backend --journal /var/lib/tangying/runtime-journal.json --server-key ... --server-cert ... --client-ca ...`。证书参数填写现场路径；生产环境不传 `--allow-insecure`。`profilePath` 可相对配置文件所在目录解析。配置可覆盖下表接口名称。

| 默认 ROS2 名称 | 类型 | 设备侧职责 |
| --- | --- | --- |
| `profiled_execute` | `tangying_robot_msgs/action/ProfiledExecute` | 校验并执行一种 canonical tool；返回命令与 Profile 身份及证据 ID |
| `profiled_driver_state` | `msg/ProfiledDriverState` | 定期发布实际 commissioned / armed / healthy / watchdog 状态、递增序号、执行限制、工具集合 |
| `profiled_observation` | `msg/ProfiledObservation` | 发布严格 `scene.reconstruction.v1` JSON 与同一次采集的可选地图/位姿状态 |
| `profiled_command_lease` | `msg/CommandLease` | 设备验证 commandId / Profile / deadline / lease 的周期性存活信号；迟到或别的命令不得续租 |
| `enter_safe_stop` | `srv/EnterSafeStop` | 校验指定程序和身份，进入安全流程并返回匹配 ACK；ACK 不代表物理动作完成 |

Profile 的独立 `profile_revision` 使用 `ros2_profile.profile_revision()` 计算，设备和 Gateway 必须一致。它与 Runtime 工具目录散列各司其职：前者确认设备合同，后者绑定审批时的完整执行目录。ROS2 action 同时携带 `schema_version=ros2.execution.v1`、robot/adapter/profile/commissioning/catalog、task/command/idempotency、deadline/lease、approval/safety、world basis/resource/fence，桥不会把这些控制信息转换成无身份的速度命令。

准入要求：状态 robot/Profile/commissioning/停止流程精确一致，实际速度和失联超时不超过 Profile，上报工具集合精确相同；commissioned、armed、healthy、watchdog_active 全部为真且 faults 为空；状态序号严格递增、时间新鲜且不能来自未来；重建新鲜；action 和停止服务都可用。状态错误或重建错误立即使物理能力不可用。命令等待期间也检查就绪条件并定期发布绑定命令的租约，失联、超时或取消后取消 action；晚到的 goal acceptance 会收到取消请求。

ROS2 publish 与停止服务不构成跨进程原子事务，设备侧必须独立实现停止锁定、deadline、租约、审批/资源 fencing 和去重。进入 safe stop 后拒绝后续新 goal，直到本地人工重新准入；不能把重复 goal 或晚到取消当成新的执行授权。普通 Runtime cancel 也进入设备停止路径，本桥保持 `_stopped`，没有可由云端复位的接口。恢复需要现场确认、按运行手册清理所需 Runtime 锁定并重启 Gateway / 重新准入设备；不能宣称取消后会自动恢复运动。

目前要求 Unix 墙钟、`use_sim_time=false`；没有把仿真时间转换为实机时间的隐式逻辑。ROS2 topic/action/service 是可信的本地设备集成边界，部署需用独立 namespace / domain 与现场 DDS 权限配置隔离设备；云端只能通过边缘协议进入，不能靠发布 Bool 解锁机器人。

## 同一采集中的地图上下文与成功证据

`ProfiledObservation.reconstruction_json` 上限 2 MiB，`robot_state_json` 上限 64 KiB，后者只接受 `active_map`、`semantic_navigation`、`base_pose`。地图 ID/版本/标定、导航目标/别名/邻接图必须完整一致，地图标定同时匹配此重建的 transformRevision；目标仍受 Profile 的工作空间和 SE2/SE3 限制。`base_pose` 是七元素 world/wxyz 位姿。地图缺失时不会伪造一个默认地图。

同一个 observationId 不允许替换地图或状态。发布中的新采集与读取中的旧采集并发时，线程内快照仍把正确的地图状态配给对应重建，避免出现“旧观测 + 新地图”的组合。这使 Go 的地点 grounding 可消费驱动公开的 `semantic.navigation.v1`，但驱动仍须真实实现导航与到达验证。

观察 topic 使用可靠、有界 `KEEP_LAST(64)` 接收队列；设备发布端也应保留相同深度，确保 action 引用的帧不会被紧随的遥测覆盖。每个状态/观测订阅使用独立互斥 callback group，避免多线程 executor 将同源帧并行重排。队列不保证无限拥塞时不丢帧；缺少准确证据 ID 仍必须停止并将结果视为未知，不能用较新的任意帧替代。

导航或验证返回成功时，桥检查 robot/Profile/command 身份、有限置信度与 observationId。所引用采集必须比本命令准入时该源的 sequence 更大、首次接收单调时间晚于准入、采集墙钟不早于准入，并且仍然新鲜；即使旧采集与命令发生在同一毫秒，也不能当作新证据。桥最多保留 64 个采集用于处理 DDS topic 和 action result 的先后顺序。此检查证明是准入后的新采集；设备必须把 terminal result 绑定真实动作结果，`verify_arrival` 必须独立检查实际位姿，不能把 action 的 accepted 或 succeeded 状态直接当作到达。

在 action 返回之后，共同 Runtime 还调用本桥的 `wait_command_capture`，最多等待 0.5 秒取得采集墙钟和接收单调时间都晚于 handler 完成时刻的新观测。Runtime 将该观测作为 `evidence_observation` 放进不可变命令终态，并将 receipt observation ID 绑定到同一份证据。Go 因而可以复查命令完成后的证据，即使后台遥测已经推进也不会丢失该帧；等不到新帧时不会降级使用缓存帧。前一段的 action 结果检查与这一段的完成后证据分别处理设备结果绑定和边缘任务闭环。

`robotState.lastReadinessFailure` 保留最近一次拒绝的具体原因、状态采集墙钟年龄与状态/观测接收单调年龄，用来区分未准入、控制器状态异常、超龄、端点不可达和观测错误；它是诊断历史，不是当前执行许可。生产时效不会因为实验宿主调度迟缓而自动放宽。

`tests/contract/ros2_profiled_smoke.py --output <证据目录>` 使用单独进程的 recorded controller、真实 ROS2 DDS、新 action/service 和 gRPC，分别跑三类设备模板的三段导航与三次独立到达检查、重放、设备进程退出和停止 ACK。无人机的此项协议实验使用 Z=1 的目标；它仍然只是内存位姿记录器，没有空气动力学、真实电机、真实 SLAM 或厂商导航栈。证据应标为 `recorded_controller_actual_ros2_dds_grpc_no_hardware`，不能写成实机试飞成功。

`scripts/run_ros2_edge_acceptance.py --binary <Linux LocalAgent 路径> --output <新证据目录>` 在已 source ROS2 与两个新消息/网关包的隔离测试环境运行。它启动并清理记录式控制器、gRPC Runtime 和 LocalAgent，显式准入当前 Profile，再提交并审批一条客厅、厨房、卧室之间共十次访问的自然语言请求。规划器为 deterministic，模型调用数为零。断言包括任务 `SUCCEEDED`、规划顺序与实际目标相同、十次导航各有独立 arrival、无机械臂 pre-position、无重复动作，以及每个 arrival 都绑定命令观测。实验目录保留请求、任务事件、观测、控制器/RPC 日志、源码和二进制 SHA-256、地图/Profile 身份、计数和耗时；任何失败必须保留原目录，再用新的目录复跑。这验证单边缘 Agent 的 ROS2 全链路和有界多步骤执行，不能据此声称经过数小时耐久测试或真实场地运动测试。
