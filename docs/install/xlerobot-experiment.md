# XLeRobot 实验前检查与首次动作

本页是[购机后 Sim2Real 上手](../sim2real/README.md)中的现场实验检查表。当前项目没有真实机器人验收结论；不要把已安装、`READY` 或已完成仿真当作物理动作许可。除注明外，命令在树莓派上执行。

## 1. 固定实验身份与安全条件

- [ ] 在 Sim2Real kit 记录所购硬件型号、上游提交、LeRobot 版本、端口映射、软件版本、标定与地图/transform。
- [ ] 完成[物理安全检查表](../safety-checklist.md)，实体急停可独立切断执行器电源，现场操作员全程在安全位置。
- [ ] 当前桌面模式禁用两轮底盘；`x.vel`、`theta.vel` 即使为零也不是允许的 action key。
- [ ] 两控制板使用稳定串口别名，`tangying-robot` 有 `dialout` 权限；校准文件与实际总线配置匹配。
- [ ] mTLS 配对完成；笔记本 `robot-agent doctor local` 通过。
- [ ] 已由硬件负责人确定本次速度、工作区、动作长度和相对位移限制。示例 `8.0` / `64` 是软件默认值，未经该设备验证不能称为安全值。
- [ ] 实体感知、策略模型与 verifier 均绑定本次 calibration/transform；没有用固定仿真实体或永远成功的 verifier 代替。

```bash
# 只检查配置、导入和文件；不连接舵机、不使能扭矩
sudo robot-agent doctor robot-pi
```

任何硬件异常先停止实验并切断执行器电源，再排查；不要在执行期间接近运动范围。

## 2. 连接服务与检查能力

默认 systemd 服务使用 `--connect`，会禁用现有扭矩并配置总线，启动前必须支撑机械臂。它不自动 arm，但连接不是无物理影响操作。

```bash
sudo robot-agent start robot-pi
sudo journalctl -u tangying-robot-edge.service -n 50 --no-pager
```

日志可能打印 `xlerobot direct edge readiness: READY` 或 `NOT_READY blockers=...`。这是能力配置检查，不证明已连接、已 arm、相机结果可信或现场安全。默认服务不会自动授权扭矩；受监督的连接与 arm 步骤按[实机上手](../sim2real/README.md)执行，先停止占用同一串口/端口的服务。

存在 blocker 时先处理其原因。`doctor` 与 `production-check` 不会替你运行感知、执行策略、测试急停或完成物理验证。不要仅为消除 blocker 增加空实现。

## 3. 第一次最小动作

1. 先在受控 bench 完成独立实体急停检查；在低风险、低速、无载荷动作中验证软件停止与急停行为。检查期间操作员保持安全距离，不把身体作为停止试验对象。
2. 由现场负责人显式授权本次 arm，只使用已验证的动作维度和限值；先单关节/夹爪，后空载臂，再轻软物体。不要把完整自然语言 fetch 作为第一个动作。
3. 单机器人动作和观测确认稳定后，才在 Console 提交限定任务。Local 创建后需批准；Fleet 页面“创建并开始”会立即批准，操作前应明确当前控制端。
4. 出现 `SAFETY_STOPPED`、`BACKEND_STOP_FAILED`、未知终态或异常运动时停止派发，使用实体急停，检查机械状态、journal 和环境结果。不得因结果不明重复同一动作。
5. `CANCELLED` 表示一次命令取消，不等于急停锁存；急停锁存必须现场显式复位。重启不会清除持久化锁存，不能删除 journal 或临时新建 SafetySupervisor 来绕过。当前复位与连接/arm 的可用接口及限制见[实机上手](../sim2real/README.md)。

## 4. 策略与感知边界

- `ROBOT_ENTITY_PROVIDER=module:function` 提供真实 scene entities；`ROBOT_VERIFIER_PROVIDER=module:function` 提供真实后置条件判断。导入 provider 不得驱动硬件。
- Fleet 策略在 Edge 调用 HTTP sidecar，形成 `action_chunk`；Local Brain 的策略装配需单独核对。LLM 配置不是动作策略配置，不能因此声称本地实机抓取已可用。
- 桌面 action 仅允许驱动已注册的手臂、头部 `.pos` 键，所有数值有限且通过各层上限检查；安全限制是软件保护的一部分，不替代机械限位、速度/力矩与实体急停。
- 命令必须在 deadline 与 lease 内完成；断网停止上限由实际 lease/看门狗与硬件响应共同决定，必须测量记录，不能假定恒为一秒。
- 缺少动作块返回 `POLICY_ACTION_CHUNK_REQUIRED`；感知与验证失败不能返回物理成功。完整协议见[策略工具](../production/policy-tools.md)。

## 5. 每次实验记录

使用[Sim2Real kit](../sim2real/README.md)按次记录 simulation、safety、estop、network、duplicate、trial、soak 证据，绑定本次配置和制品 hash；同时保存任务/命令 ID、限制值、物体与场地、成功/失败和实际停止时延。不要在日志或照片中记录密钥。

至少 30 次独立真实 trial 与要求的 soak 是试点证据门槛，不是“做满次数即安全”的认证。失败也需要如实记录；改固件、模型、标定、地图或安全限制后重新评估，不能复用旧身份的通过结果。
