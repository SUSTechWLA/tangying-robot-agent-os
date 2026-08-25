# RoboCasa 双机器人交接与实机迁移

## 当前结果

固定场景 `robocasa-handoff-v1` 在同一个 MuJoCo `MjData` 中组合 RoboCasa
KitchenArena、两个带独立前缀的 XLeRobot 和一个红色方块。系统已经跑通：

```text
中文自然语言
  -> simulation.reset_episode / observe_scene / verify_episode_ready
  -> 云端意图图与单写协调器
  -> Harness 前置条件
  -> robot-1 Edge -> robot-1 Runtime 工具
  -> 命令后的新鲜 WorldSnapshot 证据
  -> 资源 owner/fencing token 交接
  -> robot-2 Edge -> robot-2 Runtime 工具
  -> Harness 物理后置条件 -> SUCCEEDED
```

两个 gRPC Runtime 端点只是同一物理世界的机器人权限视图，不复制物体状态。
因此交接区、方块 owner、机器人 held 状态和最终目标区都只有一个权威事实。

## 为什么这是系统创新点

创新点不是“有多个 Agent”或“界面能画地图”本身，而是面向实体机器人的组合边界：

1. 工具目录说明机器人能做什么，观测目录说明系统凭什么判断已经做完；两者独立注册。
2. Console、协调器和 Harness Agent 消费同一份带 revision、source sequence、freshness 和坐标变换版本的世界事实。
3. 工具成功不等于任务成功。Harness 只接受命令之后的新鲜环境证据，并检查物体位置、稳定性、机器人释放状态、资源 owner 和 fencing token。
4. 单写协调器、事件/Outbox、幂等命令和资源 fencing 共同约束多机器人协作；异常时失败关闭，不靠 prompt 猜测恢复。
5. 云端大脑和无网 Local Brain 共用 Runtime、工具、观测与世界契约，仿真迁移到实机不修改任务语义。

这使它具备明显的研究和产品架构创新价值。当前准确定位仍是“端到端研究/产品原型”，不是已经完成大规模生产验证的分布式机器人平台。

## 安装与运行

前提：Go、Docker、Conda，且本地已有 `datasets/robocasa/robocasa` 上游仓库。

```bash
make robocasa-install
make robocasa-smoke
make robocasa-web-assets
bash scripts/robocasa-fleet.sh start
open http://127.0.0.1:18080/
```

打开 `http://127.0.0.1:18080/`。这个便捷入口只监听宿主机 loopback；RoboCasa profile 显式启用 `FLEET_AUTH_MODE=demo`，浏览器自动获取短期 `demo-operator` 会话并直接进入控制台。公网/通用 Fleet 默认仍是 `required`，受账号、TLS 和白名单保护。设备令牌没有被绕过，启动日志也不会打印密码或设备令牌。

控制台应显示：

- `LIVE`，revision 持续单调增长；
- 两台 `robocasa` 设备在线，且工具能力目录已注册；
- 场景身份 `robocasa-handoff-v1 · robocasa · <model hash>`；
- 机器人、方块、交接区、目标区、资源监护权和观测源健康；
- 双路机器人相机证据、全局占用栅格和机器人轨迹；
- 完整本地 RoboCasa 厨房 GLB、两台完整 XLeRobot、实时关节姿态，以及可选的 18 个 MuJoCo 构件物理 AABB；
- “准备新仿真回合”以及两台机器人的两个交接意图均清晰可见；后两个物理意图为 `SUCCEEDED`，Harness 为 `SATISFIED`。

三维世界交互：左键拖动平移、右键拖动旋转、滚轮围绕指针锚点缩放、单击查看对象证据、双击聚焦实体、`F` 恢复全景。工具栏提供总览、俯视、R1、R2 和机器人持续跟随；“模型”“构件边界”“标签”“任务路径”四个开关彼此独立。方块交接路径、owner/token、freshness、activity、held 和 Harness verdict 都来自权威世界，而不是从画面反推。

MuJoCo operator overview 使用独立的高位斜俯视相机，一次容纳厨房、两台机器人、红色方块与交接工作区；它和机器人头部 RGB-D 明确分栏并标注“不是机器人相机视角”。前端始终按完整画面缩放，支持单击放大查看；响应式布局不会用固定高度裁切画面。该 overview 只提高可观察性，Harness 仍仅使用机器人自身 RGB-D 和权威世界状态判定任务。

Console 从版本化 `tangying.visual-asset.v1` manifest 加载同源、内容哈希固定的场景与 XLeRobot GLB，并用 `/v1/world` 的 canonical joint keys 驱动两个模型。静态视觉资产只是表现层，绝不能作为物理成功证据；任务成功仍必须由新鲜环境观测、资源监护权和 Harness 后置条件共同证明。manifest 的 `sceneId` 或 `modelHash` 与当前模型 revision 不匹配、资产加载失败或 WebGL context 丢失时，页面会保留 `WORLD LIVE` 并明确显示 `VISUAL DEGRADED`，失败关闭到可用的语义 Canvas。

