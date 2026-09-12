# RGB-D 相机：位置错误与 D435i 统一（待实施）

用户实机检查发现两个问题，都已**用实测确认**。修复本身很小，但它会牵动自滤波与感知阈值，因此**没有直接改进 main**——补丁保存在本目录，实施步骤写在下面。

## 问题 1：底盘相机装错了位置（已确认）

修复前的实测：

| 相机 | 世界 z | 朝向 | fovy |
| --- | --- | --- | --- |
| `head_depth` | 1.185 | 垂直向下 | 70° |
| `base_depth` | **1.535** | 前下方 45° | **150°** |

**底盘相机比头部相机还高**，而且是 150° 鱼眼俯视地面——它既不在底盘底部，也不看机器人前进方向。源码注释也写着它是"front mast, 45 degrees down"，即一根立杆上的俯视相机，与"底部前向深度相机"完全是两回事。

用户要求：**放在底盘靠近地面处，向前拍摄机器人前进的深度图**。

## 问题 2：两台相机不是同一个型号

用户要求顶部与底部**参数一致，尽量用 Intel RealSense D435i**。修复前两者 fovy 分别是 70° 与 150°，是两种不同的传感器。

D435i 深度流为 **87° × 58°**（H × V）。

## 补丁内容（`patches/rgbd-d435i-cameras.patch`）

1. `xlerobot.xml` / `xlerobot_r2.xml` / `xlerobot_handoff_r2.xml`：`head_depth` 的 `fovy="70"` → `fovy="58"`。
2. `rgbd_navigation.py`：`base_depth` 从 `pos=[0.28, 0, 1.50]` + 45° 俯视 + `fovy=150`，改为
   - `pos=[0.30, 0, 0.16]`（贴近底盘底部）
   - 前向 15° 俯角（`xyaxes=[0,-1,0, sin15,0,cos15]`）
   - `fovy=58`
3. `home_scene.py`：新增共享常量 `RGBD_VERTICAL_FOV_DEG = 58`。
4. **tabletop 场景保持逐字节不变**（`[0.185, 0, 0.50]` + 45°），因为它的验收夹具断言了近地射线。

改后的实测：

```
head_depth  58 x 73 deg
base_depth  58 x 73 deg
base_depth world z=0.160, 前向 (0.966, 0, -0.259)
```

## 一个必须说明的差距

**垂直 58° 与 D435i 一致，但水平只有 73° 而不是 87°**，因为我们的帧缓冲是 4:3（320×240），而 D435i 的深度流是 16:9。要拿到真正的 87° 水平视场，需要把帧缓冲改成 16:9（例如 424×240）。这是**帧缓冲选择**，不是相机参数错误，但会让"是否真是 D435i"这句话打折扣。

## 为什么没有直接改进 main

套用补丁后 **5 个测试失败**：

| 失败测试 | 性质 |
| --- | --- |
| `test_commissioned_downward_camera_observes_near_front_ground` | **编码的是旧决策**（"俯视相机"），应按新决策重写 |
| `test_mobile_start_requires_a_measured_approach_and_preserves_visible_targets` | 需确认是重指向还是真回归 |
| `test_raw_rgbd_has_same_capture_robot_self_mask_without_private_state[base]` | **自滤波需要重新调参** |
| `test_actual_bottom_camera_self_returns_match_separate_robot_only_cad` | 同上 |
| `test_home_task_runtime_observes_only_rgbd_task_fixtures` | 感知阈值可能受影响 |

**自滤波（self-filter）这两条是安全相关的**：它决定机器人能否把自己的机身从深度图里剔除。相机换位置后，机身在新视角下的自遮挡形状完全变了，旧参数必然失效——**这类改动不能只让测试变绿，必须重新推导**。

## 实施顺序（下次从这里开始）

1. 应用补丁。
2. **先重算自滤波**：用「只有机器人、没有场景物体」的模型渲染，取出底盘的自身返回点，重新拟合自滤波区域。这是唯一有实质风险的一步。
3. 重指向 `test_commissioned_downward_camera_...`：断言新决策——**相机贴近地面、朝前、能看到前方地面与前方障碍**，而不是"俯视"。
4. 检查感知阈值：工作体积是传感器空间的门控，相机位姿变了，`_support_z` 与颜色掩码的 z 门控都要复核。
5. 决定帧缓冲是否改 16:9（换取真实 87° 水平视场）。
6. 全部通过后再跑 `make home-accept` 与建图验收。

## 另外三个问题的现状（用户同时提出）

| # | 问题 | 现状 |
| --- | --- | --- |
| 2 | 整机标定、SLAM 应在左侧**独立标签**，不要混在工作台 | 未实施。当前三块面板都挂在工作台页 |
| 3 | 稠密地图未用 WebGL 展示成功 | 阻塞：`/v1/world` 返回 **0 实体** → 场景包无 `scene_id`/`model_hash` → `fleetVisualReady` 恒为 false → 三维画布隐藏，控制台按设计回退到二维 Canvas |
| 4 | 「动作与结果」点回看观测会莫名滚到页面底部、中间空白过多 | 未修复 |
| 5 | 导航依赖语义地图，不能只有稠密点云 | 未实施。当前有占据栅格 + 房间拓扑（`HOME_ROUTE_EDGES`），但没有面向导航的语义地图产物 |
