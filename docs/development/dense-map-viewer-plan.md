# 稠密地图浏览器查看器：升级方案与任务拆解

面向「RGB-D SLAM 稠密地图在浏览器里高效展示」的升级方案。**先说清现状**——模板里的选项大多与本仓库不符，按实际情况定方案比套用通用架构更重要。

## 1. 现状与差距清单

### 1.1 实际技术栈（不是模板默认值）

| 维度 | 本仓库实际 | 与常见方案的区别 |
| --- | --- | --- |
| SLAM | **RTAB-Map**（`deploy/robot/navigation/`，容器内持久化 `/data/maps/home/rtabmap.db`） | 不是 ORB-SLAM3 / NvBlox。RTAB-Map 已产出位姿图、占据栅格与稠密点云，**无需更换 SLAM** |
| 后端 | **Go**（`console/` 控制台 API + `cmd/local-agent`）+ **Python ROS 2 桥**（`robot/ros2_ws/src/tangying_navigation`，HTTP `:18790`） | 不是 FastAPI/Node。新增服务要么是 Go，要么是 Python 侧车 |
| 前端 | **原生 classic script + three.js 0.180**，esbuild 打包，**严格 CSP** | **不是 React/Vue**，且 CSP 禁止 ESM 与内联脚本 |
| 部署 | **Docker Compose**（`deploy/cloud/`、`deploy/robot/navigation/`） | 没有 K8s / MinIO / CDN |
| 存储 | MySQL + Redis + 容器卷；证据走内容哈希 + PNG 接口 | 没有对象存储 |

### 1.2 已有能力（可直接复用，不要重写）

- `GET /v1/navigation/map?includeGrid=1`：占据栅格（`cells []int8`、`width/height/resolution/origin`）、机器人位姿、`mapRevision`、`mode`（mapping/localization）。**控制台已经代理它**。
- `web/map_view.js`（本轮新增）：canvas 绘制栅格 + 覆盖率 + 图例 + 机器人位置。
- `webgl_scene.js`：three.js 场景、相机控制、点云图层（`drawObservedCloud`）、资产注册表。
- 证据系统：内容哈希寻址、PNG 服务、保留策略 —— **地图产物应沿用同一套寻址与保留思路**。
- `mapping_coverage.py`：覆盖率、frontier、逐房间、位姿跳跃等指标与"下一个该去哪"的指引。

### 1.3 差距清单

| # | 差距 | 影响 | 阶段 |
| --- | --- | --- | --- |
| G1 | **稠密点云没有出口**：`rtabmap.db` 里的点云只能靠容器内命令导出，仓库里没有转换流水线 | 浏览器拿不到稠密地图 | P1 |
| G2 | 无 LOD / 无分块：即使导出 PLY 也是单文件全量，百万点必然卡死首屏 | 达不到性能目标 | P1 |
| G3 | 无 Range 请求支持：Go 静态服务只能整文件传输 | 无法流式加载 | P1 |
| G4 | 无地图索引：没有"有哪些地图、哪张是当前、哪张属于哪个机器人/楼层"的元数据 | 多地图管理无从谈起 | P2 |
| G5 | 前端无 Worker/WASM：解析点云会在主线程阻塞，与 <2s 首屏冲突 | 交互卡顿 | P1 |
| G6 | 无实时位姿推送（地图页）：任务事件有 WS，地图位姿没有 | 位姿只能轮询 | P2 |
| G7 | 无 3D Tiles / USD / MJCF / GLB 导出 | Real2Sim2Real 无接口 | P3 |
| G8 | 无权限与分享 | 多租户缺失 | P3 |
| G9 | 无对象存储与 CDN，地图产物与应用同机 | 水平扩展受限 | P2 |

## 2. 关键决策（含理由与代价）

这三条决定了整个方案的形状，先定它们。

### D1. 点云格式：**COPC 优先，3D Tiles 留到 P3**

| 选项 | 优点 | 代价 | 结论 |
| --- | --- | --- | --- |
| 裸 PLY/PCD | 零转换 | 全量传输，无 LOD，百万点必卡 | 仅作归档 |
| **COPC**（Cloud Optimized Point Cloud，OGC 标准，LAZ 内部八叉树） | **单文件**、内建 LOD、支持 HTTP Range 按块取、Open3D/PDAL 都能写 | 需要 LAZ 编解码 | **MVP 选它** |
| 3D Tiles (pnts) | 生态成熟、适合城市级 | 目录+瓦片集，室内单场景过重 | P3 与网格统一时再上 |

理由：室内单机器人场景的数据量在**百万到千万点**量级，COPC 的八叉树 LOD 正好覆盖；单文件也让它能沿用现有"内容哈希寻址"的证据思路，而不必先建对象存储。

### D2. 前端渲染：**继续用 three.js，不引入 Potree**

| 选项 | 优点 | 代价 |
| --- | --- | --- |
| Potree | 成熟、自带 LOD 与编辑 | ~500KB JS + WASM；面向自身目录结构；与现有 three.js 场景、CSP classic-script 约束、<2s 首屏都冲突 |
| **自建 three.js 点云图层** | 复用现有渲染管线与资产注册表；完全可控；按需只加载可见块 | LOD 逻辑要自己写（约 300–400 行） |

