# Changelog

软件版本、当轮任务与验证结果见 [v0.2.0 发布记录](docs/releases/v0.2.0.md)。单机器人限定工位是当前交付主线；软件发布与客户实机的现场放行分别验收。

## v0.2.0 - 2026-09-09

- 移动仿真初始化在操作位后方约 65 厘米；通过双机载 RGB-D、RTAB-Map / Nav2 实际接近桌面，再重新观测、抓取、放置和确认环境变化。固定工位入口继续直接就位。
- 导航工具预算为 60 秒，抓取／放置仍各 15 秒；在实际派发时生成受调用方截止时间约束的有界 deadline，长导航和安全暂停不会耗掉后续步骤的预算。未知物理结果仍禁止自动重放。
- 导航桥持久保存失效当时的就绪阻断项、输入年龄和观测时间，故障恢复后不以当前健康状态覆盖历史原因。
- 区分 Nav2 实际移动到达与当前位置确认。第二个物体任务在新鲜 RTAB-Map 定位和独立里程计均满足 15 毫米／0.04 弧度时继续；保存独立目标身份、完成来源和原始定位时间，不重复发起底盘运动或借用旧回执。
- 统一 ROS 通信实现配置，提供 Cyclone DDS 与显式 Fast DDS 回退；保持相机、定位和速度的新鲜度限制。
- 为参考工位加入实际渲染的非重复地面纹理，解决收臂后纯色地面无法建立视觉签名的问题；增加近目标连续距离评分并细化角速度采样，解决同一栅格内缺少距离梯度及末端转向量化导致的停滞。保留 25 毫米感知地图、未知区域拒绝、完整足迹检查和 1.5 秒碰撞预测。
- 修复 MuJoCo 3.12 枚举与 NumPy 关节类型比较不对称导致严格自身过滤失败的问题；生产依赖锁定已验收的 3.11.0，并增加 3.12 兼容检查。
- 固定 gRPC、Protobuf、NumPy 和 Pydantic 的发布基线，避免重新安装时的依赖漂移；生成协议代码必须与仓库一致。
- 修复 Robot Edge 的 LeRobot／Protobuf 依赖冲突，使用共同的 Protobuf 6 基线并保持协议描述不变；显式安装 Feetech SDK，固定兼容 NumPy 的 OpenCV，增加 Linux ARM64 真实依赖解析门禁。
- 新增 `run_navigation_acceptance.py`，自动保存完整任务、原始观测、图像哈希、实际位移和三帧放置验证；支持在导航工具边界暂停后继续。
- 统一源码、安装器、CLI 和内置 Runtime 的 `0.2.0` 版本身份；首次源码构建未生成安装回执时也可查询版本。
- 修复导航地图尚未读取时误报未就绪，以及 Local 子任务总进度与实际工具事件脱节；完成展示必须有对应的放置验证证据。
- 修复签名验收接收器在并发重复上传时提前关闭连接的问题：以有界流式读取处理仍在发送的重复请求，再返回冲突，不重复接收或改写已保留证据。

## 未发布 · V1 集成候选

以下保留原“未发布”升级记录，相关源码改动纳入 v0.2.0；当时的测试数字与历史签名包不自动代表本次发布。历史 rc 记录保持原始版本，V1 范围与软件包版本分别管理。

- 引入可选 RTAB-Map + Nav2 导航：双 RGB-D 原始流、采集时刻 TF/里程计、持久地图、定位状态、命令幂等与速度租约；导航前收臂，到位后重新观察，保留统一工具与 MCP 边界。
- 修复机器人自身污染地图：独立机器人 CAD 和同帧关节反馈逐像素匹配自身表面，只在导航输入中移除，不把遮挡当作空闲，不改用户原始画面。
- 修复不合理的动作确认与历史回看：归档工具实际验证帧，失败同样可回看；自由释放后检查不同帧中的支撑与位置稳定；抓取 IK 禁止隐式移动底盘。
- 统一彩色图、深度图、点云的固定显示区域；显示实际成功呈现帧的 FPS 与采集年龄，预解码后替换画面，非工作台页面暂停相机预览刷新，保留任务和安全更新。

