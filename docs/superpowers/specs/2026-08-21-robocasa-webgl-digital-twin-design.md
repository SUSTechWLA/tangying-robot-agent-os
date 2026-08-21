# RoboCasa 完整场景与双 XLeRobot WebGL 数字孪生设计

**日期：** 2026-08-21

**状态：** 已完成交互式设计确认，等待书面审阅

**第一阶段目标：** 在云端 Fleet 用户端完整渲染当前 RoboCasa 厨房场景、两台可动 XLeRobot 和原有语义标记，同时保持离线 Local Brain 可用、权威世界一致性和实机迁移边界。

## 1. 背景与问题

当前用户端的权威世界视图由 `web/world_view.js` 使用 2D Canvas 投影三维坐标。RoboCasa 构件以 MuJoCo AABB 方盒表示，机器人以矩形和文字表示。这一实现适合验证世界状态、交互与 Harness 一致性，但不能体现现有 RoboCasa 和 XLeRobot 资产的真实观感。

项目已经拥有：

- 组合后的 RoboCasa 厨房 MuJoCo 模型及其场景身份和模型哈希；
- 约 27 MiB 的 XLeRobot STL 网格、完整双臂关节树和 Apache-2.0 来源记录；
- 两个 Runtime 共享的 `MjModel/MjData`、机器人 base pose、12 个机械臂关节状态、夹爪状态和相机证据帧；
- 单调 `WorldSnapshot.revision`、实体/机器人 freshness、WebSocket 增量、任务资源租约和 Harness 判决；
- 左键平移、右键旋转、滚轮锚点缩放、预设视角、跟随、选择和聚焦交互。

另一个可见问题是用户直接打开 `file:///.../web/index.html` 时，页面无法调用同源 `/healthz`、登录、遥测和世界接口，因此只能显示 `UNAVAILABLE / 0 entities`。原始 HTML 文件不是可运行的控制台入口。

## 2. 第一阶段范围

### 2.1 包含

1. 将当前 `robocasa-handoff-v1` 场景中实际使用的厨房可视几何、材质和纹理导出为浏览器场景资产。
2. 将 XLeRobot 的可视网格、双臂、夹爪、头部和底盘组织成可独立驱动关节的浏览器模型。
3. 在用户端同时实例化 `robot-1` 和 `robot-2`，复用同一份模型几何和材质。
4. 使用 `WorldSnapshot` 驱动机器人 base pose、关节、对象位姿、活动、持有物、急停和 freshness。
5. 保留 MuJoCo 物理边界方框、对象方块、区域、标签、选择框、任务路径、资源 owner/fencing 和异常状态标记，并允许用户切换视觉层。
6. 保留现有相机证据帧，不让图像帧代替语义世界状态。
7. 资源全部由项目本地服务提供，不依赖 CDN；云端自托管和无网 Local Brain 使用相同前端包。
8. 对 `file://` 打开方式提供明确、可操作的服务入口提示。
9. 建立场景资产 manifest 和机器人关节绑定协议，为以后实机 XLeRobot 和新场景注册提供稳定扩展点。

### 2.2 不包含

- 在浏览器中运行第二套 MuJoCo 物理仿真；
- 将整个 RoboCasa 约 10 GiB 资产库随控制台发布；
- 第一阶段支持任意 RoboCasa 场景的在线即时转换；
- 改变 Harness、任务图或物理成功判定语义；
- 用渲染插值产生新的世界事实；
- 第一阶段完成照片级全局光照、路径追踪或 VR。

第一阶段交付一个确定性的 `robocasa-handoff-v1` 完整视觉场景。后续场景复用同一离线导出器和 manifest 契约。

## 3. 方案比较与选择

### 3.1 采用：离线 GLB 快照 + 本地 WebGL + 语义叠加层

从已组合并验证通过的 MJCF/MuJoCo 场景导出当前场景使用的可视几何、层级、材质和纹理，生成静态场景 GLB；将 XLeRobot 导出为带命名刚性关节节点的独立 GLB。浏览器使用本地 vendored Three.js 加载资产，由 `WorldSnapshot` 更新动态节点。

