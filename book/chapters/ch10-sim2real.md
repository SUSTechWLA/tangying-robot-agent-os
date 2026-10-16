# 第 10 章 异构机器人集成与 Sim2Real 验证

**学习目标**：制定型号接入包；区分仿真和现场证据；理解控制与安全责任。

**先修知识**：第3–5、8章；机器人坐标、控制与传感器基础。

**关键术语**：Sim2Real、型号交付、QoS、停止验证、异构接入/调度

## 10.1 统一契约不统一硬件

RobotProfile→Backend→RobotRuntimeService提供语义边界。固定臂、移动底盘、双臂、移动操作和传感器平台可表达不同能力子集；每型号仍需驱动、感知、控制与验证器。

一个双臂标签不生成协作轨迹，一个LiDAR字段不实现定位，一个custom结构不获得足式/飞行技能。接入合同只是可扩展基础。

## 10.2 型号交付包

| 资料/实现 | 应可核对的内容 |
| --- | --- |
| Profile/版本 | 实例、型号、驱动、原生关节、末端、工具 |
| 几何/标定 | TCP、坐标链、足迹、内外参、地图绑定 |
| 控制/限制 | 实际限位、速度/载荷、跟踪、失联行为 |
| 感知/观测 | 来源、采集时间/序号、变换、质量与语义 |
| 停止/故障 | 可打断性、实际反馈、故障摘能力 |
| 验证 | 正例、反例、Unknown、停止/对账和范围 |

Profile不可作为完整动力学模型或安全论证。构造factory、发现设备、读取状态不得自动arm；连接/解除扭矩本身的物理行为也需设备条件支持。

## 10.3 ROS与SDK集成

厂商接口在本地Backend转换，ROS Topic/Service/Action与TF不传播到业务任务层。控制器负责跟踪、碰撞/载荷约束与停止，Runtime负责语义命令准入、日志和受控调用。

