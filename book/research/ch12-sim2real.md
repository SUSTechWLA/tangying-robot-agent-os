# 第 12 章 核心素材：仿真、真机与 sim2real

> 素材来源：躺营 Tangying Robot AgentOS v0.6.0（commit `774bd2a2f`，`VERSION` = 0.6.0）。
> 本文只写仓库里能查到出处的东西。凡属推断，标注「(推断)」；文档与代码冲突处，单独标注「冲突」。
> 所有命令与数字均取自源码或实验记录，可直接照做。
>
> **篇幅说明（诚实声明）**：本文正文约 **10,000 汉字**，超出 4000–6000 字的目标。
> 原因是本章的 10 项要求各自都要"真实参数名 + 真实数字 + 源码位置"（如 §4 的 16 个电机与
> 叶字段取值范围、§8 的 14 个动作键与 3 处数值冲突、§6 的三组实验数字），压到 6000 字
> 必须删掉其中一项的证据。既然约定"**准确性 >> 篇幅**"，这里选择保留全部证据，
> **章节成文时可按需删减，但建议不要删数字**——数字是学生照做的依据。
> 正文引用 **137 个唯一文件 / 约 800 处 `文件:行号` 点位**，最后两节是源码索引与引用稳定性说明。

---

## 1. 三种仿真后端：各管什么、边界在哪

**先说结论：三个"后端"不在同一层级。** RoboCasa 是**构建在 MuJoCo 之上**的资产/场景层（`world.py:1` docstring 即 "One shared RoboCasa physics state exposed as two robot-local tool views"，:12 `import mujoco`，:14 复用 `tangying_sim.tools`）；Gazebo 是独立引擎、独立进程。**这个区分是理解后文的钥匙**：RoboCasa 不可能偏离 MuJoCo 语义，而 Gazebo 可以——事实上它正是靠偏离暴露了假设。

| 后端 | 角色 | 已验证能力 | 限制（有出处的） |
| --- | --- | --- | --- |
| **MuJoCo** (`sim/mujoco/`) | 主参考后端：家居五房间、双 RGB-D 渲染、任务工具、SLAM/标定/导航注册服务 | 全流程；`make home-furnished` 一句话建图与抓放 | 场景几何由代码构造，家具为固定版本开源资产 |
| **Gazebo** (`sim/gazebo/`, `robot/ros2_ws/src/tangying_navigation/`) | 第二个后端，用来暴露"仿真器假设" | 托管 14 个建图服务；一句话探索建图跑通（地图 `scan-eea3bad1fa3d`） | 见下 |
| **RoboCasa** (`sim/robocasa/`) | 双机器人交接场景 + WebGL 数字孪生的物理真值 | 固定场景 `robocasa-handoff-v1`，两个 XLeRobot 在同一 `MjData` 里 | 见下 |

**Gazebo 的限制要分两层说，否则会得出错误结论。** 2026-09-19 的当时结论是「Gazebo 现在可被观测，还不可被驱动」（`docs/development/2026-09-19-gazebo-backend-status.md:151`），`ExecuteSkill`/`CallService`/`Cancel`/`EmergencyStop` 全部返回 `UNIMPLEMENTED`——"不是接受后什么都不做"，急停那条还明确写着"不要把这当成一次停止"（:103-111）。**该文档开头的 :3-8 已声明这一结论在 2026-09-20 不再成立**，但读的人常常跳过头注，所以必须同时读 `docs/experiments/2026-09-20-gazebo-exploration-coverage.md`。

**当前状态的准确表述是**：Gazebo 已**可被观测、可被驱动建图**（14 个建图服务、控制台 `Adapter=gazebo`、镜像自足，:15-16），但 **`ExecuteSkill`、`Cancel`、`EmergencyStop` 至今仍按名字拒绝**（:113 原文："急停那条尤其重要：报一个没有执行的停止，比什么都不报更糟"）。因此 **Gazebo 上没有抓取/放置**——抓放回归由 RoboCasa 覆盖（`docs/guides/gazebo-house-operations.md:50`）。这不是"接口没接完"，而是**有意的安全选择**。另有一处当时看不出来的 bug 至今仍是教学材料：**预构建镜像烘焙的是构建时刻的契约**，改 proto 不重建镜像，agent 与机器人就各说各话（:113-125）。

**RoboCasa 的限制是它自己写明的**（`docs/operations/robocasa-handoff.md:169`）：

> 当前 RoboCasa 动作是确定性语义/运动学 pick-place，用来验证 AgentOS 分布式闭环；**它不是关节力矩控制、碰撞丰富的抓取策略或实机标定数字孪生。**

同一节 :68 记录每次启动是确定性 episode，方向限 `robot-1 → handoff-zone`、`robot-2 → right-target-zone`，重复提交或反向搬运可能返回 `FENCING_TOKEN_STALE`。

**接口是否统一？是，而且证据是硬的。** 三者共享同一条 gRPC 契约：

- `proto/robot/v1/robot.proto:9-18` 定义 `service RobotRuntime`，7 个 RPC：`GetRuntimeInfo` / `Observe`(stream) / `ExecuteSkill`(stream) / `Cancel` / `EmergencyStop` / `ListServices` / `CallService`。
- MuJoCo 与 RoboCasa 共用同一个实现：`sim/robocasa/tangying_robocasa/fleet_server.py:14` `from tangying_sim.server import RobotRuntimeService`——**RoboCasa 根本没有自己的 service 类**。
- Gazebo 侧 `robot/ros2_ws/src/tangying_navigation/tangying_navigation/gazebo_runtime_node.py:461` 实现 `robot_pb2_grpc.RobotRuntimeServicer`，并在 :474-476 复用 `tangying_robot_gateway.service.RobotRuntimeService`。
- 工具层不知道背后是谁：`docs/development/2026-09-19-gazebo-backend-status.md:25-29` 指出 `sim/mujoco/tangying_sim/server.py:489` 把技能交给 `self.world.tools.execute(...)`，而 `tools.py` 只通过 `ToolContext` 拿一个 `world`。（该文档写的是 `:491`，v0.6.0 里 `execute(` 落在 :489——同一条调用表达式内的 2 行漂移。）
- **服务目录也只有一份实现**：`ServiceRegistry`（`robot/gateway/tangying_robot_gateway/service_registry.py`）被 MuJoCo（`sim/mujoco/tangying_sim/server.py:96`）、Gazebo（`gazebo_runtime_node.py:627, 642`）和实机网关（`service.py:139-140`）共用；`ListServices` 在三处都只是 `return self.services.catalogue()`。服务表本身在 `robot/gateway/tangying_robot_gateway/robot_workflow.py:147` 的 `register()`，共 **14 项**：`calibration.get/run/save`、`mapping.status/conflicts/start/move/stop_motion/finish/cancel/activate/inventory/ensure`、`navigation.map`。Gazebo 侧 `docs/experiments/2026-09-20-gazebo-exploration-coverage.md:111` 直接写"**没有第二套实现**：调查循环、前沿策略、占据栅格、地图发布全部是 MuJoCo 用的那一份代码"。

**Go 侧根本不枚举后端**——这是"agent 对后端无感知"的实现方式，也是一个容易讲错的点。在非测试 Go 代码里 grep：`gazebo` 出现 **0 次**（唯一出现处是测试 `tasks/grounded_test.go:14, 25`），`robocasa` 1 次（`cmd/edge-worker/main.go:414`），`mujoco` 3 次（`main.go:341` 的默认 adapter、`main.go:414`、`tasks/service.go:158`）。后端身份由运行时**自报**：`proto/robot/v1/robot.proto:72` 的 `RuntimeInfo.adapter`（Gazebo 报 `"gazebo"`，`robot/gateway/tangying_robot_gateway/gazebo_backend.py:32`；RoboCasa 报 `"robocasa"`，`fleet_server.py:32`）。`tasks/service.go:152-165` 的 `NormalizeAdapter` 只归一既有别名（`""|auto|sim|simulation→mujoco`，`direct|xlerobot|xlerobot_direct→xlerobot_direct`，`ros|ros2|…→xlerobot_ros2`），**default 原样小写透传**。后果有两面：好的一面是新增后端零 Go 改动；坏的一面是**Gazebo 的能力缺口只在 Python 侧可见**，Go 层看不出任何异常。

**为什么一个项目要同时有三个后端？** 因为每个后端都在回答一个别处无法回答的问题：MuJoCo 回答"任务链在理想物理下能不能闭合"；RoboCasa 回答"两个机器人共享一个物理世界时，独占与交接在物理上成不成立"；Gazebo 回答"我们的策略里有多少是 MuJoCo 特有的假设"。第三种价值在 §6 有量化：同一个探索策略，MuJoCo 99.0%、Gazebo 79.3%，差距不在策略，而在**两个户型要求相反的盲区半径假设**。

---

## 2. 无 GPU、无 Docker 也能跑：五房间家居场景怎么来的

**场景不是下载来的数据，是代码构造的。** `sim/mujoco/tangying_sim/home_scene.py:23` 定义

```python
HOME_ROOMS = ("living_room", "home_corridor", "kitchen", "bedroom", "bathroom")
```

五房间 + 一张房间邻接表（:100-104），带停靠点坐标（:86-97）。文件 docstring 明说场景"只包含真实 RGB-D 相机能看到的东西"：墙、家具轮廓、门洞和有纹理的地板；**房间名与航点只是规划元数据，永不作为仿真真值出现在 RGB-D 观测里**（:1-6）。

> **冲突（命名）**：该文件 docstring 写 "four-room home scene"，`HOME_SCENE_REVISION = "home-4room-rgbd-v2"`（:20），但 `HOME_ROOMS` 是五个房间；`README.md:254` 也写"MuJoCo 四房间家庭场景"，而 `docs/operations/release-checklist.md:8` 要求"确认五个房间"。**以 `HOME_ROOMS` 为准：五个房间；"4room" 是遗留的 revision 字符串。** 教学时应把这一点当作"标识符一旦被证据引用就不能随便改"的实例。

**`make home-furnished` 做什么。** Makefile:113-114：

```make
home-furnished: build
	bash scripts/furnished-home-demo.sh start --sim-port 50161 --agent-port 8897
```

注释（Makefile:110-112）说明它"在隔离命名空间里启动装修家居演示"，而 `home-start`/`home-accept` 保留原始彩色物体基线。**默认控制台端口是 8897**（`docs/guides/robot-service-workflow.md:8-10`），注意 `scripts/sim-stack.sh` 自己的默认 agent 端口是 8787——Makefile 显式传 8897 就是为了不让读者打开一个没人监听的端口。

**`scripts/furnished-home-demo.sh` 做什么。** 三段（:1-38）：

1. 检查 `collada` 转换依赖，缺了就报 "Install conversion dependencies first: `.venv/bin/pip install -e '.[visual]'`"；
2. 若 `artifacts/sim-assets/aws-small-house` 不存在，跑 `scripts/prepare_home_world.py`；然后 `scripts/prepare_furnished_home.py` 产出 `artifacts/sim-assets/furnished-home`；
3. `exec bash scripts/sim-stack.sh <start|restart> ... --scene home_task --perception rgbd --home-assets "$DEMO_PACK"`。

**`scripts/sim-stack.sh` 是通用栈管理器**（`start|stop|restart|status|logs`），默认 `SIM_STACK_SCENE=tabletop`、`SIM_STACK_SIM_PORT=50051`、`SIM_STACK_AGENT_PORT=8787`、`SIM_STACK_SEED=7`、`SIM_STACK_PERCEPTION=ground-truth`（:8-14）。制品根目录默认 `artifacts/sim-stack`，可用 `SIM_STACK_ARTIFACTS_DIR` 覆盖（:7-9）。

**场景资产从哪来（"固定版本开源家具"）**——两套资产，各有 pin 与许可，**没有网络下载器**：

| 资产 | pin | 许可 | 强制方式 |
| --- | --- | --- | --- |
| 家具：AWS RoboMaker Small House World | `ff9631ca6d1db9c1ba656498151464b5ab74aafe` | **MIT-0** | `scripts/prepare_furnished_home.py:351-352` 用 `git` 读实际 revision 并断言：`if revision != SOURCE_REVISION or dirty: raise ValueError("AWS Small House checkout must be clean and pinned to " + SOURCE_REVISION)` |
| 机器人：XLeRobot MuJoCo 模型 | `3d14695e40c9c68229c0aacffca6053c75cd3eb6` | **Apache-2.0** | `sim/mujoco/assets/xlerobot/PROVENANCE.md:1-8`，随目录保留上游 LICENSE |

所以"固定版本"是**靠断言而非靠 URL**：脚本要求本地已有一份 clean 的 pinned checkout（`prepare_furnished_home.py:320`），每个 mesh/texture 都记 SHA-256，运行时由 `sim/mujoco/tangying_sim/furnished_home.py:13-25` 的 `_validated_file()` 复核，不符即 `ValueError`（失败关闭）。`docs/guides/furnished-home-demo.md:93` 补充："只转换模型素材，**不运行上游 Gazebo 插件**；程序化装修与操作工位由本仓库生成。"转换依赖在 `pyproject.toml:43-47` 的 `visual` extra（`trimesh==4.12.2`、`pycollada==0.9.3` 等）。

**一句必须写进教材的免责声明**（`PROVENANCE.md:16-21`）：

> This pinned official model … is **not a calibrated digital twin** of later two-wheel XLeRobot revisions, and its dimensions, dynamics, sensors, and calibration must **not** be treated as current two-wheel hardware ground truth.

**无 GPU 的真实代价（这一节的重点）。** 首先要纠正一个常见误解：**仓库代码从不设置 `MUJOCO_GL`**——全仓只有三处引用，都在 CI、文档与注释里，实际后端由 `mujoco` 包在 import 时自行决定。真正做工程应对的是两处：