理由：现有 `webgl_scene.js` 已经是 three.js 场景且已渲染观测点云，地图点云是**再加一个图层**。引入 Potree 等于在同一个页面里维护两套 three.js 与两套相机控制，收益不抵复杂度。

**WebGPU 优先、WebGL2 回退**：用 three.js 的 `WebGPURenderer`，失败回退 `WebGLRenderer`。但**不把它作为 MVP 前置条件**——现有场景已经在 WebGL2 上跑，先保证正确与流畅。

### D3. 服务方式：**P1 由 Go 控制台直接提供 Range，P2 再引入对象存储**

P1 不引入 MinIO/S3：地图产物放在运行时目录，Go 用 `http.ServeContent`（原生支持 Range）提供，**零新基础设施**，同时把接口契约定死（`GET /v1/maps/{id}/cloud?lod=n`）。P2 需要多机器人/远程分发时，把同一契约后面的实现换成 S3 预签名 URL + CDN，**前端不用改**。

## 3. 架构

```
┌─ 机器人 / 仿真 ─────────────────────────────────────────────┐
│  RGB-D ─► RTAB-Map ─► rtabmap.db（位姿图 + 稠密点云 + 栅格） │
│                      └─► Nav2 占据栅格 ─► HTTP :18790        │
└───────────────────────────┬─────────────────────────────────┘
                            │ 已有：/v1/navigation/map
                ┌───────────▼────────────┐
                │ 地图转换流水线（Python）│  ← 新增 map_pipeline.py
                │  db ─► 点云 ─► COPC(LOD)│
                │  db ─► 栅格 ─► PNG+JSON │
                │  db ─► 轨迹 ─► GeoJSON  │
                │        └─► manifest.json（元数据 + 哈希）
                └───────────┬────────────┘
                            │ 产物写入 maps/<mapId>/
                ┌───────────▼────────────┐
                │ Go 控制台 API           │  ← 新增 /v1/maps/*
                │  列表 / 元数据 / Range  │
                └───────────┬────────────┘
                            │
                ┌───────────▼────────────┐
                │ 浏览器                  │
                │  栅格: canvas（已有）    │
                │  点云: three.js + Worker│  ← 新增 map_cloud.js
                │  轨迹/位姿: three.js    │
                │  机器人: GLB (已有资产)  │
                └────────────────────────┘
```

**不破坏现有流程**：转换流水线只**读** `rtabmap.db`（或只读副本），导航、任务、证据链路一律不动。

## 4. 数据格式与契约

### 4.1 地图产物目录

```
maps/<mapId>/
  manifest.json      地图元数据（版本、帧、包围盒、点数、哈希）
  cloud/0.copc.laz   点云 LOD 0（最粗）
  cloud/1.copc.laz   …按 LOD 分层，或单个 COPC 内部八叉树
  grid/occupancy.png 占据栅格着色图
  grid/occupancy.json {width,height,resolution,origin,cellsHash}
  trajectory.geojson 轨迹（LineString + 每点时间戳）
  robot.glb          机器人模型（已有资产可复用）
```

### 4.2 `manifest.json`（契约核心）

```jsonc
{
  "schemaVersion": "map.manifest.v1",
  "mapId": "home-2026-09-12T10-31-00Z",
  "robotId": "xlerobot-01",
  "frameId": "map",
  "createdAtUnixMs": 1789000000000,
  "source": "rtabmap",              // rtabmap | gazebo | import
  "mode": "mapping",                // mapping | localization
  "floors": [{ "id": "ground", "zMin": -0.2, "zMax": 2.4 }],
  "bounds": { "min": [-4.0, -2.0, -0.2], "max": [6.0, 8.0, 2.4] },
  "pointCount": 4823910,
  "lodLevels": 5,
  "artifacts": {
    "cloud": { "href": "cloud/0.copc.laz", "bytes": 51024432, "sha256": "…" },
    "grid":  { "href": "grid/occupancy.png", "bytes": 20512, "sha256": "…" },
    "trajectory": { "href": "trajectory.geojson", "bytes": 51200, "sha256": "…" }
  },
  "calibrationRevision": "01bbeba3…",   // 与 robot.calibration.v1 关联
  "hash": "…"                            // manifest 自身内容哈希
}
```

沿用两个已在用的约定：**内容哈希寻址**（和证据系统一致）与**标定修订号关联**（同一张地图由哪份标定采集，任务证据与地图能互相对上）。

