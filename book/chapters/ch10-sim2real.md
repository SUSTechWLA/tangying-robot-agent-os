# 第 10 章 仿真、真机与 sim2real

> **本章的核心命题**
>
> sim2real 的第一课**不是**"怎么让它成功"，而是"**怎么让它在不知道的时候失败**"。
>
> 而 sim2real gap 最常见的根因**不是"模型不准"**，而是**一个参数被用来表达两件事**。

---

## 10.1 为什么一个项目要同时有三个仿真后端

先说一个容易搞错的结论：**三个"后端"不是一个层级上的三个东西。**

| 后端 | 它是什么 | 它回答的问题 |
| --- | --- | --- |
| **MuJoCo**（`sim/mujoco/`） | **物理引擎 + 家居场景 + 工具注册表**，主参考后端 | "任务链在理想物理下能不能闭合" |
| **RoboCasa**（`sim/robocasa/`） | **构建在 MuJoCo 之上**的资产/场景层 | "两个机器人共享一个物理世界时，独占与交接在物理上成不成立" |
| **Gazebo**（`sim/gazebo/`） | **独立的引擎与独立进程** | "我们的策略里有多少是 MuJoCo 特有的假设" |

**证据**：RoboCasa 的 `world.py:1` docstring 是 *"One shared RoboCasa physics state exposed as two robot-local tool views"*，`:12` 直接 `import mujoco`，`:14` `from tangying_sim.tools import ToolResult, default_tool_registry`。

**第三个价值可以被量化**：同一个探索策略，MuJoCo **99.0%**、Gazebo **79.3%**，而差距**不在策略**——在两个户型要求**相反**的盲区半径假设（8.5）。

### 接口是统一的，而且证据是硬的

三者共享**同一条 gRPC 契约**：

| 证据 | 位置 |
| --- | --- |
| `service RobotRuntime` 七个 RPC | `proto/robot/v1/robot.proto:9-18` |
| **RoboCasa 根本没有自己的 service 类** | `sim/robocasa/tangying_robocasa/fleet_server.py:14` `from tangying_sim.server import RobotRuntimeService` |
| Gazebo 复用网关的服务实现 | `gazebo_runtime_node.py:461,474-476` |
| **工具层不知道背后是谁** | `sim/mujoco/tangying_sim/server.py:489` 把技能交给 `self.world.tools.execute(...)`，而 `tools.py` 只通过 `ToolContext` 拿一个 `world` |
| **服务目录也只有一份实现** | `ServiceRegistry` 被 MuJoCo（`server.py:95-96`）、Gazebo（`gazebo_runtime_node.py:627,642`）和实机网关（`service.py:139-140`）共用 |

Gazebo 侧的报告直接写（`docs/experiments/2026-09-20-gazebo-exploration-coverage.md:111`）：

> **没有第二套实现**：调查循环、前沿策略、占据栅格、地图发布**全部是 MuJoCo 用的那一份代码**。

### Go 侧根本不枚举后端

这是"agent 对后端无感知"的实现方式。在非测试 Go 代码里 grep：

| 词 | 出现次数 | 位置 |
| --- | --- | --- |
| `gazebo` | **0** | 唯一出现处是测试 `tasks/grounded_test.go:14,25` |
| `robocasa` | **1** | `cmd/edge-worker/main.go:414` |
| `mujoco` | **3** | `main.go:341`（默认 adapter）、`main.go:414`、`tasks/service.go:158` |

**后端身份由运行时自报**：`proto/robot/v1/robot.proto:72` 的 `RuntimeInfo.adapter`。

而 `tasks/service.go:152-165` 的 `NormalizeAdapter` **只归一既有别名**，`default` **原样小写透传**。

**后果有两面**：

| 面 | 后果 |
| --- | --- |
| 好 | 新增后端**零 Go 改动** |
| **坏** | **Gazebo 的能力缺口只在 Python 侧可见，Go 层看不出任何异常** |

**教学要点**：这是一条通用的架构取舍——**"零改动扩展"的代价是"扩展的缺陷对上层不可见"**。

新增一个后端不需要改 Go，很好。但如果那个后端不支持 `ExecuteSkill`，**Go 层看不到任何异常信号**——它只会在调用时收到一个错误码。

### Gazebo 的状态：必须两层表述

这是本章一个必须精确处理的地方，因为**文档自身就有一个陷阱**。

| 时间 | 结论 | 出处 |
| --- | --- | --- |
| 2026-09-19 | "Gazebo 现在**可被观测，还不可被驱动**" | `docs/development/2026-09-19-gazebo-backend-status.md:151` |
| **同一文档 `:3-8`** | **已声明上述结论在 2026-09-20 不再成立** | — |
| 2026-09-20 | 现在**可被观测、可被驱动建图**（14 个建图服务、控制台 `Adapter=gazebo`） | `docs/experiments/2026-09-20-gazebo-exploration-coverage.md:15-16` |

**但当前状态也不是"全通了"**：

> **`ExecuteSkill`、`Cancel`、`EmergencyStop` 至今仍按名字拒绝**（`:113`）。

原文：

> **急停那条尤其重要：报一个没有执行的停止，比什么都不报更糟。**

**所以 Gazebo 上没有抓取/放置**——抓放回归由 RoboCasa 覆盖（`docs/guides/gazebo-house-operations.md:50`）。

**而这不是"接口没接完"，是有意的安全选择。**

### 一个仍然有教学价值的旧缺陷

**预构建镜像烘焙的是构建时刻的契约。**

改 proto 不重建镜像，agent 与机器人就**各说各话**（`:113-125`）。

**教学要点**：这是一个**分布式系统的经典陷阱**——当协议的两端来自**不同时间点的构建**时，"同一个版本号"不保证"同一个 schema"。

同类问题在任何"预编译镜像 + 动态协议"的系统里都会出现。

---

## 10.2 无 GPU、无 Docker 也能跑：五房间家居场景怎么来的

### 场景是代码构造的，不是下载的数据

```python
# sim/mujoco/tangying_sim/home_scene.py:23
HOME_ROOMS = ("living_room", "home_corridor", "kitchen", "bedroom", "bathroom")
```

五房间 + 一张房间邻接表（`:100-104`），带停靠点坐标（`:86-97`）。

文件 docstring 说明场景"只包含真实 RGB-D 相机能看到的东西"：墙、家具轮廓、门洞和有纹理的地板。而（`:1-6`）：

> **房间名与航点只是规划元数据，永不作为仿真真值出现在 RGB-D 观测里。**

**这一条至关重要**：如果房间名出现在观测里，那么"机器人知道自己在客厅"就是**仿真给的**，而不是**它感知出来的**。那整个 SLAM/定位/grounding 链条就失去了意义。

### ⚠️ 两处陈旧注释：一个关于"注释会腐烂"的实例

| # | 冲突 | 真相 |
| --- | --- | --- |
| 1 | `home_scene.py:1` docstring 与 `:21` 的 `HOME_SCENE_REVISION = "home-4room-rgbd-v2"` 都写 **"four-room"** | `:23` 的 `HOME_ROOMS` 是**五个**房间。`README.md:254` 也写"四房间"，而 `docs/operations/release-checklist.md:8` 要求"确认五个房间" |
| 2 | `sim/mujoco/assets/xlerobot_home.xml:27` 注释写 **"1.6 m central door opening"** | 同一文件 `:28-29` 的几何实测是 **3.0 m** |

**以常量和几何为准**：五个房间，中央门洞 3.0 m。"4room" 是遗留的 revision 字符串。

**教学要点**：**注释会腐烂，常量不会。**

而这里有一个更深的教训：`HOME_SCENE_REVISION` 这个**被证据引用的标识符**不能随便改——改了它，所有引用它的历史记录就对不上了。所以作者选择了保留错误的字符串，而不是修正它。

**"标识符一旦被证据引用，就不能随便改"** —— 这是一条很实际的工程约束。

### 房间几何（可直接用于课堂算净空）

| 量 | 值 |
| --- | --- |
| 房屋外轮廓 | **9.0 m × 11.0 m** |
| 墙半厚 | 0.08 m |
| 墙高 | 2.4 m |

四个门洞实测：

| 门 | 开口 |
| --- | --- |
| 客厅 → 走廊 | **3.0 m**（x ∈ [−1.5, 1.5]） |
| 走廊 → 厨房 | **1.5 m**（x = 0.8，y ∈ [2.6, 4.1]） |
| 走廊 → 卧室 | **1.5 m**（x = −0.8） |
| 卧室 → 卫生间 | **1.2 m**（y = 5.15，x ∈ [−2.65, −1.45]） |

而**底盘包络只需约 0.7 m**。

**这组数字是 8.5「门不是瓶颈」那个结论的基础。**

### 资产：靠断言固定版本，不靠 URL

两套资产，各有 pin 与许可，**没有网络下载器**：

| 资产 | pin | 许可 | 强制方式 |
| --- | --- | --- | --- |
| 家具：AWS RoboMaker Small House World | `ff9631ca6d1db9c1ba656498151464b5ab74aafe` | **MIT-0** | `prepare_furnished_home.py:351-352` 用 `git` 读实际 revision 并断言：`if revision != SOURCE_REVISION or dirty: raise ValueError(...)` |
| 机器人：XLeRobot MuJoCo 模型 | `3d14695e40c9c68229c0aacffca6053c75cd3eb6` | **Apache-2.0** | `sim/mujoco/assets/xlerobot/PROVENANCE.md:1-8`，随目录保留上游 LICENSE |

**所以"固定版本"是靠断言而非靠 URL**：脚本要求本地已有一份 **clean 的 pinned checkout**（`prepare_furnished_home.py:320`）。

而每个 mesh/texture 都记 SHA-256，运行时由 `furnished_home.py:13-25` 的 `_validated_file()` 复核，**不符即 `ValueError`（失败关闭）**。

**教学要点**：这是一个**可复现性**的好例子。

| 做法 | 失效模式 |
| --- | --- |
| 下载 URL | 上游改了内容，URL 不变 |
| 记录版本号 | 记录对了但实际用的是另一份 |
| **读实际 revision 并断言 + 每个文件记 SHA-256** | **不符就失败** |

### 一句必须写进教材的免责声明

`PROVENANCE.md:16-21`：

> This pinned official model … is **not a calibrated digital twin** of later two-wheel XLeRobot revisions,
> and its dimensions, dynamics, sensors, and calibration must **not** be treated as current two-wheel hardware ground truth.

**"不是标定过的数字孪生"** —— 这句话应该出现在每一个用开源机器人模型的项目的 README 里。

### 无 GPU 的真实代价

先纠正一个常见误解：**仓库代码从不设置 `MUJOCO_GL`**。全仓只有三处引用，都在 CI、文档与注释里。实际后端由 `mujoco` 包在 import 时自行决定。

真正做工程应对的是两处：

**① CI 显式走软件渲染**（`.github/workflows/ci.yml:16,161`）：`MUJOCO_GL: 'osmesa'` + 安装 `libosmesa6`。

`docs/production/testing-and-acceptance.md:34` 记录：

> GitHub runner 是两核、`MUJOCO_GL=osmesa` 的软件渲染环境，**整个套件比开发机慢约 2.5 倍**。

**② 为 OSMesa 下的永久阻塞加了边界**（`rendering.py:17-23`）：

