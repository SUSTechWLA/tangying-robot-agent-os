# 家庭场景发布验收清单

这份清单用于把家庭场景从开发分支交给现场集成。所有命令都应记录 commit、环境、输出目录和失败原因；通过一项不替代其他项。

## 软件与合同

- [ ] `make build`、`make generate-check`、`make lint`、`git diff --check` 通过。
- [ ] `PYTHONPATH=sim/mujoco .venv/bin/pytest -q sim/mujoco/tests/test_home_scene.py` 通过，确认五个房间、双 RGB-D 和 `verify_arrival` 证据。
- [ ] `go test ./agent/intent ./skills/manipulation ./edge/robotclient ./edge/agent ./tasks` 通过，确认家庭路线解析、计划检查点、恢复和版本持久化。
- [ ] `pytest -q tests/install/test_navigation_stack.py robot/ros2_ws/src/tangying_navigation/test/test_launch_config.py` 通过，确认 `--scene home` 同时传递到仿真、Compose、launch，并使用家庭地图路径。
- [ ] `make test-web` 通过；用户模式不显示底层动作输入，开发诊断仍可按 task/revision/step/command/observation 定位。

## 家庭仿真

- [ ] 启动 `bash scripts/home-slam-stack.sh restart --sim-port 51051 --agent-port 8878`，确认健康状态、头部/底盘相机和 `scene_revision=home-4room-rgbd-v1`。
- [ ] 用“从客厅出发，去厨房确认一下环境”创建任务，检查 `observe_scene → navigation.navigate → verify_arrival` 顺序和同次底盘证据。
- [ ] 用“巡检卧室和卫生间，最后回到客厅”检查三段路线、回到起点标记和每段独立 checkpoint。
- [ ] 在工具边界暂停、重启 Local Agent、显式继续；确认已完成导航不重复发送，未确认的物理结果保持阻断。
- [ ] 家庭模型中没有 `ikea_cart`、桌面物体或全局环境实体；相机观测为空处保持未知。

## ROS 2 / RTAB-Map

- [ ] `make navigation-restart NAVIGATION_ARGS='--build --mode mapping --scene home'` 成功，`navigation-status` 明确显示 map、定位、相机和 odom 都 ready。
- [ ] 实际覆盖五个房间并保存 `/data/maps/home/rtabmap.db`；停止后重开数据库，确认视觉词典和占据图仍可用。
- [ ] 使用 `--mode localization --scene home` 从至少三个不同起点重复路线；丢失 RGB-D、TF、odom 或视觉质量时必须停止并保留冻结失败证据。
- [ ] 逐项验证取消、断网、底部相机断流、急停、速度看门狗、地图版本漂移和进程重启；不能自动重放未知物理命令。

## 实机放行

- [ ] XLeRobot 或其他型号的 profile、驱动、相机内参/外参、odom、工具 catalog 和策略制品已绑定版本与哈希。
- [ ] 头部与底盘 RGB-D 在真实房屋照明、反光地面、窄门、低矮障碍和家具遮挡下通过观测合同检查。
- [ ] 完成低速软围栏、刹车距离、实体急停、断网归零、持物恢复和人工接管演练。
- [ ] 至少 30 次单机器人路线和一次长稳运行通过；记录成功率、定位丢失、停止延迟、失败证据和回滚版本。
- [ ] 现场负责人签字后才能进入受监护试点；无人值守生产、通用家务和多机器人协同另行验收。
