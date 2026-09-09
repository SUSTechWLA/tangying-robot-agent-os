# 素材与事实依据

本发布包对应仓库当前 `codex/v0.2-release` 分支与提交 `5ad6d6f`。

## 代码与文档事实

- 家庭场景：`sim/mujoco/assets/xlerobot_home.xml`、`sim/mujoco/tangying_sim/home_scene.py`
- 双 RGB-D 与家庭 Runtime：`sim/mujoco/tangying_sim/rgbd_navigation.py`、`sim/mujoco/tangying_sim/rgbd_runtime.py`
- RTAB-Map / Nav2 配置：`robot/ros2_ws/src/tangying_navigation/config/home_rtabmap.yaml`
- 家庭运行指南：`docs/guides/home-scene-operations.md`
- Sim2Real 说明：`docs/guides/home-sim2real.md`
- 发布边界：`docs/production/v1-release-status.md`

## 画面来源

- `inputs/workcell-v2-head-color.png` 和 `inputs/workcell-v2-head-depth.png`：限定工位 RGB-D 参考采集。
- `inputs/task-06b6fc5dfb94c65ad06ebdd6-task02-verify_place-rgb.png`：限定工位结果核对采集。
- `inputs/home-scene-rgb.png` 和 `inputs/home-scene-depth.png`：家庭场景底盘 RGB-D 现场探针，展示真实观测到的走廊与深度，不代表完整地图或实机画面。

## 发布口径

“仿真闭环已验证”指限定工位的参考任务与当前家庭场景的传感器 / 导航链路验证；不能写成通用任务成功率、真实 XLeRobot 精度或无人值守量产能力。家庭地图覆盖尚未完成时，系统会拒绝穿越未知区域，这正是本项目要保留的生产安全语义。