### 4.3 API 契约

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/v1/maps` | 地图列表（id、时间、机器人、点数、覆盖率、是否当前） |
| `GET` | `/v1/maps/{id}` | `manifest.json` |
| `GET` | `/v1/maps/{id}/cloud` | COPC 文件，**支持 `Range`**（必须返回 `206` 与 `Content-Range`） |
| `GET` | `/v1/maps/{id}/grid.png` | 占据栅格图 |
| `GET` | `/v1/maps/{id}/trajectory` | GeoJSON |
| `WS` | `/v1/maps/{id}/pose` | 实时位姿推送（P2） |
| `POST` | `/v1/maps/{id}/activate` | 设为当前地图（P2） |

**验收要点**：`Range` 必须真的分段返回（有测试断言 `206` 与字节范围），否则 COPC 的按需加载退化成整文件下载，LOD 形同虚设。

## 5. 目录结构（新增部分）

```
sim/mujoco/tangying_sim/            # 已有
robot/gateway/tangying_robot_gateway/
  map_manifest.py        # manifest 读写 + 校验 + 内容哈希（新）
  map_pipeline.py        # db → COPC / PNG / GeoJSON（新）
  mapping_coverage.py    # 已有：覆盖率与指引
console/
  maps.go                # /v1/maps/* 路由与 Range 服务（新）
  maps_test.go           # 新
web/
  map_view.js            # 已有：占据栅格 canvas
  map_cloud.js           # 点云 LOD 图层 + Worker 解码（新）
  map_cloud_worker.js    # Worker：解析 COPC 块（新）
scripts/
  build_map.py           # CLI：把一张 RTAB-Map 数据库转成地图产物（新）
docs/development/
  dense-map-viewer-plan.md   # 本文
```

## 6. 任务拆解（按阶段，每阶段可独立验收）

### P1 — MVP：一张地图能在浏览器里看，一百万个点不卡

| 步骤 | 产物 | 验收 |
| --- | --- | --- |
| 1.1 manifest 契约 | `map_manifest.py` + 测试 | 未知字段报错；内容哈希稳定；缺产物时校验失败 |
| 1.2 转换流水线 | `map_pipeline.py` + `scripts/build_map.py` | 从 `rtabmap.db` 生成 LOD 点云、栅格 PNG、轨迹；**只读**，不改数据库 |
| 1.3 Go 路由 | `console/maps.go` | 列表/元数据/文件；**Range 返回 206**；路径穿越被拒 |
| 1.4 前端点云图层 | `map_cloud.js` | 按相机距离选 LOD；只加载可见块；Worker 解析；内存可回收 |
| 1.5 首屏与帧率 | 性能测试脚本 | **首屏 < 2s**（地图图层不阻塞首屏）；**百万点 > 30 FPS** |
| 1.6 文档 | 部署与使用说明 | Docker Compose 一条命令起来 |

### P2 — 多地图、多机器人、实时位姿、对象存储

地图索引（MySQL 表 + Redis 缓存）、`activate`、WS 位姿推送、S3/MinIO + 预签名 URL（**前端契约不变**）、多楼层切换、地图删除与保留策略。

### P3 — Real2Sim2Real 与扩展

3D Tiles 导出、网格转 GLB、USD/MJCF 导出（供 MuJoCo/Isaac 复用同一场景）、权限与分享、插件式渲染器与格式适配器注册表。

## 7. 性能预算（先写下来，再实现）

| 指标 | 目标 | 如何验 |
| --- | --- | --- |
| 首屏可交互 | < 2s | 地图图层**懒加载**，不参与首屏；测量 `DOMContentLoaded` 到可下指令 |
| 百万点交互 | > 30 FPS | 构建期生成的 100 万点样例地图，脚本采样帧率 |
| 千万点 | 可加载、可漫游 | LOD 保证视野内实际绘制的点数有上限，而不是总量 |
| 单块解码 | < 50 ms | Worker 内计时，超时则降级到更粗 LOD |
| 内存 | 峰值 < 800 MB | 离开视野的块必须释放（有测试断言释放） |

**关键点：这些指标只能在构建期样例地图上测量**，所以 1.2 需要能生成合成点云用于压测，而不是依赖恰好有一次真实建图。

## 8. 与现有约束的冲突及处理

| 冲突 | 处理 |
| --- | --- |
| CSP 禁止 ESM | 新前端代码沿用 `webgl_scene.js` 的 esbuild 打包方式，作为 classic script 发布；Worker 用 `new Worker(...)` 加载同源脚本 |
| 首屏 < 2s vs 引入点云库 | 地图图层**懒加载**：首屏只有现有场景与面板，用户点"查看稠密地图"才加载解码器 |
| 不破坏 SLAM/ROS 流程 | 流水线只读 `rtabmap.db` 的副本；不写机器人端目录；导航栈的 HTTP 与容器不变 |
| 无对象存储 | P1 用 Go `ServeContent`，契约先定；P2 换实现不改前端 |

## 9. 风险

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| RTAB-Map 导出的点云在室内可能含大量重复与噪声 | 体积虚高、LOD 效果差 | 转换时做体素降采样，并在 manifest 里记录降采样参数 |
| LAZ/COPC 编解码依赖较重 | 机器人端依赖变复杂 | 转换放在开发机/云端，不在机器人上跑 |
| WebGPU 成熟度 | 兼容性风险 | 默认 WebGL2，WebGPU 作为可选加速 |
| 千万点下的内存 | 移动端崩溃 | LOD 上限 + 视野外释放 + 点数预算封顶 |
