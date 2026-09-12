# Changelog

软件版本、当轮任务与验证结果见 [v0.5.0 发布记录](docs/releases/v0.5.0.md)。当前交付主线是**单机器人在四房间家庭场景完成自然语言任务**；软件发布与客户实机的现场放行分别验收。

## Unreleased

- **intent 层支持多物体自然语言任务**：请求现在解析成**意图序列**而不是只取第一个物体。实测 `从客厅出发，去厨房拿红色杯子放进蓝色收纳盒，再拿蓝色杯子放进蓝色收纳盒，然后回到客厅` → **2 个 transfer，依次 red → blue**，且**路线与返航只挂在第一个 transfer 上**（否则会为每个物体重复规划同一段路程）。
- 支持两种表达：**分句式**（`…，再拿…`）与**并列式**（`拿红色杯子和蓝色杯子放进蓝色收纳盒`，共用一个目标）。并列式需要新的 `homeObjectList` 正则——第二个物体前没有动词，允许 `和/、/及/与/还有` 代替；单物体语法仍要求显式动词。
- **物体与目标可以分处不同子句**（`拿红色杯子，放进蓝色收纳盒`）：物体先挂起，等某个子句给出目标再配对。
- 词表补齐：**`黄色`** 与 **`盘子/碟子`、`碗`** 加入物体正则与类别映射，黄盘现在可以被自然语言指代。
- 未配对的目标位置（有一条"放进…"但前面没有物体）现在会**要求澄清**而不是被静默丢弃——与前一轮修掉的静默截断是同一类问题。
- 回归：单物体家居闭环验收仍通过（`make home-accept` exit 0），`go test ./...` 全绿。

- **多物体自然语言任务实测：跑不通，且有两种不同的失败方式**（细节与证据记入[家居场景扩充方案](docs/development/home-scene-expansion-plan.md) 3.6 节）。场景侧已准备好 4 个可观测物体，**缺的是语言层**。
- **最危险的一种：指令被静默截断。** 请求"…拿红色杯子放进蓝色收纳盒，**再拿蓝色杯子放进蓝色收纳盒**，然后回到客厅"——**任务报告成功，但只搬了红杯子**：确认步骤与单物体流程完全一致，产出中只有 `object_id: red-cup`，没有任何第二次 pick/place。用户要求两件、系统做了一件还报成功，**比直接失败更糟，因为没有人会去检查**。
- 另一种：`把红色杯子和蓝色杯子都放进…` 使 `intent` 退化为 `action: home_route`（`object/source/destination` 的 category 全为空），随后以 `NAV_STEP_LIMIT: home route has no movement segments` 失败。根因是 `homeObjectAction` 正则**要求每个子句都有 拿/取/抓/拾 动词**，而"把…和…都…"以"把"开头。
- 三条可执行结论：**颜色词表已含 蓝色/绿色**（缺的不是颜色，蓝杯绿杯可被命名）；每个子句必须能独立解析，并列结构不行；**缺 黄色 与 盘子/碟子** 词，黄盘无法被指代。修法（支持并列或**明确澄清**、意图数少于子句数时必须澄清、补词表）已写入文档，**在 intent 层修好前不应对外声称支持多物体任务**。
- 回归确认：加入 4 个物体后，原单物体家居闭环验收**仍然通过**（`make home-accept` exit 0，红杯稳定 `inside:kitchen-bin`）。

- 家居任务场景扩到 **4 个可操作物体**：红杯 / 蓝杯 / 绿杯 / 黄盘，加蓝收纳盒。实测观测集与目录**完全一致**（`all advertised observed: True`），并新增黄色掩码（与橙色靠 `g` 相对 `r` 的比例分开，否则饱和黄会被朴素橙色判据误判）。
- **又抓到一个静默失败模式并加进校验：物体不能摆在收纳盒上方。** 收纳盒占据台面很大一块（x∈[1.83,2.47]、y∈[3.42,3.94]），我最初把绿杯/橙碗/黄盘都放在它上面——它们**掉进盒子里**，落到支撑面门控之下，于是**永远不被感知**。三个物体里绿杯侥幸仍被检出，蓝杯反而因此消失，症状看起来像感知 bug。现在 `objects_over_the_bin()` 在模型校验时直接拒绝这种摆放。
- 橙碗**主动移除**：它的实测直径 0.17 m 超出当时的尺寸带，放宽到 0.22 m 后仍检不出，说明橙色掩码在**渲染光照下**的实际像素与材质 rgba 假设不符（黄盘同样饱和却能检出，所以不是尺寸问题）。**我选择移除而不是留着**——向 Agent 宣告一个它找不到的物体，比少一个物体更糟；`PERCEPTION_PENDING` 机制仍在，供下次记录已知缺口时使用。
- 顺带修正上一次遗漏的一致性：模型校验现在遍历整个目录，断言每个物体都有 body 与 free joint（此前只检查红杯）。

