# 外部 SE3 测量接入与空间地图包

`robot/gateway/tangying_robot_gateway/spatial_mapping.py` 将外部已经注册到世界坐标系的测量转换为可验证的三维导航地图。它保存完整 SE3 位姿、真实传感器光心、测量来源和版本，提供有界体素化、证据回放及不可变发布。

这不是新的 SLAM 估计算法。RTAB-Map、LIO、VIO 或设备定位适配器仍负责配准、闭环、坐标变换和定位质量。原有 `DenseSlamProvider` 与 `RobotWorkflow` 保持 SE2 限制；无人机的三维测量不能传给旧工作流后丢掉高度或倾斜角。

## 坐标与身份

每个 provider 实例只接受一个机器人、地图和注册基准。输入帧采用 `slam.registered-frame.v1`：

| 字段 | 含义 |
| --- | --- |
| `robotId`、`mapId`、`mapRevision` | 外部注册结果所对应的机器人、地图及冻结修订 |
| `frameId` | 固定 `world`；单位为米，右手坐标系，z 向上 |
| `worldFrameRevision` | 已部署的世界坐标基准/epoch；重启导致原点变化时必须更新 |
| `calibrationRevision` | 传感器标定和转换基准 |
| `sourceId`、`sourceType`、`registrationRevision` | 传感器来源及外部配准版本；类型为 `rtabmap/lio/vio/external/fixture` |
| `observationId`、`sequence`、`observedAtUnixMs` | 不可复用观测 ID、来源内递增序号及 Unix 毫秒采集时间 |
| `pose` | 同次采集的机器人位姿，七元 `[x,y,z,qw,qx,qy,qz]` |
| `sensorOrigin` | 世界坐标中的真实传感器光心 `[x,y,z]` |
| `points` | 同次采集、已注册到同一世界坐标的障碍命中点 `[[x,y,z],...]` |

ROS 的四元数字段顺序是 `x,y,z,w`，适配器必须显式转换为这里的 `w,x,y,z`。本模块不自动用底盘位置代替传感器光心，也不猜测 map→odom→world 变换。输入点应经过设备适用的自体、无效深度及动态物体处理；不能把累计全局点云配给一个新光心，伪造测量射线。

一个来源的序号、采集时间必须严格递增；来源类型或注册版本变化也会被拒绝。闭环修正造成坐标变化时，应从外部估计器导出一致的新注册结果，建立新 provider 和新地图修订。历史恢复只是重放证据，不会使旧观测重新成为新鲜定位。

## Python API

下面是人工测量的合同示例，仅用于理解字段，不代表机器人实测。

```python
from tangying_robot_gateway.spatial_mapping import (
    RegisteredSpatialSlamProvider, load_spatial_map,
)

provider = RegisteredSpatialSlamProvider(
    robot_id="uav-01", map_id="warehouse-a", map_revision="slam-solution-17",
    world_frame_revision="world-epoch-3", calibration_revision="lidar-calibration-4",
    resolution=0.25, origin=[0, 0, 0], shape=[40, 40, 20],
    max_measurement_age_ms=60_000,
)
provider.add({
    "schemaVersion": "slam.registered-frame.v1", **provider.identity,
    "sourceId": "lidar-front", "sourceType": "fixture",
    "registrationRevision": "recorded-registration-1",
    "observationId": "capture-1", "sequence": 1, "observedAtUnixMs": 1000,
    "pose": [0.4, 0.4, 1.0, 1, 0, 0, 0],
    "sensorOrigin": [0.6, 0.4, 1.1],
    "points": [[3.1, 0.4, 1.1], [3.1, 1.4, 1.1]],
}, now_unix_ms=1000)  # 显式历史时钟，仅用于离线回放

manifest = provider.publish("/tmp/spatial-map-example")
verified = load_spatial_map("/tmp/spatial-map-example")
assert verified["volume"] == provider.volume()
```

`provider.measurements()` 返回可供 CLI 或 `RegisteredSpatialSlamProvider.restore()` 使用的完整历史文档。`provenance()` 返回逐观测的位姿、光心、来源、点数及点集哈希。`volume()` 返回 `navigation.volume.v1`，数组顺序为 x 最快、然后 y、然后 z，即索引 `(z*ny+y)*nx+x`。

默认上限：250,000 个体素、10,000 帧、1,000,000 个输入点、10,000,000 次射线体素访问；每份磁盘输入/产物最多 128 MiB。调用者可降低帧、点和射线预算，不能提高固定上限。光心和端点必须位于显式配置的体素范围内；越界直接拒绝，不静默裁剪。过小或过大的分辨率被拒绝，范围为 0.01–10 米。

## 空闲空间的证据

