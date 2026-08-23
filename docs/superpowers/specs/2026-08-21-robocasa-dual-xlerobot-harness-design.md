# RoboCasa 双 XLeRobot 与 Harness Agent 设计

## 1. 目标

在 RoboCasa 厨房中建立一个可重复、可观测的双 XLeRobot 物品交接场景，跑通：

```text
中文自然语言
  → Fleet 任务解析与意图图
  → Harness Agent 前置条件/完成条件
  → 两个 Edge Worker
  → RoboCasa Robot Runtime 工具
  → 单一共享 MuJoCo 物理世界
  → Observation Registry / WorldSnapshot
  → Harness Agent 证据判定
  → Console 实时环境与任务结果
```

第一阶段的目标是建立后续系统开发的稳定仿真基线。RoboCasa 提供厨房资产、
物体、物理和相机，Tangying AgentOS 提供任务、工具、观测、资源一致性和故障
恢复。实机迁移只替换 Robot Runtime、Observation adapter 和地图/坐标变换。

## 2. 已确认约束

- 主机是 Apple Silicon macOS，Python 3.11 和 Conda 可用。
- 本地 RoboCasa 位于 `datasets/robocasa`，版本为 v1.0.1 对应的 main 分支代码。
- RoboCasa 上游 `Kitchen` 当前强制 `len(robots) == 1`，且没有 XLeRobot 的
  robosuite 原生模型与控制器。
- RoboCasa 完整厨房资产约 10 GiB；当前磁盘空间足够。
- 不修改 `datasets/robocasa` 和 `datasets/robosuite` 的受版本控制源文件。
  安装只允许产生上游已忽略的资产、宏文件和 Python 构建元数据。
- 第一阶段允许确定性语义工具驱动 XLeRobot 关节与物体 attachment；任务成功
  必须由共享世界的新观测证明，不能由工具返回值直接证明。

## 3. 方案选择

### 3.1 采用：RoboCasa 场景与 XLeRobot MJCF 组合

直接使用 `KitchenArena(layout_id=1, style_id=1)` 生成确定性厨房基础模型，选取
主操作台作为工作区。组合器把现有 XLeRobot MJCF 复制两份、完整添加命名前缀，
并合并 asset、default、worldbody、contact、equality、sensor 和 actuator 节点。
两个机器人、红色交接物体和三个语义区域存在于同一个 `MjModel/MjData` 中。

优点是保留上游 RoboCasa、复用已有工具契约，并立即获得单一共享物理世界。
后续原生控制器升级不改变 Fleet 和 Harness 接口。

### 3.2 不采用：修改 RoboCasa Kitchen 支持多机器人

解除单机器人断言还需要重写相机、初始基座、动作空间、成功判定和大量
`robots[0]` 假设，会形成需要长期维护的上游分叉。

### 3.3 暂缓：完整 robosuite 原生 XLeRobot

原生注册需要机器人模型、移动底盘 composite controller、双臂 controller、
夹爪模型、观测器和动作空间。它是第二阶段控制精度升级，不作为建立 AgentOS
仿真闭环的前置条件。

## 4. 安装与依赖隔离

新增幂等安装脚本，创建 Conda 环境 `tangying-robocasa`：

- Python 3.11；
- editable 安装 `datasets/robosuite` 的官方 master；
- editable 安装 `datasets/robocasa`；
- 安装当前 AgentOS Python 包；
- 执行 RoboCasa macros 初始化；
- 下载官方完整厨房资产；
- 执行无窗口 smoke test，证明 RoboCasa、robosuite、MuJoCo 和 offscreen renderer
  可加载。

Fleet 和 Edge Worker 不依赖此 Conda 环境。只有 RoboCasa 仿真进程在该环境中
运行，通过现有 gRPC `RobotRuntime` 协议与系统通信。

## 5. 场景与物理语义

固定场景 ID：`robocasa-handoff-v1`。

- RoboCasa：layout 1、style 1、clutter 关闭、seed 7；
- `robot-1`：操作台左前方，面向操作台；
- `robot-2`：操作台右前方，面向操作台；
- `red-block`：左侧起始区；
- `handoff-zone`：两机器人工作空间交集；
- `right-target-zone`：右侧目标区。

三个区域用低碰撞语义标记显示在操作台上。红色方块只有一个 free joint 和一个
物理实体。资源 owner、物理 custodian 和世界关系分别记录，任何时刻最多只有
一个机器人持有方块。

任务文本固定验收样例：

> 让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区

## 6. MJCF 组合边界

`MJCFComposer` 负责：

1. 从 RoboCasa `KitchenArena` 取得展开后的 XML；
2. 对两份 XLeRobot XML 的 `name` 和所有引用属性进行确定性前缀重写；
3. 将根机器人 body 放置到配置的世界位姿；
4. 合并模型段并检测重复名称、失效引用和缺失 mesh；
5. 添加交接方块、语义区域、总览相机和每台机器人证据相机；
6. 输出生成物到 `artifacts/robocasa/generated/`，不改写上游仓库。

生成模型必须通过 `mujoco.MjModel.from_xml_string`、名称唯一性和双机器人关节
存在性测试。