- 家居任务场景扩到 **3 个可操作物体**（红杯 / 蓝杯 / 绿杯，外加蓝收纳盒），**全部可被 RGB-D 感知真实观测到**（实测观测集：`blue-cup`、`green-cup`、`kitchen-bin`、`red-cup`）。
- **修掉上一轮埋下的一个真问题**：上一轮加了蓝杯，它在物理场景与目录里都存在，但**感知根本不会报告它**——蓝掩码当时只用于识别大收纳盒。也就是说标称"2 个物体"，实际只有 1 个对 Agent 可用。这类"目录承诺了、感知交付不了"的缺口比物体缺失更糟：Agent 会被告知物体存在然后找不到它，故障看起来像感知 bug。
- 修法是给感知加**通用彩色小型物体检测**（蓝/绿/橙三个掩码 + 与红杯同一套"支撑面之上、紧凑尺寸带"的测量判据），并新增 `PERCEPTION_PENDING` 声明：**目录里已登记但感知尚不支持的物体必须显式列出**，且有一条测试遍历整个目录断言"要么被观测到、要么在待办清单里"。缺口因此无法再被静默引入；本轮把该清单清空到 `()`。
- 蓝色同时被收纳盒和小物体使用，靠尺寸带区分：收纳盒要求 `x 跨度 > 0.05 且 y 跨度 > 0.15`，杯级物体要求跨度在 `0.025–0.16` 之间，两者不会互相误判。

- 家居任务场景从 **1 个可操作物体扩到 2 个**（红杯 + 蓝杯），台面从 1.5×0.5 m 加宽到 **1.9×0.64 m**（放得下多物体的前提）。关键结构改动：**物体位置、目录、构建、运行时摆放现在读同一份表**（`HOME_TASK_OBJECTS` / `HOME_TASK_OBJECT_PLACEMENTS` / `HOME_TASK_WORK_VOLUME`），因此不会再出现"场景里有但目录里没登记"（或反过来）的漂移——上一次尝试正是栽在这里。
- 新增测试断言每个物体**都落在感知工作体积内**、且**彼此净空 > 5 cm**。这两种失败在运行时都是静默的：重叠的物体开局就在碰撞，而工作体积之外的物体**永远不会被感知**，读起来像感知 bug 而不是摆放错误。
- 上一轮那个"感知全瞎"的回归**定位为分步引入时的耦合，而非台面尺寸或单个物体**：实测「只加宽台面」通过、「加宽台面 + 增加 1 个物体」也通过。因此这次改为**增量引入**，8 项家居测试全绿。
- 目录测试按约定**重新指向新决策而不是放宽**：现在断言两个物体都在目录里、都有 free joint，并新增摆放校验。

- 新增[家居场景扩充实施方案](docs/development/home-scene-expansion-plan.md)。摸清现状后的关键事实：5 房间家居的**任务夹具不在 XML 里**，而是由 `rgbd_navigation.py::_extend_home_task_spec()` 在运行时构建（任务台、蓝色收纳盒、红杯子），**可操作物体只有 1 个**；感知是**颜色分割**（红/蓝两个掩码）限制在一个固定的厨房工作体积内，且**刻意不读仿真真值**。
- 方案给出四处必须同步的改动（场景构建 / 感知颜色表 / `HOME_TASK_OBJECTS` 目录 / 运行时摆放），并指出两个容易漏掉的点：现有台面只有 1.5m×0.5m，摆 15–20 个物体会互相穿透需要加长或加层；感知扩充后**必须加"干扰物零检出"用例**——灰色/棕色物体不应产生任何观测，这证明感知不是"看见什么就报什么"。
- **关于 RoboCasa365**：它确实 vendor 在 `datasets/robocasa/`，但 `robosuite` 与 `torch` 均未安装，且它使用自己的 env 栈、不跑我们的 runtime，因此**不带**我们的感知契约、工具层、安全监督、证据链与标定关联。方案建议把"能否装上并加载一个场景"作为独立可行性验证，不与家居扩充混做。

- 性能验收阻塞点**已定位到可执行结论**：本仿真配置下三维场景不激活，控制台**按设计回退到语义 Canvas**（实测 `#fleet-godview-canvas` 可见、`#fleet-godview-webgl` 隐藏、状态 `VISUAL LOADING`、`WORLD CONNECTING`）。排查中排除了两个误导项：控制台那条"图像加载失败"**其实是页面 favicon**（无害），而 `/assets/scenes/robocasa-handoff-v1/manifest.json` **返回 200**（场景资产在服务）。结论：既非资产缺失，也非地图图层引入——它不点「加载点云」也会出现，且与地图产物/路由/解码无交集。解除条件已写明：让三维场景进入 `LIVE`。

- 定位了性能验收的阻塞点：控制台打开后**不做任何操作**等待 8 秒，三维场景始终停在 `VISUAL LOADING / 正在校验本地场景资产`，同时控制台报出一条**图像加载失败**（`data:image/svg+xml,…` 资产）。判定：**这是既有问题，与地图图层无关**——不点「加载点云」也会出现；失败发生在 `registry.load()` 的场景资产校验路径上，`createFleetWorldRenderer` 既未走到 `showFleetWorldWebGL(true)` 也未抛到 `DEGRADED`，而这条路径与地图产物、路由、点云解码没有任何交集。日志已附在运维文档里。

