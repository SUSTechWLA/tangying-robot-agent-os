# XLeRobot 生产就绪判定

当前 V1 是仿真与集成候选版，没有已完成的实机生产验收。最新结果见[V1 当前状态](production/v1-release-status.md)；购机用户从[Sim2Real 上手](sim2real/README.md)开始。

## 哪些结果可以证明什么

| 结果 | 证明范围 |
| --- | --- |
| MuJoCo / RoboCasa 测试通过 | 对应代码版本和限定仿真场景的行为 |
| `robot-agent doctor robot-pi` | 无动作配置、文件、证书与驱动兼容预检 |
| Runtime `READY` | 当前软件能力检查状态，不是现场动作许可 |
| `production-check` 的 offline READY | 已配置 provider 可导入，传统记录满足字段检查 |
| Sim2Real `check` / `report` | 版本化配置、制品与操作员记录符合阶段要求 |
| 实际生产放行 | 现场负责人验证风险、策略/感知质量、真实停止与恢复，并完成必要评审 |

以上结果不能相互替代。仓库没有随附适配所购 XLeRobot 的已训练生产模型；感知、动作策略、verifier、标定和实体安全必须独立集成。默认 systemd 服务连接并保持扭矩关闭，不自动 arm；连接前必须支撑机械臂，底盘禁用，急停锁存不随重启解除。

## 旧版离线前置检查

兼容命令仍可在树莓派执行：

```bash
sudo robot-agent production-check robot-pi
# 或
make production-check
```

它执行无动作 preflight，检查 `ROBOT_ENTITY_PROVIDER` 和 `ROBOT_VERIFIER_PROVIDER` 可导入/调用，并读取传统 `evidence/hardware-trials.json` 与 `evidence/safety-checklist.json`。它不会调用 provider 验证物体，不会评估模型输出，也不会重新进行硬件试验。

通过时输出：

```text
READY xlerobot offline prerequisites passed; physical readiness is not verified
```

底层 `scripts/xlerobot_production_check.py --json` 输出 `scope=offline_prerequisites` 的单一 JSON。导入 provider 本身不得产生设备副作用。

## 使用逐次证据而非预填通过

新现场使用 [Sim2Real kit](sim2real/README.md)记录 inventory/integration/pilot，逐次保存 simulation、safety、estop、network、duplicate、trial、soak 结果。记录绑定配置和制品 hash；实际模型、标定、地图、安全限制变化后重新检查相应证据。

旧脚本的硬件记录要求 `completed_trials >= 30` 及三个故障演练布尔值，安全记录要求实体急停和现场操作员布尔值。这些人工声明不校验事实，不能把它们当成开关预填为 true。新用户不应从文档复制一个全 true JSON 来取得 READY。

## 现场放行顺序

1. 匹配固定软件与所购硬件，完成接线、实体急停和稳定串口。
2. 现场标定，校验关节、相机、坐标变换与安全限制。
3. 无动作预检和 mTLS 配对；接入真实感知、经过评估的动作策略与 verifier。
4. 显式连接、现场 arm，先单关节/夹爪、空载 bench，再单机轻物任务。
5. 记录急停、断网、重复/未知命令与持物恢复；失败同样保留。
6. 完成独立真实 trial 和 soak 门槛，再由现场责任人对受限试点作出决定。扩大到生产任务需追加容量、长稳、备份恢复及必要安全评审。

至少 30 次 trial 或 pilot evidence 通过不会自动生成 PHYSICAL_GO 或生产认证。更详细的系统契约见[仿真到实机](production/sim-to-real.md)，恢复要求见[异常运维](production/operations-and-failures.md)。