直接打开原始 `file://.../web/index.html` 不是 live Console：该入口只给出 HTTP 服务地址，并且不启动 API/WebSocket 重试。请始终从 `http://127.0.0.1:18080/` 操作实时系统。离线验收依赖预先生成并由同一服务提供的本地资产，不会访问外部 CDN。

每次从 RoboCasa 用户端创建任务都会发送
`executionContext={"mode":"simulation_demo","newEpisode":true}`。Runtime 先通过注册工具创建新 episode，把方块、两台机器人、相机和关节恢复到确定性初态，再开始物理步骤。同一套服务可连续提交多次；每次 episode ID 必须变化，方块都从 `left-start-zone` 开始，并最终到达 `right-target-zone`，不再需要重启 profile。`newEpisode` 是一次性生命周期动作：任务运行中更新 revision 只更新未完成工作，绝不会再次 reset 已经发生变化的物理世界。

Demo 模式可不暴露账号密码地取得短期令牌并检查任务：

```bash
DEMO_TOKEN="$(curl -fsS -X POST http://127.0.0.1:18080/v1/auth/demo-session | \
  python -c 'import json,sys; print(json.load(sys.stdin)["token"])')"
curl -fsS -H "Authorization: Bearer $DEMO_TOKEN" \
  'http://127.0.0.1:18080/v1/tasks?view=summary&limit=20'
unset DEMO_TOKEN
```

`/v1/auth/demo-session` 只在显式 `FLEET_AUTH_MODE=demo` 的 loopback 演示 profile 中存在；生产模式必须使用正式 RBAC 登录。

## 证据与异常矩阵

```bash
make test-robocasa-e2e
make test-robocasa-faults
make robocasa-acceptance              # pinned round4 retained revalidation only
make robocasa-acceptance-candidate    # fresh candidate; bounded 300 s browser wait
make robocasa-acceptance-promote      # audit/revalidate candidate, then pin anchor
```

默认 `robocasa-acceptance` 不启动任何栈，只重验证 tracked anchor 固定且随仓库分发的 `artifacts/robocasa-harness/round4`。因此 clean checkout/clean archive 可离线复验，不依赖开发机遗留的 ignored artifact。新 episode 必须显式运行 candidate target；它把初始/移动中/最终世界、任务、意图、领域事件、设备状态、Harness verdict、工具活动、双机 RGB-D 轨迹和实际执行的故障矩阵写入独立 candidate 目录，并以大于零且最大 600 秒的 bounded wait 等浏览器上传（Make 默认 300 秒）。因此默认 target 不存在 zero-timeout 立即造 summary 的旁路。

新候选包的面向排查视图固定为 `tasks.json`、`tool-activities.json`、`world-trajectory.json`、`sensor-trajectory.json`、`fault-matrix.json`、`browser-network.json`、`browser-state.json`，以及 `screenshots/initial.png`、`robot-1-running.png`、`robot-2-running.png`、`completed.png`。`sensor-trajectory.json` 必须证明同一 episode 下两台机器人都有至少两个递增 capture，且 RGB/metric depth 共享 capture ID、尺寸、相机变换和 world revision。`fault-matrix.json` 保存每条实际命令的退出码、耗时和输出摘要哈希；任一场景失败则候选包失败。

Candidate 输出只允许位于仓库的 `artifacts/robocasa-harness/` 下，并明确禁止 retained root 与历史/当前 retained pack（`round3`、`round4`）。开始新 episode 前会删除并重建候选入口，同时在 canonical trusted root 下创建随机 0700 staging 并持有目录 fd。receiver、runner、session、截图、summary 和 attestation 的读取、原子写入、chmod、unlink 与递归枚举全部通过该 fd 下的 `dir_fd`/`O_NOFOLLOW` 操作完成，不使用 staging 路径；递归证据树只允许真实目录和 regular file，symlink、断链、FIFO、socket、device 或其他特殊对象会在签名前 fail closed，retained revalidation 同样拒绝。候选入口和父目录身份复核成功后才原子发布，并返回 fd-rooted 内容句柄。入口或 staging 被替换为 symlink 会 fail closed，异常清理先相对 held fd 清空内容，再只移除 device/inode 匹配的目录项。可信 root 以上允许 macOS `/var -> /private/var` 这类系统 symlink，no-follow 只约束 root 以下的用户相对路径。

浏览器控制器在另一个终端生成与 `capture-session.json` 中 run/nonce/task 一致的 payload。runner 在 task 绑定后打印私有 staging session 的绝对路径；复制该路径交给仓库 uploader，发出唯一一次认证 POST：