- 性能验收（P1 第 5 步）**未完成，且当前环境测不了**，原因已查明并记录。尝试测量时得到过 60 FPS，但**该数字作废**：三维画布处于 `hidden`，页面状态为 `VISUAL LOADING — 正在校验本地场景资产` / `WORLD CONNECTING`，`fleetVisualReady` 始终为 false，**点云根本没有被绘制**——那 60 FPS 是空闲页面的帧率。不做这个区分就会把"页面空转"当成"百万点流畅"。
- 本轮真实测到的：控制台 `DOMContentLoaded` **45 ms**；100 万点地图在浏览器中**3 层共 666,181 点同时驻留且无报错**；LOD 0 构造成 `THREE.Points`（`position.count = 1011`、`itemSize = 3`、`color.normalized = true`）。这些是数据链路与几何构建的实测，**不是渲染帧率**。
- 运维文档已把"实测 / 未测 / 为何测不了"三者分开写明，并给出取得有效帧率结论的前提条件。

- 新增 `scripts/build_map.py`（`--synthetic N` 造压测地图 / `--database` 读真实 RTAB-Map 库），以及[稠密地图运维文档](docs/development/dense-map-operations.md)：构建、`TANGYING_MAP_ROOT` 部署、接口表、依赖、已知限制与排查。
- 新增 `survey_health()` 与 2 项测试：识别"这不是一次干净的建图"。实测那份 208MB 库**包含 2 张地图、约 49.9 小时跨度的多次会话、1237 个节点中 1235 个已被删除（weight < 0，仅 2 个有效）**——这正是位姿全部相同的根因。`build_map.py` 对此**拒绝导出**（退出码 2），除非显式 `--allow-unhealthy`。
- 文档中如实标注**性能指标未测**：需求里的"首屏 < 2s""百万点 > 30 FPS"**没有任何实测数据支撑**；已测的只是最粗层 1,018 点与构建耗时 4 秒，这不等于渲染帧率。

- 新增**真实 RTAB-Map 数据库导出适配器** `rtabmap_export.py`（10 项测试，其中 3 项直接跑在真实数据库上）。用导航栈 Docker 卷里那份**真实 208MB 测绘库**（仿真家居场景，1237 个节点）验证，而不是照 schema 猜测。实测发现并据此实现：
  - `Node.pose` 是 48 字节 = 12 个 float32（3×4 变换），读轨迹不需要任何库；
  - `Data.depth` 是**用 PNG 包着原始 float32 米制深度缓冲**，不是图像。必须完整 PNG 解码后把像素字节取回来；只 inflate IDAT 会拿到既非滤波前也非滤波后的字节，**当 float32 解释会得到"看起来像深度但不是"的数字**——这个错误我先犯了，靠真实数据才发现；
  - `Data.image` 是 JPEG；颜色必须**用与点相同的有效性掩码采样**，否则第一个无效像素之后所有颜色都会错位；
  - `Data.scan` 在 RGB-D 模式下为空；`Admin.opt_cloud` 为 **NULL**（RTAB-Map 只在配置开启时才持久化拼装好的点云），所以稠密地图必须由深度帧反投影重建——实测重建出 **17,927,266 点**。
- **最重要的发现**：这份真实库的 **1237 个节点只有 1 个不同的 `Node.pose` 值**。也就是说它**没有轨迹**——在这种库上反投影，会把 1237 次扫描全部堆在同一个位置，产出一份**稠密、看起来合理、但完全错误**的点云。因此 `require_distinct_poses()` 让这种情况**直接报错而不是给出点云**：没有任何下游环节能分辨那种输出是错的。有测试固定这个行为（含"集合推导式收集的是生成器对象、守卫永不触发"这个具体陷阱）。
- 相机内参不从 `Data.calibration` 取（那是 RTAB-Map 的内部序列化），而是用机器人已有的 `robot.calibration.v1` 文档——这正是当初设计标定修订号关联的用途。

- 稠密地图 P1 第四步（部分）：新增 `web/map_cloud.js`（16 项测试）与“稠密地图”面板，以及 `GET /v1/maps/{id}/cloud?lod=N` 分层路由（1 项测试）。**分层加载的完整策略已实现并测试**：分块解码、按相机距离选层、常驻层数上限与淘汰、失败时保留粗层。
- 分块解码**严格校验**：magic、版本、以及"声明点数 × 每点字节数是否等于实际字节数"。半截分块被解码成几何体看起来就像一张地图，操作者无从分辨——有测试专门断言截断分块抛错而不是被画出来。颜色缺失时返回 `null` 而不是全零，渲染端才能区分"没有颜色"和"黑色"。
- 两条加载策略写进测试：**最粗一层永不被淘汰**（细化失败时画面不能变空，这正是保留第 0 层的理由）；按距离选层必须**单调**（靠近时绝不会反而选到更粗的层）。
- 分层路由的 `lod` 会与 manifest 的 `lodLevels` 做范围校验，解析出的路径同样被限制在地图目录内。
- 浏览器实测（真实运行，非推断）：点击"加载点云"后依次取回 `?lod=4`、`?lod=0`、`?lod=3`，解码出 **LOD 0 = 1,011 点、LOD 3 = 86,116 点、LOD 4 = 216,374 点，合计 303,501 点**，无报错。管线 → manifest → 路由 → 选层 → 解码 → 上报，整条链路在真实浏览器中跑通。
- 修掉一个**只在浏览器里才会出现的 bug**：把全局 `fetch` 存成实例属性后当方法调用，`this` 变成 layer 而不是 window，浏览器报 `Illegal invocation`。所有 node 测试都注入了自己的 fetch，因此全都测不到它——现在有一条测试专门用 vm 上下文里的全局 fetch 复现这个场景。
- **three.js 几何交接已接线**（`web/src/map_cloud_points.js`，随 three.js 一起打进 bundle）：解码出的 typed array 直接变成 `THREE.BufferGeometry` 的 `position`/`color` 属性与 `PointsMaterial`。颜色属性声明为 `normalized`——uint8 颜色若不归一化，画面会亮 255 倍，看起来像一团白色，很容易被误判成解码器坏了。点云被设为不可拾取（`raycast` 置空），否则它会吞掉本该落在机器人或语义图层上的点击。
- 浏览器实测几何交接：LOD 0 解码后生成 `THREE.Points`，`position.count = 1011`（与解码点数一致）、`itemSize = 3`、`color.normalized = true`、`vertexColors = true`、拾取已禁用。三维画布不在当前标签页时，面板仍如实报告解码统计并注明"三维视图未就绪"，而不是静默什么都不显示。
- bundle 的公开接口契约已相应收紧到 `["AssetRegistry", "MapCloudPoints", "RobotModelInstance", "WebGLSceneRenderer"]`——three.js 只存在于这个 bundle 内部，地图点云要转成几何体只能经由它。

