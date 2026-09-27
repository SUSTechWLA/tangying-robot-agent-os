# Gazebo：XLeRobot 同源完整家庭

Gazebo 的家庭入口使用已验证 MuJoCo 家庭的 XLeRobot CAD、四个房间和走廊、家具以及厨房陶瓷杯/收纳盘。`home`、`home_task`、`home_furnished` 是同一资源的兼容入口；原差速箱体、独立彩色工位和 AWS 原始房屋组合已退出部署入口。历史自动化测试夹具保留在 `tests/fixtures/`。

实际闭环状态与失败证据见[本次验收记录](../experiments/2026-09-26-gazebo-xlerobot-home-closure.md)，设计约束见[升级规范](../development/2026-09-26-gazebo-xlerobot-home-spec.md)。2026-09-27 同一 episode 的巡检、厨房检查往返、搬杯放盘及返回已通过严格套件；旧彩色工位通过记录仍属历史实验。

## 启动

需要 Docker、项目 `.venv`、MuJoCo 与家庭网格转换依赖。MuJoCo 仅在资源构建阶段使用；Gazebo 容器和 Agent 不导入 MuJoCo。

```bash
# 首次准备上游家具源，保留固定提交和许可证。
.venv/bin/python scripts/prepare_home_world.py
.venv/bin/python scripts/prepare_furnished_home.py
.venv/bin/python scripts/export_gazebo_home.py

# 先构建，避免冷构建占用服务启动的就绪期限。
.venv/bin/python scripts/build_gazebo_image.py
scripts/sim-stack.sh start --engine gazebo --scene home_furnished \
  --perception rgbd --sim-port 50161 --agent-port 8897 \
  --artifacts-dir artifacts/sim-stack/gazebo-xlerobot-home
```

打开 `http://127.0.0.1:8897/`。输出目录接受相对或绝对路径。更换资源前停止当前任务并显式重启；启动重新导出资源并验证摘要。机器人初始位于客厅，任务不得重置对象或写入机器人位姿。

## 地图与自然语言任务

```bash
.venv/bin/python scripts/build_sim_map.py --base-url http://127.0.0.1:8897 \
  --output artifacts/gazebo-home-map-acceptance --timeout 1800

# 建图和地图验收完成、没有运行中任务时，启动定位阶段。
# 保留同一资源版本和原运行目录中的两套地图（保存地图和 RTAB-Map DB）。
scripts/sim-stack.sh stop --artifacts-dir artifacts/sim-stack/gazebo-xlerobot-home
TANGYING_NAVIGATION_MODE=localization scripts/sim-stack.sh start \
  --engine gazebo --scene home_furnished --perception rgbd \
  --sim-port 50161 --agent-port 8897 \
  --artifacts-dir artifacts/sim-stack/gazebo-xlerobot-home

.venv/bin/python scripts/run_home_task_suite.py --base-url http://127.0.0.1:8897 \
  --adapter gazebo --output artifacts/gazebo-home-task-acceptance \
  --scenario patrol --scenario inspect-kitchen --scenario mug-transfer --timeout 600
```

输出目录必须不存在。验收脚本会读取该控制台自己的会话凭据；不要复制凭据到日志或提交中。先保存、激活完整实测地图，再运行任务：

- “巡检卧室和卫生间，最后回到客厅”。
- “从客厅出发，去厨房确认一下环境”，然后“从厨房出发，回到客厅”。
- “从客厅出发，去厨房拿杯子，放进收纳盘，然后回到客厅”。

启动阶段切换会重新创建物理场景，须在任务开始前完成并记录。任务之间不得重启或重置场景。`mapping` 用于采集地图，`localization` 用于已建图场景的长期任务；后续每次定位启动仍需设置该环境变量。定位模式要求已有非空且匹配资源版本的 RTAB-Map 数据库、有效视觉匹配和定位协方差，缺少这些条件不能下发导航。