> under CI software rendering (`MUJOCO_GL=osmesa`) a capture has been observed to **block inside `mjr_render` indefinitely**.
> Without a bound the caller waits forever: the camera loop stops without an error, and a test run is killed by the outer timeout before it can report which capture stalled.

**实现**：`DEFAULT_RENDER_TIMEOUT_S = 60.0`（`:23`）+ 环境变量 `TANGYING_RENDER_TIMEOUT_S`（`:27`）+ 一旦超时就置 `self._unresponsive = True`，**之后所有请求快速失败**而不是继续排队等待（`:86-110`）。

**教学要点**：这是"**卡死比失败更糟**"的一个实例。

一个无限阻塞的调用会：
1. 不给错误；
2. 让超时无法归因（你不知道是哪一次采集卡住了）；
3. **让整个测试运行被杀**。

**"一旦超时就置 unresponsive，之后快速失败"** 也是对的：继续接受请求只会让队列堆积（第 2 章修复 2 的"轮询比端点响应还快"是同一个模式）。

### 实测成本数字

`docs/development/mujoco-compatibility.md`（作者自己限定"是特定机器上的短期实验，不是任意硬件的性能承诺"）：

| 项 | 值 |
| --- | --- |
| Linux ARM64、4 CPU 配额、OSMesa 下单次观测中位 | **514–515 ms** |
| 其中 `mjr_render` | **439 ms** |
| PNG 编码 | 约 1.5 ms |
| 改单主光源投阴影后 | **272–274 ms** |
| 冷启动首个底部相机测试 | **3.21 秒**（渲染器首次建立图形上下文时，提前冻结的世界/编码器快照已超 2 秒采集预算） |

**（推断）把这三个数放在一起，无 GPU 的 sim2real 含义就清楚了**：

> **渲染是采集链路里最慢的一环（约占 85%）**，而"降低分辨率/阴影"这类优化只能把 515 ms 压到 273 ms——**仍然远超 100 Hz–1 kHz 的控制量级**。

**这从另一个方向印证了第 1 章的分层结论**：仿真里的感知回路天生就在 1–4 Hz，**不能承担控制频率**。

### 最小可跑路径

```bash
make home-furnished                 # 控制台 http://127.0.0.1:8897/
bash scripts/sim-stack.sh status --artifacts-dir artifacts/sim-stack/furnished-home
bash scripts/sim-stack.sh stop   --artifacts-dir artifacts/sim-stack/furnished-home
```

`make home-furnished` 实际做的事（`Makefile:113-114`）：

```make
home-furnished: build
	bash scripts/furnished-home-demo.sh start --sim-port 50161 --agent-port 8897
```

而 `furnished-home-demo.sh` 三段（`:1-38`）：

1. 检查 `collada` 转换依赖，缺了就报安装命令；
2. 若 `artifacts/sim-assets/aws-small-house` 不存在，跑 `prepare_home_world.py`；然后 `prepare_furnished_home.py` 产出 `artifacts/sim-assets/furnished-home`；
3. `exec bash scripts/sim-stack.sh <start|restart> ... --scene home_task --perception rgbd --home-assets "$DEMO_PACK"`

**一个关于端口的细节**：`sim-stack.sh` 自己的默认 agent 端口是 **8787**，而 Makefile **显式传 8897**。

**为什么？** 为了**不让读者打开一个没人监听的端口**——因为家庭场景的文档写的是 8897。

（这也是第 1 章 §1.5 提到的那个"两个都对"的端口差异。）

---

## 10.3 机器人适配层：换机器人不用重写任务与工具

### 三个概念的关系

```
RobotProfile    ← 声明（不是证据）
     ↓
PluginBackend   ← 把规范工具绑到已交付驱动上，但不假定它的关节
     ↓
RobotRuntimeService   ← 安全边界：审批、限幅、租约、日志、急停
```

**`RobotProfile` 的包注释是一句判决**（`core/robotcontract/contract.go:1-3`）：

> **A profile is a declaration, never evidence of hardware commissioning.**

**"profile 是声明，永远不是硬件已交付的证据。"**

结构（`contract.go:41-53`）：

```go
type Profile struct {
	SchemaVersion, RobotID, AdapterID, AdapterVersion, ModelID, Embodiment string
	Joints         []Joint
	EndEffectors   []EndEffector
	Sensors        []Sensor
	ActionLimits   map[string]ActionLimit
	Tools          []string
}
```

**Python 侧对等实现在 `contracts.py`，Go/Python 双向校验。**

### 抽象基类只有五个方法

```python
# robot/gateway/tangying_robot_gateway/backend.py:62-82
class RobotBackend:
    def capabilities(self) -> RuntimeInfo: ...
    def observe(self, request: ObservationRequest) -> Observation: ...
    def execute(self, command: Command) -> Result: ...
    def cancel(self, command_id: str, reason: str) -> bool: ...
    def stop(self, reason: str) -> None: ...
```

**五个方法。**

**教学要点**：**接口的大小决定了适配一台新机器人的成本。** 五方法意味着一个新本体要写五个函数——这是一个可以在一天内估出工作量的接口。

### `PluginBackend` 的 docstring 值得引用

（`plugin_backend.py:81-92`）：

> Constructing an adapter **never connects, arms or moves hardware**.
> Physical tools remain unavailable unless the local readiness callback explicitly reports
> that the driver has been armed.

**"构造一个适配器从不需要连接、解锁或移动硬件。"**

而 `physical_ready` 是**读状态的回调，禁止在回调里顺便 arm**（`:195`）。

**教学要点**：这是一个"**构造与激活分离**"的设计。

一个常见的安全问题：`__init__` 里顺便做了硬件初始化。**这个项目明确禁止它**——构造只建立对象，硬件可用性由一个独立的回调回答。

### 新增一台机器人要做什么

`docs/development/robot-adapters.md:293-297` 的完整清单：

| # | 项 |
| --- | --- |
| 1 | 本地适配器 Python 包 / factory |
| 2 | 该型号的 profile 配置（`robot.profile.v1`） |
| 3 | 规范感知转换器（`observation_provider()` 必须返回 `scene.reconstruction.v1`） |
| 4 | 工具 handlers |
| 5 | 驱动依赖及测试 |
| 6 | 若动作需要策略，增加对应 policy manifest 与推理服务 |
| 7 | 部署侧：设备登记、凭据、证书、启动环境和现场验收材料 |

**落地机制是能力驱动而非设备驱动**：Agent 只查运行时**自报的能力**（`edge/runtime/runtime.go:131-164` 的 `Snapshot.Capability` / `CanExecute`），这张能力表由 `profile.tools` 生成，**与机型无关**。

工具词汇表是固定的 **13 个规范工具**（`contracts.py:25-29`，Go 对等 `contract.go:110-115`）。

**端到端证据**：`tests/contract/test_heterogeneous_runtime_boundary.py:250-278` 让**真实 Go 七步计划**穿过 **arm** 与 **mobile+lidar** 两种完全不同的本体，并断言最终 `inside:tray` 关系与位姿。

### ⚠️ 但这句话有一条必须一起讲的边界

`docs/development/2026-09-18-system-review-and-improvement-plan.md:346-352` 3.5 的标题就叫：

> 可扩展性有硬编码枚举（**与「换机器人不用重写」的声明有差距**）

| 要做的事 | 必须改的地方 |
| --- | --- |
| 新增一个工具 | `skills/manipulation/plugin.go:21`、`core/robotcontract/contract.go:110` 与 `:120`（**两个手写白名单**）、新失败码还要加 `core/closedloop/closedloop.go:83` |
| 新增一个机器人本体 | `examples/robots/*.profile.json` + `contract.go:135`（`validSource`）、`:139`（`Embodiment` oneOf，**硬编码枚举**） |
| 新增一种传感器 | `core/observation/envelope.go:20-27` |

原文结论：

> 上一轮"可扩展性落地"把物体物理属性做成了数据，但**白名单和枚举还是代码**。

**所以准确的表述是两句话**：

| 声明 | 成立吗 |
| --- | --- |
| "**任务与工具不重写**" | ✅ **成立**（MCP 工具名、Fleet 任务接口、Go gRPC 客户端、任务层全部复用） |
| "**新增能力/本体/传感器零代码改动**" | ❌ **不成立** |

**教学要点**：**把这两句分开讲，比只引用 README 那句更有价值。**

一个"可扩展"的声明，必须问清楚：**扩展的是哪一层？** 这里是"任务层可扩展，类型层不可扩展"。

而"什么时候不需要改"也有明确答案（`robot-adapters.md:297`）：

> 在**现有工具语义和 schema 能表达需求**时，通常不需要修改 MCP 工具名、Fleet 任务接口、Go gRPC 客户端或给前端加入厂商 SDK。

**"能表达需求时"** —— 这个限定词是关键。

### 无硬件的可复现验证

```bash
.venv/bin/python -m tangying_robot_gateway.run_plugin schema > /tmp/tangying-robot-contracts.json
.venv/bin/python -m tangying_robot_gateway.run_plugin check --profile examples/robots/arm.profile.json
.venv/bin/python -m tangying_robot_gateway.run_plugin check --factory examples.robots.simulated:arm
```

`check --factory` 会**真的构造适配器、读一帧、检查来源与新鲜度**，然后 `stop` + `disconnect`（`:50`）。

**⚠️ 注意**：仓库里的异构示例被明确标记为 **`SIMULATION`**（`:5`）：

> 它们**不模拟物理动力学**，也不是 RGB-D、LiDAR、导航或厂商硬件驱动。

**教学要点**：这是一个"**示例代码的边界声明**"。一个叫 `examples/robots/arm` 的东西，如果不说清它不模拟动力学，读者会以为它是一台可用的仿真机械臂。

### profile 的四条不变量

| # | 不变量 |
| --- | --- |
| 1 | profile 在**一个 Runtime 生命周期内不可变**（`:89`） |
| 2 | `tools` 是**承诺的能力集合**，`available` 是当下能否用 |
| 3 | 缺 handler 的工具**标为不可用** |
| 4 | `physical_ready` 未提供 / 返回 False / 抛异常时，**物理动作不会自动变可用**（`:85`） |

**第 2 条值得单独说**：`tools` 和 `available` 是**两个不同的东西**。

| | 含义 | 变化频率 |
| --- | --- | --- |
| `tools` | **承诺**（这个型号应该能做这些） | 一次交付时确定 |
| `available` | **当下**（现在能不能做） | 随标定、故障、急停变化 |

**把两者合并，你就无法区分"这台机器人不会做这件事"和"这台机器人现在做不了这件事"** —— 而它们的处置方式完全不同。

---

## 10.4 标定：16 个电机、四层失败关闭

### 参数清单

schema 是 `robot.calibration.v1`（`calibration.py:40`）。

| 分组 | 总线 | 数量 | 参数名 |
| --- | --- | --- | --- |
| 左臂 | `left`（`/dev/tangying-left`） | 6 | `shoulder_pan`、`shoulder_lift`、`elbow_flex`、`wrist_flex`、`wrist_roll`、`gripper` |
| 右臂 | `right`（`/dev/tangying-right`） | 6 | 同名六个；**夹爪是每条臂的第 6 个关节** |
| 头部与底盘 | `shared` | 4 | `head_motor_1`、`head_motor_2`、`base_left_wheel`、`base_right_wheel` |
| 相机 | — | 2 | `head-rgbd`、`base-rgbd` |

**共 16 个电机**。