- 单机器人 V1 新增机载 RGB-D 反投影与参考工位识别，动作后重新观察确认抓取/放置；彩色、深度和局部点云不使用全知场景补全。旧真值模拟仍显式保留为开发调试。
- 修复点云抽样漏掉桌面小物体及统一单色难辨认的问题：XYZ 与同像素 RGB 对齐，物体掩码保留实测采样、参考工位使用 4096 点；优化点大小、工作区取景与物品标签。逐点颜色贯穿 Python/Go 合同、历史保存及前端，旧无色记录继续兼容，畸形颜色和同帧改色会被拒绝。
- 新增工具边界暂停、同任务重启后显式继续、已完成物理动作去重与未知结果阻断；只读步骤重新观测，拒绝通过更改版本或安全标签绕过核对。
- 保存任务/步骤关联的同帧 RGB、深度预览与规范重建，保留哈希和原始时间；工具完成状态关联成功保存的观测证据，用户可回看历史。
- 新增 ROS 2 RGB-D 消息桥，校验原始时间、对齐、深度单位、内参和采集时刻 TF；默认实机输入仅观察。本轮已在官方 Jazzy Linux 容器验证真实 gRPC→DDS 双相机、PointCloud2、TF/odom；实际 RTAB/Nav2 验收另行记录，不宣称客户实机控制或现场生产验收已通过。


- 新增异构机器人 SDK：版本化机械结构/传感器/动作能力清单、规范三维感知、可信本地插件和 CLI；不同关节名称与单位可通过同一 Runtime 接入，旧 XLeRobot 限制继续保留。
- 新增 Python/Go 双端感知验证及跨语言七步任务测试，保留原始来源/采集时刻，拒绝旧帧、错误坐标、缺失合同和设备配置漂移；World 不再将所有实体标为仿真真值。
- 新增官方 SDK 的 MCP stdio 桥接，统一提供设备/能力/观测/任务/停止工具，创建任务保持待审批；补充适配器开发与 MCP 接入文档。
- 加固通用 Runtime 的工具结果校验与不可变快照；物理执行后异常或非法结果持久急停，感知等待期间发生停止则不再进入动作 handler。MCP 支持显式私有 CA，保留证书和主机名检查。
- 修复三项 MuJoCo 交接回归：按当前几何/持有状态输出 inside/held_by 关系，保留严格起点校验。
- 修复历史签名验收包因前端升级无法重验：验证签名和完整哈希后使用历史资源清单，新候选及基线提升仍严格匹配当前源码。
- 修复本地演示只等待 HTTP 就绪而过早审批任务的问题：先验证 Runtime 与新鲜场景，恢复失败及时退出并清理自身进程；补齐世界测试就绪条件与渲染线程同步。

- 统一“躺营”品牌与明亮中文工作台，分离用户操作和开发诊断；保留三维、简洁、机器人画面、全局地图四种展示。
- 改进中英文自然语言解析：中文机器人编号、礼貌用语、常用别称、同句代词以及独立起点/终点/颜色绑定；完整已知指令优先确定性解析。
- 拒绝已识别的否定、条件及不完整理解，修复任务终点修改丢弃约束的问题；执行前验证指定起点与实际观测关系。
- 修复任务进展同版本不刷新、迟到响应覆盖和完成状态显示；开发预览统一模型/API 来源并保留 WebSocket 同源校验所需 Host。
- 新增可复现的 13 项自然语言仿真评测；记录反向搬运与完成后重新授权尚未实现的边界，不把定向交接当作通用家务能力。
- 完善 Sim2Real 私有接入包、阶段证据检查、锁定驱动兼容、显式使能与本地恢复；新增单主 World 检查点与部署持久卷支持。
- 对齐用户指南、源码地图、API/数据契约、开发预览、验证与实机交付说明。具体实现、证据日期及未完成事项见 [V1 当前状态](docs/production/v1-release-status.md)。