优点：

- 浏览器看到的几何来自当前仿真模型，而不是重新手工搭建；
- 静态厨房只加载一次，两台机器人共享一份模型资产；
- 浏览器不承担物理计算，不可能与服务端争夺权威状态；
- GLB、纹理和渲染库可完全离线缓存；
- 场景视觉层和语义标记层可以独立失败、独立测试和独立升级。

### 3.2 不采用：浏览器运行 MuJoCo WebAssembly

该方案可以直接加载 MJCF，但会把大体积物理运行时、资产解析和第二套状态推进带到用户端。即使只用于渲染，也需要处理服务端与浏览器仿真的同步和漂移；若误用浏览器状态，还会破坏当前的权威世界边界。

### 3.3 不采用：只提高视频流质量

MuJoCo 相机帧观感真实，但无法满足自由旋转、平移、缩放、对象选择和全局多机器人视角。视频保留为证据层，而不是主数字孪生。

### 3.4 不采用：继续手写更复杂的 Canvas 几何

手写机械臂和厨房轮廓不会真正复用已有模型，且需要长期维护另一套几何定义。Canvas 只保留为 WebGL/资产失败时的语义降级视图。

## 4. 总体架构

```text
RoboCasa KitchenArena + composed XLeRobot MJCF
  │ 离线、确定性导出
  ├─ scene.glb             静态厨房几何/材质/纹理
  ├─ xlerobot.glb          可复用的关节节点模型
  ├─ manifest.json         场景、模型、哈希、坐标和绑定
  └─ LICENSES/NOTICE       上游来源与再发布信息
            │ 本地 HTTP + immutable cache
            ▼
WebGLSceneRenderer
  ├─ StaticSceneLayer      RoboCasa 厨房
  ├─ RobotModelLayer       两台 XLeRobot 实例
  ├─ DynamicObjectLayer    红色方块等动态实体
  ├─ SemanticOverlayLayer  边界/区域/标签/路径/租约/告警
  └─ EvidencePanel         Runtime 相机帧
            ▲
            │ WorldSnapshot + WorldDelta（唯一状态来源）
Fleet World Projector / Harness Agent
            ▲
            │ ObservationEnvelope
Sim Runtime now / Real XLeRobot adapters later
```

浏览器视觉场景是权威世界的视图，不是新的世界模型。GLB 决定对象“长什么样”，`WorldSnapshot` 决定对象“在哪里、处于什么状态”。

## 5. 资产导出与发布

### 5.1 场景专用导出

新增确定性导出工具，输入为组合后的 `robocasa-handoff-v1` MJCF 和解析后的 MuJoCo 模型。导出器遍历当前模型实际引用的可视 geom：

- primitive geom 直接转换为 GLB primitive；
- mesh geom 只复制当前场景引用的顶点、法线、索引和变换；
- material 和 texture 保留基础颜色、透明度和当前可用贴图；
- 碰撞 geom 默认不进入视觉 GLB，由语义边界层按需显示；
- robot、红色方块和其他动态对象不烘焙到静态厨房中；
- 坐标统一为右手 Z-up 世界米制，manifest 明确从 MuJoCo 到 Three.js 的轴变换。

导出结果按 `scene_id + model_hash` 寻址。相同输入必须产生相同 manifest、节点名和内容哈希。构建和运行时都不得扫描或发布未被当前场景引用的 RoboCasa 资产。

### 5.2 XLeRobot 导出

XLeRobot 模型保留刚性节点层级，不使用会模糊机械关节边界的单一合并网格。导出至少包含：

- 底盘、三轮/两轮视觉部件和设备支架；
- 左右臂 Rotation、Pitch、Elbow、Wrist Pitch、Wrist Roll、Jaw；
- 头部 pan/tilt 和相机外观；
- 原始 MuJoCo 局部位姿、旋转轴、零位和关节范围；
- 可在运行时按名称查找的稳定节点 ID。

