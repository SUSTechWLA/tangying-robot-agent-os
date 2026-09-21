# SLAM 分层：谁是权威、接缝在哪、怎么单独对比

**日期**：2026-09-19
**背景**：目标是"每个模块单独提升对比"。这份记录定下三件事：每个阶段的权威实现、跨阶段的接缝契约、以及让对比成立的前提。

---

## 一、现状取证：不是两套竞争的栈，是按阶段分工

我最初的判断是"有两套 SLAM 栈并存，得先合并"。**取证后这个判断是错的**，需要修正。

| 阶段 | 权威实现 | 证据 |
| --- | --- | --- |
| **建图**（巡检期，产出地图） | `DenseSLAM`（Python，gateway） | `robot_workflow.py:138/284/537` 持有实例；`_build()` 写地图产物 |
| **定位 / 导航**（在线闭环） | RTAB-Map + Nav2（ROS） | `navigation_node.py:112` 订阅 `/rtabmap/info`；`:114` 订阅 `/rtabmap/localization_pose`；`:299` 上报 `rtabmap` 输入时延 |
| **导航客户端**（仿真侧） | `RTABMapClient` | `sim/mujoco/tangying_sim/rgbd_runtime.py:561`（`TANGYING_NAVIGATION_URL` 设置时启用） |

两者**不是竞争关系**：建图是离线性质的批处理，定位是在线闭环。各自在各自阶段是权威。

**跨阶段的接缝**是地图产物本身：`DenseSLAM` 建出的点云/轨迹 → `rtabmap_export.py` → RTAB-Map 数据库 + `navigation_map.nav2_artifacts` → Nav2 的 `map.yaml`/`map.pgm`。

跨这个接缝的**身份**是 `calibrationRevision` + `mapRevision`（`map_manifest` 负责校验）。这是既有设计中已经正确的部分，不需要改。

---

## 二、分层：按数据契约，不按服务

| 层 | 契约 | 实现位置 | 状态 |
| --- | --- | --- | --- |
| **L0 词汇** | `PointCloud`、`voxel_downsample`、`wrap`/`pose_se2`/`transform`/`relative`/`compose` | `geometry.py` | ✅ 本轮抽出 |
| **L0 帧** | `RgbdFrame` + `validate_frame` | `rgbd.py` | ✅ 已是独立叶子 |
| **L1 纯变换** | 点 + 传感器原点 → 占据栅格 | `map_pipeline.py` | ✅ 已是纯的 |
| **L2 纯策略** | 栅格 + 位姿 → 目标 | `exploration.py` | ✅ 已是独立叶子（零内部依赖） |
| **L3 有状态估计** | 帧序列 → 位姿 / 位姿图 | `dense_slam.py` | ✅ 本轮断开对 `map_pipeline` 的依赖 |
| **L4 能力循环** | start / status / stop | `robot_workflow.py` 内的 401 行 | ⬜ **未抽** |

### L0 抽出的内容与理由

抽出前，`dense_slam` 有 `from .map_pipeline import PointCloud, voxel_downsample`。这是**类型依赖，不是行为依赖**——估计器为了给点云命名，得导入"把地图变成栅格"的模块。在"我能不能换掉估计器而不碰栅格构建器"这个问题上，这条边恰好指向错误的方向。

抽出后：

```
geometry.py       ← 只依赖 numpy/math，无内部依赖
    ↑                        ↑
map_pipeline.py          dense_slam.py
（再导出 PointCloud）      （不再 import map_pipeline）
```

`map_pipeline` 与 `dense_slam` 都**再导出**这些名字，所以既有 import 全部不变；`robot_workflow`/`grid_navigation` 从 `dense_slam` 拿 `compose/pose_se2/relative/transform` 的写法继续可用。

**验证**：`robot/gateway/tests` **645 passed / 4 skipped**。并且冻结日志复放给出**完全相同的数字**（26705 / 32249 格）——重构没有改变行为，这一点是被复放器证明的，不是我说了算。

---

## 三、让"单独对比"成立的前提：冻结的可复放输入

**这是本轮真正的产出。** 在此之前，"单独对比某个模块"只能靠跑机器人：0.05 m/s，一次巡检二十来分钟。实际上等于没有对比。

仓库里已有的地图产物**不能**当输入，因为它们自己的编码头写着：

```
kind = capture_previews     rgb = jpeg-quality-78
depth = nearest-sample-fixed-scale-preview
rawDepthSaved = False        ← 原始深度没有存
```

240×180 的 JPEG 预览，是给人看地图的。而且 `rawDepthSaved: False` 是硬编码（`slam_keyframes.py:98`）并有测试钉住——这个记录器按设计就是预览库。