体素初始值为 `-1`，代表未知。所有命中端点先标记为 `100`，代表占用；随后只将实际测量射线穿过的体素标记为 `0`，代表已观测空闲。遇到更近的已占用体素时停止射线，不能清出障碍物后方。所有帧的占用端点优先于任何空闲标记，因此输入顺序不会将薄障碍擦除。

射线恰好从体素面、棱或顶点向负方向出发时，起点处零长度接触的体素不会被清空；只沿体素面运行的射线也不认证相邻体素内部空闲。即使机器人曾经位于某处，也不会额外清空其碰撞体或整层高度。稀疏射线通常留下大量未知体素，这是证据不足，不是可自动填充的导航区域。

这是静态、保守的占用累积，旧占用不会自动消失。动态场景应由外部地图维护器产生新的注册修订，并由控制器继续检查实时障碍。单个命中点或射线不是完整机器人可通行性证明；空间规划器还必须检查实际配置的碰撞包络。

可选 `terrain=` 使用独立 `navigation.terrain.v1`：每个 XY 单元显式给出 `heights`、`supported`、`slopeRadians`，并携带来源及时间。未知支撑必须为 `false` 且高度、坡度为 `null`。本模块不从一般表面点云推断地面支撑或落脚稳定性。地形数据会加入地图包与部署内容哈希，但其物理正确性仍由地形来源负责。

## 发布、恢复与部署

新目录包含四个文件：

- `volume.json`：导航体素及可选地形。
- `measurements.json`：可重放的完整注册测量。
- `provenance.json`：测量来源、SE3 位姿、光心、时间及点集哈希。
- `manifest.json`：上述文件的长度、SHA256 和包内容修订 `revision`。

`mapRevision` 是外部估计器的冻结地图修订；`manifest.revision` 是本地地图包的内容哈希，两者含义不同。发布先在同目录下建立临时完整包，对文件和目录执行 fsync，再原子重命名。重复发布完全相同的包可以成功；已有不同内容的目录拒绝覆盖。并行发布使用独占锁文件；若进程异常终止留下 `.目录名.publication.lock`，应先确认无发布进程且检查目录完整性，再由运维清理该锁，不自动抢锁。

`load_spatial_map()` 不止验证文件哈希，还重放 `measurements.json`，检查体素与 provenance 确实可以从这些输入推导出来。读入限制同时作用于文件大小和实际读取字节数，拒绝软链接、非常规文件、重复 JSON 键、NaN 及超预算输入。内容哈希是完整性校验，并不是签名或来源认证；现场仍需保护文件权限及来源传输。

部署应先通过该加载器验证自产地图包，再把体素、定位合同、导航策略、机器人 Profile 一起绑定进部署修订。直接编辑 cells 后保留旧 mapRevision 不是可接受的地图更新。单独来自第三方的 volume 文件必须被明确标为外部已验收体素，不能宣称具备本模块的射线测量证明。

## CLI

先将 `provider.measurements()` 的返回值存为 JSON，再执行：

```bash
.venv/bin/python scripts/build_spatial_map.py \
  --input /data/registered-frames.json \
  --output /data/maps/warehouse-a-r17
```

默认以当前 Unix 时间检查测量新鲜度。论文复现实验可使用明确的历史时钟，且应在实验记录中保留该参数：

```bash
.venv/bin/python scripts/build_spatial_map.py \
  --input /data/recorded-frames.json \
  --output /data/maps/recorded-r17 \
  --reference-time-unix-ms 1000
```

`--terrain /data/terrain.json` 可加入独立测得的地形层。CLI 只构建本地文件，不连接硬件、不驱动机器人；失败返回码为 2。终端结果包含包修订、外部 mapRevision、帧数、所选时钟与 `hardwareValidated:false`。

## ROS2 流式采集入口

`tangying_ros_gateway.spatial_mapping_node` 提供 `spatial_mapper` 可执行入口。它通过 ROS2 DDS 接收同一 `slam.registered-frame.v1`，直接调用上面的 provider；不注册导航 Action，不发送速度命令、不 arm 或解除停止。该入口使真实外部 SLAM 适配器可以连续提交注册测量，而不必先手工生成离线 JSON。

将以下配置写到绝对路径，例如 `/etc/tangying/spatial-mapping.json`。`outputDirectory` 必须是尚不存在的新地图包目录：

```json
{
  "schemaVersion": "slam.spatial-mapper.v1",
  "robotId": "uav-01",
  "mapId": "warehouse-a",
  "mapRevision": "slam-solution-17",
  "worldFrameRevision": "world-epoch-3",
  "calibrationRevision": "lidar-calibration-4",
  "resolution": 0.25,
  "origin": [0, 0, 0],
  "shape": [40, 40, 20],
  "sourceIds": ["lidar-front"],
  "outputDirectory": "/var/lib/tangying/maps/warehouse-a-r17",
  "maxMeasurementAgeMs": 60000,
  "maxFrames": 100,
  "maxMessages": 200,
  "maxRejected": 10
}
```