权威常量在 `calibration.py`：`ARM_SIDES=("left","right")`（`:47`）、`ARM_JOINTS`（`:48`）、`ARM_MOTOR_IDS`（`:53-55`）、`HEAD_AND_BASE_MOTOR_IDS`（`:56-59`）、`MOTOR_IDS`（`:61`）、`MOTOR_BUS`（`:65-67`）、`CAMERA_NAMES`（`:45`）。

### 叶字段与取值范围（照做时按这个填）

| 位置 | 精确字段 | 约束 |
| --- | --- | --- |
| `motors.<name>` | `{"id","drive_mode","homing_offset","range_min","range_max"}` | `homing_offset` ∈ [−2047, 2047]；`range_min` ∈ [0, 4094]；`range_max` ∈ [1, 4095] 且 `range_min < range_max`；`id` ∈ [1, 253]；`drive_mode` ∈ {0,1} |
| `cameras.<name>` | `{"sourceId","width","height","intrinsics","distortion","extrinsics"}` | — |
| `…intrinsics` | `{"fx","fy","cx","cy"}` | `fx,fy ≥ 1.0`；`cx ∈ [0, width−1]`；`cy ∈ [0, height−1]` |
| `…distortion` | `{"model","coefficients"}` | `plumb_bob` 必须 5 个系数；`none` 必须为空 |
| `…extrinsics` | `{"parentLink","xyz","rpy"}` | xyz/rpy 各 3 个数 |
| `geometry` | `{"gripper":{"openM","closedM"}}` | — |
| `safety` | `{"maxRelativeTargetDeg","maxActionChunkLength","maxLinearSpeedMPerS","maxAngularSpeedRadPerS"}` | — |

### 纠正常见误用

**仓库里不存在** `T_base_camera`、`hand_eye`、或名为 `extrinsic` 的 4×4 矩阵字段。

| 你以为的名字 | 真实的等价物 |
| --- | --- |
| `T_base_camera` | **`base_from_camera`** —— proto `RGBDFrame.base_from_camera`（`robot.proto:148`，16 个 double，row-major 4×4，光学系 → 机器人 base） |
| `hand_eye` | 手眼结果落在 **`cameras.<name>.extrinsics`** |
| `extrinsic` | 同上 |

计算函数：`base_from_camera(robot_from_base, world_from_camera_link, *, camera_frame_is_optical=False)`（`gazebo_bridge.py:131-154`）。

**相机外参一律用光学系（右/下/前）**，MuJoCo 的右/上/后由一次性折算：

```python
_OPTICAL_FROM_MUJOCO = np.diag([1.0, -1.0, -1.0])    # sim/mujoco/tangying_sim/calibration.py:35
```

**所以同一组数字在仿真与实机上含义相同。**

**教学要点**：这是一个"**坐标系约定必须统一**"的实例。如果仿真用 MuJoCo 系、实机用光学系，那么**同一份标定文件在两边的含义不同**——而这是最难发现的一类 bug，因为它不会报错，只会让机器人偏一点。

### 版本校验 = 内容哈希 + CAS

```python
# calibration.py:105-106
CONTENT_FIELDS = ("schemaVersion","robotId","adapterId","source","motors","cameras","geometry","safety")
```

**`updatedAtUnixMs` 刻意不进哈希**，注释写明是"who/when"的元数据：

> **re-saving identical numbers keeps the same identity.**

**"重新保存同样的数字，保持同一个身份。"**

CAS 在 `save_calibration` 里比对 `expected_revision` 与 `calibration_revision(current)`，不符抛 **`REVISION_CONFLICT`**（`:366-373`）。

**落盘是校验 → CAS → 原子写**（`:356-386`）：

```
tempfile.mkstemp(dir=..., prefix=f".{file.name}.", suffix=".tmp")   # :376
os.fsync                                                            # :381
os.replace(temporary, file)                                         # :382
```

**三个步骤缺一不可**：

| 步 | 缺了会怎样 |
| --- | --- |
| temp 文件在**同目录** | `os.replace` 跨文件系统会失败 |
| `fsync` | 掉电后可能是空文件 |
| `os.replace` | 读者可能看到半个文件 |

### 磁盘路径（仿真与实机不同名，别填错）

| 场景 | 文件名 | 根目录 |
| --- | --- | --- |
| **仿真** | `calibration.json` | `TANGYING_SIM_CALIBRATION_DIR` 或 `--calibration-dir`；演示默认 `artifacts/calibration/furnished-home` |
| **实机 XLeRobot** | `tangying-xlerobot.json` | `XLEROBOT_CALIBRATION_ROOT` → `XLEROBOT_CALIBRATION` → `/var/lib/tangying-robot-agent-os/calibration` |

`root=None` 时仿真标定**只在内存、不落盘**（`sim/mujoco/tangying_sim/calibration.py:103-105`）。

### 四层失败关闭

**标定没做或与模型矛盾时，失败关闭，而且分四个层级。** 这是本节最重要的部分。

**第 1 层：能力层（缺失）**

```python
# xlerobot_adapter/driver.py:104-122
if not self.path_exists(self.calibration_file):
    blockers.append("CALIBRATION_REQUIRED")
...
return DriverCapabilities(manipulation_ready=not blockers, blockers=tuple(blockers), ...)
```

而 `:151-153` 进一步：

```python
if not capabilities.manipulation_ready:
    return DriverResult(False, "ROBOT_NOT_READY", ...)   # ← 不下发任何动作
```

**物理工具因此不可用**——**不是"能用但提醒你"**。

其他 blocker：`CALIBRATION_INVALID`（`:137,143`）、`CALIBRATION_EMPTY`（`:139`）。

`core/closedloop/closedloop.go:134` 把 `CALIBRATION_REQUIRED` 列为**阻断码**，`tool_layer.py:101` 把它映射为 `PERMISSION_DENIED` / `RecoveryClass.PERMISSION`（**禁止自动重试**）。

**第 2 层：模型一致性层（矛盾）**

`sim/mujoco/tangying_sim/calibration.py` 的 `_verify_against_model`（`:129-169`）在**构造期**就拒绝与所渲染模型矛盾的文档，抛 **`DOCUMENT_CONTRADICTS_MODEL`**：

| 检查 | 容差 |
| --- | --- |
| 镜头不符 | — |
| `parentLink` 错 | — |
| 安装位移 | **> 5 mm**（`MODEL_MOUNT_POSITION_TOLERANCE_M`） |
| 安装旋转 | **> 2°**（`MODEL_MOUNT_ROTATION_TOLERANCE_DEG`） |
| 焦距 | **> 2%** |

无舵机条目时无法把读数换成角度 → `MOTOR_CALIBRATION_MISSING`（"**无法把读数换算成角度**"）与 `MOTOR_CALIBRATION_INVALID`。

工位不符则在派发前拒绝：`rgbd_runtime.py:687-693` 的 `Fault(code="WORKCELL_CALIBRATION_MISMATCH", severity="blocked", remedy="operator_assist")`。

**第 3 层：地图层（变化）**

**标定一变，已启用地图立即失效。**

| 位置 | 行为 |
| --- | --- |
| `robot_workflow.py:1376` | 加载地图时比对 `calibrationRevision`，不匹配抛 `LOCALIZATION_REQUIRED` |
| `:368-369` | 扫描中若标定被改，抛 `CALIBRATION_CHANGED`（"标定发生变化，请重新扫描。"） |
| `map_manifest.py:235-237` | `INVALID_HASH`（"calibrationRevision must be a 64 character content hash"） |
| `map_catalog.py:64-68` | `ValueError('map, robot, frame or calibration revision mismatch')` |

**第 4 层：契约层（处处失败关闭）**

| 位置 | 错误 |
| --- | --- |
| `contracts.py:214-216` | `ValueError("reconstruction sensor frame/type/calibration does not match profile")` |
| `ros_rgbd.py:231-236` | `ValueError("TF identity, capture time or calibration revision does not match")` |
| `core/robotcontract/contract.go:234-237` | 同上 |
| `edge/policy/manifest.go:176-177` | `ErrPolicyIncompatible` |

**四层都失败关闭。**

**教学要点**：这是一个"**同一个不变量在四层各被检查一次**"的例子。

看起来冗余，但每一层**检查的是不同的问题**：

| 层 | 检查什么 |
| --- | --- |
| 1 | 标定**存在吗** |
| 2 | 标定**和这台机器一致吗** |
| 3 | 标定**和这张地图一致吗** |
| 4 | 标定**和这份数据一致吗** |

**这与第 5 章 `ScopeOf` 那个"两道防线防的是不同改动"是同一个判断标准。**

### ⚠️ 一个重要纠正：标定没有「过期」概念

**全仓 grep 不到** `CALIBRATION_STALE` 或 `CALIBRATION_EXPIRED`。

标定文档**没有失效时间**，唯一的 `updatedAtUnixMs` 只校验 `minimum=0` 且**不进哈希**。

**本子系统里的"过期"一律指观测陈旧或租约过期**，不是标定失效：

| 位置 | 含义 |
| --- | --- |
| `contracts.py:218-221` | `"reconstruction is stale or dated in the future"`（未来容差 250 ms + `max_age_ms`） |
| `robot/gateway/.../rgbd.py:84` | `"RGB-D capture is stale or future dated"`（`DEFAULT_MAX_AGE_MS = 2000`，`:50`） |

**所以正确的说法是**：

> **"标定缺失或与模型/地图不一致时失败关闭"，而不是"标定过期"。**

**教学要点**：这是一个**措辞精确性**的问题，而且它有实际后果。

如果文档说"标定会过期"，用户会去找"怎么续期标定"——而**没有这个操作**。正确的表述会引导他去检查"标定和当前地图是否一致"。

### 一个最漂亮的教学设计：守卫之守卫

**两个测试文件问的是两个不同的问题：**

**`test_calibration_ground_truth.py`**：问"**标定是否描述了渲染器真正用的那台相机**"。

docstring（`:1-12`）指出：

> 其他 RGB-D 测试只验证"自洽"（帧内变换下重投影一致），而**自洽在变换把相机指向完全错误方向时依然成立**。

真值函数 `true_world_from_camera(model, name, tilt, pan)`（`:41-55`）直接：

```
设 head_tilt_joint / head_pan_joint 的 qpos
→ mujoco.mj_forward
→ 读 data.cam_xmat[camera_id] @ _OPTICAL_FROM_MUJOCO 与 data.cam_xpos[camera_id]
```

**ground truth 就是 MuJoCo 本身。**

容差极紧：位置 `max|Δ| < 1e-9`、旋转 `< 0.01°`、内参 `abs=1e-9`。

**`test_calibration_self_check.py`**：问"**栈能不能启动在一份与它所渲染模型矛盾的标定上**"——要求拒绝发生在**普通构造路径**里，断言 `pytest.raises(CalibrationError)` + `code == "DOCUMENT_CONTRADICTS_MODEL"`，且**消息必须点名具体相机与偏差量**（含 `"base-rgbd"`、`"mm"`、`"chassis"`、`"fx"`）。

**而"守卫之守卫"是这两行**（`test_calibration_self_check.py:203-204`）：

```python
assert MODEL_MOUNT_ROTATION_TOLERANCE_DEG < 120.543
assert MODEL_CAMERA_FOR["head-rgbd"] == "head_depth"
```

**理由**：

> 容差若宽于它所防的那个缺陷（存错 **120.543°** 的旋转），整个测试文件就会变成**永远通过**。