```bash
python scripts/upload_robocasa_browser_capture.py \
  --session <runner 输出的私有 staging>/capture-session.json \
  --payload /path/to/browser-payload.json
```

session 文件必须由 runner 以 0600 创建；uploader 只接受 `127.0.0.1` receiver、不跟随重定向、不重试，也不输出 bearer。receiver 在 auth 和长度校验后、读取 body 前原子保留一次提交；第一个有效 POST 正在处理或已提交时，其余并发请求均返回 409。解析失败且尚无文件 commit 才释放 reservation；已有任何 capture 文件时保持 single-use fail closed。session 只属于 candidate 采集期，finalize 在写最终 attestation 前删除它；任何仍含顶层 `capture-session.json` 的 retained pack 都会被拒绝。包内 `capture-anchor-candidate.json` 必须存在，并与本次 selected trusted anchor 的 canonical 内容完全一致；删除、篡改或替换任一方都会 fail closed。

每个 candidate 用同一个 `runId`/`taskId` 绑定 API 快照、asset network、浏览器 network/performance、五张 PNG 和各自的 canonical snapshot digest。浏览器 page-assets inventory 必须实际观察完整九角色闭包：document、`styles.css`、`webgl_scene.js`、`world_view.js`、`app.js`、manifest、scene GLB、robot GLB、binding；runner/server corroboration 再核对每个响应的最终 URL、无重定向、nonce、字节数和 SHA-256。确定性 frontend build identity 同时绑定这些仓库源文件。额外运行时请求只允许无 query/fragment 的 `/healthz`、`/favicon.ico`、`/v1/auth/demo-session`、`/v1/auth/ws-ticket`、`/v1/devices`、`/v1/maps/global`、`/v1/scene/frames`、`/v1/tasks`、`/v1/world`，固定 `robot_id=&limit=20` schema 的 `/v1/telemetry`，以及只带一个 13 位十进制毫秒 `t` 的 `/v1/scene/frames/robot-1|robot-2`。未声明 path/query、重复参数、fragment、redirect 或任何外部 origin 都 fail closed。`summary.json` 会重新解码 PNG、核对文件哈希、任务/场景/模型身份、双机实际关节运动、最终方块/held/source/custody 状态，以及 5 秒首交互/刷新和至少 50 FPS 的全质量 steady renderer submission-capacity 门槛；capacity 使用最后一次交互 250 ms 后、最后最多 300 个 `renderer.render()` 原始样本的平均耗时计算。当前 round4 在受控、可见浏览器中的实际 display rAF 是 60.00 FPS，签名的 steady capacity 是 170.9597 FPS，mean/median/p90/p95/max render 耗时分别约为 5.85/5.60/6.80/8.10/9.30 ms，刷新恢复为 297 ms。不同目标硬件仍必须独立验收 display rAF ≥50。submission capacity 在 GPU completion 或显示调度受限时可能高估最终可见流畅度，因此 display cadence 和 mean/median/p90/p95/max 必须原样保留。

浏览器控制器不能直接把预先存在的 JSON 当成证据。runner 先生成不可预测 nonce、bearer 和临时 Ed25519 key，并只在 loopback 启动 receiver；receiver 接收浏览器直接产生的截图字节、每次交互时刻、DOM 状态、页面 `requestAnimationFrame` 原始时间戳，以及 WebGL 每次 render 的原始耗时/时间戳。私钥不会随 upload 提前销毁；runner 先构建 fail-closed summary，再用同一把 key 生成最终 canonical attestation，覆盖 summary hash、capture-envelope hash 和每个 retained evidence file，之后立即销毁私钥。candidate 只产生未受信 anchor。`make robocasa-acceptance-promote` 会先以 candidate anchor 验证签名并重算全部 acceptance checks，只有 summary `passed: true` 且所有 checks 为 true 才原子更新 tracked anchor。runner 自己以禁缓存、禁重定向方式 corroborate 九角色闭包的 request/final URL、状态、响应 nonce header 与内容哈希；它不能替代受控浏览器 page-assets inventory，二者必须在同一 episode 中同时成立。

Harness 证据 ID 从右侧解析为 `source/sequence/observed-nanos`，只允许注册的双机器人 scene 与当前机器人 proprioception source，并逐项匹配 trajectory 中实际 post-command observation 的 source、十进制 sequence、毫秒时刻、`world` frame 和 `robocasa-world-v1` transform。fresh process 还必须观察到 robot-1/token 1/held/FRESH、robot-2/token 2/held/FRESH、environment/token 3/FRESH 的完整轨迹。