同一页只加载一份几何和材质，两台机器人通过 clone/instance 创建独立节点树。模型资产保留现有 XLeRobot Apache-2.0 LICENSE、PROVENANCE 和 fidelity notice。

### 5.3 Manifest

manifest 使用版本化 `tangying.visual-asset.v1`，至少包含：

```json
{
  "schemaVersion": "tangying.visual-asset.v1",
  "sceneId": "robocasa-handoff-v1",
  "modelHash": "...",
  "worldFrame": "world",
  "upAxis": "Z",
  "units": "meter",
  "sceneAsset": "scene.glb",
  "robotModels": {
    "xlerobot": {
      "asset": "xlerobot.glb",
      "binding": "xlerobot.binding.json"
    }
  },
  "contentHashes": {},
  "licenses": []
}
```

浏览器只在 manifest 的 `sceneId`、`modelHash` 和当前世界模型身份匹配时显示完整视觉层。哈希不匹配时保留语义场景并明确显示 `VISUAL MODEL MISMATCH`，不能静默展示旧厨房。

### 5.4 体积与缓存

- 复用相同 mesh，移除未引用资产和重复材质；
- 允许离线 mesh quantization/meshopt，解码器必须本地随包发布；
- 两台机器人不得复制二进制模型；
- HTTP 对内容哈希资产使用 immutable cache，对 manifest 使用协商缓存；
- 第一阶段以本机服务首次加载 5 秒内可交互、刷新命中缓存为目标；
- 资产体积超预算时先降低不可见小零件和纹理分辨率，不删除关节结构和机器人整体外观。

## 6. 浏览器渲染器

### 6.1 分层组件

新增独立 `WebGLSceneRenderer`，不继续把全部逻辑堆入现有 `app.js` 或 `world_view.js`：

- `AssetRegistry`：加载、校验和缓存 manifest/GLB；
- `WorldSceneController`：把 snapshot revision 应用到渲染节点；
- `RobotModelInstance`：base pose、关节、held 状态和颜色/告警；
- `SemanticOverlay`：AABB 方框、区域、任务路径、标签、选择和 custody；
- `InteractionController`：平移、旋转、滚轮锚点缩放、聚焦、跟随和键盘快捷键；
- `CanvasFallbackRenderer`：继续使用现有纯 Canvas 权威语义视图。

WebGL Canvas 使用当前世界视图的布局位置。标签和状态使用可访问的 DOM overlay；方框、区域、轨迹和选择轮廓使用 Three.js line/transparent mesh，使其与相机正确遮挡和缩放。

### 6.2 视觉表现

- 使用厨房主光、柔和环境光、阴影和与现有深色控制台协调的中性背景；
- 保留 RoboCasa 材质和纹理，不给所有构件覆盖统一颜色；
- 两台机器人使用完整模型，并用小面积身份色区分 robot-1/robot-2；
- 机器人始终保留可切换的透明包围盒、名称、activity、freshness、held 和告警徽标；
- 红色方块保留真实几何和高可见任务描边；
- handoff/start/target zone 使用半透明地面标记；
- 任务路径按 Harness 阶段改变颜色；
- stale 机器人停止外推，模型降低饱和度并显示 STALE，不让旧位姿继续伪装 LIVE；
- emergency stop 使用红色脉冲轮廓，但不改变物理状态。

工具栏增加 `模型`、`构件边界`、`标签`、`任务路径` 四个独立开关。默认同时开启模型、关键标签和任务路径；普通厨房碰撞边界默认关闭，选中构件或进入调试模式时显示。

### 6.3 渐进增强与降级

以下任一情况发生时，用户仍能看到现有语义世界：

- 浏览器没有 WebGL2；
- Three.js 或 GLB 加载失败；
- manifest/model hash 不匹配；
- GPU context lost；
- 单个机器人模型节点/关节绑定缺失。

降级时页面显示稳定错误码和可重试按钮，不把 Fleet 世界连接误标为断线。世界数据状态和视觉资产状态分开显示，例如 `WORLD LIVE / VISUAL DEGRADED`。