1. **CI 显式走软件渲染**：`.github/workflows/ci.yml:16` 与 `:161` 设 `MUJOCO_GL: 'osmesa'`，并安装 `libosmesa6`。`docs/production/testing-and-acceptance.md:34` 记录"GitHub runner 是两核、`MUJOCO_GL=osmesa` 的软件渲染环境，整个套件比开发机慢约 **2.5 倍**"——这也解释了为什么就绪预算写在环境变量里而不是写死在测试里。
2. **为 OSMesa 下永久阻塞加的边界**（`sim/mujoco/tangying_sim/rendering.py:17-23`）："under CI software rendering (`MUJOCO_GL=osmesa`) a capture has been observed to **block inside `mjr_render` indefinitely**. Without a bound the caller waits forever: the camera loop stops without an error, and a test run is killed by the outer timeout before it can report which capture stalled."实现是 `DEFAULT_RENDER_TIMEOUT_S = 60.0`（:23）+ 环境变量 `TANGYING_RENDER_TIMEOUT_S`（:27）+ 一旦超时就置 `self._unresponsive = True`，**之后所有请求快速失败**而不是继续排队等待（:86-110）。

**实测成本数字**（`docs/development/mujoco-compatibility.md`，注意作者自己限定"是特定机器上的短期实验，不是任意硬件的性能承诺"）：

- :96 —— "Linux ARM64、4 CPU 配额、OSMesa 的分段实验中，两台机器人的单次观测中位耗时约 **514–515 ms**，其中 `mjr_render` 约 **439 ms**；PNG 编码约 1.5 ms。"
- :98 —— 改成只让主光源投阴影后，"相同隔离环境的单主光源对照约 **272–274 ms**"。
- :77 —— 冷启动上下文代价："首个真实底部相机测试耗时 **3.21 秒**，渲染器首次建立图形上下文时，提前冻结的世界／编码器快照已经超过 2 秒采集预算。"

**(推断)** 把这三个数放在一起，无 GPU 的 sim2real 含义就清楚了：**渲染是采集链路里最慢的一环（约占 85%）**，而"降低分辨率/阴影"这类优化只能把 515 ms 压到 273 ms——仍然远超 100 Hz–1 kHz 的控制量级。这从另一个方向印证了 §8 的分层结论：仿真里的感知回路天生就在 1–4 Hz，不能承担控制频率。

**本机（macOS）走的是另一条路**：`make home-furnished` 跑原生进程 + Metal，Linux 容器跑 ROS 2 Jazzy（`docs/development/rtabmap-navigation.md:37`）。RoboCasa 侧另有分辨率的环境变量旁路：`ROBOCASA_RENDER_WIDTH/HEIGHT` 默认 320×240，注释写明"macOS Metal 渲染在此环境间歇不稳定，高分辨率在用户环境稳定后可调"（`sim/robocasa/tangying_robocasa/fleet_server.py:27-30`）。渲染始终是离屏的：`sim/mujoco/assets/xlerobot_home.xml:12` `<global offwidth="960" offheight="640" />`。

**房间几何（可直接用于课堂算净空）**：房屋外轮廓 **9.0 m × 11.0 m**，墙半厚 **0.08 m**，墙高 **2.4 m**。四个门洞实测：客厅→走廊 **3.0 m**（x ∈ [−1.5, 1.5]）、走廊→厨房 **1.5 m**（x = 0.8，y ∈ [2.6, 4.1]）、走廊→卧室 **1.5 m**（x = −0.8）、卧室→卫生间 **1.2 m**（y = 5.15，x ∈ [−2.65, −1.45]）。房间语义锚点全部 `contype=0 conaffinity=0`——`home_scene.py:40` 的注释写得很清楚："Semantic room anchors are non-colliding visual markers, **not truth poses**"。

> **冲突（陈旧注释，2 处）**：① `sim/mujoco/assets/xlerobot_home.xml:27` 的注释写 "with a **1.6 m** central door opening"，而同一文件 :28-29 的几何实测是 **3.0 m**；② `home_scene.py:1` docstring 与 `:21` 的 `HOME_SCENE_REVISION = "home-4room-rgbd-v2"` 都写"four-room"，而 `:23` 的 `HOME_ROOMS` 是**五个**房间（`docs/guides/home-scene-operations.md:1, 3` 同样写"四房间"却列 5 个）。两处都是**文案陈旧、几何与代码自洽**，但在教学里非常有用：**注释会腐烂，常量不会**——凡是学生要照做的数字，只能从常量或几何推导。

**最小可跑路径（照做）**：

```bash
make home-furnished                 # 控制台 http://127.0.0.1:8897/
bash scripts/sim-stack.sh status --artifacts-dir artifacts/sim-stack/furnished-home
bash scripts/sim-stack.sh stop   --artifacts-dir artifacts/sim-stack/furnished-home
```

---

## 3. 机器人适配层：`RobotProfile → PluginBackend → RobotRuntimeService`

链路在 `docs/development/robot-adapters.md:9-20` 画成一张图：硬件 → 厂商驱动 → `PluginBackend` → `RobotRuntimeService` → gRPC → Go `edge/robotclient` → Edge → Fleet → 控制台 / MCP。

**这三个东西是什么关系：**

- **`RobotProfile`** 是**声明**，不是证据。Go 侧权威结构在 `core/robotcontract/contract.go:41-53`：

```go
type Profile struct {
	SchemaVersion  string                 `json:"schemaVersion"`
	RobotID        string                 `json:"robotId"`
	AdapterID      string                 `json:"adapterId"`
	AdapterVersion string                 `json:"adapterVersion"`
	ModelID        string                 `json:"modelId"`
	Embodiment     string                 `json:"embodiment"`
	Joints         []Joint                `json:"joints"`
	EndEffectors   []EndEffector          `json:"endEffectors"`
	Sensors        []Sensor               `json:"sensors"`
	ActionLimits   map[string]ActionLimit `json:"actionLimits"`
	Tools          []string               `json:"tools"`
}
```

`core/robotcontract/contract.go:1-3` 的包注释就是一句判决："**A profile is a declaration, never evidence of hardware commissioning.**" Python 侧对等实现在 `robot/gateway/tangying_robot_gateway/contracts.py`，Go/Python 双向校验（`Profile.Validate` 在 contract.go:139，`Reconstruction.Validate` 在 :227）。

- **`PluginBackend`** 是"把规范工具绑到已交付驱动上、但不假定它的关节"的适配器。类定义在 `robot/gateway/tangying_robot_gateway/plugin_backend.py:72`，构造签名在 `:81-92`，docstring 写明："Constructing an adapter never connects, arms or moves hardware. Physical tools remain unavailable unless the local readiness callback explicitly reports that the driver has been armed."

- **`RobotRuntimeService`** 是安全边界：审批、限幅、租约、日志、急停。它接受注入的注册服务：`RobotRuntimeService(..., services=...)`（`docs/guides/robot-service-workflow.md:49`）。

**抽象基类只有四个方法**（`robot/gateway/tangying_robot_gateway/backend.py:62-82`）：

```python
class RobotBackend:
    def capabilities(self) -> RuntimeInfo: ...
    def observe(self, request: ObservationRequest) -> Observation: ...
    def execute(self, command: Command) -> Result: ...
    def cancel(self, command_id: str, reason: str) -> bool: ...
    def stop(self, reason: str) -> None: ...
```

**新增一台机器人要做什么（`docs/development/robot-adapters.md:293-297` 的完整清单）：**

1. 本地适配器 Python 包 / factory；
2. 该型号的 profile 配置（`robot.profile.v1`）；
3. 规范感知转换器（`observation_provider()` 必须返回 `scene.reconstruction.v1`）；
4. 工具 handlers；
5. 驱动依赖及测试；
6. 若动作需要策略，增加对应 policy manifest 与推理服务；
7. 部署侧：设备登记、凭据、证书、启动环境和现场验收材料。

**"换机器人不用重写任务与工具"的原始出处**是 `README.md:133` 的一句粗体：

> **换机器人不用重写任务与工具**：新本体只需实现 `RobotProfile → PluginBackend → RobotRuntimeService`，审批、租约、日志、取消、急停全部复用。

落地机制是**能力驱动而非设备驱动**：Agent 只查运行时自报的能力（`edge/runtime/runtime.go:131-164` 的 `Snapshot.Capability` / `CanExecute`），这张能力表由 `profile.tools` 生成（`plugin_backend.py:112-143`），与机型无关；工具词汇表是固定的 13 个规范工具（`robot/gateway/tangying_robot_gateway/contracts.py:25-29`，Go 对等 `core/robotcontract/contract.go:110-115`）。端到端证据是 `tests/contract/test_heterogeneous_runtime_boundary.py:250-278`：让**真实 Go 七步计划**穿过 arm 与 mobile+lidar 两种完全不同的本体，并断言最终 `inside:tray` 关系与位姿。

**但这句话有一条必须一起讲的边界**（这正是"文档与代码冲突"的标准案例）。`docs/development/2026-09-18-system-review-and-improvement-plan.md:346-352` §3.5 标题就叫「可扩展性有硬编码枚举（**与"换机器人不用重写"的声明有差距**）」：

| 要做的事 | 必须改的地方 |
| --- | --- |
| 新增一个工具 | `skills/manipulation/plugin.go:21`、`core/robotcontract/contract.go:110` 与 `:120`（**两个手写白名单**）、新失败码还要加 `core/closedloop/closedloop.go:83` |
| 新增一个机器人本体 | `examples/robots/*.profile.json` + `core/robotcontract/contract.go:135`（`validSource`）、`:139`（`Embodiment` oneOf，硬编码枚举） |
| 新增一种传感器 | `core/observation/envelope.go:20-27` |

原文结论："上一轮'可扩展性落地'（`b3e5729`）把物体物理属性做成了数据，但**白名单和枚举还是代码**。"所以准确的表述是：**"任务与工具不重写"成立**（MCP 工具名、Fleet 任务接口、Go gRPC 客户端、任务层全部复用），但**"新增能力/本体/传感器零代码改动"不成立**。教学时把这两句分开讲，比只引用 README 那句更有价值。

**不需要改的部分**（`docs/development/robot-adapters.md:297`）："在现有工具语义和 schema 能表达需求时，通常不需要修改 MCP 工具名、Fleet 任务接口、Go gRPC 客户端或给前端加入厂商 SDK。"

> **冲突（计数，3 处文档 vs 代码）**：`README.md:144`（"只给 **8** 个粗粒度工具"）、`docs/development/robot-adapters.md:249`（"**8** 个通用工具"）、以及 `robot/mcp/README.md:31-42` 的工具表都只列 **8** 个，而 `robot/mcp/tangying_mcp/server.py:157` 的 `SPECS` 实际是 **9** 个——三处都漏了 `get_survey`（:169-177）。**以代码为准：9 个。**

可复现的无硬件验证：

```bash
.venv/bin/python -m tangying_robot_gateway.run_plugin schema > /tmp/tangying-robot-contracts.json
.venv/bin/python -m tangying_robot_gateway.run_plugin check --profile examples/robots/arm.profile.json
.venv/bin/python -m tangying_robot_gateway.run_plugin check --factory examples.robots.simulated:arm
```

`check --factory` 会真的构造适配器、读一帧、检查来源与新鲜度，然后 stop + disconnect（:50）。**注意仓库里的异构示例被明确标记为 `SIMULATION`**（:5）："它们不模拟物理动力学，也不是 RGB-D、LiDAR、导航或厂商硬件驱动。"

**profile 的不变量**：profile 在一个 Runtime 生命周期内不可变（:89）；`tools` 是承诺的能力集合，`available` 是当下能否用；缺 handler 的工具标为不可用；`physical_ready` 未提供/返回 False/抛异常时物理动作不会自动变可用（:85）。`physical_ready` 是**读状态的回调，禁止在回调里顺便 arm**（:195）。

---

## 4. 标定：参数、自行录入、以及失败关闭

**标定是运行时拥有的文档**，schema `robot.calibration.v1`（`docs/development/robot-calibration.md:1`、`robot/gateway/tangying_robot_gateway/calibration.py:40`）。参考机型的完整参数（doc:13-20、:28-53）：

| 分组 | 总线 | 数量 | 真实参数名 |
| --- | --- | --- | --- |
| 左臂 | `left`（`/dev/tangying-left`） | 6 | `shoulder_pan`、`shoulder_lift`、`elbow_flex`、`wrist_flex`、`wrist_roll`、`gripper` |
| 右臂 | `right`（`/dev/tangying-right`） | 6 | 同名六个；**夹爪是每条臂的第 6 个关节** |
| 头部与底盘 | `shared` | 4 | `head_motor_1`、`head_motor_2`、`base_left_wheel`、`base_right_wheel` |
| 相机 | — | 2 | `head-rgbd`、`base-rgbd` |

**共 16 个电机**（`docs/sim2real/README.md:69` 的检查器要求"锁定硬件全部 16 个电机的合法校准结构"）。权威常量在 `robot/gateway/tangying_robot_gateway/calibration.py`：`ARM_SIDES=("left","right")`（:47）、`ARM_JOINTS`（:48，六个）、`ARM_MOTOR_IDS`（:53-55）、`HEAD_AND_BASE_MOTOR_IDS={"head_motor_1":7,"head_motor_2":8,"base_left_wheel":9,"base_right_wheel":10}`（:56-59）、`MOTOR_IDS`（:61）、`MOTOR_BUS`（:65-67）、`CAMERA_NAMES=("head-rgbd","base-rgbd")`（:45）。

