# 从测量估计 SE3：标定、跨机注册与拒绝条件

实现位于 `robot/gateway/tangying_robot_gateway/registered_alignment.py`。相机内参及手眼标定继续由 `calibration_solver.py` 负责，平面 RGB-D 里程计继续由 `dense_slam.py` 负责。本模块补充测得三维点之间的刚体注册，不发布 TF，不改变已激活地图，也不授权运动。

## 输入与坐标方向

所有点采用米制 `N × 3` 数组，数量有界。`sourceToTarget` 是把 source 坐标变为 target 坐标的正规 `4 × 4` SE3 矩阵：

```python
target_position = transform_points([source_position], result["sourceToTarget"])[0]
target_velocity = np.asarray(result["sourceToTarget"])[:3, :3] @ source_velocity
```

位置应用旋转和平移，速度只应用旋转。输入证据必须包含 `robotId`、`frameId`、`mapId`、`mapRevision`、`sourceId`、`observationId`、`observedAtUnixMs`。结果增加点数据 SHA256、算法版本、参数、残差、支撑点数量、几何条件指标与内容 revision。`qualityScore` 是几何质量分数，不是标定后的概率。

## 两种估计入口

`solve_corresponded_alignment` 接收已识别的三维点对应关系，使用 RANSAC 与 Kabsch 求解。标定时 source 可以是传感器实测靶标坐标，target 必须是独立测量的已知靶标位置。至少需要六个不同的、满足三维几何条件的内点。重复点不能增加独立支撑，反射不能冒充旋转。

`register_point_clouds` 接收两个无对应关系的点云，不接受初始真值位姿。它从完整点云、稳健中心子集的 PCA 轴及测得的主平面交点提出多个初值，执行粗到细、互为最近邻的截尾 ICP。最终门控仍使用全部输入点作为重叠率分母。

对主平面占多数且还包含足够小结构的环境，另检查去掉主平面后的结构支撑，避免只对齐墙和地面就接受错误朝向。稀疏房间曾出现墙面对称造成的 180° 错解，真实 Gazebo 测量已保存为测试夹具 `robot/gateway/tests/fixtures/field_registration_sparse_room.json.gz`。

线状或平面退化、不足重叠、过大残差、支撑数量不足以及近似等价的不同解都会拒绝。`accepted=false` 时，顶层 `sourceToTarget` 为 `null`。这仍不是任意场景下的全局定位算法；不同位置的相似建筑可以产生相似几何，调用者还需要独立的地点身份及地图版本准入。

显式设置 `return_candidates=True` 可返回有限个 `nonExecutableCandidates`，用于后续独立验证。它们不是可直接执行的变换，也不授予地图激活权限。

## 使用前的版本和时间检查

```python
matrix = validate_alignment_for_use(
    result,
    source_basis=trusted_peer_basis,
    target_basis=trusted_local_basis,
    now_unix_ms=now,
    max_age_ms=60_000,
)
```

双方 basis 精确匹配机器人、坐标系、地图及地图修订。环闭合引起地图修订变化、旧测量、未来时间、结果内容被修改或拒绝的估计都会报错。内容哈希只检测完整性，不证明发送者身份；跨机传输仍需已有认证与任务范围检查。

## Gazebo 已知场景标定工具

`sim/gazebo/field_calibration.py` 从 SDF 的 **static 碰撞几何** 单独生成 surveyed CAD 参考点云，忽略所有机器人、传感器位姿和动态模型。实际输入来自 `gz.msgs.LaserScan` 的距离与角度；解码不读取 `worldPose`。

流程如下：

1. 在明确清空近距区域的静止标定位，可声明近场排除距离，并保留原始数据、排除数量和射线索引。当前实验使用 0.6 m，处理返回自身机体的射线。该过滤不能用于运动避障输入。
2. 固定种子将测量拆成拟合子集和 20% 验证子集，记录索引；超出拟合预算的点单独记为未使用。
3. 点云注册提出有界粗候选。每个候选仅在拟合子集上按解析 CAD 表面距离进行稳健优化和评分，检查近似等价候选。
4. 最终候选在未参与拟合及选择的验证子集上检查全体残差和小结构残差。动态目标离群点保留在统计分母中。
5. 通过时返回 `estimatedSensorToWorld`。只有调用者另外提供独立测量的静止标定位 `surveyed_base_to_world`，才返回 `estimatedSensorToBase`。

这是已知场景的传感器位姿／外参估计，不声称在陌生环境中完成标定，也不估计激光雷达距离尺度、束角、时间延迟或相机内参。粗候选的重叠率和点到点距离与最终 CAD 表面验证具有不同含义，不能把粗候选通过视为标定完成。

## 离线验收与复现

```bash
PYTHONPATH=robot/gateway:. .venv/bin/python -m sim.gazebo.validate_field_alignment \
  --snapshot /absolute/path/to/sensors.json \
  --world sim/gazebo/field_validation.sdf \
  --survey-plan /absolute/path/to/predeclared-plan.json \
  --output /absolute/path/to/new-evidence-directory
```