## 7. 机器人状态与关节协议

当前 RoboCasa Runtime 已产生 12 个关节值，但 Edge 的 `numericRobotState` 只保留 reward、step 等指标。第一阶段扩展该边界，使仿真与实机都发布同一组规范状态键：

```text
joint.left.rotation
joint.left.pitch
joint.left.elbow
joint.left.wrist_pitch
joint.left.wrist_roll
joint.left.jaw
joint.right.rotation
joint.right.pitch
joint.right.elbow
joint.right.wrist_pitch
joint.right.wrist_roll
joint.right.jaw
joint.head.pan
joint.head.tilt
```

`xlerobot.binding.json` 将规范键映射到 GLB node、轴、方向、零偏和范围。仿真 adapter 把带机器人前缀的 MuJoCo joint name 规范化；未来实机 adapter 从编码器/驱动读取值后映射为相同规范键。Console 不解析 MuJoCo 私有名字，也不依赖具体驱动 ID。

每个世界 revision 到达时保存前一状态和目标状态；渲染循环在不超过遥测间隔的有限窗口内做视觉插值。revision、freshness、Harness 判决和选择信息不插值。状态过期后立即停止外推。

## 8. 动态实体、持有物与一致性

- 红色方块的位姿始终来自实体世界状态；
- 当实体关系为 `held_by` 时，渲染器可以把方块视觉节点附着到相应夹爪，但仍使用实体关系和机器人 held 双重一致性检查；
- 两者冲突时不猜测，方块显示在最后权威实体位姿并标记 `CONFLICT`；
- 交接区、目标区和资源 owner 仍由语义世界绘制；
- 模型加载、动画成功或相机画面更新均不能推进任务；
- Harness 继续只读取 `WorldSnapshot` 的新鲜、稳定物理后置条件。

## 9. `file://` 入口处理

页面初始化时检测 `location.protocol === "file:"`：

- 停止无意义的同源 API 轮询和 WebSocket 重连；
- 在实时场景和 Fleet 区域显示“控制台需要由 AgentOS 服务提供”；
- 显示默认入口 `http://127.0.0.1:18080/` 和一键打开按钮；
- 说明 HTML 文件本身不包含 Runtime/Fleet 数据；
- 不尝试绕过浏览器同源和认证策略。

通过 HTTP 服务打开后沿用当前 `/healthz` 模式发现、登录和重连流程。

## 10. 服务、离线与安全

- Three.js、loader、可选解码器、GLB、纹理和 manifest 全部通过项目内静态路径发布；
- Go embed 支持资源目录，Fleet 静态资源鉴权白名单只开放已知只读前缀；
- CSP 保持 `default-src 'self'` 和 `script-src 'self'`，不增加远程源或 `unsafe-eval`；
- GLB/manifest 有尺寸上限、schema 校验、路径归一化和内容哈希；
- 资产加载不携带 Fleet bearer token 到外部地址；
- Local Brain 使用相同嵌入资产，不要求互联网；
- 第三方资源按 Apache-2.0 和 RoboCasa/robosuite 各自许可证保留 attribution。

## 11. 测试策略

### 11.1 导出器与资产契约

- 相同 MJCF/model hash 导出结果和 manifest 稳定；
- 当前场景只包含被引用资产；
- GLB 节点、材质、纹理 URI 和内容哈希有效；
- XLeRobot 规范关节全部存在且轴、范围和层级正确；
- 两台模型共享资源但拥有独立变换；
- LICENSE、PROVENANCE 和 fidelity notice 随资产发布。

### 11.2 Go/数据契约

- `numericRobotState` 正确扁平化有限关节值并拒绝 NaN/Inf；
- Observation、World Projector 和 JSON round-trip 保留关节键；
- 实机示例 adapter 与仿真 adapter 通过同一规范关节测试向量；
- 静态资源、缓存头、CSP、鉴权和 path traversal 测试通过。

### 11.3 前端单元测试