### 新增：原始传感器日志

`robot/gateway/tangying_robot_gateway/sensor_log.py`

- **记录**：全分辨率 RGB（uint8 HxWx3）、深度（float32 米）、内参、`world_from_camera`、帧身份、标定修订、关节、底盘位姿
- **格式**：目录 = `manifest.json` + `index.jsonl`（每帧一行）+ `rgb.bin` + `depth.bin`。追加式、可流式读，不把日志读进内存
- **每帧摘要 + 整日志摘要**：复放两次必须一致，否则两次对比喂的不是同一批帧
- **拒绝而非降级**：帧被改动 → `LOG_CORRUPT`；被删除 → `LOG_FRAME_COUNT`；截断 → `LOG_TRUNCATED`；schema 不符 → `LOG_SCHEMA`

### 冻结基准

```
artifacts/replay/furnished-home/
  371 帧 · RGB 85.5 MB · 深度 114.0 MB
  机器人 xlerobot-mujoco-tabletop · 标定 c93b4e4d6b05
  日志摘要 8421d1927e18b8de
```

由 `scripts/record_sim_sensor_log.py` 沿**真实巡检轨迹**（`scan-6974b8f937e9`）驱动仿真渲染器产出——不是绕玩具房间的玩具路线，是产生那张被讨论的地图的实际路径。

**确定性已验证**：同一轨迹录两次，逐帧逐字节一致。

---

## 四、第一次离线 A/B（本轮兑现）

`scripts/replay_sensor_log.py`，同一份冻结日志，**只换建图规则一项**：

| 变体 | 已知格 | 占据 | 未知 | 参考地面命中 |
| --- | --- | --- | --- | --- |
| `points-only`（只标有点落进的格子） | 26705 | 3544 | 33.2% | **74.0%** |
| `ray-filled`（射线经过即自由） | 32249 | 3544 | 19.4% | **87.4%** |

**+13.4 个百分点**，离线几秒算完。这与上一轮纯几何估算的 +16.8 pp 同向、更保守，**独立交叉验证**了那个结论。

这就是这套东西存在的意义：一个建图层改动，从"跑二十分钟机器人还不知道有没有用"变成"几秒钟给出可比数字"。

---

## 五、还没做的部分（明确记录）

- **L4 未抽**：`robot_workflow.py` 里 401 行探索逻辑（`_explore_leg` 143 行、`_live_grid` 41 行、`_blind_mask` 33 行等）仍在 workflow 类内。**在那之前，"优化探索模块"仍然要碰一个 1395 行的文件。**
- **MCP 未接探索**：仓库**已有** MCP 服务（`robot/mcp/tangying_mcp/server.py`，371 行），
  但它是**车队控制面**的桥：8 个操作全部指向 `/v1/devices`、`/v1/world`、`/v1/tasks`
  （`server.py:158-190`），**不含任何机器人建图/探索操作**。而巡检今天是通过 gRPC
  `CallService` 调 `mapping.start` 触发的。所以第 3 步不是"建一个 MCP"，而是
  **给已有的桥增加一条通往机器人建图服务的路径**——那需要先有 L4 的窄接口，
  否则桥上挂的还是一次调用里跑完二十多分钟的会话状态机。
- **日志只覆盖建图输入**：定位/导航阶段（RTAB-Map ↔ Nav2）没有等价的复放装置。
- **真机日志未采**：冻结基准来自仿真渲染器。真机路径（ROS `rgbd_bridge`）还没有接 `SensorLogWriter`。

---

## 六、给后续模块优化的使用方式

```bash
# 录一份新日志（仿真，沿某次真实轨迹）
python scripts/record_sim_sensor_log.py --scan <scan-dir> --output artifacts/replay/<name>

# 离线复放 + 对比（同一输入，改一个变量）
python scripts/replay_sensor_log.py artifacts/replay/<name> --compare-origins

# 校验日志身份（对比前跑一次，确认两次喂的是同一批帧）
python -c "import sys;sys.path.insert(0,'robot/gateway');\
from tangying_robot_gateway.sensor_log import SensorLogReader;\
print(SensorLogReader('artifacts/replay/<name>').verify())"
```

**约定**：任何模块改动的对比，必须
1. 在同一份日志上跑（摘要写进对比结论里）；
2. 用**端到端**指标验收（覆盖率 / 位姿误差 / 任务成功率），per-module 指标只作诊断。

第 2 条不是形式。上一轮的教训：探索策略在理想执行下已经 99%，系统实际 62%，瓶颈在建图层——我为此改了三次探索算法，三次被测试挡回。**按模块各自优化最典型的失效方式，就是每个模块对着自己的代理指标变好而系统不动。**