**这条断言是在测试"测试本身是否还有意义"。**

**教学要点**：这是本书里最漂亮的一个测试设计，值得单独命名——**守卫之守卫**（a guard for the guard）。

| | 普通守卫 | 守卫之守卫 |
| --- | --- | --- |
| 断言 | "错误的输入会被拒绝" | "**这个拒绝阈值比它要防的错误更严**" |
| 失效模式 | 检查没生效 | **检查永远不可能生效** |
| 例子 | `DOCUMENT_CONTRADICTS_MODEL` 会抛出 | `TOLERANCE < 120.543` |

**为什么重要**：一个"永远通过"的检查比没有检查更糟——**它给人一种已经检查过的错觉**。

（这与第 3 章"所有的 `Freshness` 都写死为 `"FRESH"`，所以过期规则是死代码"是同一个失效模式。）

### 尚未实现（不要过度声称）

| 未实现 | 状态 |
| --- | --- |
| ROS 侧标定封装（`camera_calibration` / `easy_handeye2` / `aruco_ros`） | 未做 |
| 各精度门禁的自动判定（重投影 RMS < 0.5 px、手眼 < 5 mm / < 0.5°、里程计 < 2%） | 未做 |
| **相机引导标定** | 未做 |
| 全部实机现场验收 | 未做 |

**引导向导目前只采舵机**，没有相机参数时在**开始前**就拒绝（`scripts/calibrate_guided.py:83-85`）——共 **20 步**。

`docs/development/arm-moveit-adapter.md:409` 亦承认手眼标定在 MoveIt 适配里未验证，`frame_id != "base_link"` 直接 `IK_UNAVAILABLE`（`:155-157`）。

---

## 10.5 探索与覆盖率：三组真实实验

**这一节的价值在于：它展示了同一个问题被三次推进，而每次的结论都推翻了上一次的直觉。**

### 第一组（2026-09-15）：38.9% → 81%

**实验设计**：同一台机器人、同一个装修家庭场景、同一套探索入口，**只差四处改动**。

| 指标 | 改前（22 m / 1 段） | 改后（60 m / 3 段） |
| --- | --- | --- |
| 已探明（整图） | **38.9%** | **81%** |
| 整图未知格占比 | 61% | **19%** |
| 行驶里程 | 22.2 m | 53.7 m |
| 进入卫生间 | **0% 轨迹点**（74% 未知） | **11% 轨迹点**（20% 未知） |
| 走廊 / 厨房未知 | 0% / 11% | 0% / **0%** |
| 卧室未知 | 37% | 20% |
| 客厅未知 | 57% | 25% |
| 关键帧 | 未记录（旧上限 400） | **263 / 600** |
| 深度点不足 | **整段崩溃，地图丢失** | 跳过并继续，正常发布 |

**四处改动，全在参数与选择策略上**：

| # | 改动 | 细节 |
| --- | --- | --- |
| 1 | 新增 `FAR_REGION_FACTOR = 2.5` 与 `Frontier.region_gain` | 远处连通块必须**明显更大**（≥2.5 倍）才值得开过去 |
| 2 | `DEFAULT_EXPLORE_TRAVEL_M` **40 → 75.0** | 全屋一轮 50–60 m |
| 3 | `MAX_KEYFRAMES` **400 → 600**；关键帧间距 0.10 m/0.16 rad → **0.14 m/0.22 rad** | 预览字节预算 8→12 MB |
| 4 | 深度点不足从"抛异常终止整段"改为**跳过该帧并计数** | 连续 25 帧才优雅结束该段（`stopReason="depth_starved"`） |

**第 1 条有一个被测试抓出来的错误，值得单独讲**：

> 第一版按"**效用/米**"写是错的——**效用除以距离永远偏向近处**，被测试抓出来。

**教学要点**：**"效用/米"是一个看起来很合理的启发式，而它在数学上必然偏向近处。**

因为距离在分母上，任何有限的效用除以一个更小的距离都会变大。**所以这个"效率指标"实际上是一个"距离指标"。**

这是一个很好的例子：**一个直觉上正确的目标函数，可能在数学上表达了一个完全不同的目标。**

**"门不是瓶颈"是怎么得出的**

先**直接从 MuJoCo 模型量墙体几何**：

| 门 | 开口 |
| --- | --- |
| 客厅 → 走廊 | x ∈ [−1.5, 1.5] |
| 走廊 → 厨房 | y ∈ [2.6, 4.1] |
| 卧室 → 卫生间 | x ∈ [−2.655, −1.445] |

再数**旧地图上每条门线的通行格子**：

| 门 | 自由格 | 占用格 | 未知格 |
| --- | --- | --- | --- |
| 客厅 → 走廊（y=1.0） | 61 | 16 | 3 |
| 走廊 → 厨房（x=0.8） | 28 | 22 | 0 |
| 走廊 → 卧室（x=−0.8） | 48 | 0 | 2 |
| 卧室 → 卫生间（y=5.15） | 20 | 1 | **29（58% 未知）** |

**前三个门都有实打实的通道；只有卫生间门线一半没被观测到**——

> **那不是"门挡住了"，是机器人根本没走到能看见它的地方。**

关键对照：客厅门 **3.0 m**、厨房门 **1.5 m**、卧室门 **1.5 m**、卫生间门 **1.21 m**，走廊净宽 1.55 m，而**底盘包络只需约 0.7 m**。

**真正的瓶颈是目标选择偏爱近处**：同一段 22.3 m 里，走廊（**已经 0% 未知**）占 **28%** 轨迹点、厨房（11% 未知）占 **26%**，而最远的卫生间 **0%**。

**最有力的一条对照**：

> 改前 22.2 m 时已探明 **38.9%**；改后 **21.2 m 时已探明 66%**——
> **同样里程多探明 27 个百分点**，说明起作用的是**选择策略**而不只是预算变大。

**教学要点**：这条对照的设计很聪明。

"改前 38.9%、改后 81%" 有两个可能的解释：① 策略更好了；② 预算更大了（22 m → 60 m）。

**在相同里程上比较，就排除了第二个解释。**

### 第二组（2026-09-19）：一次被数据推翻的优化方向

**这一组的结论与第一组不矛盾，而是把问题推深了一层**：**探索算法不是瓶颈。**

**新建闭环评测台** `scripts/exploration_survey_benchmark.py`：

- 真值由 MuJoCo 几何**直接栅格化**（`build_truth`）；
- 指标是 `mapped / coverable`；
- **分母不是"所有格子"，而是"某个合法位姿能看到的真值格"的并集**。

修掉两个真值坑后：**可达地面 12043 格，任何策略的上限 13916 格**。

**基线结果**：

```
覆盖 99.0%  行程 61.6 m  23 段  23 次决策
```

> **"在理想执行下，frontier 策略几乎不会漏掉任何可覆盖的空间。"**

**而真实地图只有 62%**，缺口在**建图层**：

`map_pipeline.py` 的 `occupancy_from_points` 只有**落进某格的 3-D 点**才会让那格变已知，**没有射线投射**。

**在真实失败栅格上量化**：

| 量 | 值 |
| --- | --- |
| 真实地图 | 218×178，未知 **14712 格（37.9%）** |
| 从轨迹上的姿态**能看到**的格子 | 25011，其中**仍然未知的 6511 格 = 真实未知的 44.3%** |
| 按"射线经过即自由"填上后 | 可知率可从 **62.1% 升到 78.9%**，**零额外行程** |

**对照被测试挡回的三处探索层改动**：

| 改动 | 净收益 |
| --- | --- |
| 视点按可见性排序 | 0 |
| 簇上限 12→32 | 0 或触发"房间测完还在跑" |
| gain 语义改动 | 0 |

**唯一发布的探索层改动**：视点搜索 0.6 m → **传感器可达距离**（k-d 树替代 Python 循环）：

> **+2.4%，决策耗时 39 ms → 24 ms**

**结论**：

> **现阶段不要动探索算法**；探索层头部空间**不到 1 个百分点**；**最大单项收益在建图层 +16.8 pp**。

**方法论上的诚实记录同样值得教学**：

`density=0.3` 得到 **1.6%**、`density=0.15` 得到 **0.9%**，作者判定：

> **这不是发现，是我的建模错误。**

（单帧伯努利模型让初始地图碎到 `min_frontier_cells=8` 以下，`explore_target` 第一次就返回 `None`，机器人一步没走。）

**教学要点**：这是一个**极好的科研诚实样本**。

一个不诚实的作者会写："我们发现密度参数对覆盖率有显著影响（1.6% vs 0.9%）"。而正确的判断是："**这是我的建模错误，不是被测系统的性质。**"

**判据**：如果一个"发现"的机制可以追溯到**实验装置本身的缺陷**，那它不是发现。

### 第三组（2026-09-20）：79.3% → 99.5%，以及差距到底在哪

**结论一句话**：

> Gazebo 现在是**可驱动的后端**……同一策略在 MuJoCo 户型上覆盖 **99.0%**，在 Gazebo 户型上只有 **79.3%**，而差距**不在策略**。

**交付账**：

| 项 | 值 |
| --- | --- |
| 托管建图服务 | **14 个** |
| 修掉 Gazebo 暴露的 bug | **11 个** |
| **覆盖率** | **79.3% → 99.5%（+20.2 pp）** |
| MuJoCo | 99.0% 不变，且**少走 13%** |
| 拒绝鲁棒性 | 92.0%±11.2 → **98.4%±0.7** |

**根因不是盲区半径，是「格数改面积」**

⚠️ 这一点很容易写错，必须精确：

`minFrontierCells=8` 在 **5 cm 栅格**上是 **0.02 m²**、在 **1 m 栅格**上是 **8 m²**。

改为 **`MIN_FRONTIER_AREA_M2 = 0.05`**（`exploration.py:67`，换算在 `:510`）。

**这才是 99.5% 的来源。盲区半径是另一件仍未修的事。**

**现场地图 73.1% vs 评测台 99.5% 是同分母对比**

现场自报已探明 **72.1%**（分母是自己栅格的 60,522 格），与评测台同分母是 **73.1%（18861 / 25801 coverable）**。

> **"两个分母在这里几乎相等，所以 26 个点的差距不是度量假象，是真实的。"**

**根因不是 SLAM 算法，是里程计**

地图包 `scan-b47a762ae586` 的配准账本：

| 项 | 值 |
| --- | --- |
| registration attempts / accepted | **272 / 1** |
| `no_overlap` | 125（46%） |
| `correction_too_large` | 52（19%） |
| `too_flat_or_few_normals` | 43（16%） |
| `weak_loop` | 38（14%） |

> **112 个关键帧、1 次配准、0 次回环。**
> **这张图本质上是「里程计加一坨点云」，不是 SLAM。**

自证项还显示 **4573 格被标成自由而真值是障碍**（反向 754 格）。

**三个阴性结果（同样重要）**

| 实验 | 结果 |
| --- | --- |
| 前沿簇预算 12→24→48→96 | **逐位相同** |
| "更聪明的盲区模型" `visibility` vs `disc` | **逐位相同**（盲区 1446 格中只有 **40 格（2.8%）**能被更远的航迹位姿看见） |
| 砍掉原地扫视 `maxLooksPerLeg: 6 → 1` | **覆盖率从 73.1% 掉到 66.7%** |

**第二个阴性结果的教学价值**：一个"更聪明的模型"和原来的模型**逐位相同**，因为你**算出了它没有用武之地**——盲区里只有 2.8% 能被更远的位姿看见。

