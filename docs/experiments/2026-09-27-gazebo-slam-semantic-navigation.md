# Gazebo SLAM 与语义地点导航验收

日期：2026-09-27。基线 `7b34d52abbbd73c333ddd5566f85a6f3b17d59c6`；实现分支 `codex/gazebo-slam-semantics`。本记录对应[升级规范](../development/2026-09-27-gazebo-slam-semantic-navigation-spec.md)及[操作指南](../guides/slam-semantic-navigation.md)。本轮独立于之前的家庭抓放验收。

## 被测问题与方法

验证 Gazebo 相机/深度/同帧里程计能够构建持久 SLAM 地图；当前地图是否成为语义地点的权威；自然语言房间和工作区名称能否解析成经过地图准入的导航目标，并以实际移动后的固定传感器证据确认到位。

使用同源 XLeRobot CAD 与四房间装修家庭，不改变场景和机器人。几何来自传感器测量；功能名称来自显式标注，自动分区仅有编号。比较原版本仅依赖 commissioning 目标、单目标中文工作区不受支持，与本轮保存地图语义目录和路线内别名绑定的行为。这是源码差异与一轮工程测量，不是任务成功率的随机对照。量化统计扫描帧、配准、回环、轨迹、地图摘要及每个命令的位姿误差。

## SLAM 测量

通过注册 `mapping.start` 完成一次完整 `survey`，保存并激活 `scan-0f6e72557a1a`：

| 指标 | 本轮测量 |
| --- | --- |
| 关键帧 / 配准 / 回环 | 232 / 216 / 2 |
| 行驶距离 | 23.464025274 m |
| 发布点云 | 72,761 点 |
| 地图修订 | `4c915e1502a7de19cf2bcc526dfb6fd2832819d1e36b7ec72b17c0c36c1dbd1d` |
| 标定修订 | `68c581e7c5e70c1b1909e66ee3e68560b2d5c51eee276f684ed22fe9be80f58c` |
| 世界资源身份 | `3007f73be75ead890139fbe1c6b0a0f9bb688ec30a19161e2a4eb512037c5aae` |
| 功能地点 / 工作区 / 无名分区 | 5 / 1 / 6 |

12 个目标在当前足迹 0.32 m 下净空可用；这不代表已实际访问全部 12 个目标，也不保证未知区域间存在可走路径。卧室、浴室、走廊标注关联了几何分区；厨房和客厅停靠点没有落入分区标签，仍通过实测栅格净空认证。这说明形态分区不能等同功能房间覆盖，六个分区也不是六个功能房间。

厨房房间与厨房工作区共用相同经配置的操作停靠姿态，不能把两个标签计为两个不同物理地点。物体层保存了杯、盘、收纳容器与花瓶的真实视觉线索，来源为 `gazebo-home_furnished/head-rgbd`；记忆不代替到达后的新鲜感知，也不扩大操作对象目录。

ROS RTAB-Map 数据库位于本轮资源命名空间 `3007f73be75ead89/head-97634ababe40068d/rtabmap.db`。映射阶段关闭后只读检查：795 个 Node / Data、1,067 个 Link，131,895,296 字节，`quick_check=ok`，SHA256 `7b19f9b93e23f22a5b4df5376a66b27f8d57deab5f13beee51490adc21ae4931`。这是原生定位资源，和网关不可变地图包分别记录，不能互相替代。这个摘要属于进入定位前的状态，运行后数据库文件可能变化。

## 缺陷与失败样本

1. 旧语义目标来自 commissioning。改为加载并校验保存地图的 `semantics.json`，保留显式拓扑连接但标记来源，目标要通过实测自由格净空。
2. 感知和 SLAM 不同帧时，物体记忆错误借用了 SLAM 时间/位姿。改为使用实体捕获自身的时间与底盘位姿，过期或缺少配对则记录缺口。
3. 首次新任务 `task-714b6d112fc1dc52ac4c35ca`（LLM）请求“前往厨房工作区”，在 `navigation.navigate` 的参数校验处失败为 `TOOL_PARAMETERS_INVALID`。预摆位已经得到确认，导航名称没有通过严格参数准入。原因是 Grounding 只保留 canonical 地点，模型输出中文别名无法转换。修复为保留当前地图中指向已认证路线的别名，并在下发前解析；未认证的目标仍拒绝。补充单目标 LLM 预摆位航向。
4. 标定在驱动外改变时，语义只读服务也需及时失效。新增语义读取前标定核对，不等待下一次导航调用才撤销旧目标。

失败任务原始输出保留于 `artifacts/gazebo-semantic-navigation-tasks-1/`，未重发失败命令。修复部署发生在新一轮任务验收前；部署重启会重建场景，须作为新 episode 记录。修复后以下三个新任务在同一新 episode 内全部通过，任务间没有重启或复位。

## 自然语言任务与实际到位

