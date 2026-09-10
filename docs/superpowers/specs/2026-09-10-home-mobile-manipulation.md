# 家庭移动操作闭环规格

**目标：** 在单机器人家庭仿真中把自然语言、RGB-D 观测、房间导航、机械臂抓取/放置、动作后验证和 Harness 审计连成一条可恢复的任务链。

## 范围

- 新增 `home_task` 仿真场景，保留 `home` 作为只做导航的家庭场景。
- 场景只通过机器人头部/底盘 RGB-D 和底盘位姿工作；家庭房间和工位是可见几何，不把 MuJoCo 真值直接写入观测。
- 支持一条确定性中文复杂指令：从起始房间导航到操作房间，识别物体和目标，抓取、验证、放置、验证，再返回指定房间。
- 每个能力都是单独的 canonical tool 调用：`observe_scene`、`navigation.navigate`、`verify_arrival`、`resolve_targets`、`plan_grasp`、`manipulation.pick`、`verify_grasp`、`manipulation.place`、`verify_placement`。
- 物理工具继续要求批准、租约、幂等键和新鲜观测；失败或暂停沿用现有 journal/recovery 语义。
- Gazebo 家庭后端继续用于 ROS 2/RTAB-Map/Nav2 传感器和导航验收；本规格的完整移动操作回归使用 MuJoCo RGB-D Runtime，以便同一 Agent Harness 可以真实执行机械臂动作。

## 任务示例

```text
从客厅出发，去厨房拿红色杯子，放进蓝色收纳盒，然后回到客厅
```

期望任务图：

```text
observe_scene
→ navigation.navigate(kitchen)
→ verify_arrival(kitchen)
→ observe_scene
→ resolve_targets(red-cup, kitchen-bin)
→ plan_grasp
→ manipulation.pick(red-cup)
→ verify_grasp(red-cup)
→ manipulation.place(kitchen-bin)
→ verify_placement(red-cup, kitchen-bin)
→ navigation.navigate(living_room)
→ verify_arrival(living_room)
```

## 约束

- 任务创建阶段可以保存稳定的语义引用，执行阶段必须在目标房间用新鲜 RGB-D 重新解析；不可见物体或目标必须安全失败。
- 导航过程中机械臂必须空载且收拢；持物状态下只能执行放置或安全恢复。
- `verify_arrival` 使用新的底盘 RGB-D/里程计采集；`verify_grasp` 和 `verify_placement` 使用连续不同帧确认关系与稳定性。
- 任何尚无可信物理终态的动作保持 `PHYSICAL_OUTCOME_UNKNOWN`，禁止自动重放。