**这比"新模型提升了 3%"更有价值**：它告诉你**这个方向的天花板是 2.8%**。

**留下的真问题：一个常数承担两个职责**

`blindRadiusM = 1.0` 的几何只支持约 **0.5 m**：

| 参数 | 推算 |
| --- | --- |
| 相机高 / 俯角 | 0.16 m / 15° |
| MuJoCo 最低视线 | 44° → 看见地板最短距离 0.17 m，机身前 **≈0.47 m** |
| Gazebo 修后 | 41.4° → 0.18 m，**≈0.54 m** |

**扫描结果**：

| `blindRadiusM` | MuJoCo | Gazebo |
| --- | --- | --- |
| **1.0 m** | 99.0% | 79.3% |
| **0.7 m** | **31.9%** | **99.9%** |
| 0.45 m | 30.7% | 99.7% |
| 0.25 m | — | 17.0% |

**两个户型需要相反的值**，因为常数在同时干两件事：

1. 建模相机盲区；
2. 阻止机器人追自己的脚印。

> **"一个常数承担两个职责，就必然在其中一个户型上做错。"**

**这是本章最重要的一句话。** 它解释了 sim2real gap 最常见的一类根因。

### 一个可复现的文档/代码冲突（实测，适合当课堂练习）

`docs/experiments/2026-09-19-slam-coverage-investigation.md:154` 让学生跑：

```bash
python scripts/exploration_coverage_benchmark.py
```

**这个脚本今天会在最后一步崩掉**（已在工作区复现）：

```
地图 artifacts/maps/furnished-home/scan-6974b8f937e9/grid
  尺寸 (218, 178) 分辨率 0.05 m 未知占比 37.9% 规划余量 0.32 m 传感器 3.0 m
frontier 簇 139 个，其中 >= 8 格的 12 个进入候选（上限 12）
  0.6 m 规则（旧）:  44 簇可行动 /  2772 格已知边界
  传感器可达（新）:  51 簇可行动 /  2847 格已知边界
  差异: +75 格，占全部 frontier 的 2.4%
TypeError: explore_target() got an unexpected keyword argument 'min_frontier_cells'
```

**原因**：`scripts/exploration_coverage_benchmark.py:141` 仍以 `min_frontier_cells=` 调用，而现行签名（`exploration.py:454-457`）只有 `min_frontier_area_m2`——**正是那次"格数改面积"重构留下的未更新调用方**。

**这个崩溃反而是一份好教材**：

1. 它**顺带复现了两个关键数字**；
2. 它示范了"**重构改了签名却没改所有调用方**"这一类真实缺陷；
3. 它说明"**文档里的复现命令也是需要维护的资产**"。

**而测试没抓住它**——因为被测试覆盖的是 `exploration.py`，不是这个一次性审计脚本。

**教学要点**：

> **一次重构的完成标志是"所有调用方都改了"，不是"测试绿了"。**

### ⚠️ 引用覆盖率数字时必须带口径

仓库里"覆盖率"有**两个不可比的分母**：

| 口径 | 公式 | 典型值 |
| --- | --- | --- |
| 调查自报"已探明" | `1 − unknownFraction`，分母是**调查自己栅格的全部格子**（含墙外、墙内、家具背后任何位姿都看不见的地板） | 38.9% / 81% / 62.1% / 79.2% |
| 评测台覆盖 | `mapped / coverable`，分母是**某个合法可达位姿真正能看见的地板** | 99.0% / 99.5% |

（第三个口径用于房间级记账：`coverage_report()` 的 `overall = free/(free+unknown)`，配 `MIN_COVERAGE_RATIO = 0.85`。）

**（推断）学生最容易犯的错就是把 73.1% 和 99.5% 相减得出"建图有 26 个点的 bug"** —— 而这两个数**恰好同分母**，相减是对的；但换成别的一对数就不对了。

**正确的第一步是 `scripts/score_live_map.py`：把现场地图投影回真值坐标系、换成同一个分母再比。**

**教学要点**：**指标的口径（分母）是实验设计的一部分，不是实现细节。**

---

## 10.6 导航安全门禁：为什么「去厨房失败」是设计

### 配置

```yaml
# robot/ros2_ws/src/tangying_navigation/config/nav2.yaml:24
GridBased:
  plugin: nav2_navfn_planner::NavfnPlanner
  tolerance: 0.01
  use_astar: true
  allow_unknown: false      # ★
```

同文件里 `global_costmap` 的 `track_unknown_space: true`（`:209`），`local_costmap` 也是 `true`（`:131`），且有测试**明确钉住**两者都是 `True`。

### `NAV_FAILED NAV2_ACTION_ENDED` 的真实含义

| 部分 | 含义 |
| --- | --- |
| `NAV_FAILED` | **平台侧**的结果码——`rtabmap_client.py` 在导航失败时返回它 |
| `NAV2_ACTION_ENDED` | **Nav2 action 的终态**，由导航节点在非 `SUCCEEDED` 时填入（`navigation_node.py:421`） |

**所以这行日志读作**：

> "**平台判定导航失败；原因是 Nav2 action 结束了（非成功）**"

### 为什么这是刻意设计

`README.md:195` 写得很直白：

> **建图阶段的行为是刻意设计的安全门禁**：全局代价地图的 `allow_unknown=false`，Nav2 拒绝规划穿过尚未建图的区域。
> 所以冷启动后只能导航到已覆盖范围（例如客厅），去厨房会以 `NAV_FAILED NAV2_ACTION_ENDED` 结束——
> **这不是缺陷，而是"不知道的地方不进去"**。

**正确顺序**：

```
1. 建图：--mode mapping 下低速探索覆盖五个房间
2. 执行：--mode localization --scene home 复用同一张地图
3. 抓取：需要厨房物体时用 --scene home_task
```

**教学要点**：**"不知道的地方不进去"是一条安全门禁，不是缺陷。**

而它的理由在第 4 章讲过：地图不全意味着两件事——① 可能有障碍物；② **可能没有地板**（楼梯、阳台）。第二种情况下"勇敢"的代价是摔下去。

### 一个"知道怎么改但只改了一半"的地方

Gazebo 场景下**局部**代价地图的 `track_unknown_space` 被改成 `false`，但**全局保持 `true`**，且改动被**限定在 `scene == "gazebo_house"`**（`navigation.launch.py:53-66`）。

代码注释给了理由与边界：

> 滚动窗口局部代价地图的职责是表达**已感知的障碍**……全局代价地图保持默认，因为"**不要穿过未建图区域**"这条规则属于那里……
> **这是限定在它被测量过的场景里，而不是凭一个仿真器的测量提升为共享默认值。**

**改前实测**：**25,600 格里 21,250 格未知（83%）**，DWB 找不到合法轨迹，每个目标都以 `NAV2_ACTION_ENDED` 结束、**机器人 15 秒不动**。

同一场景里 nav2 足迹也从 **0.46×0.44 m** 改为世界的真实 **0.655×0.67 m**——

> *the configured footprint missed 0.10 m of wheel on each side*

**教学要点**：这是本章最好的一条**工程纪律**样本。

| 做法 | 风险 |
| --- | --- |
| 发现改动有效，提升为全局默认值 | **在一个仿真器上的测量不足以支撑真机默认值** |
| **限定在它被测量过的场景里** | 需要额外的配置分支 |

**尤其当改动方向会把可通行范围放宽时**——那是在**降低安全裕度**。

而测试**明确钉住**了两个 costmap 都是 `true`，所以要动必须同时改测试并给出测量证据。

---

## 10.7 Policy 与低层动作：边界在哪

### `policy/sidecar/` 是什么

一个独立的 **HTTP 推理进程**，只做"观测 → 有界关节动作块"，**没有执行权**。

三个文件，共 **352 行**：

| 文件 | 行数 | 内容 |
| --- | --- | --- |
| `contracts.py` | 192 | 全部 pydantic 契约 |
| `providers.py` | 46 | `PolicyProvider` Protocol 与 `CallableProvider` |
| `server.py` | 102 | `ThreadingHTTPServer`，默认 `127.0.0.1:8091` |

**API**：`GET /healthz`、`GET /v1/manifest`、`POST /v1/infer`。

**错误码是有界的**：`NOT_FOUND`、`CONTENT_LENGTH_REQUIRED`、`REQUEST_TOO_LARGE`、`POLICY_REQUEST_REJECTED`（422）、`POLICY_PROVIDER_FAILED`（503，**provider 内部异常不外泄**）。

### 核心契约

```python
# contracts.py:38-55
PolicyManifest:
  schemaVersion = "policy.manifest.v1"
  policyId, version
  framework ∈ {vla, imitation, reinforcement, deterministic}   # :42
  artifactSha256
  capabilities, robotModels, adapters
  observationSchema = "policy.observation.v1"
  requiredObservationSources
  maxObservationAgeMs
  actionSchema
  maxActionChunkLength
  actionBounds
  transformRevision, calibrationRevision
  training
```

**模型身份是强制的**（`:57-65`）：非 deterministic 必须是 **64 位十六进制 SHA-256**；deterministic 必须以 `deterministic:` 开头。

而 `manifest.revision()` 是对**规范化 JSON** 的 SHA-256（`:67-77`），**Go 侧用同一算法**（`edge/policy/manifest.go:128-149`）：

> Hash a key-sorted JSON object rather than Go struct field order so a Python policy sidecar
> can **reproduce the same content identity**.

**教学要点**：**跨语言的内容哈希必须约定规范化方式。**

Go 的结构体字段顺序和 Python dict 的插入顺序不同——如果直接序列化，同一份 manifest 在两边得到**不同的哈希**。修法是"**按键排序的 JSON**"。

### 校验：五重身份 + 两道边界

`validate_result`（`:160-182`）：

| # | 检查 |
| --- | --- |
| 1–4 | 四重身份比对：`manifestRevision` / `requestId` / `commandId` / `observationId` |
| 5 | **动作块长度不得超过 `maxActionChunkLength`** |
| 6 | **每个动作值必须在 `actionBounds` 内** |

**越界抛异常。**

### 动作空间是具名的 14 个关节，不是抽象维度

```python
# xlerobot_backend.py:28-32
ALLOWED_ACTION_KEYS = frozenset(
    f"{side}_arm_{joint}.pos"
    for side in ("left", "right")
    for joint in ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
) | {"head_motor_1.pos", "head_motor_2.pos"}
MOBILE_BASE_KEYS = {"x.vel", "theta.vel"}
```

即 **12 个手臂关节 + 2 个头部关节 = 14 个键**。

**底盘速度键被显式拒绝**：命中 `MOBILE_BASE_KEYS` 即返回 `MOBILE_BASE_DISABLED`（`:51-52`）——因为**默认桌面配置不使能底盘**。

动作 schema 串是 **`xlerobot.named-joints.v1`**。

**教学要点**：**"具名关节"而不是"14 维向量"**。

| | 抽象维度 | 具名关节 |
| --- | --- | --- |
| 契约 | `float[14]` | `{"left_arm_shoulder_pan.pos": 12.3, ...}` |
| 换机器人 | **维度数变了就是破坏性变更** | 加一个关节只是加一个键 |
| 可读性 | 需要一张表才能看懂 | **自解释** |

代价是 payload 更大。**而这个项目选了可读性**——因为策略的输入输出是**要被审计的**。