**叶字段名与取值范围（照做时按这个填）**：

| 位置 | 精确字段 | 约束 |
| --- | --- | --- |
| `motors.<name>` | `{"id","drive_mode","homing_offset","range_min","range_max"}`（calibration.py:108，沿用 LeRobot `MotorCalibration`） | `homing_offset` ∈ [−2047, 2047]；`range_min` ∈ [0, 4094]；`range_max` ∈ [1, 4095] 且 `range_min < range_max`；`id` ∈ [1, 253]；`drive_mode` ∈ {0,1}（:190-201） |
| `cameras.<name>` | `{"sourceId","width","height","intrinsics","distortion","extrinsics"}`（:109） | — |
| `…intrinsics` | `{"fx","fy","cx","cy"}`（:110） | `fx,fy ≥ 1.0`；`cx ∈ [0, width−1]`；`cy ∈ [0, height−1]`（:222-225） |
| `…distortion` | `{"model","coefficients"}`，`_DISTORTION_MODELS=("plumb_bob","none")`（:111） | `plumb_bob` 必须 5 个系数；`none` 必须为空（:235-238） |
| `…extrinsics` | `{"parentLink","xyz","rpy"}`（:243） | xyz/rpy 各 3 个数（:256-257） |
| `geometry` | `{"gripper":{"openM","closedM"}}`（:277-279） | — |
| `safety` | `{"maxRelativeTargetDeg","maxActionChunkLength","maxLinearSpeedMPerS","maxAngularSpeedRadPerS"}`（:265） | 边界见 :268-271 |

> **纠正常见误用**：仓库里**不存在** `T_base_camera`、`hand_eye`、或名为 `extrinsic` 的 4×4 矩阵字段。手眼结果落在 `cameras.<name>.extrinsics`（`docs/development/calibration-slam-ros-plan.md:79`）；与之等价、真正在运行链路上流动的是 **`base_from_camera`**——proto `RGBDFrame.base_from_camera`（`proto/robot/v1/robot.proto:148`，16 个 double，row-major 4×4，光学系 → 机器人 base），ROS 2 桥侧 `RawCapture.base_from_camera`（`robot/ros2_ws/src/tangying_navigation/tangying_navigation/contracts.py:142`），计算函数 `base_from_camera(robot_from_base, world_from_camera_link, *, camera_frame_is_optical=False)`（`robot/gateway/tangying_robot_gateway/gazebo_bridge.py:131-154`）。**相机外参一律用光学系（右/下/前）**，MuJoCo 的右/上/后由 `_OPTICAL_FROM_MUJOCO = np.diag([1.0,-1.0,-1.0])` 一次性折算（`sim/mujoco/tangying_sim/calibration.py:35`），所以同一组数字在仿真与实机上含义相同（doc:58）。

**"自行录入"（算法结果录入）怎么落地。** 注册服务表在 `robot/gateway/tangying_robot_gateway/robot_workflow.py:150-152`：

- `calibration.get` —— 当前文档、版本、会话和可用标定方式（只读）；
- `calibration.run` —— 调用**驱动注册的**标定算法（`mutates_world=True`）；
- `calibration.save` —— "验证并应用自行标定结果"，参数 schema 必需 `["document","expectedRevision"]`，`mutates_world=True`（:152）。

handler 在 `robot_workflow.py:178` 的 `save_calibration(self, parameters)`：`expectedRevision` 为空直接抛 `REVISION_REQUIRED`（"先读取当前标定，再保存结果。"），`algorithm` 说明限 500 字，保存成功后立刻 `self._invalidate_calibration(result["revision"])` 作废在用地图。**两个入口是同一份代码**：`docs/guides/robot-service-workflow.md:13-14` 写"支持电机表格、相机内外参、整机几何、安全限值与完整 JSON；用户可用自己的算法生成结果再导入"，且"保存必须带读取时的版本"。

**而且仓库里真的有解算算法**（这一点容易被误读成"只有个录入框"）：`robot/gateway/tangying_robot_gateway/calibration_solver.py` 的 `solve_intrinsics(views)`（Zhang 标定法，:387）与 `solve_handeye(views, *, intrinsics=None, motors=None, parent_link=None)`（AX=XB，:695-698），适配器 `IntrinsicsSolver`（:803）。**只用 numpy/scipy，不依赖 OpenCV**（:20-22）。向导把解算结果写回会话：`CalibrationWizard._solve()`（`calibration_wizard.py:781-814`）里 `entry["intrinsics"] = dict(intrinsics)`（:793）、`entry["extrinsics"] = dict(extrinsics)`（:813）。要求的最小视角数：`INTRINSICS_VIEWS = 12`（:86）、`HANDEYE_POSES = 8`（:91）；解算器自身的拒绝阈值 `MINIMUM_INTRINSICS_VIEWS = 3`（`calibration_solver.py:94`）、`MINIMUM_HANDEYE_VIEWS = 3`（:464）、`MAXIMUM_HANDEYE_RESIDUAL_PX = 3.0`（:481）。

**落盘是校验 → CAS → 原子写**（`calibration.py:356-386`）：`tempfile.mkstemp(dir=..., prefix=f".{file.name}.", suffix=".tmp")`（:376）→ `os.fsync`（:381）→ `os.replace(temporary, file)`（:382）。写文件的**只有** `save_calibration()`；调用点两处——CLI `scripts/calibrate_guided.py:147`，仿真后端 `SimulationCalibration.replace()` → `save_calibration(self.path, candidate, expected_revision=...)`（`sim/mujoco/tangying_sim/calibration.py:391-392`）。

**磁盘路径（仿真与实机不同名，别填错）**：

| 场景 | 文件名 | 根目录 |
| --- | --- | --- |
| 仿真 | `calibration.json`（`sim/mujoco/tangying_sim/calibration.py:41`） | `TANGYING_SIM_CALIBRATION_DIR` 或 `--calibration-dir`（`sim/mujoco/tangying_sim/server.py:673`）；演示默认 `artifacts/calibration/furnished-home`（`scripts/furnished-home-demo.sh:9`） |
| 实机 XLeRobot | `tangying-xlerobot.json`（`robot/ros2_ws/src/xlerobot_adapter/xlerobot_adapter/driver.py:20`、`:74`） | `XLEROBOT_CALIBRATION_ROOT` → `XLEROBOT_CALIBRATION` → `/var/lib/tangying-robot-agent-os/calibration`（`scripts/calibrate_xlerobot.py:28-32`） |

`root=None` 时仿真标定**只在内存、不落盘**（`sim/mujoco/tangying_sim/calibration.py:103-105`）。

**版本校验 = 内容哈希 + compare-and-swap。** `CONTENT_FIELDS = ("schemaVersion","robotId","adapterId","source","motors","cameras","geometry","safety")`（calibration.py:105-106）——**`updatedAtUnixMs` 刻意不进哈希**，注释写明是"who/when"的元数据，"re-saving identical numbers keeps the same identity"。`canonical_content()`（:329-332）排序紧凑序列化后取 SHA-256（:335-341）。CAS 在 `save_calibration` 里比对 `expected_revision` 与 `calibration_revision(current)`，不符抛 `REVISION_CONFLICT`（:366-373）。

**标定没做/与模型矛盾时：失败关闭，而且分四个层级。**

1. **能力层（缺失）**：`driver.py:104-122`——

```python
if not self.path_exists(self.calibration_file):
    blockers.append("CALIBRATION_REQUIRED")
...
return DriverCapabilities(manipulation_ready=not blockers, blockers=tuple(blockers), ...)
```

`driver.py:151-153` 进一步 `if not capabilities.manipulation_ready: return DriverResult(False,"ROBOT_NOT_READY",...)`——**不下发任何动作**。另有 `CALIBRATION_INVALID`（:137,143）、`CALIBRATION_EMPTY`（:139）。这些 blocker 经 `xlerobot_backend.py:138-146` 汇入 capability 视图；`core/closedloop/closedloop.go:134` 把 `CALIBRATION_REQUIRED` 列为阻断码，`tool_layer.py:101` 把它映射为 `PERMISSION_DENIED` / `RecoveryClass.PERMISSION`（禁止自动重试）。**物理工具因此不可用**——不是"能用但提醒你"。

2. **模型一致性层（矛盾）**：`sim/mujoco/tangying_sim/calibration.py` 的 `_verify_against_model`（:129-169）在**构造期**就拒绝与所渲染模型矛盾的文档，抛 `DOCUMENT_CONTRADICTS_MODEL`：镜头不符（:189）、parentLink 错（:203）、安装位移 > **5 mm**（`MODEL_MOUNT_POSITION_TOLERANCE_M`，:215）、安装旋转 > **2°**（`MODEL_MOUNT_ROTATION_TOLERANCE_DEG`，:224）、焦距超 2%（`MODEL_LENS_TOLERANCE`，:181-182）。无舵机条目时无法把读数换成角度 → `arm_kinematics.py:445` 的 `MOTOR_CALIBRATION_MISSING`（"无法把读数换算成角度"）与 :452 的 `MOTOR_CALIBRATION_INVALID`。工位不符则在派发前拒绝：`sim/mujoco/tangying_sim/rgbd_runtime.py:687-693` 的 `Fault(code="WORKCELL_CALIBRATION_MISMATCH", severity="blocked", remedy="operator_assist")`。

3. **地图层（变化）**：标定一变，已启用地图立即失效。`robot_workflow.py:1376` 加载地图时比对 `calibrationRevision`，不匹配抛 `LOCALIZATION_REQUIRED`；扫描中若标定被改，:368-369 抛 `CALIBRATION_CHANGED`（"标定发生变化，请重新扫描。"）。地图 manifest 侧是精确匹配：`map_manifest.py:235-237` 的 `INVALID_HASH`（"calibrationRevision must be a 64 character content hash"）、:258-260 的 `HASH_MISMATCH`、`map_catalog.py:64-68` 的 `ValueError('map, robot, frame or calibration revision mismatch')`。

4. **契约层（处处失败关闭）**：`contracts.py:214-216` `ValueError("reconstruction sensor frame/type/calibration does not match profile")`；`ros_rgbd.py:231-236` `ValueError("TF identity, capture time or calibration revision does not match")`；`core/robotcontract/contract.go:234-237`；策略层 `edge/policy/manifest.go:176-177` `ErrPolicyIncompatible`。

> **重要纠正："标定过期"在本仓库不存在。** 全仓 grep 不到 `CALIBRATION_STALE` 或 `CALIBRATION_EXPIRED`；标定文档**没有失效时间**，唯一的 `updatedAtUnixMs` 只校验 `minimum=0` 且不进哈希（calibration.py:325、:105-106）。本子系统里的"过期"一律指**观测陈旧或租约过期**，不是标定失效：`contracts.py:218-221` `"reconstruction is stale or dated in the future"`（未来容差 250 ms + `max_age_ms`）、`core/robotcontract/contract.go:238-241`、`robot/gateway/tangying_robot_gateway/rgbd.py:84` `"RGB-D capture is stale or future dated"`（`DEFAULT_MAX_AGE_MS = 2000`，:50）。**所以正确的说法是"标定缺失或与模型/地图不一致时失败关闭"，而不是"标定过期"。**

**ground-truth 自检：这是本节最值得讲的教学设计。** 两个测试文件问的是**两个不同的问题**：

- `sim/mujoco/tests/test_calibration_ground_truth.py` 问"标定是否描述了渲染器**真正用的那台相机**"。文件 docstring（:1-12）指出：其他 RGB-D 测试只验证"自洽"（帧内变换下重投影一致），而**自洽在变换把相机指向完全错误方向时依然成立**。真值函数 `true_world_from_camera(model, name, tilt, pan)`（:41-55）直接设 `head_tilt_joint`/`head_pan_joint` 的 qpos → `mujoco.mj_forward` → 读 `data.cam_xmat[camera_id] @ _OPTICAL_FROM_MUJOCO` 与 `data.cam_xpos[camera_id]`——**ground truth 就是 MuJoCo 本身**。容差极紧：位置 `max|Δ| < 1e-9`（:95, :120）、旋转 `< 0.01°`（:58-61）、内参 `abs=1e-9`（:133-135）。
- `sim/mujoco/tests/test_calibration_self_check.py` 问"栈能不能启动在一份与它所渲染模型矛盾的标定上"——要求拒绝发生在**普通构造路径**里（:1-13），断言 `pytest.raises(CalibrationError)` + `code == "DOCUMENT_CONTRADICTS_MODEL"`，且**消息必须点名具体相机与偏差量**（含 `"base-rgbd"`、`"mm"`、`"chassis"`、`"fx"`）。

**最漂亮的一条是"守卫之守卫"**（`test_calibration_self_check.py:203-204`）：

```python
assert MODEL_MOUNT_ROTATION_TOLERANCE_DEG < 120.543
assert MODEL_CAMERA_FOR["head-rgbd"] == "head_depth"
```

理由：容差若宽于它所防的那个缺陷（存错 120.543° 的旋转），整个测试文件就会变成**永远通过**。**这条断言是在测试"测试本身是否还有意义"**——值得作为本章的收尾案例。

**尚未实现（不要过度声称）**：ROS 侧标定封装（`camera_calibration`/`easy_handeye2`/`aruco_ros`）、各精度门禁的自动判定（重投影 RMS < 0.5 px、手眼 < 5 mm / < 0.5°、里程计 < 2%）、**相机引导标定**、以及全部实机现场验收（`docs/development/calibration-slam-ros-plan.md:85-87`、`docs/development/robot-calibration.md:114-118`）。引导向导目前**只采舵机**，没有相机参数时在**开始前**就拒绝（`scripts/calibrate_guided.py:83-85`）——共 **20 步**（左右臂各 6 步归零 + 各 1 步扫范围 + 头/底盘 4 步 + 检查保存 1 步，doc:97-104）。`docs/development/arm-moveit-adapter.md:409` 亦承认手眼标定在 MoveIt 适配里未验证，`frame_id != "base_link"` 直接 `IK_UNAVAILABLE`（:155-157）。

