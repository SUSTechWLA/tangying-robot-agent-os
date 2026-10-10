# Gazebo 抓取接近路径预检修复（2026-10-10）

## 实际失败与只读证据

`run-002/final-task.json` 中任务 `task-79f7e1765770f7b7de3e61db` 已完成建图和
卧室、卫生间、客厅巡检，在厨房抓取步骤停止为 `RECOVERABLE_FAILURE`。第 305 条
事件记录 `plan_grasp` 成功；第 315 条事件记录 `manipulation.pick` 返回
`GRASP_TARGET_UNREACHABLE`；第 318 条事件保留上层物理子任务的
`UNKNOWN_OUTCOME` 和 `automaticRetryForbidden=true`。不能根据后续离线修复
抹掉这个原始结果或授权旧任务自动重放。

本次调查只调用 `GetRuntimeInfo`、`Observe`，并读取现有 Runtime journal 的命令
事件 blob，没有 ExecuteSkill、关节命令、物体位姿写入、服务重启或世界复位。
新证据位于 `artifacts/acceptance/long-horizon-20261010/grasp-diagnosis-002/`：

- `prior-command-events.json`：原 navigate、verify_arrival、plan_grasp 的持久 protobuf 事件。
- `prior-command-decoded.json`：上述终态证据的可读字段，剔除图片与点云以便审阅。
- `pick-journal-events.json`：原失败抓取的持久事件。
- `head-raw-observation.json`、`head-observation.json`：失败后新只读观察，不能替代原命令证据。
- `offline-reproduction.json`、`approach-candidates.json`：明确标注为离线运动学分析。

原导航与 plan_grasp 的捕获位姿为
`[2.0513445513, 3.0002529953, 0.035, 0.7667298994, 0, 0, 0.6419698290]`。
相对于厨房 goal 的平面位置误差为 1.368 毫米，朝向差为 0.02667 弧度；原到位检查
确实返回 `OK`。新地图 `scan-cbeb53aa8e01` 的语义厨房目标与这个 goal 一致。
这些证据不支持将本次问题归咎于明显的地图坐标错位或 Nav2 未抵达。

原感知杯子坐标为 `[2.4515001112, 3.3337741998, 0.7909868361]`，关系已经是
`inside:kitchen-tray`。现场保留了之前的杯子位置；本次没有把杯子移回测试 fixture
的桌面初始位置，也没有因“已在盘中”而跳过请求的实际拿起、验证和放下。

## 复现出的代码缺口

`gazebo_manipulation.py` 原 `plan_grasp` 只调用 IK 检查接触、抬升、观察和放置
端点；`pick` 的实际接近却使用若干每段 4 厘米的 Cartesian 插值。目标端点可达，
并不意味着从折叠臂到该点的每个 Cartesian 插值都有连续可达的关节解。

离线使用原 journal 捕获的 joints、物体位置和公布的平面 base pose，可复现：
左侧折叠工具位置约 `[2.18336, 2.88621, 1.21560]`，预抓点约
`[2.45150, 3.33377, 0.95099]`。端点求解成功，但原接近直线第 8/15 个点
`[2.32637, 3.12491, 1.07447]` 触发 `GRASP_TARGET_UNREACHABLE`。
这条离线路径会在提交该 chunk 之前失败。原服务没有记录失败的内部 IK 点或全姿态
快照，所以不把这个离线索引冒充现场轨迹记录；实际 Runtime 使用含 IMU 倾斜的
base transform，公布的导航 pose 是平面表示。

强制增加 IK 起点可以为其中一个失败点找到另一支路，但肩部需要突然跨越约
2.79 弧度。这不适合作为直接重试。测试过的标定观察中间点也不能修复原直线支路。

## 修改及不变约束

为带标定工具的 Gazebo 家庭场景新增纯函数 `plan_empty_arm_approach`：
从高位空臂姿态展开至同一个高于测得目标中心 16 厘米的预抓点。先完整生成有界
关节路径，再按现有执行器每个关节每 tick 最多 0.025 弧度的控制插值，检查每个
中间 setpoint 的 FK。工具必须始终在预抓高度之上，计入原有 8 毫米 IK 残差容差。
低起点或路径下降穿过该平面直接拒绝；不降低安全高度来强行通过。

`plan_grasp` 和 `pick` 共用这个函数。`pick` 从新鲜反馈重新检查整条接近路径后，
才提交原 `execute_chunk`。纯函数 `plan_tip_chunk` 同时用于后续 Cartesian 路径
预检和 `move_tip`，从而检查实际插值而不再只检查若干端点。

保留原关节范围、0.5 rad/s 命令速度、每 waypoint 时限、取消/停止与反馈新鲜度
检查、8 毫米位置和 0.12 朝向误差界限。吸附距离和工具标定不变；接近后仍必须
等待新图像，重新测量目标，执行最后一次下降，再由实际 Gazebo 吸附状态、正确
物体身份、稳定抬升与落盘证据判定。没有修改物理碰撞配置或放宽验证条件。

这个 FK 检查明确只证明空工具接近的上方净空及运动学可行性，不是完整的全身
自碰撞/环境碰撞规划认证。Gazebo 物理接触仍会实际发生，后续动作失败仍应停止。
不能把离线预检通过写成实际抓取成功，更不能据此宣称实体机器人已验收。

## 测试和加载身份

新 `robot/gateway/tests/test_gazebo_grasp_approach.py` 使用固定的原测量值，覆盖
原端点可达但直线失败、每个实际控制插值的高度和关节范围、下降可达性、低起点与
超工作空间拒绝，以及 plan/pick 共用的 chunk 与过期/未来反馈不得派发。
mock 执行明确返回 `OFFLINE_DISPATCH_BOUNDARY`，不伪造抓取成功。

```sh
.venv/bin/pytest -q robot/gateway/tests/test_gazebo_grasp_approach.py \
  robot/gateway/tests/test_gazebo_manipulation.py \
  robot/gateway/tests/test_gazebo_tools.py \
  robot/gateway/tests/test_gazebo_actuation.py \
  robot/gateway/tests/test_gazebo_grounded.py
```

最终结果 **43 passed in 4.11s**；两个文件 ruff 和 `git diff --check` 通过。
生产源 SHA-256：
`cc7b4586ebaca57ef27a08ca51a59367b9121b73e2673a68ac28ef9725c1eefb`。
新测试 SHA-256：
`62c3638a18e266d019042dadb53650249eeef132352b3851e5ce1c59e98fb43a`。

只读使用 Runtime PID 32866 的进程环境在容器内解析 import，原模块路径为
`/opt/tangying-gateway/tangying_robot_gateway/gazebo_manipulation.py`，加载前文件
SHA-256 为 `4ed33a13740ce3d688e7af5b505f15be629809cd57cb3a63c9f3000c54130c9c`。
部署负责人须同步该实际文件并记录 Gateway 单组件维护边界，保留世界、地图和
journal；新的抓取任务使用独立身份。部署后的真实抓取与完整长任务结果需另外记录，
本文件不宣称补丁已加载或现场已成功。