- 稠密地图 P1 第三步：新增 `GET /v1/maps`、`/v1/maps/{id}`、`/v1/maps/{id}/cloud`、`/v1/maps/{id}/artifact/{role}`（`console/maps.go`，7 项测试），已登记进 API 参考。只读——建图是流水线的事，控制台没有理由删除别人的测绘成果。
- **Range 是这条路由存在的理由**：LOD 按需加载完全依赖服务端遵守 `Range`。有一条测试专门断言分段请求返回 `206` 且 `Content-Range` 为 `bytes 100-199/10000`、响应体正好是请求的那 100 字节——若退化成返回 `200` 加整个文件，所有客户端会静默下载整份点云，LOD 设计一分钱都不值。
- **双层路径防护**：manifest 契约已拒绝逃出地图目录的 `href`，路由层**再检查一次**，因为 manifest 可能来自别处；`mapId` 与目录双重校验。有测试用真实路径穿越（`..`、`../secret.json`、`a/b`）尝试读取地图根目录之外的文件，断言既不返回 200 也不泄漏内容。
- 单个损坏的地图不拖垮整个列表（缺 manifest 的目录被跳过），空地图根返回空列表而非错误，未知地图或未声明角色返回 404 而不是"空成功"。

- 稠密地图 P1 第二步：新增转换流水线 `map_pipeline.py`（17 项测试）。输入点云与轨迹，输出一个**可被 `map_manifest` 校验通过**的地图目录：LOD 点云分层、占据图（PNG + JSON）、轨迹 GeoJSON、manifest。**整条链路在合成数据上跑通**，不需要机器人、数据库或 RTAB-Map 环境。
- 合成点云 `synthetic_home_cloud()` 刻意做成"像真实扫描"而不是均匀随机：地板/墙面/家具/噪声按比例分布，并且**故意混入 10% 重复点**——只对均匀随机点表现良好的降采样器不能作为证据。同一 seed 结果完全可复现，压测可重复。
- 点云格式：COPC 是目标（单文件 + 内建八叉树 + Range），但 COPC 意味着 LAZ，而本环境没有任何 LAS/LAZ 工具链。因此 MVP 先用**自定义分块格式**（20 字节头 + 原始 float32 位置 + uint8 颜色，Worker 用 `DataView` 即可解码），不把重依赖放进关键路径。manifest 与格式无关，日后换 COPC 只改写入端与浏览器读取端，**文件扩展名就是格式适配器的接缝**。
- 颜色用 uint8 而非 float32：颜色是显示需求，百万点从 12 字节降到 3 字节，省 8MB。
- 过程中修掉三个真 bug：分块头长度写错（20 字节写成 24，解码必然失败）、artifact 的 `href` 没有相对地图目录（导致 trajectory 校验报"文件不存在"）、**PNG 漏了 8 字节签名**（任何解码器都会拒绝）。PNG 现在用 Pillow 独立解码验证过：未知=232、可通行=252、障碍=100，三者互不相同。
- 实测（100 万点合成数据）：LOD 五层 `[1018, 6473, 38465, 129077, 348669]`，构建 4.0 秒，产物合计 7.9MB，全部角色校验通过。**最粗一层 1018 点**，正是首屏立刻可画的数量。

- 稠密地图查看器 P1 第一步：新增 `map.manifest.v1` 契约（`robot/gateway/tangying_robot_gateway/map_manifest.py`，20 项测试）。一份地图由多个文件组成（点云 LOD、占据图、轨迹），manifest 是唯一说明它们身份、坐标系、范围与内容哈希的文档；`cloud` 与 `grid` 是必需角色，`trajectory`/`robot`/`mesh` 可选。
- 两条安全约束：**`mapId` 同时是目录名与 URL 片段，因此按单段路径校验**（`../home`、`/abs/path`、`home/2026`、超长名一律拒绝）；**每个 artifact 的 `href` 必须是地图目录内的相对路径**（拒绝绝对路径、`..`、空段），避免一个 manifest 变成路径穿越。
- 沿用两处已有约定：**内容哈希寻址**（与证据系统一致，客户端可校验取到的分块属于这张地图）与**标定修订号关联**（记录这张图由哪份 `robot.calibration.v1` 采集，地图与任务证据可互相核对）。manifest 自身也有内容哈希，被编辑或截断会在加载时报 `HASH_MISMATCH`。
- `verify_artifacts()` 逐个角色返回结果而不是遇到第一个问题就抛错——调用方需要知道"栅格是好的、点云被截断了"，而不是只知道"有问题"。未知字段一律报错，避免拼错字段静默失效。