---

## 5. SLAM 与导航：两条路线、地图持久化、两个身份字段

**两条路线并存，不是新旧替代。**

| | 自研 RGB-D 稠密 SLAM | RTAB-Map + Nav2 |
| --- | --- | --- |
| 算法名 | `planar-rgbd-icp-posegraph-v1` | RTAB-Map（视觉词典 + 位姿图）+ Nav2（NavFn 全局 / DWB 局部） |
| 输入 | 实际采集的米制深度、相机到基座外参、同帧底盘位姿 | 双 RGB-D 的 Image/CameraInfo/TF + 轮式 `odom` |
| 产出 | 平面 ICP、里程计约束、闭环匹配、稀疏位姿图优化 | `map → odom`、占据栅格 |
| 定位字段 | `poseSource = "registered_localization"` | `poseSource = "rtabmap_tf"` |
| 适用 | 平整室内底盘运动 | 坡道、楼层变换和更复杂环境 |
| 依赖 | 纯 Python，无 Docker | **需要 Docker**（README.md:186） |

出处：算法名与能力边界在 `docs/guides/robot-service-workflow.md:70`；"坡道、楼层变换和更复杂环境应注册 RTAB-Map 等其他提供者"是同一句。RTAB-Map 路线的启动：

```bash
make navigation-restart NAVIGATION_ARGS='--build --mode mapping --scene home'
make navigation-status
```

**什么时候用哪条**：需要**建图 + 保存 + 离线查看 + 语义工作区**时用注册服务路线（工作台默认走这条，`README.md:182`："OS 不按仿真或实机选择实现"）；需要**真实定位/避障/跨房间导航**时用 RTAB-Map + Nav2。两条不是二选一到底——`rtabmap_client.py` 让 MuJoCo 场景也能吃 RTAB-Map 的 `map → odom`。

**地图持久化三处**：

- 注册服务路线：`artifacts/maps/<mapId>/`，可用 `TANGYING_MAP_ROOT` 覆盖（`docs/guides/robot-service-workflow.md:30`）；
- RTAB-Map：容器卷 `/data/maps/home/rtabmap.db`（`README.md:193`）；
- 活动地图指针：`active-map.json`（`robot_workflow.py:1337-1339`），内容为 `{mapId, mapRevision, calibrationRevision}`。

**`mapRevision` 是什么**：地图包的 `manifest["hash"]`——对 manifest 全部内容（不含 `hash` 字段本身）做 SHA-256（`robot/gateway/tangying_robot_gateway/map_manifest.py:264-270`；使用点 `map_catalog.py:100`、`robot_workflow.py:1338`）。**它是内容身份，不是时间戳**，所以同一份地图重新发布两次得到同一个 `mapRevision`。

**`poseSource` 是什么**：位姿的**来源声明**，取值至少三种——`rtabmap_tf`（`navigation_node.py:325`、`contracts.py:65` 硬性要求）、`registered_localization`（`robot_workflow.py:1408`）、以及测试里出现的 `simulator_ground_truth`（`sim/mujoco/tests/test_rtabmap_client.py:133`）。它的作用是**禁止拿新鲜时间当作可信变换的证明**：`rtabmap_client.py:49-50` 把"`poseSource == "rtabmap_tf"` 且 `mapRevision` 非空"作为就绪的判据。

地图与点云的**对账**是这一层最实际的一课：一次四段从零探索实测 6494 个占用格里有 **1491 个（23%）**在发布的点云里已经没有障碍高度的点，其中包括卡住厨房目标位姿的那两格。发布时因此做对账：占用格没有点云支撑时，同格测到地面就降为自由，什么都没有就退回未知。同一张地图：占用 6494→5003，自由 24994→25722，未知 8112→8875（`docs/guides/robot-service-workflow.md:74`）。

---

## 6. 探索与覆盖率：真实数字

### 6.1 38.9% → 81%（2026-09-15，MuJoCo 装修家居）

**度量口径先定清**（否则后面的数字会误导）：这里的"已探明"= `1 − unknownFraction`，分母是**调查自己栅格的全部格子**（含墙外、柜内、相机盲区），不是房间覆盖率、也不是可达面积比——代码在 `exploration.py:629-636`，打印在 `scripts/build_sim_map.py:88`。它和 §6.3 的评测台口径**不可比**，详见 §6.5。

**实验设计**：同一台机器人、同一个装修家庭场景（`scene=home_task`）、同一套探索入口（`--mode explore`），只差四处改动（`docs/experiments/2026-09-15-slam-exploration-coverage-upgrade.md:56`）。

| 指标 | 改前（22 m / 1 段） | 改后（60 m / 3 段） |
| --- | --- | --- |
| 已探明（整图） | **38.9%** | **81%** |
| 整图未知格占比 | 61% | **19%** |
| 行驶里程 | 22.2 m | 53.7 m |
| 进入卫生间 | **0% 轨迹点**（74% 未知） | **11% 轨迹点**（20% 未知） |
| 走廊 / 厨房未知 | 0% / 11% | 0% / **0%** |
| 卧室未知 | 37% | 20% |
| 客厅未知 | 57% | 25% |
| 关键帧 | 未记录（旧上限 400） | **263 / 600**（预览 3.0 MB） |
| 深度点不足 | **整段崩溃，地图丢失** | 跳过并继续，正常发布 |

**四处改动，都在参数与选择策略上**（doc:45-50，代码位置已核对）：

1. `robot/gateway/tangying_robot_gateway/exploration.py:45` 新增 `FAR_REGION_FACTOR = 2.5` 与 `Frontier.region_gain`（:306）；判定在 :620-626：远处连通块必须**明显更大**（≥2.5 倍）才值得开过去。doc:47 特别记下第一版按"效用/米"写是错的——**效用除以距离永远偏向近处**，被测试抓出来。
2. `robot_workflow.py:44` `DEFAULT_EXPLORE_TRAVEL_M` 40 → **75.0**（全屋一轮 50–60 m）。
3. `slam_keyframes.py:22` `MAX_KEYFRAMES` 400 → **600**；关键帧间距 0.10 m/0.16 rad → **0.14 m/0.22 rad**；预览字节预算 8→12 MB、产物 12→16 MB。
4. `robot_workflow.py:366-380` 深度点不足（<100 点）从"抛异常终止整段"改为**跳过该帧并计数**；连续 25 帧才优雅结束该段（`stopReason="depth_starved"`）并发布已测绘部分。注释里写明实测动因：一次对着近墙的空白视野让一段探索在 **74%** 处崩掉，**地图完全没保存**。

**"门不是瓶颈"是怎么得出的**（doc:14-25）：先**直接从 MuJoCo 模型量墙体几何**：`wall_living_west/east` 之间是 x ∈ [−1.5, 1.5]，`wall_corridor_kitchen_*` 之间是 y ∈ [2.6, 4.1]，`wall_bed_bath*` 之间是 x ∈ [−2.655, −1.445]。再数**旧地图 `scan-83a05e99e627` 上每条门线的通行格子**：

| 门 | 自由格 | 占用格 | 未知格 |
| --- | --- | --- | --- |
| 客厅 → 走廊（y=1.0） | 61 | 16 | 3 |
| 走廊 → 厨房（x=0.8） | 28 | 22 | 0 |
| 走廊 → 卧室（x=−0.8） | 48 | 0 | 2 |
| 卧室 → 卫生间（y=5.15） | 20 | 1 | **29（58% 未知）** |

前三个门都有实打实的通道；只有卫生间门线一半没被观测到——**那不是"门挡住了"，是机器人根本没走到能看见它的地方**。关键对照是：客厅门 3.0 m、厨房门 1.5 m、卧室门 1.5 m、卫生间门 1.21 m，走廊净宽 1.55 m，而**底盘包络只需约 0.7 m**（doc:3）。真正的瓶颈是**目标选择偏爱近处**：同一段 22.3 m 里，走廊（已经 0% 未知）占 28% 轨迹点、厨房（11% 未知）占 26%，而最远的卫生间 **0%**（doc:29-39）。

**最有力的一条对照**：改前 22.2 m 时已探明 38.9%；改后 **21.2 m 时已探明 66%**——同样里程多探明 27 个百分点，说明起作用的是**选择策略**而不只是预算变大（doc:70）。

> **诚实记录一处叙述不一致**：81% 那一次的 summary/manifest 里**没有 `stopReason` 字段**，所以"它是探完了才停"这句话没有直接证据；仓库另一处出处写明那次是"跑到评测脚本 30 分钟上限才停，**不是'没得探了'**"（`artifacts/marketing/01-总体架构/素材来源与数字出处.md:67`，同处给出 `unknownFraction = 0.1906`），而 2026-09-15 文档强调的是"真正的停止判据仍然是没有可达的未知边界"（doc:80-82）。**两处叙述不一致，引用"81% 代表探完"时要谨慎**；`stopReason` 的取值枚举本身是清楚的（`complete` / `no_reachable_frontier` / `travel_budget` / `frame_budget`，`docs/guides/robot-service-workflow.md:173`）。

### 6.2 99% 与 62%：一次被数据推翻的优化方向（2026-09-19）

`docs/experiments/2026-09-19-slam-coverage-investigation.md` 的结论与 §6.1 并不矛盾，而是把问题推深了一层：**探索算法不是瓶颈。**

- 新建闭环评测台 `scripts/exploration_survey_benchmark.py`：真值由 MuJoCo 几何直接栅格化（`build_truth`），指标是 `mapped / coverable`——分母**不是"所有格子"**，而是"某个合法位姿能看到的真值格"的并集（:18-23）。修掉两个真值坑后：**可达地面 12043 格，任何策略的上限 13916 格**（:31）。
- 基线结果：`覆盖 99.0% 行程 61.6 m 23 段 23 次决策`（:38）。"在理想执行下，frontier 策略几乎不会漏掉任何可覆盖的空间"（:43）。
- 真实地图只有 **62%**，缺口在**建图层**：`map_pipeline.py` 的 `occupancy_from_points`（v0.6.0 实际在 `:273`，docstring 的 "honest about ignorance" 在 `:282`；实验文档写的是 `:260`，属版本行漂移）只有落进某格的 3-D 点才会让那格变已知，**没有射线投射**（实验文档 :72-78）。
- 在真实失败栅格上量化：`真实地图 218x178，未知 14712 格（37.9%）`；"从轨迹上的姿态能看到的格子 25011，其中仍然未知的 6511 格 = 真实未知的 44.3%";按"射线经过即自由"填上，可知率可从 **62.1% 升到 78.9%**，**零额外行程**（:84-91）。
- 对照被测试挡回的三处探索层改动（视点按可见性排序、簇上限 12→32、gain 语义改动）净收益为 0 或触发"房间测完还在跑"（:106-120）。唯一发布的探索层改动是**视点搜索 0.6 m → 传感器可达距离**（k-d 树替代 Python 循环）：**+2.4%，决策耗时 39 ms → 24 ms**（:124）。
- 结论（:130-136）：**现阶段不要动探索算法**；探索层头部空间不到 1 个百分点；最大单项收益在建图层 +16.8 pp。

**方法论上的诚实记录同样值得教学**：`density=0.3` 得到 1.6%、`density=0.15` 得到 0.9%，作者判定"**这不是发现，是我的建模错误**"——单帧伯努利模型让初始地图碎到 `min_frontier_cells=8` 以下，`explore_target` 第一次就返回 `None`，机器人一步没走（:61-66）。

### 6.3 Gazebo：79.3% → 99.5%，以及差距到底在哪（2026-09-20）

`docs/experiments/2026-09-20-gazebo-exploration-coverage.md` 的结论一句话（:5）："Gazebo 现在是**可驱动的后端**……同一策略在 MuJoCo 户型上覆盖 99.0%，在 Gazebo 户型上只有 **79.3%**，而差距**不在策略**。"

- 交付账（:11-20）：托管 **14 个建图服务**；一句话探索建图跑通；修掉 **11 个** Gazebo 暴露的 bug；**Gazebo 79.3% → 99.5%（+20.2 pp）**，MuJoCo 99.0% 不变且行程 61.6 → **53.8 m**（少走 13%）；拒绝鲁棒性 92.0%±11.2 → 98.4%±0.7。
- **先证伪"策略有缺陷"，再找根因——这是本章最值得抄的实验方法。** 把"漏掉的 coverable 格"拆成两类（:390-397）：MuJoCo 漏 183 格、其中"**仍是前沿**"者 **0**；Gazebo 漏 5466 格、其中"仍是前沿"者 **0**。**两个户型里策略都没有漏掉任何一个前沿**——那 5466 格是"从某个可达位姿看得见、但机器人从未走到它旁边"的地方。这一步排除了策略层，才让搜索范围收敛到参数。
- **真正把覆盖率提上去的是"把前沿片段下限从格数改成面积"**（§4.5，"**这是本轮真正把覆盖率提上去的那一处**"）：`minFrontierCells = 8` 是**格数**——同样的 8，在调查实际构建的 5 cm 栅格上是 **0.02 m²**，在 1 m 栅格上是 **8 m²**。"**一个格数在不同的地方意味着不同的物理尺寸，这正是一个只在一个户型上调出来的常数会在另一个户型上错二十个点的原因。**"走廊户型沿墙会产生许多小碎片长毛边，而目标选择是距离优先，于是每步都挑最近的碎片，把走廊扫了一遍却从不穿过门洞进房间。改成面积后（`MIN_FRONTIER_AREA_M2 = 0.05`，`exploration.py:67`；换算 `floor_cells = max(1, round(min_frontier_area_m2 / resolution²))` 在 **:510**）两个户型的扫描如下：

