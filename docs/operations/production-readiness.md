# XLeRobot 生产就绪判定

当前 V1 是仿真与集成候选版，没有已完成的实机生产验收。云端 Server/Orin Edge 的当前放行判断和现场证据模板见[2026-09-26 生产就绪审计](../production/field-readiness-2026-09-26.md)；原单机器人范围见[V1 当前状态](../production/v1-release-status.md)。购机用户从[Sim2Real 上手](../sim2real/README.md)开始。

## 哪些结果可以证明什么

| 结果 | 证明范围 |
| --- | --- |
| MuJoCo / RoboCasa 测试通过 | 对应代码版本和限定仿真场景的行为 |
| `robot-agent doctor robot-pi` | 无动作配置、文件、证书与驱动兼容预检 |
| Runtime `READY` | 当前软件能力检查状态，不是现场动作许可 |
| `production-check` 的 offline READY | provider 可导入；当前 Sim2Real 逐次记录、部署配置、标定文件及安装源码版本一致；journal 文件已检查或明确尚未初始化 |
| Sim2Real `check` / `report` | 版本化配置、制品与操作员记录符合阶段要求 |
| 实际生产放行 | 现场负责人验证风险、策略/感知质量、真实停止与恢复，并完成必要评审 |

以上结果不能相互替代。仓库没有随附适配所购 XLeRobot 的已训练生产模型；感知、动作策略、verifier、标定和实体安全必须独立集成。默认 systemd 服务连接并保持扭矩关闭，不自动 arm；连接前必须支撑机械臂，底盘禁用，急停锁存不随重启解除。

## 离线前置检查

在树莓派配置 `ROBOT_COMMISSIONING_KIT` 为已完成的 Sim2Real kit 绝对目录后执行：

```bash
sudo robot-agent production-check robot-pi
# 或
make production-check
```

它执行无动作 preflight，检查两个不同且当前用户可读写的字符设备、固定驱动源码、标定格式和持久 journal；不会打开串口。`XLEROBOT_CALIBRATION_ROOT` 是 direct Runtime 的实际目录，旧 `XLEROBOT_CALIBRATION` 若存在必须一致。journal 不能位于临时目录，已有文件损坏、锁存或存在未决命令时停止检查。首次部署可尚无 journal，但报告为 `uninitialized / history_verified=false`；绝不能据此认定以前的动作已对账，或删除旧文件绕过检查。

`ROBOT_ENTITY_PROVIDER` 和 `ROBOT_VERIFIER_PROVIDER` 必须可导入且为 callable；已知 mock/sim provider 路径被拒绝。检查复用 Sim2Real `pilot_report`：记录绑定配置和制品，至少 30 个不同 task ID 的 trial、实体急停/断网/重复命令及 soak 记录均须齐备。正在检查的 env 必须与 kit 的 `robot-pi.env` 完全一致，实际标定文件 hash 必须匹配 kit，`site.json.softwareRevision` 必须是安装源码的 Git commit，运行时代码不得有未提交漂移。报告不输出配置内容或凭据。

记录或 JSON 附件显式标记 Gazebo、MuJoCo、RoboCasa、mock、fake 或 simulation 时，不能用于通过硬件项目；`kind=simulation` 仅保留仿真记录身份。重复的 trial task ID 会阻止离线通过，不能复制一次结果凑够门槛。这是来源标签和制品一致性检查，不能鉴定伪造标签或普通文本日志的真实性。操作员仍须核查现场原始证据。

通过时输出：

```text
READY xlerobot offline prerequisites passed; physical readiness is not verified
```

底层 `scripts/xlerobot_production_check.py --json` 输出 `scope=offline_prerequisites` 的单一 JSON，始终带 `physical_ready=false` 和 `live_verified=false`。已安装源码的版本不证明正在运行的进程版本；实时传感器新鲜度、采集时标定绑定、真实停止时延仍需只读采样及现场测试。journal 路径检查不能证明底层挂载的持久性或服务用户的权限，部署时须确认持久卷与 `tangying-robot` 用户权限。脚本不会调用 provider、策略或硬件动作；导入 provider 本身不得产生设备副作用。

## 使用逐次证据而非预填通过

新现场使用 [Sim2Real kit](../sim2real/README.md)记录 inventory/integration/pilot，逐次保存 simulation、safety、estop、network、duplicate、trial、soak 结果。记录绑定配置和制品 hash；实际模型、标定、地图、安全限制变化后重新检查相应证据。

旧 `hardware-trials.json` 的总次数和布尔值、`safety-checklist.json` 的全 true 声明不再作为通过证据。迁移时应保存实际原始附件并用 Sim2Real `record` 逐次登记，不能自动把旧计数转换成 30 个真实试验。记录之前固定最终 env、软件和标定；任何输入变更会使原记录失效。

## 现场放行顺序

1. 匹配固定软件与所购硬件，完成接线、实体急停和稳定串口。
2. 现场标定，校验关节、相机、坐标变换与安全限制。
3. 无动作预检和 mTLS 配对；接入真实感知、经过评估的动作策略与 verifier。
4. 显式连接、现场 arm，先单关节/夹爪、空载 bench，再单机轻物任务。
5. 记录急停、断网、重复/未知命令与持物恢复；失败同样保留。
6. 完成独立真实 trial 和 soak 门槛，再由现场责任人对受限试点作出决定。扩大到生产任务需追加容量、长稳、备份恢复及必要安全评审。

至少 30 次 trial 或 pilot evidence 通过不会自动生成 PHYSICAL_GO 或生产认证。更详细的系统契约见[仿真到实机](../production/sim-to-real.md)，恢复要求见[异常运维](../production/operations-and-failures.md)。
