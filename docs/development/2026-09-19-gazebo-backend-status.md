# Gazebo 作为机器人后端：已完成什么、还差什么

> **本文的大部分"还差什么"已经在 2026-09-20 补上。** 结论「Gazebo 现在可被观测，还不可被驱动」
> 已经不再成立：运行时托管了完整 14 个建图服务，一句话建图的端到端链路已经跑通，
> 部署也从 `docker cp` + 手工 pip 变成镜像自足。
> 当前状态、过程中暴露的八个缺陷、以及与 MuJoCo 的覆盖对比见
> `docs/experiments/2026-09-20-gazebo-exploration-coverage.md`。
> 本文保留，是因为它记录的**当时**判断与四个写的时候看不出来的 bug 仍然有参考价值。

**日期**：2026-09-19
**目标**：让 Gazebo 成为系统的**又一个后端**——像 MuJoCo 或实机一样，agent 对它无感知。

---

## 一、架构：工具层与仿真器无关

这是整件事里最省力的一条发现。

```
agent ──gRPC robot.profile.v1──▶ 运行时（MuJoCo / Gazebo 桥 / 实机）
                                    │
                                    └─ ExecuteSkill ─▶ world.tools.execute(skill, ToolContext(world, ...))
```

`sim/mujoco/tangying_sim/server.py:491` 把技能指令交给 `self.world.tools.execute(...)`，
而 `tools.py` 只通过 `ToolContext` 拿到一个 `world` 对象。**工具层不知道背后是哪个仿真器。**

所以 Gazebo **不需要重写技能**（pick/place/verify 等），只需要一个**实现同样接口的 world 对象**。
这是"丝滑过渡"能成立的原因，也把工作量从"重写一套操作技能"降到"实现一个世界适配器"。

---

## 二、已完成（每项都实测过）

| 部分 | 状态 | 证据 |
| --- | --- | --- |
| 手臂 12 连杆 + 夹爪（**带接触与摩擦**） | ✅ | 从 `arm_kinematics.json` 生成；容器内 `gz sdf -k` 报 `Valid.`，`gz model` 列出 12 连杆 / 15 关节 |
| 导航 + 定位走生产路径 | ✅ | `/v1/navigation/map` → `ready: true`、`MAPPING_ODOMETRY` |
| 转换层（内参、光学系、刚性校验） | ✅ | `gazebo_bridge.py`，11 项测试 |
| 装配层（采集 → 运行时载荷） | ✅ | `gazebo_runtime.py`，12 项测试 |
| 运行时 `GetRuntimeInfo` + `Observe` | ✅ | 容器内实测：320×240、rgb 230400 B、depth 307200 B、`K[0]=221.765`、`base_from_camera=[0.28,0,0.48]` |

### 跑起来才发现的四个 bug（全是写的时候看不出来的）

1. `runtime.cameras[0]`——`cameras` 是字典，KeyError 被 gRPC 报成无消息的 `UNKNOWN`
2. 订阅了 **Gazebo 侧**话题名（`/camera/base/image`），桥发布的是 **ROS 侧**名
   （`/camera/base/rgb/image_raw`）——`ros2 topic info` 的 Publisher count 是区分线索
3. 通道数用 `len(data)//(h*step)`，无填充时恒为 1
4. `GazeboBridgeError` 逃出 `except GazeboRuntimeError`，**一帧坏数据杀死整个进程**；
   客户端收到 "connection refused" 而不是"这一帧不合法"

---

## 三、还差什么：一个 Gazebo `World` 实现

从工具层的调用点提取的确切接口（`sim/mujoco/tangying_sim/tools.py`）：

### 状态读取（简单）
- `robot_state()`、`cached_robot_state()`、`joint_positions()`
- `entities()`、`has_object()`、`has_destination()`
- `resolve()`、`resolve_all()`

### 物理操作（难，需要 Gazebo 侧的控制器与接触）
- `pick()`、`place()`、`verify_grasp()`
- `verify_inside()`
- `recover_to_safe_pose()`、`recover_cancelled_place()`
- `prepare_navigation()`
- `set_active_arm()`、`select_arm()`、`reset()`

### 观测缓存
- `_publish_sensor_snapshot()`

**"物理操作"那一组才是真正的工作量**，它要求：

1. **关节控制器**：世界里有 12 个手臂关节 + 2 个夹爪，但 SDF 里目前只有差速插件。
   需要加 `gz-sim-joint-position-controller`（或 `JointTrajectoryController`），
   并把 14 个关节接上命令话题。
2. **抓取验证**：`verify_grasp` 在 MuJoCo 里靠物理状态判定；Gazebo 侧要么读接触传感器，
   要么用夹爪关节的实际位置与被抓物体的相对位姿判定。**这是最需要实测调的一处**。
3. **安全层复用**：`SafetySupervisor`（`robot/gateway/.../safety.py`）与工具层一样是与世界无关的，
   应原样复用，不要为 Gazebo 另写一套准入检查。

### 服务那一半（`CallService` / `ListServices`）

建图（`mapping.*`）、标定、导航（`navigation.*`）这些服务住在**机器人网关**里。
Gazebo 桥要提供它们，只有两条路：