### ⚠️ 动作块上限有三处不一致

| 位置 | 值 |
| --- | --- |
| `MAX_ACTION_CHUNK_LENGTH`（`xlerobot_backend.py:25`、`xlerobot.yaml:9`） | **64** |
| 仿真 deterministic 用（`cmd/edge-worker/main.go:201`） | **8** |
| 文档与测试示例 | **8** 与 **32** |

**实际约束是 manifest 自报值**——`validate_result` 用的是 `manifest.max_action_chunk_length`。**64 只是运行时的上限。**

### ⚠️ 一处必须标注的边界：Policy 只有接口，没有实现

这一节必须讲得很清楚，否则会过度声称。

**仓库里没有任何 VLA / ACT / Diffusion Policy / 连续控制 RL 的实现。**

`providers.py` 里唯一的真类就是 `CallableProvider`（`:21`），它是一个**零权重的适配器**：

- 接收调用方提供的 callable；
- **不做 checkpoint 加载**；
- **不做归一化**；
- **不做模型管理**。

而 `contracts.py:42` 的 `framework` 枚举允许四个值，但 `artifactSha256` **只是一个身份串**——填什么都行，只要格式对。

**仓库里唯一的"具体" provider 是 Go 的 `DeterministicProvider`**（`edge/policy/deterministic.go:12-17`），返回每个 actionBound 的**中点**，pick 时 gripper=max、place 时 gripper=min，注释写明**只用于仿真/CI**。

**而 `deterministic` 被明确限制**：

```go
// edge/policy/deterministic.go:9-10
// It must not be silently selected for a physical adapter.
```

```go
// cmd/edge-worker/main.go:181
errUnsafePolicyMode = errors.New("deterministic policy mode is simulation-only")
// :188-190  非仿真 adapter 用 deterministic 直接拒绝启动
```

**文件自己也承认**：

| 出处 | 原话 |
| --- | --- |
| `docs/production/policy-tools.md:32` | **"仓库没有已训练的通用实机模型"** |
| `docs/production/v1-release-status.md:38` | "**LLM 不代替动作模型**" |

**测试用同一个 lambda 穿过三种 framework**，证明了"无框架依赖"——这既是有力证据，也说明 **framework 标签不改变任何行为**。

**教学要点**：**"接口位置"与"已实现"必须分开讲。**

一个 `framework: vla` 的 manifest 可以通过全部校验——因为**校验检查的是格式，不是权重**。**一个填了正确格式但没有真实权重的 manifest，在系统里是一个合法的策略。**

这不是缺陷（系统不负责验证模型质量），但**必须让读者知道**。

### 训练代码的真相：别把它当成 VLA 训练

`sim/mujoco/tangying_sim/training/` 是**表格式 Q-learning over 语义工具目录**：

| 项 | 值 |
| --- | --- |
| 依赖 | 只用 NumPy + MuJoCo，**无 PyTorch、无 GPU** |
| 离散动作 | 4 个工具动作 + 7 个物体槽 grounding + 3 个目的地槽 grounding |
| 阶段 | 9 个 phase |
| 状态 | `SemanticObservation.state_key()` 的 **14 元字符串元组** |
| 奖励常量 | `STEP_COST=-0.05`、`INVALID_ORDER=-0.75`、`TOOL_FAILURE=-1.0`、`REPEATED_NOOP=-0.25`、`RECOVERY=+0.30`、`WRONG_GROUNDING=-1.25`、`TIMEOUT=-2.0`、`UNSAFE=-8.0` |
| CLI 默认 | `train --episodes 1000`；`evaluate --episodes 100 --min-success-rate 0.90`（**低于阈值 exit 1**） |
| checkpoint | 原子写 JSON，含 `toolCatalogFingerprint`（SHA-256），**指纹不符即失败关闭** |

**奖励常量里 `UNSAFE=-8.0` 是最大的惩罚** —— 这说明安全违规在奖励设计里是**压倒性的负面信号**。

**两条负面事实**：

1. **`SemanticPolicy` / `load_checkpoint` 在 training 包之外零引用**——没有接进 `/v1/infer` 或任何执行路径。
2. `core/`、`orchestration/`、`agentruntime/` 对 `edge/policy` **零引用**（grep 只命中无关的 `StagePolicy`/`StopPolicy`）；策略只通过 `tasks/experience.go:43-52` 的 `PolicyEvidence` 露给 UI。

**⚠️ 三个相近名字做三件不同的事**：

| 目录 | 语言 | 做什么 |
| --- | --- | --- |
| `sim/mujoco/tangying_sim/training/` | **Python** | 表格式 Q-learning 训练器（**未接入运行时**） |
| `train/` | **Go** | checkpoint **晋级门**（`train/gate.go:27-31`） |
| `training/` | **Go** | 只读 SQLite 任务账本**导出训练数据**（`training/export.go:25-27`） |

**教学要点**：命名冲突是真实存在的，而且它会误导读者以为三者构成一条流水线——**实际上它们互不相关**。

### 为什么「任何把 LLM 放进控制回路的架构都是错的」

原文在 `docs/architecture/why-distributed.md:32`（推论），依据是三条硬约束（第 1 章详述）。

**而这句是文档化的物理约束，仓库里没有与之对应的实现循环**——`:195` 也写明：

> 一个会被 LLM 推理延迟影响的控制回路是**危险的设计**。

**100 Hz – 1 kHz 这个数量级在本仓库有两个可核对的锚点**：

| 层 | 频率 | 证据 |
| --- | --- | --- |
| MuJoCo 物理 | **500 Hz** | `xlerobot_home.xml:4` 的 `timestep="0.002"` |
| Nav2 局部控制 | **10 Hz** | `controller_frequency: 10.0` |
| Nav2 全局规划 | **2 Hz** | `expected_planner_frequency: 2.0` |
| RTAB-Map 检测 | **2–3 Hz** | `docs/development/calibration-slam-ros-plan.md:63` |

**（推断）把这三层画在一张时间轴上就能看出量级差**：

```
控制   500 Hz  ──┐
局部规划 10 Hz  ──┤  它们差三个数量级
感知   1–5 Hz   ──┤  所以分层不是架构偏好
LLM    秒级     ──┘  而是物理强加
```

### 边界在代码里的四处硬证据

| # | 证据 |
| --- | --- |
| 1 | `why-distributed.md:32-34`：模型选的是**工具**，低层动作来自 Policy Provider |
| 2 | `llm-driven-execution.md:166`：**"不让模型直接写关节序列"** |
| 3 | `README.md:142-143`：`move_arm_to_joints` 与 `navigate_to_pose` **不暴露给大模型** |
| 4 | `agent-v1.md:53`：**"当前本地 LLM 不生成低层 action_chunk"** |

而 Go 侧的接口注释把边界写进了类型（`edge/policy/provider.go:15-18`）：

```go
// Provider supplies immutable policy metadata and one bounded decision. It
// has no authority to execute a command or mutate task/world state.
type Provider interface {
	Manifest(context.Context) (Manifest, error)
	Infer(context.Context, InferenceRequest) (Decision, error)
}
```

**"It has no authority to execute a command or mutate task/world state."**

**这与第 6 章 `OpsAgent` 的字段白名单、第 8 章 `Command` 没有来源字段是同一种手法：把权限约束写进类型。**

### 一个数据层的印证

`tools.json` 里**没有任何 policy 工具**。29 个工具中 **0 个**名字含 policy。

唯一相关的是一条排除声明：

```json
"excluded_from_llm": ["move_arm_to_joints", "navigate_to_pose"]
```

**这两个低层接口只给工程调试，不给 LLM。**

**教学要点**：**这从数据层印证了边界**——模型能看到的工具集合里，**根本没有关节角与位姿原语**。

### 还没有做的事（诚实清单）

`docs/architecture/llm-driven-execution.md:138` 表格里"接进 runner"标 **❌**：

> 执行路径仍是 `graph.Order`。**这是现在最大的一块空缺。**

同一文件 `:156`：

> **还不能说**机器人的实际执行已经变成 LLM 驱动。

---

## 10.8 sim2real 的真实差距清单

### 放行前的硬要求

`README.md:213` 原文：

> 真机放行前必须完成：**双 RGB-D 与里程计标定、地图覆盖、刹车与实体急停、机械臂碰撞边界、以及至少 30 次受监护的路线/抓取验收。**

这条在**四处独立成文**，互为佐证：

| 要求 | 出处 |
| --- | --- |
| 双 RGB-D + 里程计标定、低矮障碍覆盖、断流停止、到达证据、≥30 次重复路线 | `docs/guides/home-sim2real.md:33` |
| 双 RGB-D 在**真实照明/反光地面/窄门/低矮障碍/家具遮挡**下通过观测合同 | `docs/operations/release-checklist.md:31` |
| 低速软围栏、刹车距离、**实体急停**、断网归零、持物恢复、人工接管演练 | `release-checklist.md:32` |
| **至少 30 次单机器人路线和一次长稳运行**；现场负责人**签字** | `release-checklist.md:33-34` |
| 至少 30 个不同实机任务及至少 1 小时观察 | `docs/sim2real/README.md:48` |
| 急停必须**独立于软件**切断执行器电源、全程可及 | `docs/operations/safety-checklist.md:7` |

**代码里的门槛是硬的**：

```python
# scripts/sim2real.py:20
MIN_TRIALS = 30
# :292
minimum = MIN_TRIALS if kind == "trial" else 1
```

而同一脚本 `:22` 的 `LIMITATION` 常量是**对这套证据的自我限定**：

> **仅检查接入资料及人工记录，不验证实机效果，不授权电机运动**；至少 30 次试验和 1 小时观察只是本版试点资料门槛。

`docs/sim2real/README.md:51` 进一步说明：

> 30 次和 1 小时是本版试点的**最低门槛，不是可靠性认证**。

`:208`：

> 当前版本**存在失败记录会阻止通过**。

### 「软件发布与实机放行是两项独立结论」如何体现

**五层证据，每层独立：**

**① 文档层**（`safety-checklist.md:3`）：

> The repository has no completed physical acceptance result.
> **A software READY result is not permission to move hardware.**

**② 数据层**（`v1-release-status.md:38`）把"真实策略"标为待办：

> 需要用户工位适用的模型、训练数据和延迟评估；**LLM 不代替动作模型**。

**③ 代码层**：Edge **拒绝**把 `deterministic` 模式用于实机（`errUnsafePolicyMode`）。

**④ 流程层**（`sim2real/README.md:210`）：

> 改变接入包配置、模型、标定或资料后，**旧记录会计入 `staleRecords`**。

**通过结论不能跨版本复用。**

**⑤ 复现层**（`:62`）：

> 四个子命令 `init/check/record/report` **都不打开机器人串口、不请求网络、不启动服务、不调用动作模型**。

**"检查器永远碰不到硬件，所以它证明的只是资料齐全。"**

**教学要点**：第 ⑤ 层是最容易被忽略的，也是最重要的。

一个"检查通过"的报告，如果检查器**有能力碰硬件**，那么"它没碰"就不能被证明。而这个项目的检查器**结构上碰不到硬件**——所以它能证明的东西是**有明确上界的**。

### 真机平台限制