## v0.2.0-rc.2 - 2026-08-24

- 新增 VLA、模仿学习、强化学习和确定性仿真共用的 PolicyManifest/Observation/Inference/ActionChunk 契约，策略只在 Edge 运动前执行，原始动作不进入云端或用户界面。
- 新增 Go HTTP/确定性 Policy Provider、Python 框架无关 sidecar，以及 robot model、calibration、artifact、manifest revision 的 fail-closed 兼容性校验。
- 新增六类策略与执行恢复：观测等待、策略重试、策略阻断、执行对账、安全恢复和安全停止；未知物理终态绝不自动重放。
- 用户端现在以通俗语言展示自然语言理解、任务 revision、每台机器人、工具、控制方法、环境确认和恢复过程，同时保留折叠的专业证据。
- 中文双机器人方块传递已在真实 RoboCasa/MuJoCo、双 XLeRobot、云端 Fleet、双 Edge/Runtime 和受控浏览器中闭环通过；签名 `round4` 证据包含 23/23 项检查和四个唯一策略推理证据。
- 修复 GLTF 内嵌纹理被 CSP 拦截导致模型材质缺失，以及直接操作相机后内部跟随状态与工具栏显示不一致的问题。
- 扩充生产文档，覆盖策略工具对接、sim2real 晋级、异常排查、接口契约、安全边界和发布证据。

## v0.2.0-rc.1 - 2026-08-23

- 新增运行中任务更新：自然语言修改生成不可变 TaskRevision，经用户预览确认后在 Harness 安全点切换；旧 revision、旧命令和重复提交均失败关闭。
- 新增面向普通用户的任务体验轨道，以通俗语言显示系统理解、编号步骤、机器人、工具调用、环境证据、异常恢复和最终结果，专业字段默认折叠。
- 新增完整 RoboCasa WebGL 数字孪生：厨房、两台完整 XLeRobot、权威姿态/关节、路径、标签、资源 custody 与 Canvas 安全降级。
- 新增可移植、签名、单次上传的浏览器验收证据；本次中文双机器人交接和 revision 1→2 更新通过 22/22 项检查。
- 新增生产交付手册，覆盖系统架构、快速上手、全部接口、数据契约、配置安全、异常运维、仿真到实机、测试验收和部署容量。

- 云端成为联网机器人主要产品形态；Local Brain 保留为无网络部署，两者共用 `world.snapshot.v1`、工具目录、Observation Registry 与 Robot Runtime 契约。
- 新增单逻辑红色方块的双 MuJoCo 机器人交接：交接区世界证据、单调资源 fencing、`BLOCK_AVAILABLE/BLOCK_DELIVERED` 领域事件、1 个真实进程断连恢复与 8 个确定性故障边界。
- 新增权威实时 WorldHub 与交互式 3D Console：一次性 WebSocket 票据、游标重放/缺口重同步、左键平移、右键旋转、指针锚定缩放和来源新鲜度/资源归属展示。
- 实机 XLeRobot Runtime 现在在运动前验证 robot/catalog/world/resource/fencing 身份；缺少环境观测或放置验证器时 manipulation fail-closed。

