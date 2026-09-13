# 通用移动操作 Agent：系统审查与升级记录

审查日期：2026-09-13。基线提交：`e626893`。本文区分现有能力、本次实现和仍需实机接入的部分；测试数字来自本轮执行，历史设计文档不作为实现证明。

## 结论与交付边界

现有项目具备可继续扩展的分层基础：Go Agent/TaskGraph、Runtime 协议、机器人适配器、Python 感知与工具、ROS 2 导航以及 Web 控制台。参考家庭仿真已经可以完成自然语言移动抓放任务。但“已有工具定义”不等于“任意机型可执行”，现阶段还不能称为任意底盘加机械臂的开箱即用产品。

本轮实际修复了执行安全旁路、超时并发、复合动作身份冲突、地图导出坐标/深度、语义导航的场景泄漏、仿真时钟以及地图展示问题；增加工作区候选规划和固定版本真实感家庭仿真资产。后续升级已接通环境中立的注册服务、用户自行标定、实际移动稠密 SLAM、地图加载导航及紧凑工作台，最新使用方式见[标定与建图操作指南](../guides/robot-service-workflow.md)。本文下面的验收数字记录首轮审查时点；没有连接或操作实体机器人。

## 1. 架构和可扩展性

当前交付入口应以 README 的本地单机器人工作台为准；Fleet 是可选的多机器人部署形态，不能同时存在两个控制同一机器人的调度权威。

建议保持这条唯一执行链：

```text
自然语言 → 受限意图/工具 Schema → 语义对象与地图解析 → 有界任务图
       → Runtime 统一准入（审批、版本、资源租约、幂等日志）
       → 机型适配器 → Nav2 / MoveIt / 厂商控制器
       → 命令后的新观测 → 闭环完成判定与恢复
```

较合理的已有设计：命令与观测分离、显式错误码、资源 fencing、动作结果未知时不重复执行、持久化任务/证据、机器人 profile 与控制实现分离。

主要缺口是多个层次均拥有“工具/skill”定义，却没有全部使用同一个执行入口。本次 `GatewayRobotAdapter` 改为强制接收运行中的同一个 `RobotRuntimeService`，禁止原始 Backend 直通。工具默认安全配置与服务默认配置统一；生产部署仍应显式注入自己的受支持 profile。

## 2. 工具和 Skill

现有导航、感知、机械臂、夹爪、安全、复合技能分类可以保留，不应为每个自然语言说法建立一个新底层工具。工具描述需要明确：单位、坐标系、前置条件、允许副作用、幂等性、资源、超时、返回证据，以及动作回执与完成验证的区别。

本轮改变：

- ToolExecutor 在执行入口检查嵌套 Schema、未知字段、数值类型和 NaN/Infinity。
- 单机器人所有运动工具共用准入锁；底盘移动不能与另一任务的机械臂动作并发。SDK 超时后，锁由仍在执行的工作线程保留到真正退出。
- 超时/取消返回结果未知且不可自动重试，截止后阻止复合技能继续开始下一动作。急停不受节点健康、普通运动锁、已取消任务的限制。
- 顶层 tool call ID + 子步骤序号构成命令身份；同一步重试复用身份，不同 hover/lift 或导航目标不再冲突。
- 机械臂与夹爪由部署端显式提供 actuator mapping，并检查 profile 中关节 rad、夹爪 m 与执行器单位；normalized 等其他单位需要显式标定转换。不再猜测 `joint0` 或给任意机型套 XLeRobot 前缀。
- 不存在有效控制器限速接口时，`set_speed_limit` 返回不可用，不能宣称限速已生效。
- `stop_navigation` 接受 Schema 中声明的 command_id。目标搜索要求新观测和唯一完整匹配，避免仅凭颜色片段选错物体。
- 可选 `plan_work_area` 只接收地点名；机器人身份、活动地图、当前位姿、尺寸和 IK 验证器来自可信部署上下文。