| 限制 | 值 |
| --- | --- |
| 平台 | **Raspberry Pi 4/5 上的 Ubuntu Server 24.04 arm64**（`uname -m` 应为 `aarch64`） |
| Python | **≥ 3.11** |
| XLeRobot | 固定到 `3d14695e40c9c68229c0aacffca6053c75cd3eb6`，LeRobot **0.4.1** |
| Protobuf | **6.33.5**，**不能**把生成工具单独升到要求 Protobuf 7 的版本（LeRobot 0.4.1 依赖不支持） |
| OpenCV | 4.11.0.86 |
| **默认 `mobile_base_enabled: false`** | **桌面配置不使能底盘** |

**移动版本须另行交付**：底盘 ROS 驱动、真实里程计、控制器看门狗与现场验收。

**软件限幅默认** `max_relative_target: 8.0`、`max_action_chunk_length: 64`，安全清单明确：

> **defaults 8.0 and 64 are software defaults, not hardware-certified limits**

### 首条移动操作任务的真机前置

（`docs/guides/home-sim2real.md:20-25`）

```
登记硬件
  → 完成标定（含 base_link → camera_optical 外参与轮径/轮距）
  → 先只读接入（input_mode:=ros，禁止电机使能）
  → 建图
  → 定位回放
  → 低速执行
```

**放行前仍需保留的未完成项**：

| 项 | 状态 |
| --- | --- |
| `transforms.json` | **只是结构示例**，"不能直接复制单位矩阵上线" |
| 采集时间字段 | "目前列表 provider 接口**没有独立的采集时间字段**，Runtime 时间戳不是相机采集时间" |
| 自动回位轨迹 | 本版实机**未安装**，`recover_to_safe_pose` 返回 `RECOVERY_POLICY_REQUIRED` |

### Real2Sim：反向的那一半

`docs/development/real2sim-from-robot-slam.md:5` 主张：

> **机器人自己的 SLAM 测绘成果，就是仿真场景的来源。**

**已有**：`rtabmap_export.py`（已对真实 208 MB 库验证）、`map_pipeline.py`、`xlerobot_home.xml`。

**缺的是"点云 → MuJoCo 碰撞几何"这一个转换器**（`:99`）。

**两条必须记住的现实约束**：

| # | 约束 |
| --- | --- |
| 1 | 扫描分辨率**不足以重建小物体**（1 m 外约 1 cm），所以**可操作物体用规范模型 + 感知给位姿**（`:41-46`） |
| 2 | **MuJoCo 的 mesh 碰撞体是凸包**，凹形家具（书架、桌下空间）会被**填实**，必须分块凸分解（`:87-89`） |

**教学要点**：**第 2 条是一个"仿真器的物理表示"约束，不是精度问题。**

一个书架在点云里是一个凹形结构，但 MuJoCo 会把它的凸包当作碰撞体——**于是书架变成一个实心块**。而地面规划器会认为那里不能走。

**这类问题不会报错**，只会让行为"和现实不一样"。

---

## 10.9 教学要点

### 怎么向学生解释 sim2real gap

**不要用"现实更复杂"这种话。** 用三条**可核对的具体差距**：

**① 同一策略、同一代码，换一个户型，覆盖率从 99.0% 掉到 79.3%。**

差距的根因是**一个常数（盲区半径 1.0 m）承担的职责在两种户型里互相冲突**——MuJoCo 降到 0.7 m 崩到 31.9%，Gazebo 升到 99.9%。

> **结论：gap 常常不是"模型不准"，而是一个参数被用来表达两件事。**

**② 现场 73.1% vs 评测台 99.5%，同分母。**

看似"建图有 bug"，实测根因是 **272 次配准只成功 1 次**——地图实为里程计的产物。

> **结论：先把两个数字放到同一个分母上，再谈差距。**

**③ "不知道的地方不进去"是一条安全门禁，不是缺陷。**

冷启动后去厨房 `NAV_FAILED NAV2_ACTION_ENDED` 是设计。

> **结论：sim2real 的第一课不是"怎么让它成功"，而是"怎么让它在不知道的时候失败"。**

**补一条方法论**：那些**被测试挡回的改动**与**被判定为建模错误的实验**（`density=0.3 → 1.6%`），比任何一个成功数字都更适合做课堂材料。

### 最小可跑实验路径

**主路径**（一条命令，不需要 Docker、不需要真机）：

```bash
make setup && make build
make home-furnished        # 主路径：工作台 http://127.0.0.1:8897/
```

**观察点**：`mapping.status` 的探明比例、`poseSource`、`mapRevision`。

**然后做一个能让学生"看见"版本校验的实验**：

在控制台点"整机标定 → 自行标定并录入"，用"校验并保存"**故意传一个旧的 `expectedRevision`**，亲眼看到 `REVISION_CONFLICT`。

> **这一步让学生理解"为什么保存必须带读取时的版本"。**

**可选加深项**（依次递进，全部不碰硬件）：

```bash
# 1. 覆盖率：对着真实验记录复算（--blind-radius 0.7 是那条被否掉方向的开关）
.venv/bin/python scripts/exploration_survey_benchmark.py --truth mujoco
.venv/bin/python scripts/exploration_survey_benchmark.py --truth gazebo
.venv/bin/python scripts/exploration_survey_benchmark.py --truth gazebo --blind-radius 0.7

# 2. 适配器接口：验证「换机器人不用重写工具」的正面证据
.venv/bin/python -m tangying_robot_gateway.run_plugin schema > /tmp/contracts.json
.venv/bin/python -m tangying_robot_gateway.run_plugin check --factory examples.robots.simulated:arm

# 3. 引导标定（纯仿真演练，不碰舵机）
scripts/calibrate_guided.py --list --base <已有标定>
scripts/calibrate_guided.py --simulate --base <已有标定>

# 4. sim2real 资料链：第一次 check 出现「待补齐」是预期结果
.venv/bin/python scripts/sim2real.py init --robot-id robot-1 --output site/robot-1
.venv/bin/python scripts/sim2real.py check --kit site/robot-1 --stage inventory
```

**加深项 4 的读法**：让学生打开 `site/robot-1/`，把每个文件对应到 8.8 表格里的哪一项放行条件上。

### 六道练习题

**练习 1：为什么「门不是瓶颈」这个结论必须用几何数据而不是观察来得出？**

<details>
<summary>答案要点</summary>

① **观察只能给出"机器人没进卫生间"**，无法区分"**进不去**"和"**没去**"——这两种情况的处置完全不同（修门 vs 改目标选择）。

② 本仓库做了**两个独立测量**：

- 从 MuJoCo 模型**直接量门洞开口**（客厅 x ∈ [−1.5, 1.5]，走廊→厨房 y ∈ [2.6, 4.1]，卧室→卫生间 x ∈ [−2.655, −1.445]）；
- 再**数旧地图门线上的实际通行格子**（卫生间门线 20 自由 / 1 占用 / **29 未知 = 58%**）。

③ 加上"**底盘包络仅约 0.7 m 而最窄门 1.21 m**"的尺度过关判断。

④ **轨迹点分布**（卫生间 0% 轨迹点、走廊与厨房占一半以上里程）才是完整的证据链。

**要教的是**：把"看起来的原因"变成**可测量的排他性证据**。

**加分点**：指出这里的推理是"**排他法**"——门宽够（几何）、通道存在（栅格）、但机器人没去（轨迹），所以问题在**目标选择**。三个测量缺一不可。

</details>

**练习 2：现场地图 73.1%、评测台 99.5%，这两个数能不能直接相减？怎么判断？**

<details>
<summary>答案要点</summary>

① **不能直接判断**——要先看分母：

| 口径 | 分母 |
| --- | --- |
| 调查自报"已探明" | 它自己栅格的**全部格子**（含墙外、墙内、家具背后任何位姿都看不见的地板） |
| 评测台覆盖 | **某个合法可达位姿真正能看见的地板** |

② **判断方法**就是 `scripts/score_live_map.py`：把**已发布的地图包**投影回真值坐标系，用评测台的分母重数一遍，得到 **73.1%（18861 / 25801）**。

③ 这一对**两个分母几乎相等**，所以这 26 个点是**真差距**。

④ 但根因仍不在探索层：**配准 272 次只成功 1 次、112 个关键帧、0 次回环**，4573 格被标成自由而真值是障碍。

**要教的是**：**指标的口径（分母）是实验设计的一部分，不是实现细节。**

**加分点**：指出这个案例的巧妙之处——**它恰好是同分母的**，所以相减是对的。而如果作者没有做那一步换算，读者就会得到一个"26 个点的差距"，但**不知道它是真是假**。

</details>

**练习 3：把 Policy sidecar 换成你们自己训的 VLA 模型，要改哪几个文件？哪些改法会被拒绝？**

<details>
<summary>答案要点</summary>

**要改的**：

① 只需实现 `PolicyProvider` Protocol 的两个成员（`manifest` 属性 + `infer(request) -> InferenceResult`），用 `CallableProvider(manifest, infer)` 或自建类，再 `create_server(provider, host="127.0.0.1", port=8091).serve_forever()`。

② manifest 必须是 `policy.manifest.v1`、`framework="vla"`、`artifactSha256` 是**真实权重的 64 位十六进制 SHA-256**（`deterministic:` 前缀只允许 `framework="deterministic"`）。

③ 动作必须在 `actionBounds` 内、条数 ≤ `maxActionChunkLength`，键名必须在 **14 个 `ALLOWED_ACTION_KEYS`** 里且不含 `x.vel`/`theta.vel`。

**不需要改的**：Go gRPC 客户端、MCP 工具名、Fleet 任务接口。

**会被拒绝的四处**：

| # | 拒绝 |
| --- | --- |
| 1 | manifest revision 漂移（`ErrManifestDrift`） |
| 2 | 实机用 deterministic（`errUnsafePolicyMode`） |
| 3 | 观测超出 `maxObservationAgeMs` 或缺 `requiredObservationSources`（`CheckCompatibility`） |
| 4 | 动作块超长（`ACTION_CHUNK_TOO_LONG`） |

**加分点**：指出第 2 条是**启动时拒绝**（`main.go:188-190`），不是运行时拒绝——**它在系统起来之前就拒绝启动**。

**再加分**：指出"不需要改 Go gRPC 客户端"这句话有一个重要的限定（8.3）：**"任务与工具不重写"成立，但"新增能力/本体/传感器零代码改动"不成立。** 如果你加的 VLA 需要一个新的工具，你就要改两个手写白名单。

</details>

**补充练习（可当堂跑，5 分钟）**：

让学生执行 `python scripts/exploration_coverage_benchmark.py`，观察它在打印完 `+2.4%` 之后抛 `TypeError`（8.5）。

然后提问：**为什么测试没抓住这个缺陷？**

<details>
<summary>答案要点</summary>

被测试覆盖的是 `exploration.py` 的**纯函数**，而这个**一次性审计脚本没有测试**。重构改了 `explore_target` 的关键字参数（`min_frontier_cells` → `min_frontier_area_m2`），**调用方没跟着改**。

**要教的是**：

1. **"文档里的复现命令"也是需要维护的资产**；
2. **一次重构的完成标志是"所有调用方都改了"，不是"测试绿了"**。

**加分点**：这个崩溃**顺带复现了两个关键数字**——所以一个报错的脚本仍然可能是有用的。但那是**运气**，不是设计。

</details>

**补充讨论题**：把"Gazebo 下局部代价地图 `track_unknown_space: false`"提升为全局默认值会怎样？

<details>
<summary>答案要点</summary>

答案在代码注释里（`navigation.launch.py:54-63`）：