家庭导航实际读取 `home_rtabmap.yaml`，其中 `slam_camera: head` 选择固定头部视角；两路原始深度仍参与近障检查。原生数据库位于资源摘要下的 `head-<传感器profile摘要>/rtabmap.db`。相机选择或 profile 参数变化后必须重新建图，不能把旧相机的数据库复制到新目录。启动诊断应核对 `/rtabmap/rtabmap` 的订阅、深度范围和 database_path，而不是仅查看 YAML 文件。

杯子位置由当前 RGB-D 形状测量获得，房间位置来自已配置语义目录并绑定地图版本。餐具和花瓶为环境装饰，不声明可抓取。抓放使用近接仿真吸附，必须验证抬升、释放、容器范围、直立和稳定；不能将吸附命令 ACK 当作成功。

机器人遮挡过滤只使用本机 CAD 表面及图像采集时刻的编码器；深度点距表面不超过 4 mm 才排除。RGB、Depth、底盘位姿与编码器必须按同一时刻配对，缺少有界插值测量时拒绝输出。目标仍由当前 RGB-D 测得，不能用上次规划位置补齐不可见对象。

本原型的工具点是固定指末端，配置位于 `robot/gateway/tangying_robot_gateway/assets/gazebo_xlerobot_tool.json`。五个臂关节参与 IK，第六个夹爪电机独立控制。Python 控制器与 Gazebo 物理互锁必须读取一致的配置，观察中保存工具内容摘要。更换真实夹具时需要重新标定 TCP、控制参数与接触验证，不能沿用仿真吸附配置。

持物遮挡盘面时，驱动执行工具配置中的两段有界观察航点并等待新图像；仍不可见即停止。放置使用实测载荷相对工具的局部偏移，先垂直升至盘沿净空再转运。命令后置证据必须来自完成后的源传感器时刻；观测保留 `sensor_clock_wall_bridge` 来源，该软件时钟桥仍需在真实相机上单独认证。

单目标工作区/地点导航、SLAM 几何分区与保存地图的语义目录见[SLAM 与语义导航](slam-semantic-navigation.md)。可用 `--scenario semantic-destinations` 验证工作区往返；地图中的功能名称来自显式标注，不是仅凭形状自动识别。

## Runtime 与机器人适配

Agent 只消费 `RobotRuntime` 的 profile、能力、服务和观察。`--adapter` 是连接注册键，不是任务执行算法分支。机器人驱动负责传感器坐标/时间、地图绑定、关节限制、导航控制、抓放证据、取消、停车和故障报告。

其他机器人应声明自己的关节类型、单位、范围、传感器、工具和动作限制。没有手臂的机器人不声明抓放；单臂、双臂、全向、差速或其他机构不共用未经验证的控制器。能力未就绪时应返回 blocker，不能由 Agent 猜测运动接口。

本原型保留两个底盘滑动关节和一个旋转关节，速度驱动有 500 ms 失联停车与限幅。全屋扫描通过测量、里程计和障碍守卫执行；任务导航由 RTAB-Map 与 Nav2 执行。此模型用于系统闭环验证，轮胎牵引、真空压力、真实相机误差、刹车和实体急停仍须在目标设备认证。

## 运维与资源

```bash
scripts/sim-stack.sh status --artifacts-dir artifacts/sim-stack/gazebo-xlerobot-home
scripts/sim-stack.sh logs --artifacts-dir artifacts/sim-stack/gazebo-xlerobot-home
scripts/sim-stack.sh stop --artifacts-dir artifacts/sim-stack/gazebo-xlerobot-home
```

`artifacts/sim-assets/xlerobot-home/` 包含生成的 SDF、网格、纹理和 commissioning 清单；地图、Runtime journal 与 Agent 数据按运行目录持久化。资源摘要变化后旧地图不得静默激活。删除闲置生成场景不能删除上游 CAD、许可证、保存地图、历史证据或生产数据卷。

Runtime 状态备份必须包含 `runtime/commands.json` 及 `runtime/commands.json.events/`。v2 将完整回执保存在摘要绑定的事件文件中；缺失或篡改会锁存停止，不能删除日志后重新执行未知动作。