- **代理**到网关的 gRPC（桥变成客户端 + 服务端两侧）；
- 或者让 agent 直连网关，桥只负责 `Observe`/`ExecuteSkill` 这一半。

**我倾向后者**：服务是机器人的属性，不是仿真器的属性；把网关的服务目录复制一份到桥里，
就是两个定义迟早漂移。这一条需要你定。

---

## 三·补、本轮实测（对着真 Gazebo 跑）

### 1. 五个未实现的调用**逐个验证会拒绝**

不是"看代码觉得会拒绝"，是真的连上去问：

```
ListServices    ✓ UNIMPLEMENTED: the Gazebo runtime serves observation only; ...
CallService     ✓ UNIMPLEMENTED: service calls are not bridged to Gazebo yet
Cancel          ✓ UNIMPLEMENTED: cancellation is not bridged to Gazebo yet
EmergencyStop   ✓ UNIMPLEMENTED: ... do not read this as a stop
ExecuteSkill    ✓ UNIMPLEMENTED: ... a command that is accepted and ignored is
                                   indistinguishable from a slow robot
```

急停那条是重点：它**明确说了"不要把这当成一次停止"**。

### 2. 🐛 预构建镜像里的 proto 是旧的

测 `ListServices` 时客户端直接 `AttributeError`——**镜像里 `/opt/tangying-proto` 的生成物早于
`ListServices`/`CallService` 加入 proto**（仓库里的 `robot_pb2_grpc.py` 有这两个方法，镜像里没有）。

后果有两层：

- 我节点里那两个"未实现就拒绝"的方法**根本不可达**——客户端的 stub 里连方法都没有，
  它永远不会调到服务端；
- 更一般地：**镜像烘焙的是构建时刻的契约**。改 proto 而不重建镜像，agent 与机器人就各说各话。

已确认：把仓库当前的 `robot_pb2*.py` 拷进容器后，五个调用全部按预期返回。
**正式做法是重建镜像**，不是拷文件。

### 3. 12 个关节位置控制器已加载，夹爪**能被驱动**

世界现在从同一张表生成 **12 个 `JointPositionController`**（每个手臂关节一个，
夹爪用更硬的增益——会下垂的夹爪就是会自己张开的抓取，往上层看像规划器故障）。

实测：命令 `/joint/left_arm_gripper/cmd_pos` 为 `+0.8` 和 `-0.8`，
**移动夹爪连杆的姿态在两个方向上都不相同**（对整段连杆查询取哈希，避免解析格式的坑）。

```
整段输出哈希: 初值=b0e09fc9477a  +0.8后=d66c6e69d251  -0.8后=f0bac2002457
```

**所以"手臂能被命令"是measured 的，不是假设的。**

### 4. 仍然**没有**验证的：一次真正的抓取

夹爪会动，接触几何与摩擦在 SDF 里，Gazebo 也接受了——但**没有把物体夹在两颚之间拿起来过**。
在那之前"抓取可用"这句话不能说。这是下一步最该做的一个实验，也是最容易做出错误结论的一个
（关节动了 ≠ 抓住了）。

---

## 四、当前必须明确的一件事

**Gazebo 现在可被观测，还不可被驱动。**

`ExecuteSkill`、`CallService`、`Cancel`、`EmergencyStop` 都返回 `UNIMPLEMENTED`——
不是接受后什么都不做。一个收下技能指令却毫无动作的运行时，与一台慢机器人无法区分；
急停尤其：报一个没有执行的停止，比什么都不报更糟。

要把"可观测"变成"可驱动"，最小可用路径是：

1. 世界里加 14 个关节的位置控制器（SDF 改动，可从 `arm_kinematics.json` 生成关节名清单）；
2. 写 `GazeboWorld` 实现上面那张接口表，先做**状态读取 + 导航**，再做操作；
3. 用同一套家庭任务脚本对着它跑，与 MuJoCo 的结果对比——**那才是"agent 无感知"的判据**。

---

## 五、复现方式

```bash
make gazebo-house-start                        # 栈起来了，/v1/navigation/map 应返回 ready:true
C=tangying-gazebo-house-gazebo-house-1
docker cp robot/gateway/tangying_robot_gateway/{__init__,rgbd,contracts,gazebo_bridge,gazebo_runtime}.py \
  $C:/opt/gzbridge/tangying_robot_gateway/
docker exec $C /opt/navigation-venv/bin/pip install pydantic     # rgbd.py 的传递依赖
docker exec -d $C bash -lc "source /opt/ros/jazzy/setup.bash; \
  PYTHONPATH=/opt/gzbridge:/opt/tangying-proto:/opt/ros/jazzy/lib/python3.12/site-packages \
  TANGYING_RUNTIME_PORT=50071 TANGYING_GAZEBO_CALIBRATION_REVISION=<sha256 of the world> \
  /opt/navigation-venv/bin/python gazebo_runtime_node.py"
```

**部署事项**：桥用的是网关的代码，因此镜像需要 `pydantic`。上面是临时 `pip install`，
正式做法是加进 `deploy/robot/navigation/Dockerfile`。