- manifest/model hash 选择和不匹配降级；
- snapshot revision 到模型节点和动态实体的映射；
- 关节方向、零偏、限制和插值；
- stale 停止外推、emergency/held/conflict 标记；
- 模型、边界、标签和路径开关；
- WebGL context lost 和资产失败回退到 Canvas；
- `file://` 入口提示不会启动 API 重试风暴。

### 11.4 浏览器与端到端验收

在真实运行的 `http://127.0.0.1:18080/` 上完成：

1. 首屏显示完整 RoboCasa 厨房，不再以全部 AABB 方盒代替；
2. 同时显示两台完整 XLeRobot，不再以矩形代替；
3. 执行中文方块交接任务时，两台机器人的 base pose、机械臂和夹爪随真实仿真状态变化；
4. 红色方块、交接区、目标区、任务路径、owner/fencing 和状态标签与 `/v1/world` 一致；
5. 左键平移、右键旋转、滚轮锚点缩放、预设、跟随、选择、双击聚焦和刷新恢复正确；
6. WebSocket revision gap 重同步、资产失败、世界 stale 和相机丢帧分别显示正确状态；
7. 浏览器离线运行时不请求任何外部域名；
8. `file:///.../web/index.html` 显示明确服务入口，不再只有 `UNAVAILABLE`；
9. 保存总览、robot-1、robot-2 和最终交接结果截图作为验收证据；
10. `make test`、Web 测试、Fleet e2e 和 RoboCasa Harness 测试不回归。

## 12. 性能与可用性目标

- 本机首次进入场景 5 秒内可交互，缓存刷新 1 秒内恢复主要视觉层；
- Apple Silicon 常见浏览器在 1400×700 主视图目标 50–60 FPS；
- 世界增量处理与渲染帧分离，慢资产加载不阻塞 revision 接收；
- resize 和 devicePixelRatio 有上限，后台标签页降低渲染频率；
- 两台机器人共享 GPU geometry/material；
- 标签做可见性和距离裁剪，普通厨房构件不持续显示文字；
- GPU/资产异常必须在 3 秒内进入可用的语义降级视图。

## 13. 实机迁移

实机接入仍然只替换 Runtime Tool adapter、Observation adapter、地图和坐标变换。视觉层通过设备/场景 manifest 注册：

- 机器人心跳/设备元数据声明 `modelId=xlerobot` 和 model revision；
- 实机 proprioception 发布相同规范关节键；
- 用户预先建立的真实地图导出为与 RoboCasa 场景 manifest 相同的静态 visual asset；
- 实体检测和 SLAM 在 `world` 坐标系发布动态对象；
- 视觉模型与真实硬件 revision 不匹配时明确降级，不能把当前官方模型当作已标定数字孪生；
- Harness 完全不依赖 GLB、Three.js 或渲染结果，只依赖 Observation/WorldSnapshot。

因此，第一阶段不是一次性的仿真美化，而是建立“场景资产注册 + 机器人模型注册 + 规范关节状态 + 权威语义叠加”的通用数字孪生接口。

## 14. 完成标准

只有以下条件全部满足才可宣称第一阶段完成：

1. 用户从 `http://127.0.0.1:18080/` 看到完整当前 RoboCasa 厨房和两台完整 XLeRobot；
2. 机器人关节和红色方块与真实 MuJoCo 状态同步，不是预录或独立动画；
3. 方块/边界/标签/任务路径/租约/告警标记可同时显示并可独立切换；
4. 自由相机、聚焦、跟随、刷新恢复和选择交互全部通过；
5. 视觉资产故障不会破坏权威世界、Harness 或任务执行；
6. 原始 `file://` 页面给出正确服务入口；
7. 双机器人自然语言交接任务继续由 Harness 物理后置条件判定成功；
8. 云端和无网 Local Brain 不依赖外部静态资源；
9. 资产来源、哈希、许可证和模型适用范围可追踪；
10. 自动测试、真实浏览器验收和截图证据全部通过；
11. 后续实机地图和机器人只需注册 manifest、规范关节和 Observation adapter，不修改 Harness 或前端核心渲染协议。