- 建图指引与地图图层接到前端：新增 `web/map_view.js` 与工作台“场景地图”面板（9 项测试）。一个数据源同时回答两个问题——RTAB-Map 发布的占据栅格既算出覆盖率，也画出地图，所以数字和图永远不会互相矛盾。栅格画在 **canvas** 上而不是 WebGL：占据图是几十万个平面格子，canvas 任意缩放都清晰、重绘零成本，且不与三维场景争同一份 GPU 预算。
- 关键视觉决策：**未走过的区域画得比可通行区域更浅**且明显更暗。把未知区域画成和自由空间一样，是半成品地图看起来像已完成的主要原因——这正是操作者最需要一眼看出来的东西，有测试专门断言两者颜色不同、且障碍最深。
- 面板给出覆盖率、已确认可通行/还没走过/障碍的格数与分辨率、图例，并在图上标出机器人当前位置；位姿落在栅格之外时不画（而不是画错位置）。地图未就绪时说明原因并指向开发诊断，**不画一张空网格冒充地图**。

- 整机标定向导接到前端：新增 `web/calibration.js` 卡片流与工作台“整机标定”面板。屏幕上依次是**当前第几步 / 共几步**、**现在要做的动作**（大字，用向导原文）、进度条与“已完成 4/20 步；还差左臂 3 个关节……”的总结；当前步骤高亮为“现在做这一步”，其后两步显示为“稍后”，已完成的收进“已完成的 N 步”折叠区——二十张卡片一次铺开对操作者是一堵墙，他只关心眼前这一步。
- 新增 `GET /v1/calibration/session`（`console/server.go`）：读取标定向导写出的进度快照。**步骤顺序、提示文案与总结都由 Python 向导产生，控制台只负责渲染**，避免出现第二份会与机器人实际行为漂移的流程定义。未开始标定时返回 `available:false` 与一句说明，不是错误；快照损坏时报错而不是猜。
- 向导每完成一步都会原子写入 `<session>.status.json`（`CalibrationWizard.status_path`），因此页面刷新即可跟上进度，不需要重启任何东西。前端是**镜像而非控制器**：它不驱动舵机，标定动作仍在操作者面前的终端里进行。
- 新增 8 项前端测试与 3 项 Go 路由测试；`/v1/calibration/session` 已登记进 API 参考（路由文档契约会拦住未登记的接口）。标定会话属于具体一台机器的测量数据，`artifacts/calibration/` 已加入忽略列表。

- 新增工作台“开始使用前”面板（`web/onboarding.js` + `app.js` 接线 + 样式，12 项测试）：把标定、相机、地图、连接、安全五项目标渲染成**按阻塞程度排序**的清单，顶部直接给出下一步该做什么，每项给“情况 / 要做什么 / 去处理”三行，状态分“需要处理 / 状态未知 / 已完成”并配色，急停永远排最前。
- 两条硬规则：**没有观测到的状态一律报“状态未知”，绝不默认正常**（有测试断言空输入永远不会被读成 ready——对物理机器人错说“可以开始”是这块屏幕唯一不能犯的错）；**不把不同问题混为一谈**（地图读不到时不说“请先连上机器人”，因为机器可能已连上、只是没有导航栈）。标定来源为 `simulation` 时明确写出“不是这台机器测出来的”。
- 面板加载**不发起任何 API 请求**，遵守控制台对 `file://` 页面的零请求契约（测试会数请求数）；地图状态由“重新检查”按钮读取，这是当前已知取舍。

- 新增建图覆盖与指引模块（`robot/gateway/tangying_robot_gateway/mapping_coverage.py`）：覆盖率、frontier 簇、逐房间覆盖、位姿最大跳跃、回环与深度有效率，并把每个问题**反解成一句可执行的话**（"客厅只覆盖了 20%，请进去走一圈"），给出最近的可去 frontier 坐标与行进指引；封死的未知区域不会被当成目的地。相机交集检查 `shared_view_volume()` 走同一份几何代码，并有一条针对**仿真模型**的断言：两机共同可见体积必须 >0.15 m³，否则应改用运动法求外参。11 项测试，全部由仿真数据驱动。
- 修正前一轮公布的一处数据错误：两台 RGB-D 的共同可见体积此前写作 16.5%，那是我手写脚本时把水平半角固定成常数（而非由 fovy 与 4:3 画幅导出）算出的；按正确画幅重算为 **6.0%（2888/48000 采样点，约 0.36 m³）**。结论方向不变（模型里确实有共同可见区域），但余量小得多，因此"最小可用交集写成模型断言"更必要。文档与 Changelog 已同步修正并留下修正记录。
- 新增[标定与建图的 ROS 方案](docs/development/calibration-slam-ros-plan.md)：内参用 `camera_calibration` + ChArUco（门禁：重投影 RMS < 0.5 px，且分区 RMS ≤ 1.0 px）、手眼用 `easy_handeye2` + ArUco 且**用留出位姿验证**（平移 < 5 mm、旋转 < 0.5°）、双 RGB-D 外参优先用**运动法**而不依赖共同视野（配准残差 < 10 mm）、里程计标定作为地基（直线 < 2%、旋转 < 2°、回环漂移 < 50 mm），并给出 RTAB-Map 的参数建议与实施顺序；所有结果统一写入同一份 `robot.calibration.v1`，精度门禁在保存前拒绝不合格结果。