后续产品化应提供以下能力组，但必须由真实服务实现后才发布为可调用工具：

| 能力组 | 工具/技能 | 必须具备的后端条件 |
|---|---|---|
| 初始化 | inspect_robot、calibrate_joints、calibrate_cameras、validate_calibration | 设备发现、串口驱动、标定板/手眼算法和实测报告 |
| 地图生命周期 | start_mapping、finish_mapping、validate_map、list_maps、activate_map、localize | SLAM 生命周期、Nav2 加载确认、空闲状态、定位收敛 |
| 语义层 | list_locations、resolve_location、annotate_workspace、find_object | 版本绑定的房间/台面标签、对象身份和置信度 |
| 移动操作 | plan_work_area、navigate_to_work_area、verify_arrival、pick/place、verify_effect | 足迹、IK、自碰撞、环境碰撞、持物导航、控制器与新观测 |
| 恢复 | cancel_task、inspect_outcome、relocalize、safe_retract | 明确恢复策略；不能把故障动作从头重放 |

目前 Python 复合抓取仍是参考编排，不能凭借接口存在宣称具备通用抓取轨迹；真正的接近、下降、接触、抬升以及持物验证应由机型控制器/MoveIt 方案接管并逐段验证。

## 3. 自然语言与 Harness

原 Go LLM 入口主要支持桌面 pick/place/fetch，家庭任务更多依赖确定性中文解析。新增加 `navigate_route`，模型输出语义地点，不允许输出坐标、批准编号或其他未声明字段。支持中文地点别名；通用目标必须与当前机器人、活动地图和标定版本一致，并落在 profile 导航范围内。返航使用命令前新观测的真实起点。

后续服务隔离升级已移除 Agent 中的样板房坐标和场景判断。地点、对象线索和工作区由机器人服务发布，并绑定机器人、地图与标定版本；驱动缺少语义解析器时拒绝导航或移动抓放。参考场景的固定布局仅保留在该驱动的标注实现中。

Harness **已有实现**，关键代码在 `core/closedloop`、`core/harness`、`edge/agent` 与验收脚本。它包含动作后新观测、稳定性验证、错误分类、恢复及回放。本次验收使用真实运行的仿真后端，而不是 mock 成功回执。

仍需要增加真实模型评测集：同义改写、代词、多个相似物品、未知房间、地图切换、工具超时、断网重启、持物导航和机械臂失败恢复。应分别统计语义正确率、参数/版本合法率、物理成功率、未知结果重放率与错误放行率；不能只以 JSON 可解析或命令 SUCCESS 作为成功。

本轮未调用外部在线模型，因而没有宣称真实 LLM 自由表达准确率已达标。

## 4. SLAM、导航地图、语义工作区与 WebGL

地图应由同一优化后的坐标系生成不同产物，而不是把显示点云直接交给导航：

```text
同步 RGB-D + CameraInfo + TF + 里程计
 → RTAB-Map 优化位姿/回环
 → 稠密彩色云与 LOD（用户查看）
 → SLAM 占据栅格 + Nav2 动态障碍层（机器人导航）
 → 房间、台面、对象标签（语义定位）
```

本轮修复/新增：

