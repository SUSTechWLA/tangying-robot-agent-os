# 用户端 Robot Agent Console

Local Agent 启动后直接打开浏览器：

```text
http://127.0.0.1:8787/
```

## 已交付功能

- 自然语言任务输入；
- OpenAI 兼容 LLM endpoint、model 和 API key 的本地配置；
- API key 只写入权限为 `0600` 的本机配置，状态接口只返回 `hasApiKey`；
- 配置保存后通过 `robot-agent restart local` 生效；
- MuJoCo / XLeRobot direct / XLeRobot ROS2 适配器选择；
- 任务创建、审批、取消；
- WebSocket 实时审计事件；
- LLM 计划来源与子任务数量展示；
- Robot Runtime 语义状态：活动、模式、E-stop、异常；
- 传感器 / 语义状态 JSON 面板；
- MuJoCo XLeRobot 实时 PNG 场景，渲染不可用时自动降级为语义俯视图；
- 场景面板支持“实时画面 / 自由视角 3D”切换：拖拽旋转、滚轮缩放、一键复位；
- 自由视角 3D 渲染真实遥测位姿、桌台、机器人、物体、轨迹、官方 XLeRobot、IKEA RÅSKOG 置物推车与头部/推车深度相机标记；
- 观测到的机器人位姿、关节、夹爪、持有物、当前工具、奖励和验证置信度；
- 编排质量指标面板。

## Fleet RoboCasa WebGL 数字孪生

联网双机器人验收使用 Fleet Console，而不是上面的 Local Console 端口：

```bash
make robocasa-web-assets
bash scripts/robocasa-fleet.sh start
open http://127.0.0.1:18080/
```

页面从同源 `tangying.visual-asset.v1` manifest 加载完整 RoboCasa 厨房和 XLeRobot GLB。两个机器人由 `/v1/world` 中至少 12 个有限 canonical `joint.*` 值分别驱动；红色方块、三区域、路径阶段、owner/token、freshness、activity、held 与 Harness verdict 同样只读取权威状态。视图支持平移、旋转、指针锚点缩放、总览/俯视/R1/R2、跟随、选择、双击聚焦和 `F` 复位，模型、构件边界、标签、任务路径四个开关互不耦合。

视觉资产从不证明物理动作成功。资产失败、WebGL context 丢失或 manifest 的场景/模型 revision 不匹配时，世界实时链路继续工作，状态显示 `WORLD LIVE / VISUAL DEGRADED`，并切换到语义 Canvas。原始 `file://` 页面不是 live Console，只显示 `http://127.0.0.1:18080/` 服务入口且不会重复请求 API/WebSocket。真实地图复用相同 manifest schema；实机 XLeRobot adapter 必须发布与 binding 对齐的 canonical joint keys 和模型 revision。

## 开发者视角

Console 不只是任务面板，还完整展示一次自然语言到机器人执行的转换链路：

```text
自然语言
  → Intent（目标物体 / 目的地 / 动作）
  → Task Plan（LLM 或 deterministic 生成的技能图）
  → Capability / Tool（observe_scene、resolve_targets、manipulation.pick、...）
  → Robot Runtime 状态（activity、mode、E-stop、anomalies）
  → 传感器 / 语义状态（entities、robotState）
```

页面中可以在“任务计划与审计”查看每一步 skill 调用与事件；在“Robot Runtime”查看当前能力状态；在“场景”中观察机器人执行；在“传感器 / 语义状态”查看真实遥测数据。目标是把 RViz/Gazebo 的“数据可视化 + 执行监控”能力集成到同一个前端。

## 数据链路

```text
Robot / MuJoCo Observe
  → Local Agent robotclient.Telemetry()
  → 启动即采样、之后每秒采样的后台 observer
  → 进程内 TelemetryHub
       - JSON 语义状态历史
       - 独立场景帧缓存
  → 本地 Console 每秒 GET /v1/telemetry?adapter=...
  → 本地 Console GET /v1/scene/frame?adapter=...
```

遥测是低速率可观测数据，不是高频控制数据。相机、LiDAR、IMU、关节原始流仍保留在机器人端；用户端看到的是语义实体、活动状态和可展示的 robot_state。

## 未来建图融合

`scene-canvas` 已经按实体位姿渲染。后续接入 SLAM/点云融合时，只需把融合结果转换为同一组 scene entities：

```json
{
  "entityId": "wall-01",
  "category": "wall",
  "pose": [1.2, 0.4, 0.0, 1, 0, 0, 0]
}
```

用户端无需修改渲染逻辑，即可从仿真地图切换为真实环境数字孪生。

## API

- `POST /v1/tasks`：自然语言创建任务。
- `GET /v1/config/status`：读取不含密钥的 LLM 配置状态。
- `PUT /v1/config/llm`：将 LLM 配置写入本机私有配置文件。
- `GET /v1/runtime`：读取树莓派能力、版本、就绪状态与阻塞原因。
- `POST /v1/tasks/{id}/approve`：批准物理任务。
- `GET /v1/tasks/{id}/events/ws`：实时事件。
- `GET /v1/telemetry?adapter=mujoco&limit=20`：用户端读取遥测。
- `GET /v1/scene/frame?adapter=mujoco`：读取最新场景帧；响应禁止缓存，无画面时返回 `404 SCENE_FRAME_UNAVAILABLE`。
- `GET /v1/orchestration/metrics`：编排质量指标。