- 新增 Fleet 云端一键 Docker 部署：mysql + redis + fleet-control-plane + nginx（HTTPS 控制台 + 8444 mTLS gRPC 透传），8080 仅 expose 于 Docker 内网、不发布到宿主机。
- 新增 edge-worker：从 Redis Stream 直连或 HTTP 长轮询拉取任务，连接 Robot Runtime，上报状态/事件/遥测。
- 新增机器人公网接入 mTLS gRPC 网关（FleetGateway）：Register/Link 双向流、断线指数退避重连、心跳与设备租约（过期自动离线）、服务器命令下行（急停/取消）。
- 新增云端协调器：意图级多机器人任务图、事件驱动跨机器人刷新与重新投递、意图声明租约超时自动回收。
- 新增多机器人全局地图融合（占用栅格 + 轨迹 + 实体）与云端 Console（登录/设备/任务/遥测/地图）。
- 新增双 MuJoCo 仿真场景：`--robot-id`/`--xml` 参数与世界偏移 r2 场景生成脚本（`scripts/gen_scene_variant.py`）。
- 新增认证边界：操作员 Bearer token（HMAC，24h）、每机器人独立且绑定 `X-Robot-ID` 的设备凭据（`fleet/auth`）、nginx 客户端 IP 白名单（`FLEET_ALLOWED_CIDRS`）。
- 新增实时上帝视角（God View）：Robot Runtime 场景帧经 edge-worker 500ms 周期上行（HTTP base64 / mTLS gRPC 原始字节），`/v1/scene/frames` 提供双机实时画面；`/v1/maps/global` 扩展 held/placements 语义；`/v1/world` 输出机器可读世界状态供 harness agent 编排。
- 仿真可观测性：`--human-speed` 墙钟节流让技能动画以可观看速度执行（默认 0 不影响验收）；世界锁内滚动快照（~25Hz）使观察无阻塞，执行期间画面/物体/held 持续可见。
- Added pure distributed AgentOS brain/isolation design: controlplane.Brain, edge/runtime.Router, per-step RobotID, and event-driven GraphRuntime node refresh while keeping robot runtime unaware of command origin.
- Added Alibaba Cloud Fleet control plane: Go HTTP API, MySQL task repository, Redis cache/stream queue, Docker Compose one-click deployment and deploy-alicloud.sh.

- Added pure distributed AgentOS brain/isolation design: , , per-step , and event-driven  node refresh while keeping robot runtime unaware of command origin.