1. 深度 PNG 区分 16 位毫米深度与 RTAB-Map/OpenCV BGRA 打包浮点深度，纠正 Pillow 通道顺序。
2. 使用真实相机分辨率、内参和 base_link→光学相机外参。非刚性变换、NaN、缺失外参、未支持的相机父节点或畸变不能静默通过。
3. SQLite `Node.pose` 是原始里程计，不能当成回环优化后的地图位姿。CLI 数据库导出要求 `--optimized-poses`，读取官方 format 11，并核对节点 ID/时间戳。
4. 导出 Nav2 `navigation/map.yaml`、`map.pgm`，保留占据/空闲/未知三态、行方向和原点 yaw。导航生成使用原始几何，不用显示 LOD 平均后的点，避免薄障碍消失。
5. 优先传入权威 SLAM 占据栅格。点云高度投影仅是待验收候选：可见地面点不能证明机器人整个足迹可通过，也不能推断未看到的区域为空。
6. MapCatalog 验证地图身份、manifest 哈希、所有声明产物、相机标定版本和语义标签；地图 revision 不允许原地覆盖。所有 LOD 均记录哈希，拒绝目录逃逸。工作区规划核对 Nav2 阈值和图像关联，并限制输入大小。
7. 工作区规划在未知/障碍/边界膨胀后搜索连通空闲区域，按臂展和目标高度筛选底盘候选，支持地图原点旋转。IK/碰撞校验器未接入时只能返回候选，始终不自动授权执行。
8. ROS 仿真时间转换成 Unix 观测时间，保留实际 age；仿真暂停时旧观测会过期，恢复后也不重新刷新旧 TF 的年龄。
9. 已实现独立 Three.js/WebGL 点云画布、地图选择、LOD 按需加载及销毁后的异步响应保护。不同地图不再直接叠到其他机器人场景。二维地图机器人标记随位姿更新，并计算原点旋转。

**地图查看与地图激活是独立操作。** 地图产物 API 只读；后续升级通过注册的 `mapping.activate` 服务增加了显式激活。参考驱动验证版本、标定和世界坐标系后加载栅格并发布 `active_map`；硬件/Nav2 提供者还必须等待地图加载与重新定位收敛。`map_to_world` 变换与 `planning_context` 必须由运行时提供新鲜、已验证的数据。

密集点云或栅格本身不理解“厨房的杯子”。用户意图需要落到带版本的语义标签或当前感知对象，再选择能够操作该对象的位置，而不是简单导航到房间中心。

## 5. 标定、家庭仿真和使用方式

修正轮电机 homing_offset：轮子使用零偏移和完整角度范围。模拟向导产物标记为 simulation，不能错误标记为 measured。纠正相机视锥计算中把半角直接当正切的错误；当前两个相机重叠区域很小，不能仅凭包围盒宣称标定板可用。

原向导引用了不存在的 `calibration_hardware` 模块。现在明确报告尚无内置真机驱动，并允许通过 `--hardware-factory module:factory` 接入已验收的 CalibrationHardware。此变更没有实现真实相机外参、手眼或轮径/轮距自动标定。

更真实的家庭仿真采用 AWS Small House 的**静态资产**，固定 `ros2` 历史版本 `ff9631ca6d1db9c1ba656498151464b5ab74aafe`。上游 RoboMaker 已停止维护；本项目复用许可下的几何/纹理，不运行上游 Classic 插件。下载器保留许可证和来源记录，生成用于 Harmonic 的静态模型，修复无效惯性和纹理路径，使用本项目 RGB-D/里程计/控制器。

```bash
.venv/bin/python scripts/prepare_home_world.py
# 输出 artifacts/sim-assets/aws-small-house-harmonic.sdf
# 首次下载需要联网；后续固定版本可离线重建。

TANGYING_GAZEBO_WORLD=/assets/aws-small-house-harmonic.sdf \
  make gazebo-house-start
```

底层 compose 已挂载 `/assets` 和设置模型资源目录。启动前仍按原入口配置导航 token。新房屋需要重新勘测、定位和标注，不能复用 MuJoCo 参考房间坐标。默认 spawn 仅用于加载测试，部署者需要确认出生点与机器人足迹无碰撞。

地图导出示例（在安装 RTAB-Map 的环境执行原生导出）：

