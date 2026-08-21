# RoboCasa 双机器人交接与实机迁移

## 当前结果

固定场景 `robocasa-handoff-v1` 在同一个 MuJoCo `MjData` 中组合 RoboCasa
KitchenArena、两个带独立前缀的 XLeRobot 和一个红色方块。系统已经跑通：

```text
中文自然语言
  -> 云端意图图与单写协调器
  -> Harness 前置条件
  -> robot-1 Edge -> robot-1 Runtime 工具
  -> 命令后的新鲜 WorldSnapshot 证据
  -> 资源 owner/fencing token 交接
  -> robot-2 Edge -> robot-2 Runtime 工具
  -> Harness 物理后置条件 -> SUCCEEDED
```

两个 gRPC Runtime 端点只是同一物理世界的机器人权限视图，不复制物体状态。
因此交接区、方块 owner、机器人 held 状态和最终目标区都只有一个权威事实。

## 为什么这是系统创新点

创新点不是“有多个 Agent”或“界面能画地图”本身，而是面向实体机器人的组合边界：

1. 工具目录说明机器人能做什么，观测目录说明系统凭什么判断已经做完；两者独立注册。
2. Console、协调器和 Harness Agent 消费同一份带 revision、source sequence、freshness 和坐标变换版本的世界事实。
3. 工具成功不等于任务成功。Harness 只接受命令之后的新鲜环境证据，并检查物体位置、稳定性、机器人释放状态、资源 owner 和 fencing token。
4. 单写协调器、事件/Outbox、幂等命令和资源 fencing 共同约束多机器人协作；异常时失败关闭，不靠 prompt 猜测恢复。
5. 云端大脑和无网 Local Brain 共用 Runtime、工具、观测与世界契约，仿真迁移到实机不修改任务语义。

这使它具备明显的研究和产品架构创新价值。当前准确定位仍是“端到端研究/产品原型”，不是已经完成大规模生产验证的分布式机器人平台。

## 安装与运行

前提：Go、Docker、Conda，且本地已有 `datasets/robocasa/robocasa` 上游仓库。

```bash
make robocasa-install
make robocasa-smoke
make robocasa-web-assets
bash scripts/robocasa-fleet.sh start
open http://127.0.0.1:18080/
```

打开 `http://127.0.0.1:18080/`。这个便捷入口只监听宿主机 loopback；公网入口是受 TLS 和白名单保护的 443。登录凭据保存在 `deploy/cloud/.env`，启动日志不会打印密码或设备令牌。

控制台应显示：

- `LIVE`，revision 持续单调增长；
- 两台 `robocasa` 设备在线，且工具能力目录已注册；
- 场景身份 `robocasa-handoff-v1 · robocasa · <model hash>`；
- 机器人、方块、交接区、目标区、资源监护权和观测源健康；
- 双路机器人相机证据、全局占用栅格和机器人轨迹；
- 完整本地 RoboCasa 厨房 GLB、两台完整 XLeRobot、实时关节姿态，以及可选的 18 个 MuJoCo 构件物理 AABB；
- 任务的两个意图均为 `SUCCEEDED`，Harness 为 `SATISFIED`。

三维世界交互：左键拖动平移、右键拖动旋转、滚轮围绕指针锚点缩放、单击查看对象证据、双击聚焦实体、`F` 恢复全景。工具栏提供总览、俯视、R1、R2 和机器人持续跟随；“模型”“构件边界”“标签”“任务路径”四个开关彼此独立。方块交接路径、owner/token、freshness、activity、held 和 Harness verdict 都来自权威世界，而不是从画面反推。

Console 从版本化 `tangying.visual-asset.v1` manifest 加载同源、内容哈希固定的场景与 XLeRobot GLB，并用 `/v1/world` 的 canonical joint keys 驱动两个模型。静态视觉资产只是表现层，绝不能作为物理成功证据；任务成功仍必须由新鲜环境观测、资源监护权和 Harness 后置条件共同证明。manifest 的 `sceneId` 或 `modelHash` 与当前模型 revision 不匹配、资产加载失败或 WebGL context 丢失时，页面会保留 `WORLD LIVE` 并明确显示 `VISUAL DEGRADED`，失败关闭到可用的语义 Canvas。

直接打开原始 `file://.../web/index.html` 不是 live Console：该入口只给出 HTTP 服务地址，并且不启动 API/WebSocket 重试。请始终从 `http://127.0.0.1:18080/` 操作实时系统。离线验收依赖预先生成并由同一服务提供的本地资产，不会访问外部 CDN。

每次启动是一个确定性 episode。方块到达最终目标后，再次提交同一任务会正确返回对象不在起始区，而不是伪造成功。重复演示需重启 profile：

```bash
bash scripts/robocasa-fleet.sh stop
bash scripts/robocasa-fleet.sh start
```