构建和加载 ROS2 工作区后启动：

```bash
export TANGYING_SPATIAL_MAPPING_CONFIG=/etc/tangying/spatial-mapping.json
ros2 run tangying_ros_gateway spatial_mapper
```

节点依赖 `tangying_robot_gateway` Python 包，与已有 Profiled Gateway 使用同一安装环境。源码开发时需要将仓库 `robot/gateway` 加入 `PYTHONPATH`。节点默认名称为 `tangying_spatial_mapper`，默认接口如下；配置可以通过 `namespace`、`nodeName`、`frameTopic`、`saveService`、`statusService` 修改名称：

| 接口 | ROS2 类型 | 行为 |
| --- | --- | --- |
| `spatial_mapping/registered_frames` | `std_msgs/msg/String` | `data` 是一个完整注册帧 JSON；Reliable、KeepLast(8) |
| `spatial_mapping/status` | `std_srvs/srv/Trigger` | 返回状态、总消息数、接受/拒绝帧数、最后错误和包修订 |
| `spatial_mapping/save` | `std_srvs/srv/Trigger` | 冻结当前已接受测量，原子发布新地图包 |

适配器每次只发布本次采集的命中点与该次实际光心。来源须在配置的 `sourceIds` 内，最多 32 个，不能靠消息自行注册新设备。节点默认每条 JSON 上限 2 MiB，配置最大 8 MiB；总消息数最大 100,000，拒绝数最大 10,000。达到总消息或拒绝预算后进入 `faulted`，停止采集并拒绝发布；这是可观测的资源故障，不会解除任何执行限制。单条坏帧未达到预算时被记录和拒绝，其后同一正确来源的新鲜帧仍可继续采集。

保存前查询状态并调用服务：

```bash
ros2 service call /spatial_mapping/status std_srvs/srv/Trigger '{}'
ros2 service call /spatial_mapping/save std_srvs/srv/Trigger '{}'
```

空会话不能保存。保存成功后会话进入 `saved`，后续消息不再改变该地图；重复 save 返回相同包修订。磁盘发布失败进入 `save_failed`，仍冻结原快照，允许恢复存储后重试同一快照。新一轮扫描必须使用新的输出目录和一致的外部注册版本重新启动，不自动追加或覆写旧包。

也可进行有界采集，收到 N 个**接受成功**的帧后自动保存退出：

```bash
ros2 run tangying_ros_gateway spatial_mapper --frames 100 --timeout-seconds 120
```

超时返回失败，绝不把不完整等待称为达到指定采集帧数。在线节点使用 Unix 毫秒验证采集时间，启动时和运行中都拒绝 `use_sim_time=true`。Gazebo 或 rosbag 的仿真时间必须由明确记录时钟映射的外部适配器转换，或使用上面的离线 CLI 显式历史时间机制，不能把 `/clock` 秒数直接伪装成 Unix 时间。

DDS 仍需部署适用的网络隔离、SROS2 身份与资源限制。JSON 校验和 `sourceIds` 是内容接入约束，不能认证一个有权限向该 topic 发布消息的恶意参与者；2 MiB 限制发生在接收后的应用边界，不能代替 DDS 中间件的接收预算。这个节点没有新增原始 RGB-D/LiDAR→SLAM 估计算法，也不推断 TF。RTAB-Map/LIO/VIO 适配器负责时间同步、传感器自体过滤、世界坐标注册、真实光心转换以及闭环修正后的版本更新。

`tests/contract/ros2_spatial_mapping_smoke.py --output /tmp/spatial-mapper-smoke` 在已加载 ROS2 的环境中执行实际 DDS 测试：子进程采集器先拒绝空保存及未登记来源，再从记录式房间表面测量生成地图包，重放校验后生成改变高度的三维路线；分别覆盖 Trigger 保存和 `--frames` 自动保存。保存日志、发布帧、包文件、路线及实验摘要，明确标记无硬件验证。

## 回归与实验边界

`robot/gateway/tests/test_spatial_mapping.py` 验证高度和倾斜位姿保留、光心与底盘分离、未知空间、近处障碍遮挡、射线边界、重放及版本漂移拒绝、预算、包损坏、即使重算哈希也无法伪造体素推导、恢复、原子重复发布和 CLI 历史回放。`tests/contract/test_ros2_spatial_mapping_node.py` 验证采集器来源、流量预算、冻结保存和存储失败重试；该 CPU 测试不替代实际 DDS 测试。

这些测试证明合同与几何证据处理行为，不证明定位误差、闭环质量、无人机动力学、足式落脚稳定或实机安全。对应指标应在 ROS2 传输测试、控制器仿真和实机实验中分开记录。