- 重复或乱序观测；
- 过期 fencing token；
- 接收机器人离线；
- 相机丢失但语义 ground truth 仍可用；
- 外力移动方块；
- 急停和动作取消；
- 原子 checkpoint、模型哈希不匹配；
- 控制面重启后的目录恢复与在线机器人身份防抢占；
- Edge 真实进程重启后 source sequence 仍单调；
- Runtime 真实进程重启后从 checkpoint 恢复完成态。
- RGB/depth renderer 分别故障、捕获冻结和证据不前进；
- 错机器人、错 episode、重复、倒序和 future-world 捕获；
- World stream gap、旧浏览器响应、多 dashboard 定时器与有界 summary；
- Coordinator 重启、Redis/Outbox 暂停与领导租约续期。

Runtime checkpoint 使用原子替换，并绑定场景 ID、episode、模型哈希和状态版本。恢复不匹配时失败关闭。

## 实机 Runtime 接入契约

实机不是在云端新增一套分支逻辑，而是注册一个新的 Runtime adapter。至少同时注册以下三类能力，不能只注册动作工具：

### 1. Tool adapter

保留语义工具名和参数边界，例如 `observe_scene`、`resolve_entity`、`plan_grasp`、`pick`、`verify_grasp`、`place`、`verify_place`。每条物理命令必须携带：

- 工具目录 revision/fingerprint；
- 基于哪个 world revision 规划；
- resource id 与 fencing token；
- idempotency key、deadline、approval 和 safety profile。

实机 adapter 把这些工具映射为运动规划、轨迹执行、夹爪和安全控制。LLM 不生成或放宽安全字段。

### 2. Observation adapter

Harness 依赖环境变化，所以实机必须持续发布可归因、可排序的状态：

- 机器人基座、关节、夹爪、held object、故障、急停和动作阶段；
- 物体类别、稳定 ID、世界系位姿、置信度和 `inside/on/held_by` 关系；
- 工作区、交接区、目标区、障碍物和占用栅格；
- `source_id`、跨重启单调的 `source_sequence`、采集时间、freshness budget；
- `frame_id`、`transform_revision`、地图 revision、模型/标定 revision；
- 命令相关 evidence id，使 Harness 能证明观测发生在命令提交之后。

相机、深度、里程计、关节编码器和外部定位可以是多个 source。World Projector 只接收通过 schema、序列、时间和坐标变换校验的事实；某个 source 过期时显式降级或阻止任务，不用最后一帧冒充当前环境。

### 3. 地图与坐标变换

实机部署前先建立与仿真同语义的地图：固定 `world` frame、机器人定位 frame、工作区、交接区、目标区和障碍物。发布从传感器/机器人 frame 到 `world` 的版本化变换，并将地图哈希或 revision 注册到设备与观测元数据。

地图更新必须产生新 revision。任务规划记录使用的 revision；执行后若地图或标定已变化，Harness 要求重新 grounding，而不是在旧坐标上继续动作。

### 4. 实机视觉注册

真实工作区地图沿用 `tangying.visual-asset.v1` manifest：注册稳定 `sceneId`、地图/标定派生的 `modelHash`、同源 GLB 路径、内容哈希和许可证。真实 XLeRobot adapter 同时发布与 binding 对齐的 `joint.*` canonical keys、有限数值关节位置和当前模型 revision。任何视觉 manifest、adapter 模型或标定 revision 不一致都会退回语义 Canvas；不得用近似旧模型掩盖不一致。

## 迁移顺序

1. 用实机 adapter 跑只读 Observe 和地图对齐，不允许运动。
2. 通过工具/观测共同契约测试，验证序列、freshness、frame 和模型 revision。
3. 单机低速、无负载执行，接入实体急停、watchdog 和安全范围。
4. 单机 pick/place 通过 Harness 后，再开启交接区资源 fencing。
5. 双机执行至少 30 次受监督试验，注入断网、重启、相机丢失、定位漂移和外力移动。
6. 只有 `docs/production-readiness.md` 的硬件门槛全部满足，才声明实机生产就绪。

## 已知边界

- 当前 RoboCasa 动作是确定性语义/运动学 pick-place，用来验证 AgentOS 分布式闭环；它不是关节力矩控制、碰撞丰富的抓取策略或实机标定数字孪生。
- 浏览器 GLB 是确定性导出的视觉树，不等同于 MuJoCo renderer 的像素输出，也不替代碰撞、动力学或传感器事实；语义 Canvas 仍是故障回退层。
- 默认只安装本场景需要的最小 RoboCasa 资产；完整资产使用 `make robocasa-install-full`。
- WorldHub/游标的完整跨节点持久化、存储切主和长期网络 chaos 仍是生产化工作。
- 当前没有声称真实 XLeRobot 已完成物理交接；实机必须走上述观测、地图和安全验收。