| 前沿片段下限（面积 m²） | 0.02 | 0.03 | 0.04 | **0.05** | 0.06 | 0.08 | 0.12 | 0.16 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **Gazebo 四室** | 79.3% | 99.4% | 99.5% | **99.5%** | 99.1% | 99.1% | 98.7% | 74.0% |
| **MuJoCo 装修家庭** | 99.0% | 98.3% | 98.8% | **99.0%** | 99.0% | 98.8% | 85.0% | 71.6% |

  取 0.05 的理由不是"它得分最高"，而是**两个户型在 0.03–0.08 之间都在 98.8% 以上——0.05 落在一个平台上，不是一个碰巧得分的点**（:461）。
- **现场地图 73.1% vs 评测台 99.5% 是同分母对比**（§4补）：现场自报已探明 72.1%（分母是自己栅格的 60,522 格），与评测台同分母是 **73.1%（18861 / 25801 coverable）**，"两个分母在这里几乎相等，所以 26 个点的差距不是度量假象，是真实的"（:565-571）。
- **根因不是 SLAM 算法，是里程计**：地图包 `scan-b47a762ae586` 的配准账本——`registration attempts: 272 / accepted: 1`，失败分类 `no_overlap 125 (46%)`、`correction_too_large 52 (19%)`、`too_flat_or_few_normals 43 (16%)`、`weak_loop 38 (14%)`。"**112 个关键帧、1 次配准、0 次回环。**这张图本质上是**里程计加一坨点云**，不是 SLAM"（:576-588）。自证项还显示 **4573 格被标成自由而真值是障碍**（反向 754 格）（:572）。
- **三个阴性结果**（同样重要）：前沿簇预算 12→24→48→96 **逐位相同**（:486-495）；"更聪明的盲区模型" `visibility` 与 `disc` **逐位相同**，因为盲区 1446 格中只有 **40 格（2.8%）**能被更远的航迹位姿看见（:502-509）；砍掉原地扫视 `maxLooksPerLeg: 6 → 1` 反而让同分母覆盖率从 **73.1% 掉到 66.7%**（:599-609）。
> **注意区分"修好的"与"仍未修的"**：上表 99.5% 是**改前沿下限**拿到的，与盲区半径无关。盲区半径是**另一件事，本轮没有改生产常量**。
>
> **留下的真问题**：`blindRadiusM = 1.0` 的几何只支持约 **0.5 m**（相机高 0.16 m、俯角 15°：MuJoCo 最低视线 44° → 看见地板最短距离 0.17 m，机身前 ≈0.47 m；Gazebo 修后 41.4° → 0.18 m，≈0.54 m）。扫描（:426-438）：**1.0 m → 99.0% / 79.3%；0.7 m → 31.9% / 99.9%；0.45 m → 30.7% / 99.7%；0.25 m → — / 17.0%**（前者 MuJoCo、后者 Gazebo）。两个户型需要**相反**的值，因为常数在同时干两件事：建模相机盲区 + 阻止机器人追自己的脚印。**"一个常数承担两个职责，就必然在其中一个户型上做错"**（:518-541）。

### 6.4 一个可复现的文档/代码冲突（实测，适合当课堂练习）

`docs/experiments/2026-09-19-slam-coverage-investigation.md:154` 让学生跑：

```bash
python scripts/exploration_coverage_benchmark.py
```

**这个脚本今天会在最后一步崩掉。** 实跑输出（已在本工作区复现）：

```
地图 artifacts/maps/furnished-home/scan-6974b8f937e9/grid
  尺寸 (218, 178) 分辨率 0.05 m 未知占比 37.9% 规划余量 0.32 m 传感器 3.0 m
frontier 簇 139 个，其中 >= 8 格的 12 个进入候选（上限 12）
  0.6 m 规则（旧）:  44 簇可行动 /  2772 格已知边界
  传感器可达（新）:  51 簇可行动 /  2847 格已知边界
  差异: +75 格，占全部 frontier 的 2.4%
TypeError: explore_target() got an unexpected keyword argument 'min_frontier_cells'
```

原因：`scripts/exploration_coverage_benchmark.py:141` 仍以 `min_frontier_cells=` 调用 `explore_target`，而现行签名（`exploration.py:454-457`）只有 `min_frontier_area_m2`——**正是 §6.3 那次"格数改面积"重构留下的未更新调用方**。

**这个崩溃反而是一份好教材**：① 它**顺带复现了两个关键数字**（`scan-6974b8f937e9` 的 218×178 / 37.9% / 139 簇 / 12 候选，以及视点搜索 0.6 m → 传感器可达的 **+2.4%**，44 簇→51 簇、2772→2847 格）；② 它示范了**重构改了签名却没改所有调用方**这一类真实缺陷，而且**测试没抓住它，因为被测试覆盖的是 `exploration.py` 而不是这个一次性脚本**；③ 它说明"文档里的复现命令"也是需要维护的资产。

### 6.5 引用覆盖率数字时必须带口径

仓库里"覆盖率"有两个**不可比**的分母，跨文档引用时必须写明用的是哪一个：

| 口径 | 公式 | 出处 | 典型值 |
| --- | --- | --- | --- |
| 调查自报"已探明" | `1 − unknownFraction`，分母是**调查自己栅格的全部格子**（含墙外、墙内、家具背后任何位姿都看不见的地板） | `exploration.py:629-636`；`survey.py:281, 300` 逐步上报 | 38.9% / 81% / 62.1% / 79.2% |
| 评测台覆盖 | `mapped / coverable`，分母是**某个合法可达位姿真正能看见的地板** | `scripts/exploration_survey_benchmark.py`（`coverage = 100*mapped/denominator`，`coverable = ceiling & (reachable\|occupied)`） | 99.0% / 99.5% |

仓库自己已经声明过这一点（`docs/development/2026-09-21-map-visibility-and-inventory.md:190-193`、`2026-09-20-gazebo-exploration-coverage.md` §4补）。另有第三个口径用于**房间级**记账：`mapping_coverage.coverage_report()` 的 `overall = free/(free+unknown)`，配 `MIN_COVERAGE_RATIO = 0.85`（`mapping_coverage.py:25, 235`）。**(推断)** 学生最容易犯的错就是把 73.1% 和 99.5% 相减得出"建图有 26 个点的 bug"——正确的第一步是 `scripts/score_live_map.py`，把现场地图投影回真值坐标系、换成同一个分母再比。

**复现**（照做，注意第 0 步）：

```bash
# 0. 清干净上一次的 SLAM 状态：RTAB-Map 数据库跨 run 累积，不清就不是同一个实验
docker exec tangying-gazebo-house-gazebo-house-1 rm -rf \
  /data/maps/gazebo_house/workflow/scan-* /data/maps/gazebo_house/workflow/active-map.json \
  /data/maps/gazebo_house/rtabmap.db*
docker restart tangying-gazebo-house-gazebo-house-1
make gazebo-house-start                        # 或 scripts/gazebo-house-stack.sh start
.venv/bin/python scripts/exploration_survey_benchmark.py --truth mujoco
.venv/bin/python scripts/exploration_survey_benchmark.py --truth gazebo
.venv/bin/python scripts/exploration_survey_benchmark.py --truth gazebo --blind-radius 0.7
```

---

## 7. 导航安全门禁：为什么"去厨房失败"是设计

**配置源码**：`robot/ros2_ws/src/tangying_navigation/config/nav2.yaml:24`

```yaml
    GridBased:
      plugin: nav2_navfn_planner::NavfnPlanner
      tolerance: 0.01
      use_astar: true
      allow_unknown: false
```

同文件里 `global_costmap` 的 `track_unknown_space: true`（:209），`local_costmap` 也是 `true`（:131），且有测试**明确钉住**两者都是 `True`（`test/test_launch_config.py:20`、:21）。

**`NAV_FAILED NAV2_ACTION_ENDED` 的真实含义**：`NAV_FAILED` 是**平台侧**的结果码——`sim/mujoco/tangying_sim/rtabmap_client.py` 在导航失败时返回它（测试 `test_rtabmap_client.py:91`）；`NAV2_ACTION_ENDED` 是 **Nav2 action 的终态**，由导航节点在非 `SUCCEEDED` 时填入：`robot/ros2_ws/src/tangying_navigation/tangying_navigation/navigation_node.py:421`——

```python
goal_id, state, "" if state == "SUCCEEDED" else "NAV2_ACTION_ENDED",
```

所以这行日志读作："**平台判定导航失败；原因是 Nav2 action 结束了（非成功）**"。

**实测记录**（`docs/guides/home-scene-operations.md:117`）：`ready=true`、`mode=mapping`、`mapRevision=e9726707…`、`poseSource=rtabmap_tf`；客厅 `navigation.navigate` 与 `verify_arrival` 均 CONFIRMED，随后客厅→厨房的 `navigation.navigate` 以 `NAV_FAILED NAV2_ACTION_ENDED` 结束，规划器报 `GridBased plugin failed to plan from (-0.00, -1.25) to (2.20, 3.35)`。同一文件 :115 给出当时的定位状态 `MAPPING_ODOMETRY`、`currentFrameWords=159`、`dictionaryWords=112`、`knownCells=6589`。

**为什么这是刻意设计**：`README.md:195` 写得很直白——

> **建图阶段的行为是刻意设计的安全门禁**：全局代价地图的 `allow_unknown=false`，Nav2 拒绝规划穿过尚未建图的区域。所以冷启动后只能导航到已覆盖范围（例如客厅），去厨房会以 `NAV_FAILED NAV2_ACTION_ENDED` 结束——这不是缺陷，而是"不知道的地方不进去"。

`README.md:195` 的同一段在 `README.md:197-199` 给出正确顺序（该结论此前也在 `CHANGELOG.md` 里被定性为"属安全门禁而非回归"；**注意 CHANGELOG.md 在本会话期间被并发改写，行号不可引用，见文末「关于引用稳定性」**）：**建图 → 定位复用同一张地图 → 需要厨房物体时用 `--scene home_task`**。

**一条容易搞错的细节**：Gazebo 场景下**局部**代价地图的 `track_unknown_space` 被改成 `false`，但**全局保持 `true`**，且改动被**限定在 `scene == "gazebo_house"`**（`robot/ros2_ws/src/tangying_navigation/launch/navigation.launch.py:53-66`）。代码注释给了理由与边界："滚动窗口局部代价地图的职责是表达**已感知的障碍**……全局代价地图保持默认，因为'不要穿过未建图区域'这条规则属于那里……**这是限定在它被测量过的场景里，而不是凭一个仿真器的测量提升为共享默认值**"（:54-63）。改前实测：**25,600 格里 21,250 格未知（83%）**，DWB 找不到合法轨迹，每个目标都以 `NAV2_ACTION_ENDED` 结束、机器人 15 秒不动（:57-59）。同一场景里 nav2 足迹也从 0.46×0.44 m 改为世界的真实 **0.655×0.67 m**（:67-81）——"the configured footprint missed 0.10 m of wheel on each side"。

**这条门禁与"返程失败"是同一件事的两面**：README.md:151 记录参考地图上返程点未认证可通行，`navigation.navigate` 以 `GOAL_NOT_CLEAR` 结束——"这是'不知道的地方不进去'，不是缺陷，但**在这一版上返程确实没走通**"。

---

## 8. Policy 与低层动作：边界在哪、怎么落地

**`policy/sidecar/` 是什么**：一个独立的 **HTTP 推理进程**，只做"观测 → 有界关节动作块"，**没有执行权**。三个文件，共 352 行：

- `contracts.py`（192 行）：全部 pydantic 契约；
- `providers.py`（46 行）：`PolicyProvider` Protocol 与 `CallableProvider`；
- `server.py`（102 行）：`ThreadingHTTPServer`，默认 `127.0.0.1:8091`。

**API 契约**（`server.py:26-52`）：`GET /healthz` → `{"status":"ok"}`；`GET /v1/manifest` → manifest；`POST /v1/infer` → 推理。错误码是**有界的**：`NOT_FOUND`、`CONTENT_LENGTH_REQUIRED`、`REQUEST_TOO_LARGE`、`POLICY_REQUEST_REJECTED`（422，校验失败）、`POLICY_PROVIDER_FAILED`（503，provider 内部异常不外泄）。

**核心契约**（`contracts.py`）：

- `PolicyManifest`（:38-55）：`schemaVersion="policy.manifest.v1"`、`policyId`、`version`、**`framework ∈ {vla, imitation, reinforcement, deterministic}`**（:42）、`artifactSha256`、`capabilities`、`robotModels`、`adapters`、`observationSchema="policy.observation.v1"`、`requiredObservationSources`、`maxObservationAgeMs`、`actionSchema`、`maxActionChunkLength`、`actionBounds`、`transformRevision`、`calibrationRevision`、`training`。
- **模型身份是强制的**（:57-65）：非 deterministic 必须是 **64 位十六进制 SHA-256**；deterministic 必须以 `deterministic:` 开头。`manifest.revision()` 是对规范化 JSON 的 SHA-256（:67-77），**Go 侧用同一算法**（`edge/policy/manifest.go:128-149` 注释明写"Hash a key-sorted JSON object rather than Go struct field order so a Python policy sidecar can reproduce the same content identity"）。
- `InferenceRequest`（:126-141）：`requestId`、`commandId`、`taskId`、`taskRevision`、`stepId`、`robotId`、`capability`、`targetRef`、`manifestRevision`、`observation`、`worldRevision`、`resourceId`、`fencingToken`。
- `ObservationBundle`（:111-123）：`sources{fresh,confidence,anomalies}`、`robotState`、`entities`、`frames{uri,mime,sha256,width,height,observedAt}`——**大帧走 FrameReference，不塞进状态流**。
- `InferenceResult`（:148-157）：`actions: list[Action]`，`Action.values: dict[str,float]`。
- **校验**（`validate_result`，:160-182）：五重身份比对（manifestRevision / requestId / commandId / observationId）+ **动作块长度不得超过 `maxActionChunkLength`** + **每个动作值必须在 `actionBounds` 内**，越界抛异常。