- 新增[实机前置工作的前端方案](docs/development/sim2real-onboarding-frontend.md)，回答四件事：①用视锥采样实测两台 RGB-D 在当前模型中的共同可见区域（**6.0%**、2888/48000 采样点，包围盒 x∈[−0.85,0.85]、y∈[0.12,0.68]、z∈[0.02,0.79] m，约 0.36 m³；初稿写的 16.5% 来自一处把水平半角固定为常数的手算错误，已修正），纠正"没有相交区域"的前提，并给出底盘相机下移收窄、头部相机前倾 10–15°、把最小可用交集写成模型断言、以及无共同视野时用运动法求外参的部署策略；②建图覆盖指标（覆盖率、frontier 数、位姿均匀度、回环收敛、深度有效率、运动过快）与"下一个该去哪"的补拍指引；③稠密地图展示：`three@0.180.0` 已是现有依赖、`webgl_scene.js` 已是 three.js 场景、控制台已渲染观测点云，因此这是既有管线增加图层而非新建技术栈，并给出点云/栅格/轨迹/frontier 四个图层的实现与性能注意事项；④梳理九项需要前端的实机前置工作与实施顺序。文中明确标注该文是方案而非已完成功能，并列出三项必须真机确认的事实。

- 新增**面向非技术用户的引导式整机标定**（`robot/gateway/tangying_robot_gateway/calibration_wizard.py` + `scripts/calibrate_guided.py`）：共 20 步，先逐条确认四项安全检查，再对左右两臂各 6 个关节逐个提示姿态归零（含每臂夹爪），每臂一次扫掠自动采样六个关节的行程，最后头部/底盘与核对保存。每一步都写明是哪条臂、哪个关节、哪条总线上的几号舵机，不出现 `homing_offset`、`range_min` 这类名字。
- 向导的设计约束：一步一个动作；只记录实际读到的值（读数异常则该步保持未完成、不推进进度）；每完成一步写盘，支持 `s` 保存退出与 `--resume` 续做；手臂没动时给出"没有检测到移动，请扶着这条手臂慢慢移动到两个极限后重试"这类可执行提示；扫掠至少采样两次，否则单次采样永远得到 min == max 而误报失败。
- 硬件接口只有一个很小的协议（`describe` / `blocking_problems` / `read_motor_position` / `close`），真机与仿真后端实现同一份，因此整条流程可以在没有机器人的情况下完整测试；`--simulate` 可用于演练，`--list` 只打印计划、不碰硬件也不需要机器人依赖。
- 明确边界：引导流程当前**只采集舵机**；相机内参/外参需由 `--base` 提供（出厂参数、上次标定或仿真推导值），缺失时在开始前就拒绝而不是走完再报错。运行时 RPC 与控制台面板、以及真机现场验收**尚未完成**，文档中已如实标注。
- 收缩契约中一处自相矛盾的校验：原先先判"必须同时有 motors 和 cameras 之一"再逐字段要求四者齐全，前面的判断是死代码；现在只保留一条规则（舵机、相机、夹爪行程、安全上限必须齐全）。

- 新增整机标定契约 `robot.calibration.v1`（`robot/gateway/tangying_robot_gateway/calibration.py`）：一台参考机器人 = **两个 RGB-D + 两条机械臂 + 每条臂一个夹爪**，共 16 个舵机（每臂 6 个关节含夹爪，两条臂各占一条总线所以左右舵机 ID 都是 1–6）+ 头部 2 + 底盘 2。文档同时描述舵机（沿用 LeRobot `MotorCalibration` 字段，现有 `validate_calibration_data` 仍是舵机字段权威）、相机内参/畸变/外参、夹爪行程与安全上限；严格拒绝未知字段，避免真机上"拼错字段等于什么都没做"。
- 标定身份是**内容哈希**（不含时间戳）：同样数字存两次修订号不变，证据因此能声明观测是在哪份标定下采集的；保存支持 compare-and-swap，`REVISION_CONFLICT` 防止并发编辑静默覆盖别人的测量值；写入为原子替换。
- 仿真不是特例：`sim/mujoco/tangying_sim/calibration.py` 从 MuJoCo 模型推导出同一份文档（相机内参由 `cam_fovy` 与画幅算出，外参由相机世界位姿折算到父连杆），运行时**真的使用**它——每次 RGB-D 采集的 `K` 与 `camera→world` 来自标定文档，改一个数字就能在观测里看到后果。`robot_state` 新增 `calibration_revision` 与 `calibration_source`，与 `model_revision`/`scene_revision` 并列。新增 `--calibration-dir`（空则只推导不落盘）。
- 修正推导过程中的一处坐标约定错误：外参在推导时已折算为光学坐标（右/下/前），解析时又折算了第二次，导致每个采集旋转 180°、感知看不到任何实体；现在推导与渲染走同一组世界位姿量，两者逐元素一致（位置 1e-18、旋转 1e-16）。
- 新增 `docs/development/robot-calibration.md` 与 11 项契约测试。**尚未实现**：面向非技术用户的引导式标定向导、承载它的运行时 RPC 与控制台面板；真机标定未做现场验收。

