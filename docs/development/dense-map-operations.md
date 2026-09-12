# 稠密地图：构建、部署与运维

把机器人建出的地图变成浏览器能看的东西，这一页说明怎么跑、在哪跑、以及**什么情况下不应该跑**。

本文对应[升级方案](dense-map-viewer-plan.md)的 P1。

## 1. 一条命令构建地图

```bash
# 用仿真/压测数据造一张地图（不依赖机器人）
scripts/build_map.py --synthetic 1000000 \
    --output artifacts/maps/loadtest --map-id loadtest --robot-id loadtest

# 用机器人上真实建出的 RTAB-Map 数据库
scripts/build_map.py --database /path/to/rtabmap.db \
    --output artifacts/maps/home --map-id home --robot-id xlerobot-01 \
    --calibration artifacts/calibration/xlerobot-01.json --camera head-rgbd
```

输出是一个**自校验的地图目录**：

```
artifacts/maps/<mapId>/
  manifest.json         元数据 + 每个产物的内容哈希
  cloud/lod0.bin …      分层点云，lod0 最粗
  grid/occupancy.png    占据栅格图
  grid/occupancy.json   栅格尺寸/分辨率/原点
  trajectory.geojson    轨迹（有的话）
```

命令结束时会逐个产物校验大小与哈希，并打印结果。

## 2. 服务给浏览器

控制台按 `TANGYING_MAP_ROOT`（默认 `artifacts/maps`）读取地图：

```bash
TANGYING_MAP_ROOT=artifacts/maps make build
TANGYING_MAP_ROOT=artifacts/maps ./scripts/sim-stack.sh restart --perception rgbd --scene home_task
```

接口（只读，均已登记在 [API 参考](../production/api-reference.md)）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/v1/maps` | 地图列表 |
| `GET` | `/v1/maps/{id}` | `manifest.json` 原文 |
| `GET` | `/v1/maps/{id}/cloud` | 最细一层，**支持 `Range` 返回 `206`** |
| `GET` | `/v1/maps/{id}/cloud?lod=N` | 第 N 层，N 与 `lodLevels` 范围校验 |
| `GET` | `/v1/maps/{id}/artifact/{role}` | 按角色取 `grid`/`trajectory` 等 |

浏览器端在工作台「稠密地图」面板点**加载点云**：先取最粗一层立刻出图，再按相机距离细化。分层加载依赖 `Range`——如果服务端退化成整文件返回，客户端会静默下载整份点云。

## 3. 依赖

| 组件 | 需要什么 |
| --- | --- |
| 构建流水线 | Python 3.11 + numpy；从数据库重建时还需要 **Pillow**（深度是 PNG 包着的 float32 缓冲，必须完整解码） |
| 控制台 | 无需额外依赖，`ServeContent` 自带 `Range` |
| 浏览器 | 近两年的 Chrome/Edge/Firefox/Safari；canvas 与 WebGL2 |

**LAZ/COPC 与 PDAL/Open3D 目前都不需要**：MVP 用自定义分块格式，20 字节头 + float32 位置 + uint8 颜色，Worker 用 `DataView` 解码，零解析库。

## 4. 什么数据库不能导出

**这不是谨慎，是实测结论。** 导航栈卷里那份 208MB 库（仿真家居，1237 节点）**不是一次干净的建图**：

```
⚠ 数据库包含 2 张地图；它们不是同一次建图，合并导出会得到错误的地图
⚠ 有 1235 个节点已被删除（weight < 0），仅 2 个仍然有效
⚠ 时间跨度约 49.9 小时，远超单次建图；这是多次会话累积的库
```

`build_map.py` 会**拒绝**这类库（退出码 2），除非显式加 `--allow-unhealthy`。原因是**在这种库上反投影会把不同会话的扫描堆在一起，产出一份稠密、看起来合理、但完全错误的地图**——而下游没有任何环节能分辨它是错的。

要得到可用的库，需要一次**干净的建图运行**：一张地图、一段连续会话、节点未被删除。若位姿全部相同（这份库就是），`reconstruct_cloud()` 会直接报错而不是返回点云。

## 5. 性能与容量（实测与未测）

| 项 | 实测 |
| --- | --- |
| 100 万点构建 | 约 4 秒 |
| LOD 五层点数 | 1,018 / 6,473 / 38,465 / 129,077 / 348,669 |
| 产物大小 | 约 7.9MB（100 万点输入） |
| 首屏可画的点数 | **1,018**（最粗一层） |
| 浏览器取层 | `?lod=4`、`?lod=0`、`?lod=3` 各返回 `200`，解码 303,501 点无报错 |

**未测**：需求里的「首屏 < 2s」「百万点 > 30 FPS」**没有任何实测数据支撑**。已经测到的是最粗层只有 1,018 点（首屏可画的数量级）与构建耗时，这些**不等于渲染帧率**。要给出结论需要一次带帧率采样的浏览器压测。

## 6. 已知限制

| 限制 | 说明 |
| --- | --- |
| `Admin.opt_map` 未读取 | RTAB-Map 的占据栅格字节长度与其声明分辨率对不上矩形，布局未确认；**猜一个错误布局比不读更糟**，运行时地图走 Nav2 发布的栅格 |
| 颜色是全有或全无 | 任一帧颜色解码失败，整份点云不带颜色——而不是给一个长度不匹配的数组 |
| 深度上限 6 m | 室内 RGB-D 在无测量处返回垃圾或无穷，反投影会把点散布到整个房间 |
| 无对象存储 | P1 由 Go 直接提供文件；P2 换 S3 预签名 URL 时**前端契约不变** |
| 无 WebGPU | 默认 WebGL2 跑通；WebGPU 作为可选加速尚未接入 |

## 7. 排查

| 现象 | 原因 |
| --- | --- |
| `/v1/maps` 返回 `count: 0` | `TANGYING_MAP_ROOT` 没设或指错；它必须在**启动控制台时**就设好 |
| 面板显示"三维视图未就绪" | 三维画布不在当前标签页，点云未加入场景；数据仍已取回并可解码 |
| 加载点云无反应且无报错 | 检查浏览器控制台；历史上出现过 `fetch` 被当方法调用导致的 `Illegal invocation` |
| 地图列表有目录但没列出 | 该目录缺 `manifest.json`，或 manifest 损坏——单个坏地图不会拖垮整个列表 |