```bash
rtabmap-export --poses --poses_format 11 --opt 0 --output survey --output_dir /tmp/map-export survey.db
# 将实际生成的 base_link optimized pose 文件传入 --optimized-poses。
.venv/bin/python scripts/build_map.py --database survey.db \
  --optimized-poses /tmp/map-export/survey_poses.txt \
  --calibration calibration.json --camera base-rgbd \
  --map-id home-r2 --robot-id robot-01 --output artifacts/maps/home-r2 \
  --occupancy-grid slam-grid.json --semantic-workspaces workspaces.json
```

`slam-grid.json` 为 `width,height,resolution,origin:[x,y,yaw],cells:[行][列]`；`workspaces.json` 例如 `[{"name":"厨房台面","aliases":["kitchen counter"],"target":[3,3,0.9]}]`。这些位置必须来自该次勘测/标注。MapCatalog.plan 所需 map_revision 使用构建结果的 manifest hash，不能由模型随意指定。

工作区工具通过 `build_registry(..., map_catalog=catalog, planning_context=provider)` 显式接入。provider 返回 `map_id,robot_id,calibration_revision,map_revision,start_xy,envelope,localization_fresh`，可附带 `validate_candidate(pose,target)`。机器人驱动负责将经验证的候选送入现有 Runtime→导航→到位验证→重新观察→操作闭环。

## 本轮验证

- Go 全套测试、构建与 lint：见本轮验证记录。
- 家庭自然语言闭环：**通过**。任务 `task-1795bb31bd018b392c68563b`，12 个步骤、13 份观测、26 张图像；最终放置关系与稳定性检查通过，返回客厅。
- 证据：`artifacts/acceptance/system-audit-20260913/summary.json` 和同目录原始数据。
- WebGL：实际浏览器中选择 `sim-home-hi` 并显示已保存点云；不是只验证 bundle 导出。
- 新家庭世界：SDF 校验和 Harmonic 服务端 100 次迭代加载验证；不等于该世界完整建图/导航验收。
- 全套 Python、ROS 容器、Web 和最终静态检查的数字写入下方验收结果；硬件和在线 LLM 未验收。

### 最终验收结果

| 检查 | 本轮结果 |
|---|---|
| Go 全套 | 通过（`make test-go`） |
| Python 全套 | 1522 passed，35 skipped；跳过项依赖额外环境 |
| 最终 Gateway/工具定向回归 | 577 passed；最后单位/阈值修复另有定向回归通过 |
| 独立 runtime 边界 | 2 passed |
| ROS 2 容器测试 | 57 passed |
| Web | 325 passed |
| 构建、lint、tools.json 一致性 | 通过 |
| 家庭闭环 | 12 步骤全部有完成证据，约 46.8 秒 |
| AWS 新家庭世界 | SDF valid，Harmonic 100 次迭代退出码 0，修复后无 Gazebo Error 日志 |

完整测试输出保存于本机 `/tmp/tangying-audit/`。工作区仍保留所有修改，未生成提交。探索覆盖率的下一目标也已修正为未知区域边界的已知空闲侧；只提供观察建议，不能直接授权导航到未知区域。

## 官方依据

- [Nav2 Costmap 配置](https://docs.nav2.org/jazzy/configuration_and_development/configuration_guide/core_servers/costmap_2d/)：静态地图、障碍层与足迹安全距离应分层处理。
- [RTAB-Map 原生导出实现](https://github.com/introlab/rtabmap/blob/master/tools/Export/main.cpp)：优化位姿、机器人/相机坐标区别与 pose format 11。
- [RTAB-Map 深度压缩实现](https://github.com/introlab/rtabmap/blob/master/corelib/src/Compression.cpp)：浮点深度的 BGRA PNG 包装。
- [AWS Small House 固定版本](https://github.com/aws-robotics/aws-robomaker-small-house-world/tree/ff9631ca6d1db9c1ba656498151464b5ab74aafe)：家庭静态模型、纹理与许可证。
- [RoboCasa](https://github.com/robocasa/robocasa)：更适合厨房操作任务分布；不替代真实家庭多房间勘测。本次未下载其多 GB 全量数据。