QoS影响可靠性、期限、历史和来源活性。发布/订阅不兼容可能没有数据；“reliable”也不等于样本总是新鲜或效果恰好一次。应记录本机QoS、来源健康和时效。[ROS2 Jazzy官方QoS资料](https://raw.githubusercontent.com/ros2/ros2_documentation/jazzy/source/Concepts/Intermediate/About-Quality-of-Service-Settings.rst)

## 10.4 仿真能验证什么

仿真可检验身份、协议、任务顺序、环境谓词、恢复和特定几何闭环；实体摩擦、延迟、标定、柔性、负载、传感器故障和制动仍有差距。

Gazebo家庭任务使用规定模型、地图、目标杯/盘及验证器。修复抓取可见路径和持物越盘沿，不证明任意器具或所有真实场景可用。协议测试里的sensor/actuator fixture也不能算实体硬件测量。

## 10.5 从集成到现场验收

```text
无动作协议/状态验证
 → 控制与实际停止
 → 传感器/标定/规范观测
 → 单技能闭环
 → 受监护组合任务
 → 失联/重启/持物等故障
 → 明确范围的运行与维护
```

每一级提供可重复的配置和证据，不以“启动Docker”跳过物理验证。工业机器人及集成应用的适用要求可从[ISO10218-1:2025](https://www.iso.org/standard/73933.html)、[ISO10218-2:2025](https://www.iso.org/standard/73934.html)官方范围开始核对；本书不复制其条款，也不把工业范围自动套用所有家庭/移动设备。

## 10.6 异构多机的额外工作

当前单capability plan绑定robot/catalog，Task还带单Adapter。跨驱动合作需每节点身份/目录合同、能力匹配、共同坐标、同物关联、资源交接与联合验证；接入两个Profile不是已经实现这些能力。

## 10.7 标定、策略与反馈的组合

标定建立传感器、关节、TCP 与世界的关联。运动学参数、相机内参、外参和地图绑定有不同来源/版本；“运行过一次标定程序”不能自动证明全部正确。

接入时分别验证单位/轴序、静态重复性、已知几何的误差和动作后反馈。变更夹具、相机位置或本体结构时，识别受影响目录/标定/地图与批准内容，不在旧任务中静默替换。

动作策略 sidecar 可以提供轨迹或动作候选，低层控制器负责跟踪和实际约束，Runtime 负责受控准入与 journal，verifier 判定业务后置条件。策略输出合法、不触及单一限位或控制器报告完成，都不能单独证明杯子到盘。

| 差距来源 | 验证方法的例子 |
| --- | --- |
| 几何/标定 | 已知参照物、变换反例与版本切换 |
| 延迟/丢包 | 受控延迟与旧帧，检查拒绝和停止/保持 |
| 摩擦/负载 | 按适用范围变化初态与负载，记录滑落/未知 |
| 控制/停止 | 真实反馈与监督测量，注明模式、速度和条件 |

这些是实验设计方向，具体现场动作和指标由设备方案确定。移植成功应保留新设备的独立证据，不沿用仿真或另一型号的“通过”。

### 三类移动机器人如何共用 ROS 2 执行链

共享目标Host只决定授权内的本机能力序列，不能让一种本体继承另一种本体的动态约束或停止策略。mobility 合同分别声明 domain、motionModel、poseModel、单位、速度限制、出发准备和设备所有的停止流程。UGV、legged、multirotor 共享 navigation.navigate 和 verify_arrival，但控制器根据本体实际完成准备与运动。纯导航设备无需机械臂抓放就绪，无人机也不使用地面 pre_position。

| 接口 | 设备责任 | 边缘检查 |
| --- | --- | --- |
| ProfiledExecute Action | 接受带 command/lease/deadline/批准/资源的命令，验证并执行路径 | robot/Profile/commissioning/catalog、目标范围与路径身份 |
| ProfiledDriverState | 实际 commissioned/armed/healthy/watchdog、工具和限制 | 有界新鲜状态；空间部署摘要一致 |
| ProfiledObservation | 同次重建、完整定位与实际运动结果 | 来源、时间/序号、地图/世界/标定和证据 |
| CommandLease / EnterSafeStop | 独立失联保护和登记停止策略 | 匹配 ACK、锁存及禁止后续运动 |

空间桥在 action 之前规划并再次检查定位没有漂移或过期；navigation_plan_json 携带 planId、部署/地图、命令和起始定位，结果返回 navigation_plan_id。完成后须有确切命令后的新观测，检查 XYZ 距离和四元数夹角，再独立执行到达验证。已接受 goal 或控制器 succeeded 不能跳过这些证据。

无人车可使用经调试的制动；足式需要保持平衡或厂商程序；多旋翼由受控降落或完整厂商故障流程处理失联。停止 ACK 表示登记流程已响应，不等于已停稳或已落地。高频租约、轨迹跟踪与硬件 watchdog 留在设备，不能依赖云端逐步任务消息。

两端必须重新构建新增 Action/状态字段。默认 Unix 墙钟、use_sim_time=false；DDS 属于受信本地设备边界，生产部署应配置实际 namespace/domain 和权限。合同、配置与限制见 [异构运动接入](../../docs/development/heterogeneous-mobility.md)和[空间部署](../../docs/development/spatial-navigation.md)。

## 10.8 小结与阅读

项目实现见[PluginBackend](../../robot/gateway/tangying_robot_gateway/plugin_backend.py)与[实机集成指南](../../docs/guides/hardware-agent-integration.md)。行业交付模板见附录H。

## 10.9 分层习题

基础题 B：列出仿真成功无法直接证明的三类实机性质。

工程题 E：为一个新移动操作本体制作型号资料和分级验收表。

研究题 R：选择一类Sim2Real差距，提出控制变量、故障注入和迁移有效性指标。

作答应写明假设、判据与证据。评价要点见[附录 I](../appendix/I-exercise-guide.md)，实践路线见[附录 F](../appendix/F-learning-labs.md)。