> **这是限定在它被测量过的场景里，而不是凭一个仿真器的测量提升为共享默认值。**

**为什么不能提升**：真机 profile **没有被测量过**，而一个仿真器上的测量不足以支撑真机默认值——**尤其当改动方向会把可通行范围放宽时**（那是在**降低安全裕度**）。

测试还**明确钉住**了两个 costmap 都是 `true`，所以要动必须**同时改测试并给出测量证据**。

**要教的是**：**"在 A 上有效的改动"和"可以作为全局默认值的改动"是两件事。**

判据是：① 有没有在**目标环境**上测量过？② 改动方向是**收紧还是放宽**？

放宽的改动需要更强的证据，因为它降低了安全裕度。

</details>

### 学生最容易误解的四个点

| # | 误解 | 纠正 |
| --- | --- | --- |
| 1 | "Gazebo 已经完全可用了" | **`ExecuteSkill` / `Cancel` / `EmergencyStop` 至今仍按名字拒绝**，Gazebo 上**没有抓放**。这是有意的安全选择 |
| 2 | "仓库里有 VLA 模型" | **没有任何 VLA / 连续 RL 实现。** 唯一真类是零权重的 `CallableProvider`；唯一具体 provider 是仿真专用的 `DeterministicProvider` |
| 3 | "标定会过期" | 全仓 grep 不到 `CALIBRATION_STALE` / `CALIBRATION_EXPIRED`。正确表述是"**缺失或与模型/地图不一致时失败关闭**" |
| 4 | "覆盖率 73% 说明建图有 bug" | 要先看**分母**。而且这一对的根因是**配准 272 次只成功 1 次**——不是建图算法，是里程计 |

---

## 10.10 本章小结

1. **三个仿真后端不是一个层级上的三个东西**，各自回答一个别处无法回答的问题。**Go 侧根本不枚举后端**——代价是"扩展的缺陷对上层不可见"。

2. **无 GPU 可跑的代价是渲染占采集链路的 85%**（514–515 ms，其中 `mjr_render` 439 ms），**仍然远超控制频率**——这从另一个方向印证了第 1 章的分层结论。

3. **"换机器人不用重写任务与工具"要分成两句**：「任务与工具不重写」✅ 成立；「新增能力/本体/传感器零代码改动」❌ 不成立（还有两个手写白名单和硬编码枚举）。

4. **标定没有"过期"概念**，只有"缺失或与模型/地图不一致时失败关闭"。**四层各检查一个不同的问题**，都与 `ScopeOf` 那个"两道防线防的是不同改动"的判断标准一致。

5. **`blindRadiusM = 1.0` 一个常数承担两个职责**（建模相机盲区 + 阻止机器人追自己的脚印），**于是必然在其中一个户型上做错**（0.7 m 时 MuJoCo 崩到 31.9%、Gazebo 升到 99.9%）。**这是 sim2real gap 最常见的一类根因。**

6. **最漂亮的测试设计是"守卫之守卫"**：`assert MODEL_MOUNT_ROTATION_TOLERANCE_DEG < 120.543` —— **一个"永远通过"的检查比没有检查更糟**。

7. **sim2real 的第一课不是"怎么让它成功"，而是"怎么让它在不知道的时候失败"。**

---

## 10.11 源码索引

### 仿真后端

| 内容 | 位置 |
| --- | --- |
| 五房间定义 | `sim/mujoco/tangying_sim/home_scene.py:23` |
| 房间邻接表与停靠点 | `home_scene.py:86-104` |
| "房间名不进观测" | `home_scene.py:1-6` |
| 工具层不知道后端 | `sim/mujoco/tangying_sim/server.py:489` |
| **RoboCasa 复用同一 service** | `sim/robocasa/tangying_robocasa/fleet_server.py:14` |
| Gazebo 实现 `RobotRuntimeServicer` | `gazebo_runtime_node.py:461,474-476` |
| **Gazebo 三 RPC 仍拒绝** | `docs/experiments/2026-09-20-gazebo-exploration-coverage.md:113` |
| Gazebo 状态两层表述 | `docs/development/2026-09-19-gazebo-backend-status.md:3-8,151` |
| 服务目录共用一份 | `robot/gateway/tangying_robot_gateway/service_registry.py`；`robot_workflow.py:147` |
| `NormalizeAdapter` 透传 | `tasks/service.go:152-165` |
| proto 七 RPC | `proto/robot/v1/robot.proto:9-19` |
| `RuntimeInfo.adapter` | `proto/robot/v1/robot.proto:72` |

### 资产与渲染

| 内容 | 位置 |
| --- | --- |
| 资产 pin 断言 | `scripts/prepare_furnished_home.py:320,351-352` |
| SHA-256 运行时复核 | `sim/mujoco/tangying_sim/furnished_home.py:13-25` |
| **"不是标定过的数字孪生"** | `sim/mujoco/assets/xlerobot/PROVENANCE.md:16-21` |
| CI 用 osmesa | `.github/workflows/ci.yml:16,161` |
| CI 慢 2.5 倍 | `docs/production/testing-and-acceptance.md:34` |
| **OSMesa 永久阻塞的边界** | `sim/mujoco/tangying_sim/rendering.py:17-27,86-110` |
| 渲染成本实测 | `docs/development/mujoco-compatibility.md:77,96,98` |
| 一键启动 | `Makefile:113-114`；`scripts/furnished-home-demo.sh:1-38` |
| 栈管理器默认端口 8787 | `scripts/sim-stack.sh:8-14` |

### 适配与标定

| 内容 | 位置 |
| --- | --- |
| **`RobotProfile` 是声明不是证据** | `core/robotcontract/contract.go:1-3,41-53` |
| `RobotBackend` 五方法 | `robot/gateway/tangying_robot_gateway/backend.py:62-82` |
| `PluginBackend` 构造不碰硬件 | `plugin_backend.py:81-92,195` |
| 新增机器人的七项 | `docs/development/robot-adapters.md:293-297` |
| **可扩展性的反证** | `docs/development/2026-09-18-system-review-and-improvement-plan.md:346-352` |
| 无硬件验证命令 | `docs/development/robot-adapters.md:50` |
| 异构边界端到端测试 | `tests/contract/test_heterogeneous_runtime_boundary.py:250-278` |
| 标定 schema 与常量 | `robot/gateway/tangying_robot_gateway/calibration.py:40-67,105-110` |
| 叶字段取值范围 | `calibration.py:190-279` |
| **`updatedAtUnixMs` 不进哈希** | `calibration.py:105-106,325` |
| CAS 与原子写 | `calibration.py:356-386` |
| 四层失败关闭 | `driver.py:104-153`；`sim/mujoco/tangying_sim/calibration.py:129-169`；`robot_workflow.py:1376`；`contracts.py:214-216` |
| `base_from_camera` | `proto/robot/v1/robot.proto:148`；`gazebo_bridge.py:131-154` |
| 光学系折算 | `sim/mujoco/tangying_sim/calibration.py:35` |
| **守卫之守卫** | `sim/mujoco/tests/test_calibration_self_check.py:203-204` |
| ground-truth 自检 | `sim/mujoco/tests/test_calibration_ground_truth.py:1-12,41-55,95,120,133-135` |

### 探索与覆盖率

| 内容 | 位置 |
| --- | --- |
| **38.9% → 81% 的四处改动** | `docs/experiments/2026-09-15-slam-exploration-coverage-upgrade.md:45-82` |
| `FAR_REGION_FACTOR` 与 `region_gain` | `robot/gateway/tangying_robot_gateway/exploration.py:45,306,620-626` |
| 深度点不足的处理 | `robot_workflow.py:366-380` |
| **99% vs 62%（瓶颈在建图层）** | `docs/experiments/2026-09-19-slam-coverage-investigation.md:18-43,72-91,130-136` |
| `occupancy_from_points` 无射线投射 | `map_pipeline.py:273,282` |
| **79.3% → 99.5%（格数改面积）** | `docs/experiments/2026-09-20-gazebo-exploration-coverage.md:5,11-20` |
| `MIN_FRONTIER_AREA_M2` | `exploration.py:67,510` |
| **盲区半径的两难** | `docs/experiments/2026-09-20-gazebo-exploration-coverage.md:518-541` |
| 配准账本（272/1） | `docs/experiments/2026-09-20-gazebo-exploration-coverage.md:576-588` |
| 三种覆盖率口径 | `exploration.py:629-636`；`scripts/exploration_survey_benchmark.py`；`mapping_coverage.py:25,235` |
| **崩溃的审计脚本** | `scripts/exploration_coverage_benchmark.py:141` vs `exploration.py:454-457` |

### 导航门禁

| 内容 | 位置 |
| --- | --- |
| `allow_unknown: false` | `robot/ros2_ws/src/tangying_navigation/config/nav2.yaml:24` |
| 两个 costmap 都是 `true` | `nav2.yaml:131,209`；`test/test_launch_config.py:20-21` |
| `NAV2_ACTION_ENDED` 的来源 | `navigation_node.py:421` |
| Gazebo 局部改 `false`（限定场景） | `launch/navigation.launch.py:53-66` |
| 足迹修正 0.46×0.44 → 0.655×0.67 | `navigation.launch.py:67-81` |
| **"不是缺陷，是安全门禁"** | `README.md:195-199` |

### Policy

| 内容 | 位置 |
| --- | --- |
| `PolicyManifest` | `policy/sidecar/tangying_policy_sidecar/contracts.py:38-77` |
| 校验（五重身份 + 两道边界） | `contracts.py:160-182` |
| `CallableProvider`（零权重） | `providers.py:11-46` |
| **跨语言内容哈希** | `contracts.py:67-77`；`edge/policy/manifest.go:128-149` |
| Go 侧 Provider 接口 | `edge/policy/provider.go:15-18` |
| **deterministic 禁止用于实机** | `edge/policy/deterministic.go:9-10`；`cmd/edge-worker/main.go:181,188-190` |
| 14 个关节键 | `xlerobot_backend.py:25-32,51-66` |
| **"没有已训练的通用实机模型"** | `docs/production/policy-tools.md:32` |
| 三个相近目录的分工 | `train/gate.go:27-31`；`training/export.go:25-27`；`sim/mujoco/tangying_sim/training/` |
| **"接进 runner" 标 ❌** | `docs/architecture/llm-driven-execution.md:138,156` |

### sim2real 放行

| 内容 | 位置 |
| --- | --- |
| **放行前硬要求** | `README.md:213` |
| `MIN_TRIALS = 30` 与 `LIMITATION` | `scripts/sim2real.py:20,22,292` |
| **"不是可靠性认证"** | `docs/sim2real/README.md:51,208-210` |
| **"软件 READY 不是移动硬件的许可"** | `docs/operations/safety-checklist.md:3` |
| 检查器碰不到硬件 | `docs/sim2real/README.md:62` |
| 平台限制 | `docs/install/robot-pi.md:16-38` |
| 真机前置顺序 | `docs/guides/home-sim2real.md:20-25` |
| Real2Sim 的两条约束 | `docs/development/real2sim-from-robot-slam.md:41-46,87-89,99` |

---

**上一章**：[第 9 章 本地单机形态](../chapters/ch09-local-single-machine.md) · **下一章**：[第 11 章 可观测性、事故诊断与自动恢复](../chapters/ch11-observability-recovery.md) —— 事故记录、177 个故障码、以及「沉默被读成健康」。