**VLA / 模仿学习 / RL 在本项目里的位置**：`providers.py:21-22` 的 `CallableProvider` docstring 一句话说清——"Bind a VLA, imitation-learning, or RL callable to the stable protocol"。三种 framework 是**同一接口下的三个标签**，差别在 manifest 里，不在代码路径上。

**但必须把"接口位置"与"已实现"分开讲：**

- **仓库里没有任何 VLA / ACT / Diffusion Policy / 连续控制 RL 的实现。** `providers.py` 里唯一的真类就是 `CallableProvider`（:21），它是一个**零权重的适配器**——接收调用方提供的 callable，不做 checkpoint 加载、不做归一化、不做模型管理。`contracts.py:42` 的 `framework` 枚举允许 `vla/imitation/reinforcement/deterministic` 四个值，但 `artifactSha256` 只是一个**身份串**（:43、:57-65）：填什么都行，只要格式对。
- 仓库里唯一的**具体** provider 是 Go 的 `DeterministicProvider`（`edge/policy/deterministic.go:12-17`），返回每个 actionBound 的中点、pick 时 gripper=max、place 时 gripper=min（:55-64），注释写明只用于仿真/CI。
- 测试用**同一个 lambda** 穿过三种 framework，证明了"无框架依赖"（`policy/sidecar/tests/test_contracts.py:104-115`）——这既是有力证据，也说明 framework 标签不改变任何行为。
- 文档自己也承认：`docs/production/policy-tools.md:32`"**仓库没有已训练的通用实机模型**"；`docs/production/v1-release-status.md:38`"LLM 不代替动作模型"。

`deterministic` 是第四种，且被明确限制：`edge/policy/deterministic.go:9-10` 注释"**It must not be silently selected for a physical adapter**"；`cmd/edge-worker/main.go:181` `errUnsafePolicyMode = errors.New("deterministic policy mode is simulation-only")`，非仿真 adapter 用 deterministic 直接拒绝启动（:188-190）。

**动作空间是具名的 14 个关节，不是抽象维度。** 真机键集合在 `robot/gateway/tangying_robot_gateway/xlerobot_backend.py:28-32`：

```python
ALLOWED_ACTION_KEYS = frozenset(
    f"{side}_arm_{joint}.pos"
    for side in ("left", "right")
    for joint in ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
) | {"head_motor_1.pos", "head_motor_2.pos"}
MOBILE_BASE_KEYS = {"x.vel", "theta.vel"}
```

即 **12 个手臂关节 + 2 个头部关节 = 14 个键**。取值上限 `MAX_ABSOLUTE_ACTION_VALUE = 100.0`（:26），`gripper.pos` 额外限制在 **[0, 100]**（:63-66）。**底盘速度键被显式拒绝**：命中 `MOBILE_BASE_KEYS` 即返回 `MOBILE_BASE_DISABLED`（:51-52）——因为默认桌面配置不使能底盘。其他拒绝码：`ACTION_KEY_REJECTED`、`ACTION_VALUE_NOT_NUMERIC`、`ACTION_VALUE_NOT_FINITE`、`ACTION_VALUE_OUT_OF_RANGE`、`GRIPPER_VALUE_OUT_OF_RANGE`、`POLICY_ACTION_CHUNK_REQUIRED`、`ACTION_CHUNK_TOO_LONG`、`ACTION_CHUNK_MALFORMED`（:39-66）。动作 schema 串是 **`xlerobot.named-joints.v1`**（`cmd/edge-worker/main.go:200`、`docs/sim2real/README.md:133`）。

> **冲突（动作块上限，三处不一致）**：`MAX_ACTION_CHUNK_LENGTH = 64`（`xlerobot_backend.py:25`、`robot/ros2_ws/src/xlerobot_adapter/config/xlerobot.yaml:9`）；仿真 deterministic 用 **8**（`cmd/edge-worker/main.go:201`）；文档与测试示例用 8 与 32（`docs/production/policy-tools.md:75`、`test_contracts.py:94`）。**实际约束是 manifest 自报值**——`validate_result` 用的是 `manifest.max_action_chunk_length`（`contracts.py:51、171-172`），64 只是运行时的上限。

**tools.json 里没有任何 policy 工具。** 29 个工具中 0 个名字含 policy；唯一相关的是一条排除声明：`"excluded_from_llm": ["move_arm_to_joints", "navigate_to_pose"]`（`tools.json:1145-1148`，两项定义在 :929 与 :977）。这两个**低层接口只给工程调试，不给 LLM**（`README.md:142`）。这从数据层印证了边界：**模型能看到的工具集合里根本没有关节角与位姿原语。**

**训练代码的真相（别把它当成 VLA 训练）**：`sim/mujoco/tangying_sim/training/` 是**表格式 Q-learning over 语义工具目录**，只用 NumPy + MuJoCo，**无 PyTorch、无 GPU**。`env.py:219` 的 `SemanticToolEnv`：离散动作 = 4 个工具动作 + 7 个物体槽 grounding + 3 个目的地槽 grounding（:43-58），9 个 phase（:63-73），状态是 `SemanticObservation.state_key()` 的 **14 元字符串元组**（:131-170）；奖励常量 `STEP_COST=-0.05, INVALID_ORDER=-0.75, TOOL_FAILURE=-1.0, REPEATED_NOOP=-0.25, RECOVERY=+0.30, WRONG_GROUNDING=-1.25, TIMEOUT=-2.0, UNSAFE=-8.0`（:222-229）。`qlearning.py:66-78` 的 `train(*, episodes, seed, alpha=0.25, gamma=0.95, epsilon_start=1.0, epsilon_end=0.05, epsilon_decay=0.99, max_steps=20, transient_failure_rate=0.02, env_factory)`。CLI 默认 `train --episodes 1000`、`evaluate --episodes 100 --min-success-rate 0.90`（低于阈值 exit 1）（`cli.py:16-33`）。checkpoint 原子写 JSON，含 `toolCatalogFingerprint`（SHA-256），**指纹不符即失败关闭**（:199-260）。

**两条负面事实**：① `SemanticPolicy` / `load_checkpoint` 在 training 包之外**零引用**——没有接进 `/v1/infer` 或任何执行路径；仓库根的 `train/`（Go，checkpoint 晋级门，`train/gate.go:27-31`）与 `training/`（Go，只读 SQLite 任务账本导出训练数据，`training/export.go:25-27`）**与这个 Python RL 训练器无关**，三个相近名字做三件不同的事。② `core/`、`orchestration/`、`agentruntime/` 对 `edge/policy` **零引用**（grep 只命中无关的 `StagePolicy`/`StopPolicy`）；策略只通过 `tasks/experience.go:43-52` 的 `PolicyEvidence` 露给 UI。

**为什么"任何把 LLM 放进控制回路的架构都是错的"**：原文在 `docs/architecture/why-distributed.md:32`（推论），依据是同一文件 :24-28 的三条硬约束表：

| 约束 | 数量级 | 后果 |
| --- | --- | --- |
| 控制回路 | 100 Hz – 1 kHz | **不能**穿过一次网络往返，更不可能穿过一次 LLM 推理 |
| 急停 | 毫秒级 | **不能**等网络——等得到的那不叫急停 |
| 感知带宽 | 点云 / 图像，每秒数十 MB | **不能**原样上传 |

**这句是文档化的物理约束，仓库里没有与之对应的实现循环**——`docs/architecture/why-distributed.md:195` 也写明"一个会被 LLM 推理延迟影响的控制回路是危险的设计"。**100 Hz – 1 kHz 这个数量级在本仓库有两个可核对的锚点**：MuJoCo 模型 `sim/mujoco/assets/xlerobot_home.xml:4` 的 `timestep="0.002"`（= **500 Hz**）；RTAB-Map 侧的 `Rtabmap/DetectionRate` 建议值只有 2–3 Hz（`docs/development/calibration-slam-ros-plan.md:63`），而 Nav2 的 `controller_frequency: 10.0`、`expected_planner_frequency: 2.0`（nav2.yaml:17、:28）。**(推断)** 把这三层画在一张时间轴上就能看出量级差：控制 500 Hz / 局部规划 10 Hz / 感知 1–5 Hz / LLM 一次推理秒级——它们差三个数量级，所以分层不是架构偏好而是物理强加。

**边界在代码里的落地**（四处硬证据）：

1. `docs/architecture/why-distributed.md:32-34`："本仓库里模型选的是**工具**，低层动作来自 Policy Provider（VLA / 模仿学习 / RL）。"
2. `docs/architecture/llm-driven-execution.md:166`（"不做什么"清单）："**不让模型直接写关节序列**：低层动作仍然来自 Policy Provider，模型选的是**工具**。"
3. `README.md:142-143`：`move_arm_to_joints`（关节角）与 `navigate_to_pose`（位姿）**不暴露给大模型**，"原始接口只留给工程调试"；"模型不接触关节角、轮速或坐标"。
4. `docs/architecture/agent-v1.md:53`："当前本地 LLM 不生成低层 action_chunk；Fleet 策略链由 `edge/worker` 调用 `edge/policy`。"

**Go 侧接口**（`edge/policy/provider.go:15-18`）：

```go
// Provider supplies immutable policy metadata and one bounded decision. It
// has no authority to execute a command or mutate task/world state.
type Provider interface {
	Manifest(context.Context) (Manifest, error)
	Infer(context.Context, InferenceRequest) (Decision, error)
}
```

感知/验证侧与策略侧的**动作 schema 是具名的**：`xlerobot.named-joints.v1`（`cmd/edge-worker/main.go:200`、`docs/sim2real/README.md:133`）。真机接入时 sidecar 起法在 `docs/sim2real/README.md:131`：`create_server(provider, host="127.0.0.1", port=8091).serve_forever()`，Edge 侧要 `EDGE_POLICY_MODE=http` + `EDGE_POLICY_ENDPOINT`（`docs/development/robot-adapters.md:243`）。

**还没有做的事（诚实清单）**：`docs/architecture/llm-driven-execution.md:138` 表格里"接进 runner"标 ❌——"执行路径仍是 `graph.Order`。**这是现在最大的一块空缺**"。同一文件 :156 强调"**还不能说**机器人的实际执行已经变成 LLM 驱动"。

---

## 9. sim2real 的真实差距清单

**放行前的硬要求**（`README.md:213` 原文）：

> 真机放行前必须完成：双 RGB-D 与里程计标定、地图覆盖、刹车与实体急停、机械臂碰撞边界、以及至少 30 次受监护的路线/抓取验收。

这条在四处独立成文，互为佐证：

| 要求 | 出处 |
| --- | --- |
| 双 RGB-D + 里程计标定、低矮障碍覆盖、断流停止、到达证据、≥30 次重复路线 | `docs/guides/home-sim2real.md:33` |
| 双 RGB-D 在真实照明/反光地面/窄门/低矮障碍/家具遮挡下通过观测合同 | `docs/operations/release-checklist.md:31` |
| 低速软围栏、刹车距离、实体急停、断网归零、持物恢复、人工接管演练 | `docs/operations/release-checklist.md:32` |
| **至少 30 次单机器人路线和一次长稳运行**；现场负责人签字 | `docs/operations/release-checklist.md:33-34` |
| 至少 30 个不同实机任务及至少 1 小时观察 | `docs/sim2real/README.md:48` |
| 急停必须**独立于软件**切断执行器电源、全程可及 | `docs/operations/safety-checklist.md:7` |

**代码里的门槛是硬的**：`scripts/sim2real.py:20` `MIN_TRIALS = 30`；:292 `minimum = MIN_TRIALS if kind == "trial" else 1`。同一脚本 :22 的 `LIMITATION` 常量是对这套证据的自我限定："仅检查接入资料及人工记录，不验证实机效果，不授权电机运动；至少 30 次试验和 1 小时观察只是本版试点资料门槛。"`docs/sim2real/README.md:51` 进一步说明"30 次和 1 小时是本版试点资料的最低门槛，**不是可靠性认证**"，:208 记录"当前版本**存在失败记录会阻止通过**"。

**"软件发布与实机放行是两项独立结论"如何体现**（`README.md:215`，原话是"仿真通过不等于实机可用，这是刻意的"）：

1. **文档层**：`docs/operations/safety-checklist.md:3`"The repository has no completed physical acceptance result. **A software READY result is not permission to move hardware.**"
2. **数据层**：`docs/production/v1-release-status.md:38` 把"真实策略"标为待办——"需要用户工位适用的模型、训练数据和延迟评估；**LLM 不代替动作模型**"。
3. **代码层**：`docs/sim2real/README.md:133`"Edge 拒绝把 `deterministic` 模式用于实机"；`cmd/edge-worker/main.go:181` `errUnsafePolicyMode`。
4. **流程层**：`docs/sim2real/README.md:210`"改变接入包配置、模型、标定或资料后，**旧记录会计入 `staleRecords`**"——通过结论不能跨版本复用。
5. **复现层**：`docs/sim2real/README.md:62`"四个子命令 `init/check/record/report` 都不打开机器人串口、不请求网络、不启动服务、不调用动作模型"——**检查器永远碰不到硬件**，所以它证明的只是资料齐全。

