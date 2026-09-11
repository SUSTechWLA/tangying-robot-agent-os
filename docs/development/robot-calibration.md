# 整机标定：`robot.calibration.v1`

一台机器人的**型号**和这台**具体机器**是两回事。型号说"肩部旋转关节行程 ±2.1 rad"，你手上这台有自己的舵机零点、自己的齿轮间隙、自己那颗装偏了几毫米的相机。在这些数字被应用之前，接地、抓取规划、到位判定和证据全都是错的。

标定因此是**运行时拥有的文档**，不是某个厂商或某个仿真器的属性：

- 运行时发布它、应用它；**Agent 只看到契约**（`robot.profile.v1` + 观测 + 产生这些观测的标定修订号），永远不需要知道对面是仿真还是实机；
- 仿真从 MuJoCo 模型推导出同一份文档，所以两边字段相同、可编辑、含义一致；
- 修订号是**内容哈希**：同样一组数字永远得到同一个修订号，证据因此能声明"这次观测是在哪份标定下采集的"。

## 一台参考机器人的标定范围

参考机型是两个 RGB-D、两条机械臂、每条臂一个夹爪：

| 分组 | 总线 | 数量 | 说明 |
| --- | --- | --- | --- |
| 左臂 | `left`（`/dev/tangying-left`） | 6 | `shoulder_pan`、`shoulder_lift`、`elbow_flex`、`wrist_flex`、`wrist_roll`、`gripper` |
| 右臂 | `right`（`/dev/tangying-right`） | 6 | 同名六个关节；**夹爪是每条臂的第 6 个关节** |
| 头部与底盘 | `shared` | 4 | `head_motor_1`、`head_motor_2`、`base_left_wheel`、`base_right_wheel` |
| 相机 | — | 2 | `head-rgbd`（头部俯仰连杆）、`base-rgbd`（底盘） |

两条臂各自一条总线，因此**左右两侧的舵机 ID 都是 1–6**，靠总线（以及设备路径）区分，不是冲突。夹爪没有独立舵机 ID，它是本条臂的第 6 号舵机。

代码里的权威定义在 `robot/gateway/tangying_robot_gateway/calibration.py`：`MOTOR_IDS`、`MOTOR_BUS`、`arm_layout()` 与 `gripper_motors()`。编辑器用 `motor_layout()` 渲染分组，不需要自己硬编码任何名称。

## 文档结构

```jsonc
{
  "schemaVersion": "robot.calibration.v1",
  "robotId": "xlerobot-01",
  "adapterId": "xlerobot",          // 或 mujoco
  "source": "measured",             // measured | simulation | default
  "updatedAtUnixMs": 0,
  "motors": {                       // 16 个；沿用 LeRobot MotorCalibration 字段
    "left_arm_gripper": {"id": 6, "drive_mode": 0,
                         "homing_offset": -132, "range_min": 940, "range_max": 3120}
  },
  "cameras": {
    "head-rgbd": {
      "sourceId": "xlerobot-01/head-rgbd",
      "width": 640, "height": 480,
      "intrinsics": {"fx": 610.2, "fy": 609.8, "cx": 319.5, "cy": 239.5},
      "distortion": {"model": "plumb_bob", "coefficients": [0.01, -0.02, 0, 0, 0]},
      "extrinsics": {"parentLink": "head_tilt_link", "xyz": [0.05, 0.0, 0.06],
                     "rpy": [1.5708, 0.0, 0.0]}
    }
  },
  "geometry": {"gripper": {"openM": 0.081, "closedM": 0.0}},
  "safety": {"maxRelativeTargetDeg": 8.0, "maxActionChunkLength": 64,
             "maxLinearSpeedMPerS": 0.05, "maxAngularSpeedRadPerS": 0.2}
}
```

约定：

- **`motors` 沿用上游 LeRobot 的 `MotorCalibration` 字段**，所以 `xlerobot_adapter.calibration.validate_calibration_data` 仍是舵机字段的权威校验，不需要第二套定义。
- **相机外参用光学坐标（右/下/前）**，与运行时契约一致；MuJoCo 的右/上/后在推导时一次性折算掉，因此同一组数字在仿真与实机上含义相同。
- **未知字段一律报错**。一个拼错的字段名如果被静默忽略，等于在真机上什么都没做。
- 校验失败返回机器可读的 `code`（`UNKNOWN_MOTOR`、`REVISION_CONFLICT`、`OUT_OF_RANGE` …）加一句人话，编辑器直接展示，不需要自己解释。

