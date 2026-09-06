# XLeRobot 集成边界

购机安装与试验步骤见[Sim2Real 上手](sim2real/README.md)，本页解释当前驱动支持什么。当前尚无真实硬件验收结论；软件兼容测试不能证明电气、负载、标定或停止响应已经通过。

## 固定版本与所购设备

当前集成基线固定 XLeRobot two-wheel 提交 `3d14695e40c9c68229c0aacffca6053c75cd3eb6` 和 LeRobot `0.4.1`。安装器把上游放在 `/opt/XLeRobot`，把 `xlerobot_2wheels` 与 `model` 安装到隔离 Python 环境。

所购硬件与最新官方教程可能属于另一版本；先核对型号、总线/电机名、固件、控制板、相机、方向和标定格式，不把“XLeRobot”名称相同当作兼容证明。官方文档是装配参考，AgentOS 的支持范围以固定版本和[当前发布状态](production/v1-release-status.md)为准。

## 默认直连与兼容层

默认运行 ROS2-free Gateway、Safety Supervisor 与 XLeRobot 驱动。驱动源码暂位于 `robot/ros2_ws/src/xlerobot_adapter/xlerobot_adapter/`，目录名不意味着默认服务要启动 ROS 2。

```text
Edge / Local Brain → mTLS RobotRuntime → Safety / Journal
  → XLeRobotDirectBackend → XLeRobotDriver
  → upstream_compat → 固定 XLeRobot + LeRobot → USB 总线控制板 → 舵机
```

项目内部 `upstream_compat.py` 提供固定版本桌面兼容实现，不修改上游 checkout。它检查 LeRobot 版本和实际导入的 robot/config 源文件 SHA-256，拒绝未经适配的漂移；处理双总线标定分组、LeRobot 0.4.1 总线调用与动作键映射。`.pos` 是外部动作键后缀，底层总线使用不带后缀的电机名。

## 连接与动作授权

- `XLeRobotDriver.connect()` 显式打开连接并保持扭矩关闭；读观测或收到 ExecuteSkill 不会隐式 connect。
- `arm(operator_present=True)` 是独立现场授权：读取当前位置、写入保持目标，再只启用手臂/头部扭矩。两轮底盘保持停机/禁用。
- 默认 systemd 服务带 `--connect`，连接但不自动 arm。连接会禁用现有扭矩并配置寄存器，因此启动前必须支撑机械臂，不能称为无物理影响。具体参数与前台进程互斥见[Sim2Real 上手](sim2real/README.md)。
- 能力检查显示 `ROBOT_NOT_ARMED` 时不应绕过；`READY` 仅是软件能力状态，仍需真实 provider、命令校验和现场安全条件。
- stop/取消后的驱动授权按当前安全状态失效，需要现场重新检查。急停由 Runtime journal 持久化，重启不解除。现场复位使用独立 `--reset-stop --operator-present --operator NAME --reset-reason TEXT` 命令；它要求交互终端和原 journal 的独占锁，先审计后复位，不连接/arm 或重放旧命令。不能通过新建 SafetySupervisor 绕过运行中的服务。

## 标定、动作与停止边界

默认端口是 `/dev/tangying-left` 和 `/dev/tangying-right`；固定校准文件为 `/var/lib/tangying-robot-agent-os/calibration/tangying-xlerobot.json`。配置须与真实电机名、ID 和范围完整匹配；仅能解析 JSON 不够。标定脚本是显式现场流程，可能启用扭矩或要求人工移动关节，不在 systemd 后台自动执行。

桌面 action 仅接受已知手臂/头部 `.pos` 键，拒绝任何 `x.vel` / `theta.vel`、未知键、布尔值、NaN/Inf 和非法数值。归一化绝对范围为 `[-100,100]`，夹爪 `[0,100]`；这些不是角度、速度或力矩单位。兼容层按当前反馈对每次相对目标增量限幅；不要把合法绝对范围与物理安全范围混为一谈。

`XLEROBOT_MAX_RELATIVE_TARGET=8.0`、`XLEROBOT_MAX_ACTION_CHUNK_LENGTH=64` 是软件默认值，需要现场负责人为具体机械结构、负载和任务选择并验证。停止需尝试底盘归零、执行器扭矩关闭与相机清理；连接或 arm 中断/失败时保持停止边界，不能自动重试运动。

## 预检与能力失败关闭

`robot-agent doctor robot-pi` 执行 no-motion preflight，检查版本/模块、串口、校准、配置和证书；它不调用 connect、send_action 或 arm。Python provider 必须能安全导入，不在 import 时访问设备。

实机任务另外需要真实实体感知、匹配策略的有界 `action_chunk` 和结果 verifier。Fleet Edge 的 HTTP policy 接入见[策略工具](production/policy-tools.md)；Local LLM 配置不能代替动作策略。缺少能力、非法动作、未知终态、过期观测或安全锁存均不允许伪造完成。

首次动作前完成[硬件检查表](safety-checklist.md)与[实验前检查](install/xlerobot-experiment.md)。软件急停不能替代独立切断执行器电源的实体急停。