- 产品聚焦到**单机器人四房间家庭场景**：README 从"多条平行路线"改为**一条家居自然语言任务闭环**（观察 → 导航 → 到达确认 → 重新观察 → 解析目标 → 规划抓取 → 拿取 → 抓取确认 → 放置 → 放置确认 → 返回 → 到达确认），把 `make home-start` / `make home-accept` 作为唯一入口；Fleet 多机器人、RoboCasa 双机与固定工位下沉到"其他路线（暂不聚焦）"，代码与测试保留。`docs/README.md` 同步改为家居闭环主线，并新增"其他路线"小节。
- 新增 Makefile 入口：`home-start` / `home-restart`（`--scene home_task`，含厨房红色杯子）、`home-accept`（命令行复现同一任务并保存每步观测）、`home-routes`（`--scene home` 纯路线）。
- 端到端验证家居闭环：`make home-accept` 通过，12 步全部有证据（4 个物理工具步骤、13 份历史观测、26 张校验图像），最终 `red-cup` 稳定处于 `inside:kitchen-bin`（3 帧、位移 < 3e-8 m）；家庭运行时 11 个工具中 10 个被这条链路覆盖（安全工具按设计不在顺利路径上）。
- 验证并记录真实 SLAM 路径：`navigation-stack.sh restart --build --mode mapping --scene home` 构建成功，`ready=true`、`mapRevision=e9726707…`、`poseSource=rtabmap_tf`；客厅导航与 `verify_arrival` 成功，客厅→厨房被 Nav2 以 `allow_unknown=false` 拒绝规划——与文档既有说明一致，属安全门禁而非回归。
- 记录建图阶段的真实缺口（`docs/guides/home-scene-operations.md`）：工具层已注册 `explore_for` / `scan_environment`，但家庭运行时不提供对应执行技能，导航桥也没有速度接口，因此**五个房间的覆盖目前需要人工受控驱动**，尚无自动探索入口；补齐它是通往实机的前置工作。
- `tests/test_repository.py` 的 README 定位断言从"以云端产品开头"改为"以单机器人家庭闭环开头，且 Fleet 内容不得出现在开头"——产品决策变更后，测试改为守住新决策并禁止旧定位回流。

- 精简发布树：删除 `.superpowers/`（8 份 agent 会话任务报告，全仓零引用，`.dockerignore` 早已把它排除出镜像）、`artifacts/promotion/`（20 份小红书推广素材，零引用）与 `artifacts/replay-verification/`（2 份无引用产物），并同步移除 `.dockerignore` 里已失效的排除项。签名验收证据（`artifacts/robocasa-harness/`、`artifacts/closure-verification/`）与数字孪生 3D 资产（`web/assets/scenes/`，代码与前端测试直接依赖）保留。
- `docs/superpowers/` 只保留 `specs/`（14 份设计决策记录），删除 `plans/`（15 份按日期的实施清单）。README、架构文档、文档索引与 `docs/distributed-agentos.md` 的链接同步改为只指向决策记录；两份契约测试改为断言决策记录存在**且**发布树里不再出现计划链接——文档链接检查扫描不到该归档，只有这条断言能防止死链回归。

- 分支管理整理：新增默认分支 `main`，指向最新且完整可发布的状态（原默认分支是 8 月 25 日的 `codex/v0.1`，比主线落后 15 个提交，这是"看不出哪个分支是最新"的根因）。发布一律用 `vX.Y.Z` 附注标签标记，不再为每个版本保留长期分支；已删除 11 条远程分支——8 条内容已完全包含在 `main`（提交仍可从 `main` 到达，发布身份由标签保留），3 条早于 v0.2 的分歧分支（`codex/v0.1`、`codex/live-task-feedback`、`codex/sim-motion-fix`，其独有文件经核对为 v0.3.0 已删除的死代码或被 main 更新证据取代的旧产物）。同时开启"合并后自动删除头分支"。
- 新增[分支与发布规范](docs/development/branching.md)：唯一的长期分支、发布标签规则、功能分支前缀与生命周期、判断分歧分支能否删除的命令，并从 README 与文档索引链接。