该命令只读取测量，不控制模拟器，且拒绝覆盖已有目录。输出保存输入快照、独立 CAD、冻结源码、每台机器人的标定结果、跨机注册结果和文件摘要。所有估计写完之后，才另行访问仿真状态与安装位姿生成 `truth-evaluation-only.json`。状态和传感器的时间差会明确记录；不同时间的真值比较不是严格同步的精度测量。

`--survey-plan` 可省略；使用时读取采集前冻结的 `gazebo.independent_calibration_capture_plan.v1` 文档中的 `surveyedBasePoses`，检查世界文件摘要和声明时间。它只在估计之后将 sensor→world 换算为 sensor→base，不提供求解初值，也不读取计划中的配置传感器外参。此处的 survey 是预先布置的仿真标定位，不是机器人自主测得的未知机体安装关系。

离线脚本的 `observedAtUnixMs` 来自 hub 快照采集的墙钟时间；原始传感器采集时间仍是快照中的 simulation stamp。二者不能互相替代，不应把这份离线墙钟元数据直接当作实机传感器新鲜度证明。在线运动流程需要按实际采集时钟完成同步和超时检查。

同一快照的算法开发、参数比较和重复处理只算一个数据集。被拒绝的结果与错误开发结果都应保留；实际控制是否使用估计必须另行记录，不能把离线注册通过写成估计已驱动机器人完成任务。

每次拟合的 20% 保留点不自动构成独立科研测试集：如果开发过程已观察同一快照的失败或保留集结果，该快照只能作为开发数据。评估泛化需要冻结方法之后另外采集新数据，并保持方法与验收门槛不变。

## 同一已知 CAD 锚点的跨机关系

`sim/gazebo/field_shared_anchor.py` 提供独立的 `compose_shared_cad_anchor`。它仅用于已知、独立测绘的同一场景：两份标定都必须被接受，且场景修订、参考点云摘要、锚点地图／坐标系以及每个来源机器人的地图版本、传感器来源和观测身份必须匹配调用者指定的身份。未通过标定、不同地图、错误坐标系或不同参考点云均拒绝组合。

设两台机器人传感器到共同世界坐标系的估计分别为 `world_T_A` 与 `world_T_B`，则 `A_T_B = inverse(world_T_A) @ world_T_B`。该计算只使用两份已接受的估计，不使用模拟器机器人位置或配置传感器安装参数。它既不重新拟合，也不改变直接点云注册的结果；函数不会发布 TF、激活地图或授权运动。

```bash
PYTHONPATH=robot/gateway:. .venv/bin/python -m sim.gazebo.field_shared_anchor \
  --alignment-directory artifacts/experiments/gazebo-measured-alignment-20261018-003 \
  --output /absolute/path/to/new-shared-anchor-evidence
```

工具先验证上游证据清单中的文件摘要，再新建输出目录。`fallback-registration.json` 记录共同锚点、输入标定摘要、来源文件摘要和派生变换；原始 `peer-registration.json` 的拒绝状态保留。派生文件写入以后才生成单独的真值评价。内容摘要用于回溯和完整性验证，不能代替跨机身份认证。

这条路径依赖共同已知地图：它不能证明两台机器人在无先验陌生环境中直接完成了地图对齐。机器人若在采集之后运动，还必须结合各自当前里程计、时钟、地图修订和已验证外参，把扫描时刻的传感器关系转换到当前地图与机体关系，不能把离线矩阵当成持续有效的机器人相对位姿。

## 2026-10-18 冻结与独立采集结果

证据 `gazebo-measured-alignment-20261018-002` 使用开发时已见过的扫描，三台已知 CAD 标定和一次直接跨机注册均通过；这不是独立泛化测试。随后冻结注册与标定估计器源码，在同一场景的新标定位采集独立扫描，得到 `gazebo-measured-alignment-20261018-003`。采集计划先于快照冻结，预声明机体标定位仅用于把估计出的 sensor→world 换算为 sensor→base。

| 独立采集项目 | 结果 | 事后评价／拒绝原因 |
| --- | --- | --- |
| ground-a 外参 | 通过 | 对配置安装参数平移差 1.234 mm、旋转差 0.001097 rad |
| ground-b 外参 | 通过 | 平移差 0.443 mm、旋转差 0.000073 rad |
| uav-a 外参 | 通过 | 平移差 2.120 mm、旋转差 0.000938 rad |
| ground-b→ground-a 直接点云注册 | 拒绝 | RMSE 0.160232 m 超过 0.16 m，小结构重叠率 0.278351 低于 0.55 |
| 同一已知 CAD 锚点派生 | 通过独立准入 | 平移差 3.496 mm、旋转差 0.001100 rad；原始直接注册仍拒绝 |

最后一行保存在独立目录 `gazebo-measured-alignment-20261018-003-shared-anchor`，没有新增采集，也不算新增独立实验。此批扫描与用于事后评价的状态相差 100 ms，表内数值是该条件下的仿真比较，不是同步测量精度或硬件精度保证。上述所有估计均未用于控制机器人；在线任务的定位来源需要另见运行报告，不能由这些离线结果推断。