## 证据与异常矩阵

```bash
make test-robocasa-e2e
make test-robocasa-faults
make robocasa-acceptance
```

`robocasa-acceptance` 把初始/最终世界、任务、意图、领域事件、设备状态和 Harness verdict 写入 `artifacts/robocasa-harness/manual/`。故障矩阵覆盖：

- 重复或乱序观测；
- 过期 fencing token；
- 接收机器人离线；
- 相机丢失但语义 ground truth 仍可用；
- 外力移动方块；
- 急停和动作取消；
- 原子 checkpoint、模型哈希不匹配；
- 控制面重启后的目录恢复与在线机器人身份防抢占；
- Edge 真实进程重启后 source sequence 仍单调；
- Runtime 真实进程重启后从 checkpoint 恢复完成态。

Runtime checkpoint 使用原子替换，并绑定场景 ID、episode、模型哈希和状态版本。恢复不匹配时失败关闭。

## 实机 Runtime 接入契约

实机不是在云端新增一套分支逻辑，而是注册一个新的 Runtime adapter。至少同时注册以下三类能力，不能只注册动作工具：

### 1. Tool adapter

保留语义工具名和参数边界，例如 `observe_scene`、`resolve_entity`、`plan_grasp`、`pick`、`verify_grasp`、`place`、`verify_place`。每条物理命令必须携带：

- 工具目录 revision/fingerprint；
- 基于哪个 world revision 规划；
- resource id 与 fencing token；
- idempotency key、deadline、approval 和 safety profile。

实机 adapter 把这些工具映射为运动规划、轨迹执行、夹爪和安全控制。LLM 不生成或放宽安全字段。

### 2. Observation adapter

Harness 依赖环境变化，所以实机必须持续发布可归因、可排序的状态：

- 机器人基座、关节、夹爪、held object、故障、急停和动作阶段；
- 物体类别、稳定 ID、世界系位姿、置信度和 `inside/on/held_by` 关系；
- 工作区、交接区、目标区、障碍物和占用栅格；
- `source_id`、跨重启单调的 `source_sequence`、采集时间、freshness budget；
- `frame_id`、`transform_revision`、地图 revision、模型/标定 revision；
- 命令相关 evidence id，使 Harness 能证明观测发生在命令提交之后。

相机、深度、里程计、关节编码器和外部定位可以是多个 source。World Projector 只接收通过 schema、序列、时间和坐标变换校验的事实；某个 source 过期时显式降级或阻止任务，不用最后一帧冒充当前环境。

### 3. 地图与坐标变换

实机部署前先建立与仿真同语义的地图：固定 `world` frame、机器人定位 frame、工作区、交接区、目标区和障碍物。发布从传感器/机器人 frame 到 `world` 的版本化变换，并将地图哈希或 revision 注册到设备与观测元数据。

地图更新必须产生新 revision。任务规划记录使用的 revision；执行后若地图或标定已变化，Harness 要求重新 grounding，而不是在旧坐标上继续动作。

### 4. 实机视觉注册

真实工作区地图沿用 `tangying.visual-asset.v1` manifest：注册稳定 `sceneId`、地图/标定派生的 `modelHash`、同源 GLB 路径、内容哈希和许可证。真实 XLeRobot adapter 同时发布与 binding 对齐的 `joint.*` canonical keys、有限数值关节位置和当前模型 revision。任何视觉 manifest、adapter 模型或标定 revision 不一致都会退回语义 Canvas；不得用近似旧模型掩盖不一致。

## 迁移顺序

1. 用实机 adapter 跑只读 Observe 和地图对齐，不允许运动。
2. 通过工具/观测共同契约测试，验证序列、freshness、frame 和模型 revision。
3. 单机低速、无负载执行，接入实体急停、watchdog 和安全范围。
4. 单机 pick/place 通过 Harness 后，再开启交接区资源 fencing。
5. 双机执行至少 30 次受监督试验，注入断网、重启、相机丢失、定位漂移和外力移动。
6. 只有 `docs/production-readiness.md` 的硬件门槛全部满足，才声明实机生产就绪。

## 已知边界

- 当前 RoboCasa 动作是确定性语义/运动学 pick-place，用来验证 AgentOS 分布式闭环；它不是关节力矩控制、碰撞丰富的抓取策略或实机标定数字孪生。
- 浏览器 GLB 是确定性导出的视觉树，不等同于 MuJoCo renderer 的像素输出，也不替代碰撞、动力学或传感器事实；语义 Canvas 仍是故障回退层。
- 默认只安装本场景需要的最小 RoboCasa 资产；完整资产使用 `make robocasa-install-full`。
- WorldHub/游标的完整跨节点持久化、存储切主和长期网络 chaos 仍是生产化工作。
- 当前没有声称真实 XLeRobot 已完成物理交接；实机必须走上述观测、地图和安全验收。