## 身份与并发

`calibration_revision` 是文档**内容**（`schemaVersion`/`robotId`/`adapterId`/`source`/`motors`/`cameras`/`geometry`/`safety`）的 SHA-256，不含时间戳：

- 同样的数字存两次 → 修订号不变，证据不会因为"又保存了一次"而失效；
- 任何一个数字变了 → 修订号变，证据能指出是标定变了；
- 保存支持 compare-and-swap：调用方读到的修订号在保存时必须仍然匹配，否则返回 `REVISION_CONFLICT`——**别人的测量值不会被静默覆盖**。

运行时会把它发布在 `robot_state` 里，和 `model_revision`、`scene_revision` 并列：

```json
{"calibration_revision": "01bbeba3…", "calibration_source": "simulation"}
```

## 仿真与实机的一致性

仿真不是特例，它发布同一份文档，只是**推导来源不同**：相机内参由 `cam_fovy` 与画幅算出，外参由模型里相机的世界位姿折算到父连杆；舵机给出一台新编程单元的标称值（ID 按注册表、无零点偏移、满量程）。

运行时会把这份文档**真的用上**：每次 RGB-D 采集附带的 `K` 与 `camera→world` 变换来自标定文档，而不是渲染时的临时计算。于是改一个数字就能在观测里看到后果——这正是实机上"标定错了"的样子，而不是被仿真器掩盖过去。

## 面向非技术用户的引导式标定

标定不能要求用户读关节名、算编码器偏移。`robot/gateway/tangying_robot_gateway/calibration_wizard.py` 把它变成一条**逐步向导**，入口是 `scripts/calibrate_guided.py`：

```bash
scripts/calibrate_guided.py --list --base <已有标定>        # 先看一遍要做什么，不碰硬件
scripts/calibrate_guided.py --simulate --base <已有标定>     # 用仿真机器人演练整个流程
scripts/calibrate_guided.py --robot-id xlerobot-01 --base <已有标定> --output xlerobot-01.json
scripts/calibrate_guided.py --resume --robot-id xlerobot-01 --base <已有标定> --output xlerobot-01.json
```

### 用户在每一步看到什么

准备阶段逐条确认四项安全检查（通电与接线、周围无人、急停在手边、机械臂可自由转动），之后按顺序走：

| 阶段 | 步数 | 用户做什么 |
| --- | --- | --- |
| 左臂/右臂 逐关节归零 | 各 6 步 | 用手把这一步说到的关节摆到指定姿态（例如"把小臂伸直，与地面平行"），确认 |
| 左臂/右臂 活动范围 | 各 1 步 | 扶着这条手臂来回移动到两个极限，系统连续采样自动记下六个关节的最小/最大值 |
| 头部与底盘 | 4 步 | 头部两个电机摆正、两个驱动轮自由转动 |
| 检查并保存 | 1 步 | 核对参数，保存后立即生效 |

共 **20 步**。每一步都写明是**哪条臂、哪个关节、哪条总线上的几号舵机**，以及这一步为什么这么做；用户不需要知道 `homing_offset` 或 `range_min` 这些名字。

### 刻意采用的设计

- **一步一个动作**：不会出现"设置偏移量"，只会出现"用手把这一节转到……然后确认"。
- **活动范围按臂采样，不按关节**：扶着一条手臂扫一遍就教会六个关节，两次而不是三十二次确认。
- **只记录观测到的值**：某一步读数异常时该步保持未完成，不会写入一个"看起来合理"的数字，也不会推进进度。
- **随时可以停**：每完成一步都写盘，输入 `s` 保存退出，之后用 `--resume` 从下一步继续；总结里始终写明还剩哪条臂的几个关节。
- **不静默失败**：手臂没动会明确说"没有检测到移动，请扶着这条手臂慢慢移动到两个极限后重试"。

### 尚未实现

- **相机标定**：引导流程目前只采集舵机。相机内参/外参需要由出厂参数、上一次标定或仿真模型推导值提供（`--base`）；没有相机参数时向导会在**开始前**就拒绝，而不是让用户走完二十分钟再报错。
- **运行时 RPC 与控制台面板**：目前通过脚本运行；把同一份 `status()` 负载接到控制台是下一步。
- **真机现场验收**：尚未在真实机器人上走完这套流程。上面所有行为都在仿真后端上验证，**不声称实机标定已经完成**。
