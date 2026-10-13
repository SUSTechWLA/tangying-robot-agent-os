# 扩展 SLAM Provider

[开发入口](README.md) · [架构与能力边界](../architecture/modular-extensibility.md) · [本轮变更](2026-10-13-modular-extensibility.md)

本接口让建图工作流不再依赖 DenseSLAM 的内部字段。它是**同一进程内的平面 RGB-D 测量会话端口**，不是任意 ROS SLAM 的网络插件协议，也不是三维导航接口。源码为 [`slam_provider.py`](../../robot/gateway/tangying_robot_gateway/slam_provider.py)，当前适配器为 [`dense_slam_provider.py`](../../robot/gateway/tangying_robot_gateway/dense_slam_provider.py)。

## 1. 先选择正确的扩展层

- 若替换同类 RGB-D 测量算法，实现这里的 `SlamProvider`，由驱动组装处传入 factory。
- 若导入已有地图，优先使用标准 `map_pipeline` / `MapCatalog` 制品与校验路径，不把历史文件伪装成新采集。
- 若替换实际 RTAB-Map / Nav2 侧车，另实现定位来源、质量、地图状态、导航目标与取消合同。当前 `robot/ros2_ws/src/tangying_navigation` 仍显式使用 RTAB-Map 消息和来源判据；实现这里的 Python Provider 不会自动替换那些接线。
- 若使用 LiDAR、VIO、倾斜底盘或 SE(3)，走独立的 [注册空间测量](spatial-mapping.md)与[完整定位/空间导航](spatial-navigation.md)合同；外部算法负责位姿估计、同步、外参和坐标变换。不能修改 observation_kind 字符串让 SE3 输出进入平面工作流。

## 2. Provider 交付什么

`RobotWorkflow` 通过显式 `slam_factory` 创建每个新会话。factory 必须返回一个空实例；驱动保留具体算法选择和经过测量的参数。默认 Dense 参数仍为 `odometry_sigma=(.025,.025,.015)`，未声明绝对里程计的设备不注入绝对位姿因子。

| 接口 | 责任 |
| --- | --- |
| `info: SlamProviderInfo` | 有界 provider ID/version；`registered_rgbd`、`se2`、`driver_world`、`metres`；记录地图 source 类别 |
| `frame_count` / `max_frames` | 便宜的会话计数和预算；每个 factory 产物从空会话开始 |
| `add(observation) -> bool` | 验证并整合同一次采集；返回是否接受新关键帧，重复/不合格测量不能变成新证据；可恢复的深度不足使用 `InsufficientDepthError` |
| `progress() -> SlamProgress` | 计数、实测行程、最新实测里程计、轨迹和最多 3000 个点的预览；无需构建完整点云 |
| `optimize()` | 优化当前会话估计；不操作设备、不修改已发布地图 |
| `snapshot() -> SlamSnapshot` | 按需返回独立、可校验的几何和来源快照，优化后旧快照不应被实现内部数组改写 |
| `keyframe_document(...)` | 返回有真实采集来源的关键帧预览文档；无图像则返回 `None`，不可补造图片 |

关键数值语义：

- `poses`、`odometry` 为每次接受测量的 `[x,y,yaw]`，`trajectory` 为对应的 `[x,y,z]`；轨迹 x/y 与估计位姿相同。
- cloud 点和上述位姿在驱动稳定的 world 坐标系中。保存地图的锚点转换由工作流完成；provider 不提前应用 `mapFromWorld`。
- `sources[i]` 是第 i 个点来自的当前会话观测索引，长度等于点数，范围在 `[0, frame_count)`。继承地图的特殊来源由工作流管理，不在 provider 快照中返回 `-1`。
- `timestamps` 为严格递增的真实接受测量 Unix 毫秒，不能用读取或保存时间刷新历史证据。
- `provenance` 使用 `slam.session.v1`，记录真实算法和测量诊断；工作流补充 provider 身份、机器人/地图/标定、world revision、锚点和导航证据版本。
- 点、位姿必须有限；颜色若存在，逐点对齐且在 0～255 范围。长度、坐标模型、来源范围、非有限数值等非法快照在写地图之前拒绝。

工作流串行调用 provider 的采样与快照方法。此端口固定本会话 provider 身份，不允许采样后整体替换 info。此端口不提供任意后台写入的并发一致性，也不管理外部 ROS 进程；外部算法须自己确保快照原子性和生命周期。

## 3. 接线方式与迁移

当前组装位置：

