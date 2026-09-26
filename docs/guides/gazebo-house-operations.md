# Gazebo 家庭场景操作

当前家庭后端使用与 MuJoCo 相同的 XLeRobot CAD、四房间布局和厨房陶瓷杯/收纳盘。完整启动、建图、自然语言任务和停车命令见 [Gazebo 默认引擎指南](gazebo-default.md)。逐轮错误、修复及实测结论见 [同源家庭闭环记录](../experiments/2026-09-26-gazebo-xlerobot-home-closure.md)。

## 接口与数据边界

Gazebo 发布 RGB、米制深度、CameraInfo、点云、IMU、关节反馈及里程计。ROS 使用生成 commissioning 清单中的相机外参和资源摘要；RTAB-Map / Nav2 负责传感器建图与任务导航。Agent 通过通用 RobotRuntime 发布任务、消费能力与观察；实际驱动执行关节和速度命令。

地图保存后必须绑定资源及标定版本。更换网格、关节、相机或布局后重新建图；未知空间不得用模型全知地图填充。本原型 PlanarDrive 发布实际物理世界位姿，保存地图显式声明相对与绝对测量不确定度；累计误差的实机轮式里程计不具有这一绝对锚定。抓放插件物理状态只用于动作验证，不参与 RGB-D 对象定位。

## 独立 ROS 调试入口

`scripts/gazebo-house-stack.sh` 保留为独立 ROS 容器调试入口，会准备并导出同源资源。其默认导航端口为 `18791`、ROS domain 为 `62`；完整 Agent 演示使用 `sim-stack.sh`。两个入口不能同时占用相同端口与 ROS domain。

```bash
make gazebo-house-start GAZEBO_HOUSE_ARGS="--build --mode mapping"
make gazebo-house-status
make gazebo-house-logs
make gazebo-house-stop
```

数据库和地图保存在 Docker 数据卷；停止不会删除卷。切换 `localization` 需要有效 RTAB-Map 数据库及传感器定位就绪；网关保存地图和 RTAB-Map 数据库是两个明确的数据资源，不可将其文件互换。

## 历史记录的适用范围

2026-09-20 的差速底盘探索覆盖率和后续彩色工位验收是历史实验，保留于 [实验索引](../experiments/README.md)。其房屋、机器人、目标对象和分母不同，不构成当前 XLeRobot 完整家庭成功率。原 RoboCasa 自然语言回归也不能证明 Gazebo 抓放。

实机接入需要替换 ROS 驱动、实测相机与关节标定、底盘足迹、动力学限制和急停，再重新建图与认证。模拟吸附成功不证明实体夹爪或真空吸盘可用。
