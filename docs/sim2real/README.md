# 购买 XLeRobot 后，怎样接入躺营系统

本版的交付目标是：**固定桌面、限定物体与任务、现场有人监护的 XLeRobot 双臂试点**。系统提供任务编排、仿真、策略接口、执行保护、状态追踪和控制台；购买机器人并不能自动获得适合你家或工位的识别模型与动作模型。首次接入由设备集成人员完成，日常用户通过中文工作台操作。

当前界面已区分“用户工作台”和“开发诊断”。本指南将一次性的安装、标定与模型接入放在交付阶段，用户端不需要填写串口、模型地址或动作参数。当前没有图形化标定工具或一键训练实机模型；不要把它们当成已实现功能。

最新自然语言改进支持中文编号、口语别称和同句后续“它”，但[13 项仿真评测](../development/natural-language-evaluation.md)只覆盖固定任务与拒绝路径。RoboCasa 的单回合单向交接不能作为客户工位任意搬运的验证；反向搬运、连续任务重新授权与跨任务记忆需要另行实现并在现场验证。

## 先确认购买范围

**单机器人首版建议先使用 Local Agent + 一个 Runtime。** 云端 Fleet 属于可选部署，并非购机后完成首个任务的必选项。保持统一 `robotId`、profile、重建和工具契约，未来可把同一适配器接到 Fleet；不能同时由两个控制端给同一台机器人派发动作。

### 只有机载 RGB-D 数据时怎样开始

1. **先显示真实数据。** 相机驱动提供彩色、对齐深度、内参和原始采集时间；页面展示同一次采集的彩色和深度预览。断流或过期显示不可用，不用仿真画面替代。参考启动与原理见[单机器人闭环](../development/single-robot-loop.md)。
2. **标定后获得三维点。** 通过深度和内参反投影，再用采集时刻的光学坐标变换转换到工位 `world`。只显示实际观测到的点，视野外和遮挡区保持未知。RGB8 深度预览用于人眼检查，算法输入仍是米制深度数组。
3. **识别后获得可操作实体。** 适配现场的检测器在 RGB 上给像素掩码，系统用对应深度求位置，输出有来源/时间/标定版本的 `scene.reconstruction.v1`。只拿到图像并不等于已知道“哪个是杯子、放到哪里”。随附颜色检测器仅用于参考工位，真实现场需要独立验收。
4. **工具使用同一份规范数据。** 实机 handler 用真实控制器完成抓取、放置、停止，并把本体反馈与动作后 RGB-D 观测返回相同 Runtime 工具。Agent 的任务、审批、恢复和追溯保持相同；替换的是传感器输入、标定、检测器与控制器。
5. **最后才扩展移动建图。** 固定工位可采用固定世界坐标；移动底盘需要另接定位/SLAM、采集时刻 TF 和已观测地图。当前点云是单帧局部观测，不是已完成的 SLAM，也不能把仿真占用图当实机已知地图。定位丢失时应阻止依赖世界坐标的动作。

选择 ROS 2 时，新增[ROS 2 RGB-D 接入](../development/ros2-rgbd.md)提供 `Image/CameraInfo/TF` 桥；默认仅观察和停止，不使能运动。没有 ROS 的设备也可直接生成同一 `RgbdFrame`。新源码包含 ROS 消息合同测试，本轮没有真实 ROS/DDS 设备或相机连接结果。

| 项目 | 需要准备 | 由谁确认 |
| --- | --- | --- |
| 机器人 | 与锁定 `xlerobot_2wheels` 电机布局匹配的双臂 XLeRobot，两块串口控制板，原厂规定电源 | 设备集成人员对照实物与清单 |
| 机载计算机 | 本项目安装脚本面向 Ubuntu Server 24.04 arm64 的 Raspberry Pi 4/5 | 集成人员安装系统、网络与 SSH |
| 笔记本/工作站 | 运行 Edge Worker 与实机动作策略；是否需要 GPU 取决于选定模型 | 算法工程师实测延迟与内存 |
| 相机与工位 | 能覆盖抓取区、夹爪和放置区；固定相机、灯光、容器与可识别物体 | 集成人员选择型号并做内外参标定 |
| 物理保护 | 可直接切断执行器电源的急停、支撑与机械限位、清晰的工作区边界 | 现场负责人按负载评估 |
| 云端 | Fleet、MySQL、Redis、HTTPS、每台设备独立凭据和持久存储 | 运维人员 |