- Added explicit Agent / Robot Runtime / Middleware / ROS 2 / real-time / hardware boundaries, with executable dependency tests that prevent concrete infrastructure and transport SDKs from entering Agent core packages.
- Added vendor-neutral Middleware ports for task/execution state, bounded queues, events, cache, locks and traces; moved the default WAL SQLite store under `middleware/sqlite` and injected the in-memory queue at the composition root.
- Replaced task-graph-aware robot execution with semantic Runtime commands and capability names; protobuf/gRPC mapping now stays in `edge/robotclient` and the Python Runtime service boundary.
- Decoupled Safety, XLeRobot direct, and ROS 2 backends from generated protobuf types using transport-neutral Runtime models, while keeping high-rate camera/LiDAR/IMU/joint data robot-local.
- Preserved the layered architecture specification, implementation plan, and middleware adapter guide as versioned development design assets; PostgreSQL, Redis, and Kafka remain optional future adapters rather than default dependencies.
- Replaced the hosted control plane with one laptop Local Agent process serving Console/API, LLM orchestration, task execution and SQLite persistence; removed the cloud binary, PostgreSQL store, Compose stack and cloud installer role.
- Simplified laptop-to-Raspberry-Pi operation to direct mTLS gRPC initiated by the laptop, while the Pi keeps only the bounded command/E-stop safety journal.
- Preserved the approved local-first architecture specification and delivery plan as durable design assets linked from the current architecture documentation.
- Added LLM self-orchestration: the planner chooses and orders skills from the registered catalog, with deterministic fallback, self-consistency voting and `/v1/orchestration/metrics` quality scoring.
- Added a user-facing Robot Agent Console: natural-language task creation, live task/audit views, Robot Runtime and sensor/semantic telemetry, a MuJoCo top-down scene renderer and orchestration metrics.
- Added a Local Agent telemetry bridge (`POST /v1/telemetry` / `GET /v1/telemetry`) so simulation and future real XLeRobot sensor state are observable in the same console.
- Added an explicit XLeRobot production go/no-go gate (`robot-agent production-check robot-pi`) requiring providers and recorded 30-trial safety evidence.
- Added a pluggable task Agent: deterministic parser plus optional OpenAI-compatible function calling with deterministic fallback.
- Added the `fetch` tool ("把红色杯子拿过来") and a front `delivery_tray` in MuJoCo for closed-loop fetch simulation.
- Added compound one-sentence task sequences ("先放 A，再把 B 拿过来") with deterministic parsing, multi-tool OpenAI planning, per-subtask skill graphs and resumable execution.
- Expanded MuJoCo to all advertised objects (red/blue/green cups, bottles and blocks), both storage bins and the delivery tray, plus an 18-goal object/destination acceptance matrix.
- Added `make sim2real-check`, `make deploy-robot-pi` and `scripts/robot-pi-quick-deploy.sh` for repeatable simulation acceptance and fast Raspberry Pi direct-edge installation.
- Added a ROS2-free `XLeRobotDirectBackend` and `tangying_robot_gateway.run_direct_edge` so the first real XLeRobot release no longer requires ROS2.
- Added explicit entity, policy and verifier provider hooks that fail closed until real perception and policy are installed.
- Added a ROS2-free Raspberry Pi systemd template and the V1 Agent / Sim2Real contract document.
- Added the `edge/runtime` Robot Runtime boundary: structured `CapabilityInfo`, runtime availability checks before every task, per-command deadline enforcement, cancel and emergency-stop client methods.
- Added low-rate `SemanticState` to observations so Agent code sees activity and safety status instead of raw sensor streams.
- Hardened `SafetySupervisor` with command identity checks, lease bounds, bounded action-chunk key/value validation and controlled cancellation that remains distinct from the E-stop latch.
- Added structured capability descriptors to MuJoCo, XLeRobot direct and ROS 2 backends; ROS 2 read-only skills now stay on the gateway side of the ROS boundary.
- Hardened XLeRobot for physical experiments: configurable `max_relative_target` / action-chunk length, thread-safe fail-closed driver, local stop latch, provider exception mapping, graceful service shutdown, and a no-motion XLeRobot preflight.
- Added the XLeRobot experiment runbook for first physical motion, E-stop drills, provider contracts and post-stop service restart.

## v0.1.0-rc.2 - 2026-08-17

- Added one role-based installer for simulation, cloud, laptop Local Agent, and Raspberry Pi Robot Edge.
- Added the `robot-agent` lifecycle, configuration, diagnosis, pairing, and simulation-demo CLI.
- Added a bounded full-process MuJoCo demo and loopback-safe cloud defaults.
- Added laptop-to-Pi P-256 mTLS pairing with local-only CA custody and explicit trust-root rotation.
- Replaced the prototype Raspberry Pi units with hardened XLeRobot and Robot Edge services, localhost ROS discovery, stable dialout udev aliases, and no-motion preflight.
- Pinned XLeRobot and LeRobot integration, added an explicit interactive calibration tool, and fail closed without the exact calibration file or policy action chunks.
- Added endpoint runbooks for fresh installation, startup, upgrades, recovery, and the no-STM32 XLeRobot topology.

This release candidate has automated simulation evidence only. Stable `v0.1.0` still requires the physical emergency stop, network interruption checks, local perception/policy integration, and 30 hardware trials in `docs/safety-checklist.md`.

## v0.1.0-rc.1 - 2026-08-17

- Extracted a reusable distributed Agent Core from the Tangying video production architecture.
- Added natural-language tabletop pick-and-place intent parsing and manipulation skill graphs.
- Added cloud orchestration, Local Agent execution, Robot Gateway contracts, and operator controls.
- Added MuJoCo simulation, Raspberry Pi ROS 2 packages, Safety Supervisor, and XLeRobot adapter.
- Added contract, restart, safety, simulator, API, and full-process end-to-end tests.

This release candidate has automated simulation evidence only. Stable `v0.1.0` requires the physical emergency stop, network interruption checks, and 30 hardware trials described in `docs/safety-checklist.md`.