## 7. Runtime 与工具

新增 `sim/robocasa/tangying_robocasa` 包。一个 `RoboCasaFleetRuntime` 进程拥有
共享 `MjModel/MjData` 和仿真锁，并暴露两个 gRPC 端口。每个端口绑定唯一
`robot_id`，但两个 Runtime view 操作同一世界。

沿用工具名称：

- `observe_scene`
- `resolve_targets`
- `plan_grasp`
- `manipulation.pick`
- `verify_grasp`
- `manipulation.place`
- `verify_placement`
- `recover_to_safe_pose`

每条命令继续验证 schema、robot ID、idempotency key、deadline、catalog revision
和 fencing token。工具实现调用 RoboCasa world adapter；不会把 adapter 名称写入
任务语义。

第一阶段的 pick/place 使用确定性关节轨迹和 attachment：方块只在夹爪到达抓取
窗口后附着，只在夹爪到达目标窗口后释放。MuJoCo 中的方块位置、接触/区域关系
和释放后的稳定观测才是完成证据。

## 8. Observation 与地图

每个 Robot Runtime 发布：

- robot base pose、关节、活动、急停状态；
- 可见实体的世界坐标、类别、属性、关系和 confidence；
- 本地 occupancy grid，包含 origin、cell size 和 frame ID；
- RGB 证据帧；
- `sourceSequence`、`observedAt`、`transformRevision` 和 catalog revision。

两个机器人共享 `robocasa-world-v1` 坐标系。Edge 不重复变换已经是 world frame
的数据。WorldHub 保持 observation ID 幂等、source sequence 单调和 freshness
判定；Console 继续消费全局融合地图与 `WorldSnapshot`。

## 9. Harness Agent

新增显式 Harness evaluator，而不是让协调器散落地判断完成：

- 输入：冻结的意图、claim 时的 evidence basis、当前 `WorldSnapshot`；
- 输出：`WAITING`、`SATISFIED`、`RETRYABLE_FAILURE` 或 `FAILED_SAFE`，以及机器可读
  reason 和采用的 observation IDs；
- 条件：命令后的 world revision、机器人 source sequence 和实体 observation count
  均前进；相关 source 新鲜；实体连续稳定至少两次；资源 owner/fencing 一致；
  方块位于目标区域；机器人没有急停或持有残留物。

发送方满足交接谓词后才能发布 `BLOCK_AVAILABLE`。接收方完成后方块 owner 回到
environment，且只发布一次 `BLOCK_DELIVERED`。Harness 决策写入领域事件和证据包。

## 10. 故障与恢复

正常流程之外至少覆盖：

1. observation 重复与乱序；
2. Edge Worker `SIGSTOP`、租约过期和重连；
3. RoboCasa Runtime 在释放前崩溃；
4. RoboCasa Runtime 在物理释放后、结果 ACK 前崩溃；
5. stale fencing token 与重复工具命令；
6. 接收机器人在交接后离线；
7. 相机丢帧但语义观测仍健康；
8. 语义观测冻结但相机仍更新；
9. 外力把方块移出目标区；
10. 碰撞、不可达目标和急停。

安全不变量：不双重持有、不凭旧观测推进、不因重复命令重复释放、不因画面更新
伪造世界新鲜度、失败时不越过交接屏障。

## 11. 持久化与证据

仿真 checkpoint 保存 episode ID、seed、仿真时间、关键 qpos/qvel、方块 custodian、
工具幂等结果和 observation sequence。重启只允许恢复同一 episode；不兼容模型
hash 必须失败关闭。

验收输出位于 `artifacts/robocasa-harness/<run-id>/`：

- 安装与版本清单；
- 生成 MJCF hash；
- 初始/最终 `WorldSnapshot`；
- task、intent、domain events 和 Harness verdict；
- 工具调用 trace；
- 故障矩阵结果；
- 两路机器人视频和 Console 截图。

## 12. 验收标准

必须同时满足：

- RoboCasa 官方环境与资产在独立 Conda 环境可无窗口启动；
- 单一 MuJoCo 模型包含两个 XLeRobot 和一个共享红色方块；
- 中文任务解析为 `robot-1 → robot-2` 两个意图；
- 两个 Edge Worker 调用具体工具类并完成交接；
- Harness 只根据命令后的新鲜世界证据推进；
- 最终方块在 `right-target-zone`，owner 为 environment，两个意图成功；
- Console 实时正确显示 RoboCasa 场景、两机器人、方块、资源和观测健康；
- 浏览器左拖、右拖、滚轮缩放和断线重连通过；
- 故障矩阵通过且无一致性不变量破坏；
- 现有 MuJoCo、Local Brain 和 Fleet 测试不回归。

## 13. 实机迁移

实机先加载与仿真同语义的地图：世界 frame、工作区、交接区、目标区和障碍物。
XLeRobot Runtime 注册同名工具、Observation catalog 和 transform revision。
Harness、任务图、资源租约和 Console 不修改。只有仿真 attachment 工具替换为真实
规划/控制，RoboCasa observation adapter 替换为相机、里程计、关节和地图融合。