**真机平台限制**（`docs/install/robot-pi.md:16-38`）：

- 只支持 **Raspberry Pi 4/5 上的 Ubuntu Server 24.04 arm64**（`uname -m` 应为 `aarch64`）；
- Python 要求 **>= 3.11**（`pyproject.toml:8` `requires-python = ">=3.11"`）；
- XLeRobot 固定到提交 **`3d14695e40c9c68229c0aacffca6053c75cd3eb6`**（`robot/ros2_ws/src/xlerobot_adapter/config/xlerobot.yaml:10`、`docs/sim2real/README.md:32`），LeRobot **0.4.1**；
- **Protobuf 6.33.5**，不能把生成工具单独升到要求 Protobuf 7 的版本（LeRobot 0.4.1 依赖不支持）；OpenCV 4.11.0.86；
- 默认 `mobile_base_enabled: false`（xlerobot.yaml:7）——**桌面配置不使能底盘**；移动版本须另行交付底盘 ROS 驱动、真实里程计、控制器看门狗与现场验收（`docs/sim2real/README.md:32`）；
- 软件限幅默认 `max_relative_target: 8.0`、`max_action_chunk_length: 64`（xlerobot.yaml:8-9），安全清单明确"**defaults 8.0 and 64 are software defaults, not hardware-certified limits**"（safety-checklist.md:13）。

**首条移动操作任务的真机前置**（`docs/guides/home-sim2real.md:20-25`）：登记硬件 → 完成标定（含 `base_link → camera_optical` 外参与轮径/轮距）→ **先只读接入**（`input_mode:=ros`，禁止电机使能）→ 建图 → 定位回放 → 低速执行。放行前仍需保留的**未完成项**（`docs/sim2real/README.md:116`）：transforms.json 只是结构示例，"不能直接复制单位矩阵上线"；`:127` 指出"目前列表 provider 接口没有独立的采集时间字段，Runtime 时间戳不是相机采集时间"；`:173` 本版实机**未安装经过标定的自动回位轨迹**，`recover_to_safe_pose` 返回 `RECOVERY_POLICY_REQUIRED`。

**Real2Sim 的方向（反向的那一半）**：`docs/development/real2sim-from-robot-slam.md` 主张"机器人自己的 SLAM 测绘成果，就是仿真场景的来源"（:5）。已有：`rtabmap_export.py`（已对真实 208 MB 库验证）、`map_pipeline.py`、`xlerobot_home.xml`；**缺的是"点云 → MuJoCo 碰撞几何"这一个转换器**（:99）。两条必须记住的现实约束：扫描分辨率不足以重建小物体（1 m 外约 1 cm），所以**可操作物体用规范模型 + 感知给位姿**（:41-46）；**MuJoCo 的 mesh 碰撞体是凸包**，凹形家具（书架、桌下空间）会被填实，必须分块凸分解（:87-89）。

---

## 10. 教学价值

### 10.1 怎么向学生解释 sim2real gap

不要用"现实更复杂"这种话。用本仓库的三条**可核对的具体差距**：

1. **同一策略、同一代码、换一个户型，覆盖率从 99.0% 掉到 79.3%；差距的根因是一个常数（盲区半径 1.0 m）承担的职责在两种户型里互相冲突**——MuJoCo 降到 0.7 m 崩到 31.9%，Gazebo 升到 99.9%。结论：gap 常常不是"模型不准"，而是**一个参数被用来表达两件事**。
2. **现场 73.1% vs 评测台 99.5%，同分母**。看似"建图有 bug"，实测根因是 **272 次配准只成功 1 次**——地图实为里程计的产物。结论：先把两个数字放到**同一个分母**上，再谈差距；分母不同造成的 26 个百分点是度量假象，同分母造成的才是真差距。
3. **"不知道的地方不进去"是一条安全门禁，不是缺陷**。冷启动后去厨房 `NAV_FAILED NAV2_ACTION_ENDED` 是设计。结论：**sim2real 的第一课不是"怎么让它成功"，而是"怎么让它在不知道的时候失败"**。

补一条方法论：`2026-09-19-slam-coverage-investigation.md` 里那些**被测试挡回的改动**与**被判定为建模错误的实验**（`density=0.3 → 1.6%`），比任何一个成功数字都更适合做课堂材料。

### 10.2 建议的最小可跑实验路径（学生可复现）

主路径只需一条命令，不需要 Docker、不需要真机；其余四条是可选加深项：

```bash
make setup && make build
make home-furnished        # 主路径：工作台 http://127.0.0.1:8897/
.venv/bin/python scripts/build_sim_map.py --base-url http://127.0.0.1:8897 \
  --name '家庭一楼' --output artifacts/acceptance/my-survey
```

观察点：`mapping.status` 的探明比例、`poseSource`、`mapRevision`。接着在控制台点"整机标定 → 自行标定并录入"，用"校验并保存"**故意传一个旧的 `expectedRevision`**，亲眼看到 `REVISION_CONFLICT`——这一步让学生理解"为什么保存必须带读取时的版本"。

可选加深项（依次递进，全部不碰硬件）：

```bash
# 1. 覆盖率：对着真实验记录复算（--blind-radius 0.7 是那条被否掉方向的开关）
.venv/bin/python scripts/exploration_survey_benchmark.py --truth mujoco
.venv/bin/python scripts/exploration_survey_benchmark.py --truth gazebo
.venv/bin/python scripts/exploration_survey_benchmark.py --truth gazebo --blind-radius 0.7
# 2. 适配器接口：验证"换机器人不用重写工具"的正面证据
.venv/bin/python -m tangying_robot_gateway.run_plugin schema > /tmp/contracts.json
.venv/bin/python -m tangying_robot_gateway.run_plugin check --factory examples.robots.simulated:arm
# 3. 引导标定（纯仿真演练，不碰舵机）
scripts/calibrate_guided.py --list --base <已有标定> && scripts/calibrate_guided.py --simulate --base <已有标定>
# 4. sim2real 资料链：第一次 check 出现"待补齐"是预期结果
.venv/bin/python scripts/sim2real.py init --robot-id robot-1 --output site/robot-1
.venv/bin/python scripts/sim2real.py check --kit site/robot-1 --stage inventory
```

加深项 4 的读法：让学生打开 `site/robot-1/`，把每个文件对应到 §9 表格里的哪一项放行条件上。

### 10.3 练习题（含答案要点）

**练习 1：为什么"门不是瓶颈"这个结论必须用几何数据而不是观察来得出？**

答案要点：① 观察只能给出"机器人没进卫生间"，无法区分"进不去"和"没去"；② 本仓库做了**两个独立测量**——从 MuJoCo 模型直接量门洞开口（客厅 x ∈ [−1.5, 1.5]，走廊→厨房 y ∈ [2.6, 4.1]，卧室→卫生间 x ∈ [−2.655, −1.445]），再数旧地图门线上的实际通行格子（卫生间门线 20 自由 / 1 占用 / **29 未知 = 58%**）；③ 加上"底盘包络仅约 0.7 m 而最窄门 1.21 m"的尺度过关判断；④ 轨迹点分布（卫生间 0% 轨迹点、走廊与厨房占一半以上里程）才是完整的证据链。**要教的是：把"看起来的原因"变成可测量的排他性证据。**

**练习 2：现场地图 73.1%、评测台 99.5%，这两个数能不能直接相减？怎么判断？**

答案要点：① 不能——调查报的是 `1 - unknownFraction`，分母是它自己栅格的全部格子（含墙外、墙内、家具背后任何位姿都看不见的地板）；评测台报的是 `mapped / coverable`，分母是"某个合法可达位姿真正能看见的地板"；② 判断方法就是 `scripts/score_live_map.py`：把**已发布的地图包**投影回真值坐标系，用评测台的分母重数一遍，得到 73.1%（18861 / 25801）；③ 两个分母几乎相等，所以这 26 个点是**真差距**；④ 但根因仍不在探索层：配准 272 次只成功 1 次、112 个关键帧、0 次回环，4573 格被标成自由而真值是障碍。**要教的是：指标的口径（分母）是实验设计的一部分，不是实现细节。**

**练习 3：把 Policy sidecar 换成你们自己训的 VLA 模型，要改哪几个文件？哪些改法会被拒绝？**

答案要点：① 只需实现 `PolicyProvider` Protocol 的两个成员（`manifest` 属性 + `infer(request) -> InferenceResult`，`providers.py:11-15`），用 `CallableProvider(manifest, infer)` 或自建类，再 `create_server(provider, host="127.0.0.1", port=8091).serve_forever()`；② manifest 必须是 `policy.manifest.v1`、`framework="vla"`、`artifactSha256` 是**真实权重的 64 位十六进制 SHA-256**（`deterministic:` 前缀只允许 `framework="deterministic"`）；③ 动作必须在 `actionBounds` 内、条数 ≤ `maxActionChunkLength`，键名必须在 14 个 `ALLOWED_ACTION_KEYS` 里且不含 `x.vel`/`theta.vel`，否则 `validate_result` 抛异常 → HTTP 422 `POLICY_REQUEST_REJECTED`；④ **不需要改** Go gRPC 客户端、MCP 工具名、Fleet 任务接口（`docs/development/robot-adapters.md:297`）；⑤ 会被拒绝的四处：manifest revision 漂移（`ErrManifestDrift`）、实机用 deterministic（`errUnsafePolicyMode`）、观测超出 `maxObservationAgeMs` 或缺 `requiredObservationSources`（`CheckCompatibility`）、动作块超长（`ACTION_CHUNK_TOO_LONG`）。

> **补充练习（可当堂跑，5 分钟）**：让学生执行 `python scripts/exploration_coverage_benchmark.py`，观察它在打印完 `+2.4%` 之后抛 `TypeError`（§6.4）。然后提问：**为什么测试没抓住这个缺陷？** 答案要点：被测试覆盖的是 `exploration.py` 的纯函数，而这个一次性审计脚本没有测试；重构改了 `explore_target` 的关键字参数（`min_frontier_cells` → `min_frontier_area_m2`），调用方没跟着改。**要教的是："文档里的复现命令"也是需要维护的资产**，以及一次重构的完成标志是"所有调用方都改了"，不是"测试绿了"。

> **补充一条值得当堂讨论的反例**：把"Gazebo 下局部代价地图 `track_unknown_space: false`"提升为全局默认值会怎样？答案在代码注释里（`navigation.launch.py:54-63`）：**这是本轮唯一一处"知道怎么改但只改了一半"的地方**，因为真机 profile 没有被测量过，而一个仿真器上的测量不足以提升为共享默认值——尤其当改动方向会把可通行范围放宽时。测试还**明确钉住**了两个 costmap 都是 `true`（`test_launch_config.py:20-21`），所以要动必须同时改测试并给出测量证据。

---

## 源码索引

**proto / 契约**
- `proto/robot/v1/robot.proto:9-18`
- `core/robotcontract/contract.go:1-3, 17-53, 62-77, 139, 227`
- `robot/gateway/tangying_robot_gateway/backend.py:16, 45, 62-82`
- `robot/gateway/tangying_robot_gateway/contracts.py`
- `robot/gateway/tangying_robot_gateway/plugin_backend.py:1-14, 72, 81-92, 112-143`
- `robot/gateway/tangying_robot_gateway/runtime.py:20, 38`

**仿真后端**
- `sim/mujoco/tangying_sim/home_scene.py:1-6, 20, 23, 86-104`
- `sim/mujoco/tangying_sim/server.py:491`
- `sim/mujoco/tangying_sim/rendering.py:19`
- `sim/mujoco/assets/xlerobot_home.xml:4`
- `sim/gazebo/CMakeLists.txt`, `sim/gazebo/grounded_transport.cc:1-36`
- `sim/robocasa/tangying_robocasa/world.py:1, 12-14`
- `sim/robocasa/tangying_robocasa/fleet_server.py:14, 27-30, 32, 82`
- `robot/ros2_ws/src/tangying_navigation/tangying_navigation/gazebo_runtime_node.py:39, 62, 461, 474-476, 544-593, 654-655`
- `docs/development/2026-09-19-gazebo-backend-status.md:3-8, 25-29, 98-109, 113-125, 135-145, 151`

**脚本与 Makefile**
- `Makefile:110-119, 143-155, 209-242`
- `scripts/sim-stack.sh:7-13, 39-42, 44-65, 913-920, 969-997`
- `scripts/furnished-home-demo.sh:1-37`
- `scripts/sim2real.py:20, 22, 292`

**机器人适配**
- `docs/development/robot-adapters.md:5, 9-20, 24-35, 41-52, 70-91, 141-150, 195-197, 243-245, 293-297`
- `docs/guides/robot-service-workflow.md:3, 8-16, 30, 32-51, 70-78, 84, 114-124`
- `robot/gateway/tangying_robot_gateway/xlerobot_backend.py:117, 138-146, 233`

**标定**
- `docs/development/robot-calibration.md:1, 13-24, 28-53, 55-68, 78-80, 84-118`
- `robot/gateway/tangying_robot_gateway/calibration.py:40, 61-98, 129-306, 356-372`
- `sim/mujoco/tangying_sim/calibration.py:41`
- `robot/gateway/tangying_robot_gateway/robot_workflow.py:44, 150-152, 366-380, 1337-1339, 1376, 1404-1408`
- `robot/ros2_ws/src/xlerobot_adapter/xlerobot_adapter/driver.py:20, 96-145, 350-369`
- `robot/ros2_ws/src/xlerobot_adapter/config/xlerobot.yaml:1-10`
- `robot/gateway/tangying_robot_gateway/tool_layer.py:101, 212`
- `core/closedloop/closedloop.go:134`