三项任务均使用当前配置的 LLM 规划，未切换为硬编码坐标脚本。验收套件严格核对 `command_observation`、回执捕获 ID、地图修订、原始 RGB/深度摘要与实际底盘位姿；门限保持 0.08 m / 0.15 rad。

| 请求 | 任务 ID | 确认步骤 | 校验图像 | 实际目的地 |
| --- | --- | --- | --- | --- |
| 前往厨房工作区 | `task-5e92d153f713c0b8f2e08030` | 4 | 10 | kitchen_work_area |
| 返回客厅 | `task-726c37060f70c1e24757d07b` | 5 | 12 | living_room |
| 巡检卧室和卫生间，最后回到客厅 | `task-a34797c79335022defa192f8` | 12 | 26 | bedroom → bathroom → living_room |

合计 3 个成功任务、21 个确认步骤、48 张校验图像、5 次实际导航；物理重试 0、任务期间复位 0。五次导航的实际位置误差分别为 0.002100、0.004820、0.016357、0.001026、0.001522 m；最大航向误差 0.029042 rad。误差是固定命令完成观测中的目标与实际位姿差，不是模型预测或规划终点。全部使用新地图 `scan-0f6e72557a1a` 的同一修订。

原始运行目录为 `artifacts/gazebo-semantic-navigation-tasks-2/`。可提交的[验收清单](2026-09-27-gazebo-slam-semantic-navigation-acceptance.json)保存 83 个原始文件摘要、各导航命令与捕获 ID、五个目标/实际位姿，以及地图包资源摘要；本地原始数据保留在 artifacts 中。镜像为 `sha256:036fca5e30180a2a0a7fec3c33861fcc72832a66cd6ae2ea90de6510e17acb6c`，源码标签 `c59517d65eafff3c35e1f453de636e59622ae6cea0bc94585a88085fe8b64fed`，与验收时本地网关/ROS 源码指纹一致。家庭 world 内容摘要与先前闭环一致（`3c1d94f6305369933aa186a5a2edc06ae771ab48188f7f777cf4391f1cae3132`），新的导出 source/resource 身份单独记录，没有换成其他家庭模型。

## 软件回归与部署过程

已通过 `make build`、`make lint`、`make generate-check`、`make book-check`；相关 Python 115 项、Go `edge/agent` / `edge/robotclient` / `skills/manipulation`、文档与书籍 10 项均通过。全量 `make test` 在物理任务全部结束后运行并以退出码 0 完成：Go 全部通过；Python 边界 2 项及主套件 2,418 项通过，40 项按条件跳过（跳过不计为实机/外部环境认证）；Web 479 项通过。主 Python 套件耗时 805.44 秒。README Go 文档一致性检查也通过。日志保留于 `artifacts/gazebo-semantic-navigation-full-test.log`，本轮只验证软件门禁，未打新发布标签。上一轮 Draft PR #12 的完整[实机/异构接入指南](../guides/hardware-agent-integration.md)与[修订记录](../development/2026-09-27-hardware-integration-guide-review.md)也纳入集成分支；该步骤只修改文档，代码指纹保持上述验收身份，书籍与文档检查再次通过（10 项）。

本轮在扫描前有准备启动/更新部署；保存地图后切换到定位模式；首次别名失败后部署新 Go 二进制并开启最终新 episode。所有部署重启与失败样本保留，最终三任务之间没有重启。历史资源 namespace `0baf96957cca77ef` 的数据库归档保留为 `rtabmap.before-semantic-slam-20260927.db`，它不是新地图的原生数据库。诊断时曾使用旧 namespace 检查数据库，随后按当前资源身份纠正；最终收据为新 namespace 的只读检查结果。

## 原始资源与复现

- 扫描：`artifacts/gazebo-semantic-slam-survey-1/`，日志同名前缀 `.log`。
- 地图：`artifacts/sim-stack/gazebo-xlerobot-home/gazebo/maps/home_furnished/workflow/scan-0f6e72557a1a/`。
- 原生建图收据：`artifacts/gazebo-semantic-slam-native-mapping.json`。
- 语义服务收据：`artifacts/gazebo-semantic-navigation-service-receipts.json`；12 地点可读取，厨房工作区解析到 world `[2.05,3,0.035,...]`，未知地点拒绝为 `LOCATION_NOT_FOUND`。

操作命令见指南。依次完成 `mapping` 扫描、保存地图、停止并切换 `localization`，再以新输出目录运行 `semantic-destinations` 和 `patrol`。两套地图均保留。不要直接重写原生数据库身份或保存地图，也不复位进行中的任务。

## 局限

这是一次仿真工程验收，不能推出任务统计成功率、真实轮胎牵引、真实相机或 Orin/GPU 性能认证。功能命名为显式标注，尚无自动视觉房间功能分类。分区、路线拓扑、工作区底盘可达、机械臂 IK/物理操作是不同证据。真实设备必须单独验证坐标原点/重定位、足迹、制动、传感器配对和驱动安全约束。