- Gazebo：[`gazebo_workflow.py`](../../robot/gateway/tangying_robot_gateway/gazebo_workflow.py)。保留该设备现有 odometry 参数和绝对锚定声明。
- MuJoCo：[`workflow_services.py`](../../sim/mujoco/tangying_sim/workflow_services.py)。保持原有模型和经过校验的捕获/运动端口。
- 分析脚本：[`evaluate_semantic_cloud.py`](../../scripts/evaluate_semantic_cloud.py)。明确使用参考 provider；脚本回放结果不算真机建图。

迁移现有 Python 调用者：

```python
from tangying_robot_gateway.dense_slam_provider import DenseSlamProvider

# 在现有 RobotWorkflow(...) 参数中显式添加：
# slam_factory=DenseSlamProvider
# 有设备专属参数时使用返回全新实例的 lambda/factory。
```

原来 `slam_factory=DenseSLAM` 的自定义调用也需要改为适配器。旧的 `frames`、`registrations`、`loops`、`previews` 和 `MAX_FRAMES` 不属于新端口；参考算法自身仍保留它们供算法内部和专门测试使用。不要在工作流中添加 `isinstance(provider, 某厂商)` 分支，也不要通过 `__getattr__` 偷渡内部状态。

新增实现先复制测试的**结构**，替换为真实算法与真实测量，不能将合成 fixture 用于设备部署。契约样例 [`test_slam_provider.py`](../../robot/gateway/tests/test_slam_provider.py) 的 `RecordedPlanarProvider` 不继承/创建 DenseSLAM，能经过采样、实时网格、保存、哈希校验和地图启用，证明调用方可以替换实现；它不证明新 SLAM 算法精度。

## 4. 每个新实现如何验收

1. 拒绝错单位、错坐标模型、非有限位姿、点来源越界、错时序和版本混用。
2. 验证相同测量不会增加证据，优化后既有快照保持；取消、构造失败和会话重置不留下占用。
3. 使用同一受控输入，核对点云、关键帧、来源、地图摘要与旧实现；先确认行为一致，再讨论性能。
4. 在对应实际引擎/设备上验证地图保存/启用、重定位、导航、定位失效、地图版本变化和动作后证据。
5. 若输出能进入语义执行，另跑语义绑定、版本冲突、恢复不重复执行与全部失败记录。

仓库内回归入口：

```bash
.venv/bin/python -m pytest -q \
  tests/architecture/test_mapping_dependencies.py \
  robot/gateway/tests/test_slam_provider.py \
  robot/gateway/tests/test_robot_services.py \
  robot/gateway/tests/test_gazebo_workflow.py
```

保存新的 provider provenance 会让新地图制品得到新的内容摘要，这是预期身份变化。既有地图不改写，不给旧图补造 provider metadata；当前读取端继续读取原有地图包。算法/地图切换时核对任务冻结的地图和语义版本，失效的候选重新规划与批准，不能直接更新 token 继续运动。

## 5. 续建地图的证据修正

本轮同时修复已有续建逻辑：新云和新轨迹先转换到 map 坐标，继承制品保持原坐标；当前点来源仅对应当前采集起点。继承点 `source=-1` 不参与当前传感器的射线清障，避免负索引借用最后一个起点把未知单元误判为空闲。

GeoJSON 合并轨迹的 `timesUnixMs` 与坐标等长；旧制品有合法且长度匹配的采集时间就保留，缺失或不可恢复的历史时间标为 `null`，不能复制本次时间填充。现有坐标查看器只读取几何，支持这个未知值；新写入器拒绝长度错配。原始地图不改写；使用旧版本续建得到的地图时，应重新核对几何/导航证据或重新建图，软件升级不会修正旧制品内的错误。修复前后的独立回归与范围见[变更记录](2026-10-13-modular-extensibility.md)。

## 5. 平面与空间 Provider 的选择

| 数据与目标 | 接口 | 接入责任 |
| --- | --- | --- |
| 平面 RGB-D 建图和原 RobotWorkflow | SlamProvider / DenseSlamProvider | SE2 会话快照、来源和原地图包 |
| 已经在共同 world 注册的三维测量 | RegisteredSpatialSlamProvider / slam.registered-frame.v1 | 外部 SLAM 估计位姿与版本，提供实际光心和命中点；ROS spatial_mapper 采集/发布 |
| 完整空间定位和运动 | spatial.localization.v1 / spatial.deployment.v1 | 与地图/世界/标定匹配的定位质量；体素/地形规划和控制器路径回执 |

两条建图通道并存，不互相隐式投影。空间包保留原测量并能重放验证摘要与体素推导；loop closure 或坐标校正应形成新地图/世界修订，再重新准入与规划。接口字段完整不等于估计误差小，ATE/RPE 与地图质量需单独测量。