**SLAM / 导航 / 门禁**
- `robot/ros2_ws/src/tangying_navigation/config/nav2.yaml:14-24, 116-131, 199-221`
- `robot/ros2_ws/src/tangying_navigation/launch/navigation.launch.py:35-51, 53-81, 92-109`
- `robot/ros2_ws/src/tangying_navigation/test/test_launch_config.py:20-21`
- `robot/ros2_ws/src/tangying_navigation/tangying_navigation/navigation_node.py:187, 325, 421`
- `robot/ros2_ws/src/tangying_navigation/tangying_navigation/contracts.py:65`
- `sim/mujoco/tangying_sim/rtabmap_client.py:49-50, 128, 278, 365`
- `sim/mujoco/tests/test_rtabmap_client.py:91, 103, 133`
- `robot/gateway/tangying_robot_gateway/map_manifest.py:161-167, 237, 254-270`
- `robot/gateway/tangying_robot_gateway/map_catalog.py:100, 155`
- `robot/gateway/tangying_robot_gateway/map_pipeline.py:273, 282`（实验文档写 `:260`）
- `robot/gateway/tangying_robot_gateway/semantic_services.py:61, 82, 179-189, 242, 278`
- `docs/development/rtabmap-navigation.md:37, 41-52, 74-80, 131`
- `README.md:151, 160-161, 182-201, 213-215, 254`
- `docs/guides/home-scene-operations.md:115-117`
- `docs/guides/gazebo-house-operations.md:46, 48, 81`

**探索与覆盖率**
- `docs/experiments/2026-09-15-slam-exploration-coverage-upgrade.md:3, 14-39, 45-50, 56-70, 78-82, 88-91, 95-102`
- `docs/experiments/2026-09-19-slam-coverage-investigation.md:4-8, 18-31, 38-43, 61-66, 72-91, 95-102, 110-124, 130-142, 148-158`
- `docs/experiments/2026-09-20-gazebo-exploration-coverage.md:5, 11-20, 36-57, 484-545, 555-612, 614-631, 639-677, 683-689`
- `robot/gateway/tangying_robot_gateway/exploration.py:45, 306, 324-326, 590-626, 629-636, 639-659`
- `robot/gateway/tangying_robot_gateway/slam_keyframes.py:22, 65, 99`
- `robot/gateway/tangying_robot_gateway/dense_slam.py:28, 270`
- `robot/gateway/tangying_robot_gateway/mapping_coverage.py:275`
- `sim/mujoco/tests/test_rtabmap_client.py`、`scripts/build_sim_map.py`、`scripts/exploration_survey_benchmark.py`、`scripts/score_live_map.py`

**Policy 与低层动作**
- `policy/sidecar/tangying_policy_sidecar/contracts.py:13-14, 38-77, 80-123, 126-182`
- `policy/sidecar/tangying_policy_sidecar/providers.py:11-46`
- `policy/sidecar/tangying_policy_sidecar/server.py:26-102`
- `edge/policy/provider.go:15-20`
- `edge/policy/manifest.go:99-149`
- `edge/policy/deterministic.go:9-10, 13-37`
- `cmd/edge-worker/main.go:181-200, 341, 413-419`
- `docs/architecture/why-distributed.md:13, 24-34`
- `docs/architecture/llm-driven-execution.md:11-19, 41-52, 75-95, 138-141, 156-168`
- `docs/architecture/agent-v1.md:25, 53`
- `docs/production/policy-tools.md`
- `docs/production/sim-to-real.md:9, 25-35, 86-88`
- `docs/development/calibration-slam-ros-plan.md:63`

**sim2real / 真机放行**
- `docs/sim2real/README.md:1-8, 15-34, 38-51, 62-79, 100-133, 141-158, 173, 191-210, 221-235`
- `docs/guides/home-sim2real.md:3, 7-25, 29-37`
- `docs/operations/release-checklist.md:7-34`
- `docs/operations/safety-checklist.md:3-22`
- `docs/install/robot-pi.md:4-38`
- `docs/development/real2sim-from-robot-slam.md:3-11, 35-46, 50-69, 79-89, 91-103`
- `docs/production/v1-release-status.md:38, 62`
- `docs/operations/robocasa-handoff.md:1-30, 68-70, 133`
- `pyproject.toml:8, 16`

**第三轮补充引用（覆盖率专项复核新增）**
- `robot/gateway/tangying_robot_gateway/exploration.py:37-38, 45, 47, 67, 79, 83, 191, 207, 223, 268, 277, 306-326, 337, 382, 454-457, 499, 510-512, 551-552, 629-636, 639, 668, 689`
- `robot/gateway/tangying_robot_gateway/survey.py:36, 52-116, 216, 264, 281, 300, 371, 396-413, 415`
- `robot/gateway/tangying_robot_gateway/mapping_coverage.py:25, 151, 199, 235`
- `robot/gateway/tangying_robot_gateway/map_pipeline.py:186, 189, 218, 221, 273, 281-283, 344-353`
- `robot/gateway/tangying_robot_gateway/robot_workflow.py:383, 820-823`
- `sim/mujoco/tangying_sim/dense_slam.py:352`
- `scripts/exploration_coverage_benchmark.py:59, 77, 82, 116, 127-141`
- `scripts/exploration_survey_benchmark.py:69-78, 81, 156, 257, 273, 308, 518, 539, 624-651, 682-700`
- `scripts/score_live_map.py:106-125`
- `scripts/exploration_robustness_benchmark.py:444-450`
- `scripts/build_sim_map.py:88`
- `docs/experiments/2026-09-20-gazebo-exploration-coverage.md:386-397, 426-438, 445-467, 484-545, 565-609`
- `docs/experiments/2026-09-19-exploration-importance-and-coverage.md:57, 69-91, 114-128`
- `docs/experiments/2026-09-19-slam-layering-and-replay.md:100-103`
- `docs/experiments/2026-09-19-exploration-robustness-experiment.md:49-59`
- `docs/experiments/2026-09-20-slam-exploration-experiment-report.md:269-279, 347-354, 567-570, 618-635`
- `docs/development/2026-09-21-map-visibility-and-inventory.md:150-170, 190-193, 199`
- `artifacts/slam-exploration-coverage/full-house-summary.json`（mapId `scan-900dbcd3337b`、travelBudgetM 60、legs 3、travelledM 53.7、keyframes 263/600、`unknownFractionWholeMap` 0.19）
- `artifacts/marketing/01-总体架构/素材来源与数字出处.md:67`
- `robot/ros2_ws/src/tangying_navigation/worlds/tangying_home.sdf:80-87`

**第二轮补充引用**
- `README.md:133, 142, 144`
- `docs/development/2026-09-18-system-review-and-improvement-plan.md:346-352`
- `docs/guides/furnished-home-demo.md:15-19, 52-55, 90-97`
- `docs/guides/home-scene-operations.md:1-3, 22-25`
- `docs/development/mujoco-compatibility.md:3, 77, 96, 98`
- `.github/workflows/ci.yml:16, 161`
- `docs/production/testing-and-acceptance.md:34`
- `sim/mujoco/tangying_sim/rendering.py:17-23, 27-34, 86-110`
- `sim/mujoco/assets/xlerobot_home.xml:4, 12, 19-65`
- `sim/mujoco/assets/xlerobot/PROVENANCE.md:1-21`
- `scripts/prepare_furnished_home.py:17-18, 320, 351-352, 402, 425-427`
- `sim/mujoco/tangying_sim/furnished_home.py:13-25, 55-159, 352`
- `pyproject.toml:43-47`
- `proto/robot/v1/robot.proto:72, 148`
- `tasks/service.go:152-165`、`tasks/grounded_test.go:14, 25`
- `robot/gateway/tangying_robot_gateway/gazebo_backend.py:32`
- `robot/ros2_ws/src/tangying_navigation/tangying_navigation/gazebo_runtime_node.py:627, 642, 655`
- `robot/gateway/tangying_robot_gateway/service.py:125, 139-140, 148-149, 617`
- `robot/gateway/tangying_robot_gateway/service_registry.py:24-33, 56, 75, 83, 93`
- `sim/mujoco/tangying_sim/server.py:71, 96, 256-257, 644, 673`
- `edge/runtime/runtime.go:131-164, 172-185`、`edge/worker/worker.go:47-54, 70`
- `robot/gateway/tangying_robot_gateway/contracts.py:25-29, 112-123, 214-221, 227`
- `tests/contract/test_heterogeneous_runtime_boundary.py:250-278`
- `robot/gateway/tangying_robot_gateway/calibration.py:45-67, 103-111, 114-120, 190-201, 222-257, 265-279, 296-306, 325-341, 344-386, 389-405`
- `robot/gateway/tangying_robot_gateway/calibration_solver.py:20-22, 94, 387, 464, 481, 695-698, 803`
- `robot/gateway/tangying_robot_gateway/calibration_wizard.py:86, 91, 396-399, 623, 781-814, 866-871`
- `sim/mujoco/tangying_sim/calibration.py:35, 39, 41, 50-53, 103-109, 129-169, 181-226, 330-372, 376-397`
- `sim/mujoco/tests/test_calibration_ground_truth.py:1-12, 31, 34-55, 58-61, 64-149`
- `sim/mujoco/tests/test_calibration_self_check.py:1-13, 28, 38-56, 59-204`
- `robot/gateway/tangying_robot_gateway/robot_workflow.py:178-190`
- `robot/gateway/tangying_robot_gateway/map_manifest.py:235-260`
- `robot/gateway/tangying_robot_gateway/map_catalog.py:64-68`
- `robot/gateway/tangying_robot_gateway/arm_kinematics.py:445, 452`
- `sim/mujoco/tangying_sim/rgbd_runtime.py:687-693`
- `sim/mujoco/tangying_sim/workflow_services.py:95-111`
- `robot/gateway/tangying_robot_gateway/rgbd.py:50, 84`
- `robot/gateway/tangying_robot_gateway/ros_rgbd.py:231-236`
- `edge/policy/manifest.go:176-177`、`edge/policy/types.go:51-52, 158-160`
- `edge/worker/policy.go:14, 16-68, 70-134`
- `cmd/edge-worker/main.go:131, 136, 155-157, 181, 188-190, 200-201, 220, 234`
- `robot/gateway/tangying_robot_gateway/xlerobot_backend.py:25-32, 39-66`
- `policy/sidecar/tests/test_contracts.py:29, 94, 104-115`
- `policy/sidecar/tests/test_server.py:71, 94-117`
- `tools.json:929, 977, 1145-1148`
- `robot/mcp/tangying_mcp/server.py:157, 169-177`、`robot/mcp/README.md:31-42`
- `sim/mujoco/tangying_sim/training/env.py:43-73, 82, 131-170, 219-243, 275, 325`
- `sim/mujoco/tangying_sim/training/qlearning.py:33, 66-78, 103-121, 153, 199-260`
- `sim/mujoco/tangying_sim/training/cli.py:16-33, 109`
- `train/gate.go:1-38, 27-31`、`training/export.go:25-27`、`training/quantify.go:5-11`
- `tasks/experience.go:43-52`、`tasks/experience_events.go:41`
- `docs/development/calibration-slam-ros-plan.md:79, 85-87`
- `docs/development/arm-moveit-adapter.md:155-157, 409`
- `docs/production/policy-tools.md:32, 75, 139-140, 153-161, 171`
- `docs/development/robot-calibration.md:55-68, 97-104, 114-118`
- `scripts/calibrate_guided.py:83-85, 93-94, 147`
- `scripts/calibrate_xlerobot.py:28-32`
- `scripts/gazebo-house-stack.sh:15`、`scripts/robocasa-fleet.sh:23-26`
- `deploy/robot/navigation/gazebo-house.compose.yaml:11, 15, 19`

---

## 关于引用稳定性（必读）

本章所有行号均在 **v0.6.0 / commit `774bd2a2f`** 的工作区状态下核对。核对过程中发现**工作区正在被并发改写**，有两类行号必须特别说明：

1. **`CHANGELOG.md` 已被改写。** 会话开始时该文件约 431 KB，其后被替换为约 30 KB / 200 行的版本，其中"控制回路 100 Hz – 1 kHz"与"任何把 LLM 放进控制回路的架构都是错的"两行**在当前工作区已不存在**。本章因此**不引用 `CHANGELOG.md` 的任何行号**；这两句话的稳定出处是 `docs/architecture/why-distributed.md:26` 与 `:32`。`git status` 显示该文件为已暂存的修改态。
2. **行号漂移是常态，不是例外。** 已在本章内标注三处文档与代码不一致（`sim/mujoco/tangying_sim/server.py` 的 `execute(` 文档写 `:491` / 实际 `:489`；`map_pipeline.py` 的 `occupancy_from_points` 实验文档写 `:260` / 实际 `:273`；三个 `maxActionChunkLength` = 8 / 32 / 64），另有**四处陈旧注释或不一致数值**（`xlerobot_home.xml:27` 注释的 "1.6 m" 门洞 vs 几何 3.0 m、`home_scene.py` 的 "four-room" 命名 vs 5 个房间、nav2.yaml 共享足迹 0.46 × 0.44 m vs Gazebo SDF 碰撞体 0.65 × 0.55 m、MCP 工具"8 个"vs 代码 9 个）。

**给学生的操作建议：引用行号前先 `git rev-parse HEAD` 确认提交；把常量名当作稳定锚点，把行号当作线索。** 需要长期稳定的引用时，用符号（`ALLOWED_ACTION_KEYS`、`FAR_REGION_FACTOR`、`HOME_ROOMS`、`MAX_KEYFRAMES`）而不是行号。
