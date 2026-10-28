# Changelog

按软件版本记录核心变化。软件发布与机器人实机现场放行分别验收；实验细节集中在 [实验报告索引](docs/experiments/README.md)。

## Unreleased

- 改善边缘持续运行与模型成本管理：SQLite 按流最新值生成协作视图，长任务审计增量追加，GOAL/RECOVERY 输出预算与可汇总调用用量，重复目标的持久规划经验及当前条件重验证；同步 README、架构和运维资料。实现、兼容与实际验证见[升级记录](docs/development/2026-10-28-edge-scalability.md)。

- 强化边缘执行与教材 3.2.0：修复慢目录读取后的授权竞态、operation 读预算耗尽后无效轮询、身份重配队首堵塞与跨 peer 凭据错误复用；接通可选缓存提示、可缺失的模型用量和失败轨迹，保留取消后的审计写入。重复软件实验、实现边界与教材推导见[本轮记录](docs/development/2026-10-28-edge-brain-reliability.md)。

- 精修工作台视觉呈现：分层表面与环境光、品牌导航、主操作/安全按钮层级、场景空态、任务与统计卡、自定义复选框和轻量动效；全部落在样式与图标层，不改变可见控件结构、接口、轮询或状态语义。见[前端说明](docs/frontend/console-v1.md#视觉与交互规范)。

- 增强异构 ROS 2 边缘 harness、持久通信消费和运维可观察性；未知命令保持关联，遥测/任务库失明不误报恢复。新增三机型重复任务、通信故障和探索验收。见[实现记录](docs/development/2026-10-18-edge-swarm-readiness.md)与[分层实验](docs/experiments/2026-10-18-edge-swarm-readiness.md)。

- 扩展已知地图覆盖与多层地面导航：显式 ROI、可替换规划/传感器、本地预测计划、分层支撑和 Runner 软件闭环，以及独立协调消融。两次 315/315 已知覆盖和 350 次全部 partial 的未知探索分别保存；见[实现](docs/development/2026-10-18-spatial-coverage-upgrade.md)、[实际实验与来源](docs/experiments/2026-10-18-spatial-coverage-upgrade.md)和[教材补充](book/updates/2026-10-18-spatial-coverage-upgrade.md)。

- 增加异构三维探索研究层：有界观察选点与射线收益、复用本体几何路径准入、版本化覆盖账本与未知执行 fence、可替换地图/驱动/报告端口，以及配对消融、指标重算和来源归档。当前为软件闭环，不自动启用 ROS 2 三维探索；范围见[设计记录](docs/development/2026-10-17-heterogeneous-spatial-exploration.md)和[实验协议](docs/experiments/2026-10-17-spatial-exploration-protocol.md)。

- 增强首次未知场景探索：实测带方向 yaw 闭环初始扫描、地图外未知统计、可达/遮挡候选与无增益视点退役，修复足迹重开拒绝区、深度失效累计及换段取消覆盖；扫描证据和停止原因进入地图来源，新增不可覆盖的验收记录器。见[实施记录](docs/development/2026-10-17-exploration-first-scan.md)及[全部实验尝试](docs/experiments/2026-10-17-exploration-first-scan.md)。

- 发布独立书籍 2.2.0，将云端粗目标授权、Local/Worker 共享 Host、边缘规划/诊断/恢复、分层预算与上下文写入正文；同步架构、API、部署和学习资料，新增实验 11/12 并归档 2.1.0 完整来源。引用 2026-10-15 软件与实际 Gazebo 全部尝试，保留失败和证据范围。[变更审校](docs/development/2026-10-16-architecture-book-refresh.md)。

- 增强单机器人长程恢复：统一续跑后的云端失败上报，恢复有完整子账本证明的已确认动作，增加完成条件瞬态读取的持久有界恢复；未知效果、证据错配、取消、期限和预算仍拒绝继续。新增连续 32–128 动作 campaign、云边丢包/重建矩阵与研究证据采集器；见[实施记录](docs/development/2026-10-15-single-robot-robustness.md)。

- 新增[分层目标委托](docs/architecture/hierarchical-edge-objectives.md)：云端批准目标与工具/参数/效果包络，Local 与 Worker 共用 Edge Host 生成子计划、诊断并在安全范围内重规划；新增目标 API、能力目录摘要、持久检查点及云端子计划/完成证据核验。11 站合同任务完成一次拒绝后的剩余规划，未知写结果禁止重放；[实施记录](docs/development/2026-10-15-hierarchical-edge-objectives.md)与[测试报告](docs/experiments/2026-10-15-hierarchical-edge-objectives.md)区分软件证据和未做的实机验收。

- 重整总 README 的新手入口：增加单机/云边框架图、完整顶层目录职责、源码阅读主线和按角色的学习路线，首次启动只运行一套家庭仿真；清晰区分业务源码、生成代码与本地产物。[更新记录](docs/development/2026-10-14-readme-onboarding.md)。

- 发布独立书籍 2.1.0，补齐点云语义、持久边缘合同、设备数据交换及地形/三维导航；新增两组软件实验并保留 2.0.0 全源恢复。修复不同出版身份覆盖已有输出目录，更新当前模块/部署/数据指南；清理 27 个明确临时文件，原始实验和失败证据保留。[维护与出版记录](docs/development/2026-10-14-book-and-maintenance-review.md)。

- 新增[统一 2D / 地形 / 3D 导航](docs/development/spatial-navigation.md)：保留平面 SLAM/Nav2，接入外部 SE3 注册测量、三态体素地图包、带世界版本和六维质量的定位，新增空中体素与地面支撑约束路径规划。ROS 2 控制器通过部署摘要和路径 ID 握手，完成后检查完整位姿；未知空间、过期定位与版本变化拒绝移动。补充自然语言末尾返回实测起点、二维合图坐标约束及仿真任务目录隔离。实施与证据见[计划记录](docs/development/2026-10-14-spatial-navigation-plan.md)和[实验报告](docs/experiments/2026-10-14-spatial-navigation.md)，不代表新增成熟 SLAM 估计器或具体设备飞控/步态认证。

- 升级[边缘 Agent 实机测试候选版](docs/operations/edge-field-testing.md)：跨重启固定完整机器人执行契约，Worker 持久领取通知并等待有界取消和遥测退出；补充无人车、无人机与足式运动/停止合同、ROS 2 设备接口，以及独立持久数据通信进程。云端与对等 Agent 的数据不获得动作权限；既有上下文、审批和未知结果对账保持。实现与实际测试见[变更记录](docs/development/2026-10-13-edge-field-candidate.md)，具体型号和现场物理验收另行完成。

- 固化[云边与 SLAM 模块边界](docs/architecture/modular-extensibility.md)：共享任务/遥测值与原子存储端口移入 core，共享交接校验独立于 Fleet，Redis 取任务在入口装配；建图工作流使用可校验的 SLAM Provider，保留原算法与地图读取兼容。新增依赖守卫及非 Dense 提供者替换验证，拒绝 Fleet 遥测中的设备身份错配；修复续建地图的重复坐标变换、轨迹/时间错配与继承点错误清障，未知历史时间明确保留。扩展方式、限制和测试见[实施记录](docs/development/2026-10-13-modular-extensibility.md)，不等同新增真机、任意 SLAM 或异构机群任务认证。

- 按[系统学习、开发升级、部署运维、研究论文](docs/README.md)重整文档入口，增加书籍章节与系统增量对应、全量分类目录及维护规则；修正开发入门、建图兼容 API、Local Policy、标定界面和默认 Gazebo 资源的旧说明。历史实验与书籍正文保留原身份，详见[整理记录](docs/development/2026-10-13-documentation-map.md)。

- 接通[点云语义到长程执行](docs/architecture/semantic-execution-protocol.md)：自然语言查询证据冻结为有序 `semanticRoute`，边缘逐段规划并持久化候选，Gazebo 通过签名绑定复用 Runtime/Nav2 对齐、导航和到位验证；新增真实内部感知 `semantic.observe`，区分到达观察位与唯一对象确认。地图/语义/标定/锚定变换变化使旧绑定失效；恢复保持原检查点，未完成绑定在 Runtime 重启后需显式对账重规划；已完成历史站点须完整核验后跳过。异构接口保持能力声明，尚未据此认证真实机器人导航。修复能力目录重复占用上下文与候选计算引起定位过时，新增[实际五站任务、故障恢复和完整失败记录](docs/experiments/2026-10-13-semantic-execution.md)。

- 新增绑定 SLAM 点云的[三维语义地图](docs/guides/semantic-point-cloud.md)：Console 三维选区标注、实测点集合与包含关系、有界查询、过期/重叠检测、独立语义版本与并发 CAS、观察位置候选。保持几何地图不可变，保留地图/标定/来源身份；新接口不直接授权导航或抓取。验证记录区分单测、历史 Gazebo 点云回放与尚未进行的新物理任务。

- 修复家庭抓放跨子句再次指名同一待拿物体造成的重复搬运；冲突引用要求澄清，明确多物体/多次操作保持。新增批准前及派发后的搬运次数验收、无批准的拒绝请求采集。[2026-10-13实验](docs/experiments/2026-10-13-system-evaluation.md)保留长程自报成功但验收失败及短程修复复验，不宣称修复后完整长程或实机已通过。

- 数字版2.0.0将书稿重整为四篇/19章/9附录的教材与工程参考版，统一概念/案例/实现/57题，新增16周课程、工程手册、习题指导和篇/节导航；保留1.2.0全源及原生成器恢复。见[书籍入口](book/README.md)与[编辑审校](docs/development/2026-10-11-book-textbook-editorial-review.md)。

- 补齐 v0.7.0 的[长程协作实现导读](docs/architecture/long-horizon-collaboration.md)，说明新增功能、Agent 事件/持久账本、云边回执、上下文一致性、异构接入与多机交接；修正旧运行时与 Fleet 文档对发布权限和租约过期的表述。仅更新文档，不新增实机或异构多机验收结论。

## v0.7.0 - 2026-10-10

正式发布见 [v0.7.0 发布记录](docs/releases/v0.7.0.md)。包含下列候选阶段变更及长程单任务、多 Agent 诊断恢复、上下文/证据管理和云边交互协议；完整结论见[长程验收报告](docs/experiments/2026-10-10-long-horizon-protocol.md)。

- 补齐单机 Local HTTP 动作策略入口，约束设备/标定/观测与派发有效期，保留策略诊断和语义恢复绑定；修正 ROS 未实现能力误报、持久 journal 与独占锁，收紧真机预检和现场证据绑定。见[真机集成升级记录](docs/development/2026-10-05-physical-robot-integration.md)。本轮是软件验证，尚无真实设备长程完成证据。

- 接通能力任务的实时 Ops/Recovery 事件链，修复跨任务故障归属、同工具不同步骤的调查去重与失败证据缺失。只读瞬时 RPC 故障经独立诊断复验后最多重试一次并继续原计划，预算持久化，未知物理结果不自动重试。新增[长程 Gazebo 验收与更新记录](docs/development/2026-10-05-long-horizon-recovery.md)、故障代理和严格证据验收脚本；修复 SQLite 相对数据目录启动。

- 工作台 UX 收紧：深蓝文字导航、准备与异常摘要、桌面现场/任务并排、窄屏任务优先；修复问题筛选文字对比度与旧 Gazebo 示例。Local 历史增加内容/编号搜索、同页回放、编号复制与关联诊断；原始事件按需展开，历史可读与现场新鲜度分开。保留[规范](docs/development/2026-09-27-console-ux-spec.md)和[审核记录](docs/development/2026-09-27-console-ux-review.md)，同步用户说明及 book 增量入口。

- Gazebo 完整移动 SLAM 新建 232 帧地图，保存地图语义成为命名目标来源；增加房间/工作区/无名几何区域与 `semantic.locations` / `semantic.resolve`，校验实测足迹净空、精确地图修订和坐标逆变换。功能名称使用显式标注，未知空间不开放。
- 支持单目标自然语言地点与工作区导航；修复 Grounding 后中文别名丢失、单目标 LLM 预摆位缺少航向及物体记忆的跨帧时间/位姿混用。三个 LLM 任务、五次实际导航和 48 张原始图像严格校验通过，任务期间无复位或物理重试。
- 收紧家庭语义导航的模型数值目标，必须匹配 Grounding 已认证路线位姿；任意坐标/航向在物理执行前拒绝。最终版另完成两个 LLM 工作区往返任务、9 个确认步骤与 22 张图像校验；原巡检证据保持原版本身份。
- 保留 [SLAM 语义导航规范](docs/development/2026-09-27-gazebo-slam-semantic-navigation-spec.md)、[操作指南](docs/guides/slam-semantic-navigation.md)及[独立实测报告](docs/experiments/2026-09-27-gazebo-slam-semantic-navigation.md)，同步 README、文档和 book 增量入口。

- Gazebo 家庭部署统一为与 MuJoCo 同源的 XLeRobot CAD、四房间/走廊与装修家具；退役旧差速模型及彩色工位部署入口，异构回归夹具与历史证据保留。
- 完成同一物理 episode 的 LLM 巡检、厨房检查往返、杯子抓放及返回：4 个任务、34 个确认步骤、7 段导航、76 张摘要验证原图，无物理重试或任务内重置。[升级规范](docs/development/2026-09-26-gazebo-xlerobot-home-spec.md)、[逐轮报告](docs/experiments/2026-09-26-gazebo-xlerobot-home-closure.md)和[验收清单](docs/experiments/2026-09-27-gazebo-xlerobot-home-acceptance.json)保留版本与失败回溯。
- 修复捕获时位姿/编码器配对、相机时钟和命令后置证据、CAD 自身遮挡过滤、五轴 IK 与独立夹爪、固定指 TCP、持物观察与载荷中心放置；保持碰撞、关节到位与盘内稳定判据。
- 原生家庭导航采用明确的头部传感器 profile，数据库按资源/profile 隔离；保存地图可由声明绝对锚定的驱动注入绝对位姿因子，轮式里程计默认不使用该因子。
- 公共 Runtime journal 升级摘要绑定事件文件，保留 v1 迁移、原字节重放和未知结果停止锁存；Agent 增加只读能力恢复等待与标准技能参数校验，修复异常任务关联、活动执行误判及毫秒单位。
- 同步当前部署/接入指南与书稿增量入口；真实 Orin NX、GPU 服务器、轮胎牵引、实体急停与实际夹具仍需独立认证。

### 2026-09-22 候选集成

详见 [v0.7.0 发布记录](docs/releases/v0.7.0.md)。

- 默认 Gazebo Harmonic 8.15.0 / ROS 2 Jazzy / ros_gz 1.0.24，接入原生简化吸附、彩色工位感知与双物体 Agent 任务；增加四场景独立物理验收和版本证据，边界见[实验报告](docs/experiments/2026-09-22-gazebo-stable-integration.md)。
- 修复 Gazebo 收臂事务、启动接地高度、相机遮挡与同色背景干扰、快速导航后的观测时序及工具健康依赖；OSMesa 关闭高成本阴影/反射并验证深度几何保持一致。
- 修复建图校正导致的里程计导航目标漂移，增加 Gazebo 差速控制器及取消竞争处理；修正模型工具名映射与思考模式工具选择兼容性。
- 增加 Gazebo 运行时、RGB-D/位姿桥接、注册建图服务、机械臂几何生成与部署入口，复用既有机器人契约。
- 改善自然语言发起建图、勘测/探索、地图库存与选择、语义工作区、导航后可操作性验证，以及标定求解和引导界面。
- 增加可选物理接地验证：动作合约、三值结论、证据引用、失败分类与禁止盲重试；保留工具回执和物理事实的区别。
- 增加 Agent 决策上下文、阶段字段、确定性表达、模型/阶段适用性限制及可复现实验脚本；默认保持 legacy 表达，研究策略按需开启。
- 增加系统级离线 Eval：能力与工具注册、结果契约、分项指标、严格配对比较、统计门禁、可靠性与覆盖报告。
- 汇总自 v0.6.0 以来的监督/恢复、权限与审批范围、任务回放、问题列表和控制台轮询修复；恢复动作仍接受运行时权限与物理验证约束。
- 修复全量静态检查发现的问题，包括验收脚本遗漏 sys 导入、无效测试断言和最终停止命令失败时缺少诊断。
- 同步软件/ROS 版本，补齐评测开发依赖及 CI 的协议生成与文档检查；实验原始数据、缓存和本地备份默认不入库。

## v0.6.0 - 2026-09-13

- 装修家庭场景、双 RGB-D 闭环、注册标定和移动建图、地图关键帧诊断及自然语言移动抓放。
- 版本与受限仿真验收证据见 [v0.6.0 发布记录](docs/releases/v0.6.0.md)；更早的过程记录可从 Git 历史及对应开发文档查询。

## v0.5.0 - 2026-09-11

- 把“任务全过程回放”补成任何时候都能完整复盘；详见[发布记录](docs/releases/v0.5.0.md)。
- 补齐“任何时候都能复盘”的入口：任务记录此前只列出最近 50 条且不显示编号，更早的任务（例如需要追查的历史失败）在界面上完全无法到达。现在每条记录都显示可复制的任务编号；新增“按任务编号回放”输入框，可直接打开列表窗口之外的任意任务，编号不存在时明确提示“找不到任务编号”以区别于“服务暂时无法读取”；新增“只看”筛选（全部／失败或中断／已取消／成功／进行中）；超出 50 条由“显示更多”继续展开。回放标题也列出任务编号，便于把截图对回日志。
- 回放的空缺处改为说明原因而不是留白：执行链路为空时按任务状态区分“任务尚未开始执行”“在调用任何工具之前被取消”“失败发生在任务分解或目标绑定阶段”等结论（此前对等待批准的任务也会报成分解失败，把排查引向不存在的缺陷）；没有任务说明记录时说明是记录缺失，不再显示 `—`。
- 终态任务的说明记录返回 404 后不再每轮轮询重复请求同一个不会成功的地址；点“重新读取”仍可强制重新请求。
- 修复浏览器验收脚本的三处误判：单击滚动到底部不会触发视口之外的 `loading="lazy"` 缩略图，脚本会因健康图片超时而失败（改为逐步滚动再等待）；用 `REPLAY_TASK` 指定任务时按列表可见文本查找，窗口之外的任务永远选不中（改为通过编号查找框打开，并断言回放标题就是该编号）；把“必须有工具步骤”当作通用契约，导致分解阶段就失败、确实没有任何工具调用的任务被判失败（改为要求面板解释这种空缺，需要强制断言时用 `REPLAY_EXPECT_STEPS=1`）。
- 修复 `page.waitForFunction(fn, { timeout })` 的误用：第二个参数是传给页面函数的实参而不是选项对象，两处等待因此一直使用 30 秒默认值，超时配置从未生效。

## v0.4.0 - 2026-09-11

- 新增标准机器人工具层（27 个工具，25 个默认提供给 LLM）与工作台“任务全过程回放”；详见[发布记录](docs/releases/v0.4.0.md)与[工具层 ADR](docs/superpowers/specs/2026-09-11-standard-robot-tool-layer-adr.md)。

- 新增工作台“任务全过程回放”：把一次自然语言任务的事件、任务说明、历史观测与恢复状态对齐成一份可读档案。按发生顺序列出每个工具步骤（中文名、原始工具名、状态、耗时、派发次数、是否改变世界、调用参数、命令编号），在写步骤下直接给出确认它的那次采集（同帧彩色与深度、采集时间、相对命令的时间偏移、来源、采集编号、字节数与 RGB SHA-256）。面板同时做一致性检查并列出不一致项：写工具无证据、证据早于命令、引用的采集不在历史中、采集登记的步骤与引用步骤不符、成功但写步骤未确认、任务结束却无任何工具活动、存在未知终态需要现场核对。
- 回放实现分为纯逻辑（`web/task_trace.js` 的 `buildTaskTrace` 对齐与判定）与渲染（DOM 构造 `renderTaskTraceNodes`、供测试断言的 `renderTaskTrace`），按显示内容变化才重建，任务运行时每 250 ms 节流更新、进入终态立即更新；只读，不创建、批准或取消任务。
- 新增 `scripts/check-task-replay.cjs` 浏览器验收：断言面板可见、模块已发布、步骤与事件已列出、每张证据缩略图真实解码、页面无 JS 错误。
- 把文档站点的内部链接检查从 `docs/production` 扩展到全部现行文档（历史归档 `docs/superpowers` 除外），立即发现并修正了一处指向不存在页面的链接。
- 修复 `test_web` 覆盖不到的接线缺陷：新脚本未加入 `web/embed.go` 的 `//go:embed` 列表会被内嵌控制台漏掉；`task_trace.js` 曾含 ESM `export`，在严格 CSP 下作为 classic script 解析失败并静默禁用整个模块。

## v0.3.0 - 2026-09-11

- 新增 `core/closedloop`：`Track` 状态机（派发 → 成功待证据 → 已验证 / 重试 / 升级）、失败分类（瞬时、感知、规划、权限、资源、参数、未知终态、致命）、有界重试与确定性退避，以及 `Gate` 完成门禁。未知物理终态永不自动重试，只能进入对账。
- 新增 `mutates_world` 契约字段，贯穿 `core/skills`、`skills/manipulation`、`robot/gateway`、`robot.proto`、MuJoCo 运行时与 Go 客户端。写工具声明与只读工具分离，`emergency_stop` 作为物理但非场景写的例外有显式说明。
- Go Agent 在通用执行路径上强制闭环：任何写工具返回成功后，必须附上命令派发之后采集、带观测标识的新鲜证据，否则该步骤保持 `STARTED`、任务进入可恢复失败，并拒绝以成功文案收尾。此前的后置验证只覆盖参考清单中的三个 `verify_*` 步骤，适配器新声明的写工具可以只凭返回码记为完成。
- 时间新鲜度按运行时实际精度判定：派发时刻截断到毫秒，证据与派发落在同一毫秒视为命令后，前一个毫秒仍被拒绝；判据由 `DispatchPrecision` 单点定义，`Gate` 与 `Track` 共用。
- 新增回归测试：缺证据的写必须失败关闭且步骤留在 `STARTED`、只读工具不受门禁影响、adapter 通过能力声明新增的写工具同样受约束、未知终态不可重试、退避与重试上限可断言。`sim/mujoco/tests/test_world_mutation_contract.py` 校验运行时能力声明与规范工具集一致。
- 修复相机路径从未发布 `verification_confidence`：判定写在服务实例上，而对外状态来自世界对象，控制台与证据读取端在相机路径上一直看到 0.0，确定性路径正常；现在写入 world 并在场景捕获时发布。
- 旧真值调试运行时不为观测提供标识，因此其写工具稳定失败关闭。`scripts/demo.sh` 与 `tests/e2e` 的物理任务改在相机工作台（`--perception rgbd --scene tabletop`）运行，`scripts/demo.sh` 同时显式使用 `simulation` 安全 profile。真值模式保留只读调试用途。
- 清理无引用实现：删除 `core/toolcatalog` 与 `controlplane`（全仓无生产引用，仅历史计划文档提到）；修正 `docs/distributed-agentos.md` 中指向已删除包的边界描述。浏览器回归输出 `artifacts/ui-v1/` 改为忽略目录并在文档中说明重新采集方式。
- 决策记录见[闭环与语义升级 ADR](docs/superpowers/specs/2026-09-10-closed-loop-semantic-upgrade-adr.md)，其中说明语义地图与主动感知搜索为何不在本版实现，以及后续接入点。

## v0.2.0 - 2026-09-09

- 新增四房间家庭 MuJoCo 场景（客厅、走廊、厨房、卧室、卫生间），家庭路线自然语言解析、逐房间导航检查点和基于底盘 RGB-D/位姿的 `verify_arrival` 证据；`--scene home` 已贯通仿真、Compose、RTAB-Map/Nav2 launch，并使用独立家庭地图路径。家庭路线与实机放行边界见[家庭场景指南](docs/guides/home-scene-operations.md)。
- 降低双机器人 Fleet 调试预览的重复阴影绘制成本：保留主光源投影和全部照明，补光不再重复投影复杂网格。图像尺寸、原始观测时间及 1 秒新鲜度门槛保持不变；单机器人 RGB-D 和共享模型资源不受该工厂配置影响。
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
- `make setup` 按前端锁文件安装依赖，修复干净工作区缺少 Three.js 而无法运行前端测试的问题。
- 修复 Linux / macOS 前台会话关闭后的进程清理：同一进程已退出但尚未回收时清除旧记录，继续拒绝向身份不匹配的活进程发信号；覆盖 HUP、启动中断与清理期间的再次中断。
- 补齐 ROS CI 的独立协议依赖；真实相机几何测试先丢弃冷启动渲染，再重新采集，原有观测新鲜度检查保持不变。

## 未发布 · V1 集成候选

以下保留原“未发布”升级记录，相关源码改动纳入 v0.2.0；当时的测试数字与历史签名包不自动代表本次发布。历史 rc 记录保持原始版本，V1 范围与软件包版本分别管理。

- 修复固定工位入口可能启动错误世界：`make rgbd-start` / `rgbd-restart` 显式声明 `--scene tabletop`；直接调用 `scripts/sim-stack.sh` 而省略 `--scene` 时沿用记录场景会打印 `reusing recorded scene ...` 及切换命令，`status` 现在报告实际运行场景（运行时观测为准，未出帧时标注来自记录配置）。此前家庭路线后重开栈会让固定工位任务在绑定阶段失败而看不出原因。
- 改善绑定失败诊断：`grounding absent`（无匹配）与 `grounding ambiguous`（多个候选）分开报告，消息附带机器人、adapter、观测数量、可见实体列表和场景切换命令，现场从“场景不对”和“相机/识别不对”中直接分辨；绑定失败前不产生物理动作或证据。
- 自然语言接受指示代词终点：“右边那个盒子”“这个箱子”等说法解析为同一已配置容器，不改变容器集合；无方位的指示代词仍不做左右假设。
- 修复 `scripts/sim-stack.sh status` 只报告进程健康、不报告场景的问题，并补充对应回归测试与[任务找不到物体](docs/install/troubleshooting.md)排查步骤。

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