本仓库锁定 XLeRobot 提交 `3d14695e40c9c68229c0aacffca6053c75cd3eb6` 和 LeRobot `0.4.1`。新购硬件可能采用不同版本；先核对电机 ID、驱动模式、控制板和接口，不能直接替换为最新代码。驱动会拒绝未经验证的源代码漂移。本版不使能移动底盘，不包含自主导航。

上游的[软件说明](https://xlerobot.readthedocs.io/en/latest/software/index.html)可用于理解装配后的调试与学习路线；本项目实际使用的接口以[锁定源代码](https://github.com/Vector-Wangel/XLeRobot/blob/3d14695e40c9c68229c0aacffca6053c75cd3eb6/software/src/robots/xlerobot_2wheels/xlerobot_2wheels.py)为准。

## 一次性交付流程

| 步骤 | 操作者与位置 | 做什么 | 进入下一步的依据 |
| --- | --- | --- | --- |
| 1. 定义任务 | 用户 + 集成人员 | 确定物体、容器、工位、负载与成功条件，例如“把指定杯子放入固定托盘” | 任务范围写入接入包，不承诺任意家务 |
| 2. 在仿真中验证 | 开发人员，笔记本 | 跑同一任务的规划、确认、执行、验证、取消和故障流程 | 保留仿真任务 ID、日志和报告 |
| 3. 登记设备 | 集成人员，笔记本 | 生成接入包，填写机器人 ID、两个串口序列号、部署地址与交付版本 | `inventory` 检查通过 |
| 4. 安装与配对 | 集成人员，树莓派/笔记本/云端 | 安装对应软件、配置稳定串口映射、签发证书、分配设备凭据 | `doctor` 无动作预检通过，身份与证书一致 |
| 5. 标定 | 现场集成人员 | 电机标定、相机内外参、工位坐标系，记录安全动作范围 | 标定文件和版本齐全，实际位置经测量验证 |
| 6. 接入感知和策略 | 算法/集成人员 | 识别真实物体，接入匹配模型，实现真实抓取与放置的结果验证 | 清单、模型哈希、输入来源、标定和变换版本一致 |
| 7. 先观察 | 现场人员 | 支撑机械臂，使用未使能的服务检查相机、关节、设备和诊断状态 | 场景、物体、左右臂与坐标对应，数据持续更新 |
| 8. 最小实机动作 | 现场人员 + 开发人员 | 清场后显式使能，先空载低幅动作，再进行限定物体任务 | 日志、画面与物理结果一致；异常可解释 |
| 9. 故障试验 | 现场人员 + 运维 | 急停、断网、重复命令、进程重启、恢复流程、连续任务观察 | 当前版本记录至少 30 个不同实机任务及至少 1 小时观察 |
| 10. 交付用户 | 负责人 | 审阅证据、培训、确定值班与回滚方案 | 负责人签署现场验收；离线脚本通过不能代替签署 |

30 次和 1 小时是本版试点资料的最低门槛，不是可靠性认证。负载、抓取成功率、允许失败类型、停止距离/时间及长期运行要求由现场评估制定，实际标准通常需要更长测试。

## 生成和填写接入包

开发环境依照[新开发入门](../development/getting-started.md)安装。以下在仓库根目录执行；已安装 CLI 时，把 `.venv/bin/python scripts/sim2real.py` 换成 `robot-agent sim2real` 即可。

```bash
.venv/bin/python scripts/sim2real.py init --robot-id robot-1 --output site/robot-1
.venv/bin/python scripts/sim2real.py check --kit site/robot-1 --stage inventory
```

第一次检查出现“待补齐”是预期结果。生成目录与文件为私有权限，重复 `init` 不覆盖；`site/` 已被 Git 忽略。四个子命令 `init/check/record/report` 都不打开机器人串口、不请求网络、不启动服务、不调用动作模型。

| 文件 | 要填写或放入的内容 |
| --- | --- |
| `site.json` | 设备 ID、左右串口序列号、实际软件交付版本、限定任务、Fleet 地址、机器人主机名、world ID、标定与变换版本 |
| `robot-pi.env` | 树莓派运行配置，特别是 `ROBOT_ID`、稳定串口、感知/验证 provider。与 `site.json` 相符 |
| `edge.env` | 笔记本 Edge 配置、该设备独立 token、证书路径、真实模型端点、`EDGE_*_REVISION`。不要复用别人的设备 token |
| `artifacts/tangying-xlerobot.json` | 实物电机标定输出。检查器要求锁定硬件全部 16 个电机的合法校准结构 |
| `artifacts/transforms.json` | 真实工位坐标变换资料，格式见下文 |
| `artifacts/policy-manifest.json`、`policy.bin` | 真实策略清单与实际权重文件；可以在 `site.json.files` 改为包内相对路径 |
| `artifacts/perception-validation.json` | 当前标定与坐标变换下的感知测试说明及结果 |
| `evidence/`、`attachments/` | `record` 保存的测试记录及附件副本 |

`softwareRevision` 应标明经过审阅的源码提交及构建编号；有工作区补丁时同时保存补丁或构建产物摘要，不能只填基础提交号。离线检查只能核对你登记的资料，无法证明设备正在运行该构建。

安装配置到设备时使用 `install -m 0600` 并交给 `tangying-robot` 服务用户所有，不要覆盖已经配对的证书或运行 journal。感知 provider 是你们信任的已安装 Python 模块；配置中填写 `package.module:function`，不是任意外部代码地址。

感知输出还必须能支持语义实体绑定：物体与目标区域具有唯一身份、匹配的 category/attributes/relation。用户指定起点时，当前 Go 客户端需要从 Runtime 观测读到物体的 `inside:<区域ID>` 或 `on:<区域ID>` 关系，并唯一匹配该区域；仅有相机图像或 XYZ 坐标不足以通过这项检查。缺失或不一致应返回明确失败，不能填造关系。类型与 World 映射见[数据契约](../production/data-contracts.md#意图中的实体与起点)。

## 安装、串口和证书

树莓派完整步骤见[实机安装](../install/robot-pi.md)，快捷入口见[快捷部署](../install/robot-pi-quick.md)。云端使用[云部署指南](../install/alicloud-cloud.md)，生产 Compose 现在将权威世界快照写入 `fleet-world` 命名卷。只运行一个主控制平面；不要用同一份文件快照做多主 HA。

连接串口前支持好机械臂并确认执行器状态。按 USB 序列号建立 `/dev/tangying-left` 与 `/dev/tangying-right`；插拔后也要对应同一侧。不能凭 `ttyACM0/1` 的编号判断左右。

笔记本完成 [Local 安装与证书配对](../install/local.md)中的安装和 `robot-agent pair` 步骤即可取得本地 mTLS 证书。**Fleet 实机路线不需要启动 Local Agent 来派发任务**；同一 Runtime 只由一个控制形态派发。

```bash
# 笔记本，人工核对首次 SSH 指纹
robot-agent pair xlerobot.local --ssh-user ubuntu
# 树莓派，无动作预检
sudo robot-agent doctor robot-pi
```

把配对得到的 `ROBOT_CA / ROBOT_CERT / ROBOT_KEY / ROBOT_SERVER_NAME` 对应填入 `edge.env` 的 `EDGE_RUNTIME_CA / CERT / KEY / SERVER_NAME`。它们是笔记本本地路径，不能填树莓派上的文件路径。Fleet HTTPS CA 与 Runtime CA 是不同信任链；分别配置。默认接入包使用 HTTPS 任务轮询；如果启用 Fleet gRPC 通道，再按[配置参考](../production/configuration-and-security.md)配置独立的 `EDGE_MTLS_*` 证书与 CN。

`ROBOT_ID`、`EDGE_ROBOT_ID`、Fleet 设备凭据中的 ID 必须相同；gRPC Fleet 通道还要求设备证书身份相符。接入包检查不验证证书过期、链或现场网络，这些还需 `doctor` 和联机测试。

## 标定、感知与模型怎么接

标定由现场人员按[安全检查表](../safety-checklist.md)操作，使用 `scripts/calibrate_xlerobot.py --acknowledge-hardware-motion`。标定可能要求人工移动关节；连接本身也会禁用现有扭矩和配置总线，机械臂必须有支撑。不能承诺带载连接绝对不动。

`transforms.json` 至少包含：

```json
{
  "robotId": "robot-1",
  "transformRevision": "workcell-a-v1",
  "transforms": [
    {"parent": "world", "child": "camera", "matrix": [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]}
  ]
}
```

这只是结构示例，单位、坐标轴和实际矩阵必须由标定得到，不能直接复制单位矩阵上线。检查器检查结构、有限数值和资料绑定，不测量坐标是否真实；完整坐标解释见[Sim2Real 坐标接入约定](../production/sim-to-real.md)。

感知/验证由树莓派 Runtime 加载：

```text
ROBOT_ENTITY_PROVIDER=my_site.providers:scene_entities
ROBOT_VERIFIER_PROVIDER=my_site.providers:verify
```

- `scene_entities()` 返回实体字典列表：唯一 `entity_id`、`category`、字符串 `attributes`、空或 7 位有限数 `pose_xyz_quat`、0–1 的有限 `confidence`、字符串 `relation`。异常或非法数据会产生 `ENTITY_PROVIDER_FAILED`，不会把坏数据当成有效场景。
- `verify(skill, target_ref, parameters)` 返回 `BackendResult`。必须依据动作后的新传感器证据确认抓住/放到正确位置；不能因为串口发送成功就返回成功。未知物体、遮挡、旧画面和相机故障应返回失败。
- 目前列表 provider 接口没有独立的采集时间字段，Runtime 时间戳不是相机采集时间。**感知模块必须自行检查真实采集时间、设备身份与坐标版本并拒绝旧缓存**，此项列为现场验收必测。原生多相机流与帧级时间戳是后续版本工作。

`perception-validation.json` 记录 `robotId`、`calibrationRevision`、`transformRevision`、`calibrationSha256`、`transformsSha256`、`procedure`、`passed`。两个 SHA-256 是对应文件的完整字节摘要；修改标定文件后必须重做感知验证。`procedure` 应写明如何检查物体识别、遮挡、旧帧、左右坐标和动作后结果，并附实际测试日志，不能只写“通过”。

实机策略在笔记本运行，复用 [Policy 接口](../production/policy-tools.md)中的 `PolicyManifest`、`CallableProvider` 与 `create_server`。对接函数接收 `InferenceRequest`，将观测交给训练好的模型，输出有界关节动作；`create_server(provider, host="127.0.0.1", port=8091).serve_forever()` 提供 `/v1/manifest` 和 `/v1/infer`。

策略清单必须支持 `xlerobot_direct` adapter 和 `xlerobot-dual-arm` model，声明真实模型 SHA-256、观测来源、最大观测年龄、动作边界、动作块长度和标定/变换版本。当前接入包要求观测源为 `scene` 与 `proprioception`，动作 schema 为 `xlerobot.named-joints.v1`。需要原生相机帧输入的模型必须先扩展观测接入，不能只填模型地址。Edge 拒绝把 `deterministic` 模式用于实机。LLM 负责理解任务，不能代替控制机械臂的策略模型。默认限幅值是软件边界，不是对你的硬件已验证的安全参数。

```bash
.venv/bin/python scripts/sim2real.py check --kit site/robot-1 --stage integration
```

这一步只检查文件及配置一致性，不导入现场 provider，也不调用推理服务。通过后仍必须验证现场模型延迟、姿态、动作和成功判定。

## 观察、使能与第一次任务

默认 systemd 服务通过 `--connect` 连接设备但不使能机械臂。连接会禁用当前扭矩并配置总线；**先支撑好机械臂**。控制台会显示 `ROBOT_NOT_ARMED`，这是正常的未使能状态。

开始受监护试验前停止服务，防止两个进程争用端口和 journal。以下只由集成人员在树莓派现场交互终端操作。配置文件必须是自己审阅过的 `KEY=VALUE` 文件；不要 `source` 不可信文件。

```bash
sudo robot-agent stop robot-pi
sudo -u tangying-robot bash
cd /opt/tangying-robot-agent-os
set -a
source /etc/tangying-robot-agent-os/robot-pi.env
set +a
export PYTHONPATH="$PWD/python:$PWD/robot/gateway:$PWD/robot/ros2_ws/src/xlerobot_adapter"
.venv/bin/python -m tangying_robot_gateway.run_direct_edge --connect --arm --operator-present
```

`--arm` 只对当前进程有效，需要交互终端、已配置的感知与验证模块及无安全锁存。它先读取关节当前位置作为保持目标，再使能机械臂/头部，底盘保持关闭。使能仍是物理操作，不是绝对无位移保证。不要把 `--arm` 写入开机服务。Ctrl-C 退出后机械臂不保持使能，需提前支撑。

随后在笔记本启动你们已验证的策略服务和 Edge Worker：

```bash
go build -o bin/edge-worker ./cmd/edge-worker
# 在自己审阅、填写完成的配置所在的终端
set -a
source site/robot-1/edge.env
set +a
./bin/edge-worker
```

控制台打开“我的机器人”，确认设备在线和实时观测；进入“工作台”填写已验收范围内的任务。**Fleet 的“创建并开始任务”会立即创建并批准执行**，点击前须核对请求、目标及现场条件；计划在任务详情查看。独立 Local 模式才是查看计划后再批准，两者不要混淆。第一轮先做空载最小动作和停止测试，再进入拿放任务。观察到目标错误、姿态异常或通信故障时立即停止；物理急停用于现场紧急情况。

`cancel`、急停、异常停止都会撤销本次机械臂使能；不应自动继续旧任务。本版实机未安装经过标定的自动回位轨迹，`recover_to_safe_pose` 返回 `RECOVERY_POLICY_REQUIRED`，按下面的现场流程恢复。保留失败任务 ID，先查明结果再重新执行。

## 停止后怎样恢复

软件复位不是物理急停的替代。现场人员先切断危险动作、支撑机械臂，确认物体位置并取消旧业务任务；然后停止 systemd/前台 Runtime。重启不会清除持久化急停锁存。

使用上节同一服务用户、配置与 Python 路径，在现场交互终端单独执行：

```bash
.venv/bin/python -m tangying_robot_gateway.run_direct_edge \
  --reset-stop --operator-present --operator "现场负责人" \
  --reset-reason "已检查工作区与持物，取消旧任务并确认恢复条件"
```

复位不连接电机、不使能、不自动重新执行命令。它先保存原始 journal 与操作者说明；不确定结果的旧命令键继续返回 `EXECUTION_OUTCOME_UNKNOWN`，新进程不会重放其动作。文件损坏会拒绝复位，需工程人员从审阅过的备份恢复，不能删除 journal 绕过保护。运行中的 Runtime 持有进程锁，未停止时复位会被拒绝。

完成复位后，另行执行 `--connect --arm --operator-present`，核对当前场景，再发起新的经过确认的任务。旧结果、取消事件和复位审计应一并交给开发人员分析。

## 保存证据与交付

每次测试必须基于当前模型、标定、代码和配置。先让 `integration` 检查通过，再登记真实日志；脚本会复制附件并记录 SHA-256。记录是人工报告，不具有防伪签名，也不验证视频内容。

```bash
robot-agent sim2real record --kit site/robot-1 --kind trial \
  --operator "现场负责人" --result passed --task-id "实际任务UUID" \
  --notes "固定工位杯子放入托盘；核对画面和最终位置" --evidence /absolute/path/task.log

robot-agent sim2real record --kit site/robot-1 --kind soak \
  --operator "现场负责人" --result passed --duration-seconds 3600 \
  --notes "记录温度、失败率、延迟、断连和恢复结果" --evidence /absolute/path/soak.log

robot-agent sim2real report --kit site/robot-1
robot-agent sim2real report --kit site/robot-1 --json > site/robot-1/report.json
```

另需登记 `simulation`、`safety`、`estop`、`network`、`duplicate` 五类证据。每类的过程和真实测量要在说明/附件中写清。失败也必须登记 `--result failed`；短于一小时的失败观察可以记录实际时长。当前版本存在失败记录会阻止通过，应调查问题、更新交付版本并重新验证，不能删除失败来凑数。同一任务 ID 重复登记不会增加试验数。

改变接入包配置、模型、标定或资料后，旧记录会计入 `staleRecords`。证据附件被改动则检查失败。`report` 只输出资料检查与缺口，不输出 token。交付资料包括接入包、日志、构建版本、恢复/备份流程和现场负责人验收签字；共享前自行检查日志中的敏感信息。

## 普通用户每天要做什么

1. 确认工位、指定物体和急停状态，等待集成人员完成该班次准备。
2. 打开用户工作台，确认设备可用，选择已验收任务或输入范围内的要求。
3. 核对目标和现场再开始；Fleet 按钮会立即批准，Local 则另有计划批准步骤。结果异常时停止并保存任务编号。
4. 在“任务记录”查看结果；把问题摘要交给支持人员。无需切换开发模式调参。

开发人员使用“开发模式 → 开发诊断”回溯任务 ID、版本、命令、观测、策略和错误；具体入口见[控制台说明](../frontend/console-v1.md)。`operator` 界面版本是展示裁剪，不构成权限隔离；面向纯用户的独立发行还需服务端授权和功能裁剪验收。

## 常见阻塞

| 提示 | 处理 |
| --- | --- |
| `ROBOT_NOT_ARMED` | 确认现场条件，由集成人员显式使能；不要修改检查逻辑绕过 |
| 上游兼容/标定格式失败 | 核对锁定版本和全部电机 ID，重新执行实物标定，禁止伪造校准文件 |
| `ENTITY_PROVIDER_FAILED` | 检查相机、真实采集时间、实体格式、坐标和 provider 日志 |
| `VERIFIER_INVALID_RESULT` / `VERIFIER_FAILED` | 检查动作后的观察与有限置信度；不能用固定成功返回值 |
| `POLICY_HASH` / `POLICY_REVISION` | 核对实际模型文件与现场清单，重新验证变更后的版本 |
| `CONFIG_MISMATCH` | 对齐 robot ID、主机名、world 和标定版本；修改后重新生成测试证据 |
| `EXECUTION_OUTCOME_UNKNOWN` | 不重试旧动作，检查实物结果，取消旧任务，执行本地恢复流程 |
| World 快照损坏/锁被占用 | 保留文件，停止重复主进程，工程人员恢复；禁止多主共享本地快照 |
| 设备离线/证书失败 | 对照两侧日志检查时间、地址、证书链和身份；不要开启不安全传输来通过验收 |

本版实际完成项与仍待现场验证项统一见 [V1 发布状态](../production/v1-release-status.md)。