- 修复 CI 在 `make test` 上必然失败且不给原因的问题。根因是 `sim/mujoco/tests/test_home_scene.py` 的一次 RGB-D 采集在 CI 的软件渲染（`MUJOCO_GL=osmesa`）下卡在 `mjr_render` 里：`SceneRenderer` 用 `future.result()` **无限等待**渲染线程，而 `timeout_method = "thread"` 会转储全部线程并杀掉整个 pytest 进程——于是 CI 只报告“make test 失败”，既不指出失败的测试，也不打印摘要。现在渲染与关闭都有上限（`TANGYING_RENDER_TIMEOUT_S`，默认 60 秒），超时后渲染器标记为不可用并立刻报错而不是排队继续等；`timeout_method` 改为 `signal`，被挂起的测试单独失败并保留完整摘要。
- 修复文档链接指向被 gitignore 的产物导致 CI 必失败的问题：`docs/development/rtabmap-navigation.md` 链接了 `artifacts/acceptance/…/workcell-v2-commissioning.json`，而 `.gitignore` 排除了 `artifacts/acceptance/`，该文件只存在于本机工作区。现在文档说明该文件不随 Git 分发，并在**纯净检出**中验证链接检查通过。
- e2e 就绪预算改为可配置且更符合 CI：`TANGYING_E2E_STARTUP_TIMEOUT_S`（默认 90，原写死 20）与 `TANGYING_E2E_LIFECYCLE_TIMEOUT_S`（默认 120，原写死 35）。本地在负载下复现过同一条 `startup telemetry unavailable` 失败，是同一个预算过紧的问题；预算约束的是坏掉的栈，不是慢的栈。
- CI `test` 作业上限从 20 分钟提高到 45 分钟：软件渲染下完整套件的耗时接近或超过原上限，原上限会在报告任何结果前杀掉作业。
- 新增 `tests/e2e/test_readiness_budgets.py`（预算下限与环境变量覆盖）与渲染器卡死回归测试（超时后立即报错、后续请求快速失败、关闭不被拖住）。

- 按部署目标整理仓库：`deploy/` 现在只有三个目标目录——`cloud/`（Fleet 控制面 Compose）、`robot/`（树莓派 systemd 单元与 udev 规则、`navigation/` 导航容器栈）、`local/`（开发机 Local Agent 单元与环境模板）。原来的 `deploy/raspberry-pi`、`deploy/laptop`、`deploy/navigation` 与一个不表明目标的 `deploy/config/` 合并进对应目标，样例外配置跟着使用它的目标走；新增 [`deploy/README.md`](deploy/README.md) 说明每个文件装到哪台机器。
- 新增[部署目标与代码归属](docs/deployment.md)：云端、机器人端、本地单机三个目标的进程、端口、源码目录、部署文件与启动命令，逐条列出；并说明为什么 Go/Python 包路径不按目标搬动（模块路径是内部接口，`tests/architecture` 按前缀校验依赖方向）。
- 新增一键启动 `scripts/start-all.sh` 与 `make up` / `make down` / `make stack-status` / `make stack-logs`：默认只起仿真与本地控制台（无需 Docker、无需硬件），`--with-cloud`、`--with-navigation`、`--with-fleet-sim`、`--demo` 按需加入云端、导航与双机演示。脚本只按顺序调用各目标已有的生命周期脚本并汇总健康状态，`down` 只停 `up` 记录过的组件；新增 `check` 只校验前置条件、不启动任何进程。
- 新增 `tests/install/test_start_all.py`：校验 `deploy/` 目标目录与文档一致、顶层目录都被分类、导航 Compose 的构建上下文在移动后仍指向仓库根目录（少一层就会解析到 `deploy/` 并导致镜像构建找不到 ROS 工作区）、一键脚本只做编排而不重复实现生命周期，以及 `check`/`down` 的行为。
- 导航栈移动到 `deploy/robot/navigation/` 后同步修正 Compose 的 `context: ../../..`、Dockerfile 的 `COPY` 路径与 `.dockerignore` 规则；安装脚本、导航脚本、Sim2Real 校验与相关文档同步更新。

- 修复“动作与结果”“任务步骤”内容全部挤在左侧、右侧大片留白的问题：任务进展面板本来就横跨整行，但每条记录仍是单列文本。现在每条记录分两列——左边是执行了什么（步骤标题、说明、目标、参数），右边是它的证据与「回看当时观测」按钮，按钮因此在面板里对齐成一列，便于逐条扫描；宽度不足 860px 时自动折回上下排列。
- 修复点击「回看当时观测」瞬间跳到页面底部的问题：处理函数在平滑滚动之后又调用了一次不带 `preventScroll` 的 `focus()`，浏览器会立即滚动到焦点元素，直接取消了动画。现在先以 `preventScroll` 聚焦、再平滑滚动，并在落点面板上短暂高亮，长距离滚动结束时知道停在哪里。回放面板恰好在滚动途中重绘时会把目标推走，因此滚动停下后会再校正一次位置。
- 工作台视觉与交互刷新：把 `web/styles.css` 顶部整理成设计令牌（墨色层级、三级描边、下沉表面、强调色组、抬升阴影、圆角、动效时长与缓动），组件改用令牌而不是各自写色值；面板与卡片改为细描边加低对比阴影，统计块从灰块棋盘改为「标签 + 数值」的分层卡片，任务记录行改为带可复制编号徽章的卡片，回放步骤与证据卡补齐状态色与悬停反馈。
- 修复路由切换后停留在上一页滚动位置的问题：切换入口回到顶部，重新进入当前页面不滚动；受减少动画设置约束。
- 修复路由切换时页面标题出现整块焦点方框的问题（`tabindex="-1"` 的标题不再显示焦点环，交互控件保留 `:focus-visible` 描边）。
- 手机端：任务编号不再在状态徽章旁折断，改为整行显示；底部栏的“开发模式”不再折行。
- 新增两份回归测试：路由切换的滚动行为（含重新进入当前页面不滚动）与「回看当时观测」的平滑滚动与落点标记。

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
