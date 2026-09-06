# Fleet 云端控制平面（分布式 AgentOS 部署画像）

Fleet 是 Tangying Robot Agent OS 的云端部署画像：一个 Docker Compose 栈提供
云端控制平面（MySQL + Redis + Fleet 控制平面 + nginx），任意数量的机器人在
公网接入，用户通过云端 Console（登录 / 设备 / 任务 / 遥测 / 全局融合地图）
操作整个机群。

```text
浏览器 (Cloud Console)
   │  HTTPS 443 (nginx, TLS + 白名单)
   ▼
nginx ──► fleet-control-plane :8080 (仅 Docker 内网, 不暴露公网)
   │            │
   │            ├─ MySQL :3306       任务持久化 (JSON 文档)
   │            ├─ Redis :6379       任务流队列 (Streams) + 设备租约 + 遥测
   │            └─ :8443 mTLS gRPC   机器人公网接入网关 (FleetGateway)
   │  TCP 8444 (nginx stream passthrough)
   ▼
edge-worker (每台机器人一个进程; 可运行在机器人侧局域网)
   ├─ 任务源: Redis Stream 直连 或 HTTP 长轮询 GET /v1/queue/next
   ├─ mTLS gRPC Link: 心跳 / 设备租约 / 服务器命令 (取消、急停)
   └─ mTLS gRPC Robot Runtime (MuJoCo 仿真或树莓派 XLeRobot)
```

## 快速开始（本地一键）

```bash
# 1) 启动云端 (mysql + redis + fleet-control-plane + nginx)
./scripts/fleet-up.sh up
#    生成 deploy/cloud/.env (操作员凭据 + 设备令牌 + 强密码)
#    生成 deploy/cloud/certs/ (mTLS CA + 服务器证书 + 每台机器人客户端证书)
#    生成 nginx 客户端 IP 白名单; 等待 https://127.0.0.1/healthz 就绪

# 2) 启动两台 MuJoCo 机器人 (robot-1 / robot-2) 各配一个 edge-worker
./scripts/fleet-sim.sh start

# 3) 跑通分布式协同任务闭环
./scripts/fleet-sim.sh handoff
#    创建任务: 让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区
#    -> robot-1 放入共享交接区 -> WorldSnapshot 连续稳定验证
#    -> fencing token 移交 robot-2 -> robot-2 放入目标区 -> 任务 SUCCEEDED

# 4) 本机浏览器打开 http://127.0.0.1:18080/；公网仍使用 https://127.0.0.1/
#    登录凭据可通过显式执行 scripts/fleet-up.sh env 查看
#    设备列表 / 多机器人全局融合地图 / 协同任务节点 / 每台机器人遥测
```

常用管理命令：

```bash
./scripts/fleet-up.sh status|logs|down|restart|env
./scripts/fleet-sim.sh stop|status|logs [edge-1|edge-2|sim-1|sim-2]
```

当前 V1 的单主恢复、身份与未完成生产条件见[V1 状态](production/v1-release-status.md)。新开发者见[开发快速上手](development/getting-started.md)，购机设备见[Sim2Real 上手](sim2real/README.md)。

## 云端组件

| 组件 | 端口 | 说明 |
| --- | --- | --- |
| nginx | 443 (HTTPS), 8444 (TCP), 127.0.0.1:18080 (HTTP) | 公网唯一入口；控制台 TLS、客户端 IP 白名单、gRPC 透传；18080 仅本机开发 |
| fleet-control-plane | 8080 / 8443（仅 Docker 内网） | HTTP API + 嵌入的 Console Web 应用 + mTLS gRPC 网关 |
| mysql 8.4 | 3306（内网） | Task/Revision/事件/Outbox 存储；由版本化建表/迁移维护，JSON 文档不等于免迁移 |
| redis 7 | 6379（内网） | `fleet.tasks.ready.{robot}` 每机器人任务流 + 设备注册表 + 遥测缓存/轨迹 |

### 环境变量（deploy/cloud/.env）

| 变量 | 说明 |
| --- | --- |
| `FLEET_OPERATOR_USER` / `FLEET_OPERATOR_PASSWORD` | Console 登录凭据 |
| `FLEET_DEVICE_CREDENTIALS` | 每台 edge-worker 独立的数据面凭据，格式 `robot-1:token1,robot-2:token2`；请求必须同时携带匹配的 `X-Robot-ID` 与 `X-Device-Token` |
| `FLEET_AUTH_SECRET` | 操作员 token HMAC 密钥（留空则每次启动随机） |
| `FLEET_ROBOTS` | 本机群机器人 id 列表（默认 `robot-1,robot-2`） |
| `FLEET_HTTPS_PORT` / `FLEET_GRPC_PORT` | 公网入口端口（默认 443 / 8444） |
| `FLEET_ALLOWED_CIDRS` | nginx 客户端 IP 白名单（逗号分隔 CIDR，或 `all`） |
| `FLEET_INTENT_LEASE` | 意图声明租约（默认 2m，超时自动回收） |
| `FLEET_WORLD_ID` / `FLEET_WORLD_FRESHNESS` | 世界一致性域 / 来源新鲜度预算 |
| `FLEET_WORLD_DELTA_RETENTION` | 实时增量保留数量；游标落后时要求快照重同步 |
| `FLEET_LEADER_LEASE` / `FLEET_RESOURCE_LEASE` | 单写协调器租约 / 共享物料 fencing 租约 |
| `FLEET_HANDOFF_MAX_AGE` | 交接完成证据允许的最大年龄 |
| `FLEET_DEVICE_LEASE` / `FLEET_HEARTBEAT_INTERVAL` | 设备租约 / 心跳间隔（默认 15s / 5s） |
| `FLEET_GRPC_REQUIRE_CN` | mTLS 客户端证书 CN 必须等于 robot id（默认开启） |
| `AGENT_*` | 可选云端 LLM 编排（DashScope OpenAI 兼容端点） |

### edge-worker 环境变量

| 变量 | 说明 |
| --- | --- |
| `EDGE_ROBOT_ID` | 云端机器人身份（必填） |
| `EDGE_FLEET_URL` | 云端数据面 URL（公网画像 `https://域名`，开发画像 `http://127.0.0.1:8080`） |
| `EDGE_DEVICE_TOKEN` | 本机独立的数据面令牌（必填，对应 `FLEET_DEVICE_CREDENTIALS` 中 `EDGE_ROBOT_ID` 的条目） |
| `EDGE_FLEET_CA` | 可选：fleet CA 证书路径，用于校验 HTTPS 反向代理证书（自签证书画像必填） |
| `EDGE_FLEET_GRPC` + `EDGE_MTLS_CA/CERT/KEY/SERVER_NAME` | 可选：mTLS gRPC 公网接入通道（心跳/租约/服务器命令） |
| `EDGE_RUNTIME_ADDR` + `EDGE_RUNTIME_CA/CERT/KEY/SERVER_NAME` | Robot Runtime 地址与 mTLS 配置；仿真画像用 `EDGE_RUNTIME_INSECURE=1` |
| `EDGE_TASK_SOURCE` | `http`（长轮询，默认）或 `redis`（直连 Redis Stream，需 `REDIS_ADDR`） |
| `EDGE_WORLD_POSE` | 可选世界偏移 `x,y,z,yaw`（场景未烘焙偏移时使用） |
| `EDGE_TELEMETRY_INTERVAL` | 遥测上报间隔（默认 2s） |

`FLEET_WORLD_SNAPSHOT_PATH` 控制直接运行时的单主世界持久化；未设置则为内存世界。Compose 使用 `fleet-world` 卷中的 `/var/lib/tangying-fleet/world.json`。锁冲突、损坏或保存失败按失败关闭处理；不提供跨主机 HA，恢复后 delta 需重同步且要等待新观测。详见[部署与容量](production/deployment-and-capacity.md)。

## 完整 Fleet 流程

1. **任务入队**：操作员创建并审批任务 → 云端解析意图（支持
   「N号机器人…」绑定）→ 按机器人把任务 id 扇出到
   `fleet.tasks.ready.robot-N`（Redis Stream 消费者组）与共享 `any` 流。
2. **拉取任务**：edge-worker 从 Redis Stream 直连消费（局域网画像），或
   HTTP 长轮询 `GET /v1/queue/next?robot_id=robot-1`（经 nginx，公网画像）。
3. **认领意图**：worker 调 `POST /v1/tasks/{id}/intents/next`，云端协调器
   按序发放可执行意图（绑定机器人匹配 / 未绑定任意机器人认领），并置为
   RUNNING（声明租约 2m，超时自动回收，worker 崩溃不阻塞任务）。
4. **执行**：worker 对 Robot Runtime 做能力预检 → 场景 grounding →
   确定性物料化 7 步计划（observe → resolve → plan_grasp → pick →
   verify_grasp → place → verify_place，安全字段全部本地重造）→ 逐步骤
   执行并上报 `STEP_STARTED` / `STEP_SUCCEEDED` 事件。
5. **上报与世界栅栏**：意图本地七步执行后调用 complete，但协调器只在
   `EntityInside + EntityStable + RobotHeld(empty) + SourceFresh` 全部为真时推进。
   `409 WORLD_NOT_READY` 只重试 completion，不重放物理动作。验证后产生唯一
   `BLOCK_AVAILABLE` 并递增 fencing token，再事件驱动投递下一机器人；最终
   目标区同样验证后产生 `BLOCK_DELIVERED`。失败走
   `.../fail`，fail-closed：后续意图永不 READY，任务 `FAILED`。
6. **遥测**：worker 每 2s 上报世界系位姿 + 感知实体 + 本地占用栅格
   （实体光栅化，15×15 @ 0.1m）。主通道是 mTLS gRPC Link 流；链路断开时
   自动回退 HTTP `POST /v1/telemetry`（观测不依赖控制通道）。

## 机器人公网接入（mTLS gRPC）

- **证书**：`scripts/fleet-certs.sh` 生成 CA（私钥留在云端）、网关服务器
  证书、每台机器人一张客户端证书（CN=robot id）。证书目录
  `deploy/cloud/certs/` 是部署机密，已 gitignore。
- **Register**：edge-worker 启动时用客户端证书拨打
  `FLEET_GATEWAY_LISTEN`（默认 :8443），服务端 `RequireAndVerifyClientCert`
  + TLS 1.3，并强制校验客户端证书 CN 与 robot id 一致；配置项不能放宽该身份边界。
- **Link 双向流**：worker 每 5s 心跳 → 云端续租（15s）并回 Ack（含租约
  到期时间）；设备列表 `online` 由租约新鲜度判定，租约过期自动离线。
- **断线重连**：Link 断开后 worker 指数退避重连（1s→30s 封顶），重连后
  重新 Register + Link；期间任务数据面（HTTP/Redis）不受影响。
- **服务器命令下行**：Console 对设备执行「急停 / 取消步骤」→ 云端
  `PushCommand` → Link 下发 → worker 调 Robot Runtime 的
  `EmergencyStop` / `Cancel`。

## WebGL 数字孪生（真实模型渲染 + 鼠标交互）

工作台主画面是 **WebGL 数字孪生**（不再是占位几何）：从权威 MuJoCo 模型
确定性导出的 `scene.glb`（RoboCasa 厨房）与 `xlerobot.glb`（带关节节点的
XLeRobot）由本地自托管 Three.js 渲染器加载，`WorldSnapshot`（revision 驱动
的 WebSocket 增量）实时驱动机器人 base pose、关节、物体位姿、持有物与
freshness。支持左键平移 / 右键旋转 / 滚轮缩放 / 单击选择 / 双击聚焦 /
预设视角 / 跟随，语义叠加层（边界框、区域、标签、任务路径、资源
owner/fencing、异常标记）可独立开关。资产服务带 sha256 immutable cache
与 LICENSE/PROVENANCE，全离线自托管（无 CDN）；渲染器或资产失败时自动
降级到语义 Canvas 视图。任务过程经 MISSION RELAY 面板复述理解、展示步骤
与工具动作结果。渲染分辨率由 `ROBOCASA_RENDER_WIDTH/HEIGHT` 控制
（默认 320×240 保持稳定）。

## 实时上帝视角（God View）与 harness 反馈

Fleet 控制台提供「游戏式」实时上帝视角，用于观察多机器人协同执行与 harness
反馈：

1. **相机证据层**：每台机器人的 Robot Runtime 可渲染场景 PNG（320×240，
   内部 ~25Hz 快照），edge-worker 以 500ms 周期上传；云端缓存并暴露
   `GET /v1/scene/frames/{robot}`（`GET /v1/scene/frames` 列出在线画面）。
   控制台 1.5s 刷新双机并排实时画面。
2. **权威世界上帝视角**：`GET /v1/world` 返回 `world.snapshot.v1`，包含
   `revision/eventCursor`、robots、entities、resources、sources 与 freshness；
   `GET /v1/world/events/ws` 用一次性票据从游标续传，缺口返回
   `RESYNC_REQUIRED`。相机和旧融合栅格是辅助证据，不覆盖语义世界。
3. **交互**：左键拖动平移，右键拖动旋转，滚轮围绕指针所在世界点缩放，
   双击聚焦实体，`F` 恢复全景。画面只接受单调 revision，跳号先重同步。
4. **Harness 边界**：Harness Agent 读取与协调器完全相同的 WorldSnapshot，
   依赖来源新鲜度、观测证据、资源 owner/token 和环境变化，不读日志猜状态。
5. **可视时间**：仿真默认极速执行（毫秒级完成任务）。需要"看得见过程"
   时给 sim 加 `--human-speed 0.02`（每插值步 sleep 20ms），
   `scripts/fleet-sim.sh` 默认开启（`FLEET_HUMAN_SPEED=0` 可关闭）；
   验收/CI 使用默认 0，速度与结果不受影响。
6. **执行期实时性**：sim 世界锁保护物理状态，但观察走锁内滚动快照
   （`world.cached_*`，~25Hz 刷新），Observe/遥测永不阻塞，技能动画期间
   画面、物体位置与 held 状态持续可见。

## 多机器人全局地图融合

每台机器人上报（世界系，两个仿真场景通过 `scripts/gen_scene_variant.py`
生成的偏移 XML 直接把世界偏移烘焙进场景，因此位姿/实体天然同坐标系）：

- 世界系位姿 `pose [x, y, z, yaw]`（来自 Runtime `base_pose`，四元数→yaw）；
- 感知实体（id / 类别 / 位姿 / 置信度）；
- 本地占用栅格（机器人系，实体光栅化）。

`fleet/fusion` 是确定性纯函数：把每台机器人的本地栅格按位姿旋转平移进
全局栅格（max 合并）、按实体 id 去重（置信度高者胜）、附每台机器人轨迹。
`GET /v1/maps/global` 返回融合结果，Console 用 Canvas 渲染占用热区、
机器人航向三角形、多 robot_id 轨迹折线与实体标签。

## 安全边界

- 8080 不发布到宿主机：Compose 中 fleet-control-plane 只有 `expose`，
  公网唯一入口是 nginx（443 HTTPS + 8444 TCP 透传）。
- Console 路由需要操作员 Bearer token（`POST /v1/auth/login`，HMAC 签名，
  24h 过期）；设备数据面路由只接受 `X-Device-Token`（`fleet/auth` 路由
  白名单，设备令牌访问 Console 路由返回 403）。
- nginx 客户端 IP 白名单：`FLEET_ALLOWED_CIDRS` 生成 allow/deny 规则。
- gRPC 通道 mTLS 强制（网关无明文端口）；LLM 永远不能设置安全字段
  （deadline / lease / approval / idempotency 由 edge-worker 本地重造）。

## 生产（阿里云 ECS + ALB）

在 ECS 检出经审阅版本后，从仓库根目录运行 `./scripts/fleet-up.sh up`，按[阿里云部署指南](install/alicloud-cloud.md)配置域名、证书、来源白名单与独立设备凭据。可选 `scripts/deploy-alicloud.sh` 只发布已审阅并提交的 HEAD；要求本地 Go、已核对 SSH known_hosts 与远端 Docker，保留远端配置。具体使用条件见阿里云指南。只开放受控的 22/443/8444；不得公开 8080。

## API 摘要

| 方法 | 路径 | 认证 | 说明 |
| --- | --- | --- | --- |
| POST | `/v1/auth/login` | 公开 | 操作员登录，返回 Bearer token |
| GET | `/v1/devices` | 操作员 | 设备列表（在线/租约/能力） |
| POST | `/v1/devices/{id}/estop` | 操作员 | 经 mTLS 链路下发急停 |
| POST | `/v1/tasks` | 操作员 | 创建（自然语言，支持多机器人绑定） |
| POST | `/v1/tasks/{id}/approve` | 操作员 | 审批并入队（按机器人扇出） |
| GET | `/v1/tasks/{id}/intents` | 操作员 | 意图级任务图快照 |
| GET | `/v1/queue/next?robot_id=` | 设备 | 长轮询取任务 |
| POST | `/v1/tasks/{id}/intents/next` | 设备 | 认领意图 |
| POST | `/v1/tasks/{id}/intents/{i}/complete\|fail` | 设备 | 意图终态上报 |
| POST | `/v1/telemetry` | 设备 | 遥测上报（位姿/实体/占用栅格） |
| GET | `/v1/telemetry?robot_id=` | 操作员 | 单机遥测 + 轨迹 |
| GET | `/v1/maps/global` | 操作员 | 多机器人全局融合地图（含 held/placements） |
| GET | `/v1/scene/frames` `/v1/scene/frames/{robot}` | 操作员 | 实时画面帧（God View） |
| GET | `/v1/world` | 操作员 | harness 机器可读世界状态快照 |

## 无 Docker 开发画像

`FLEET_STORE=memory` 且不设 `REDIS_ADDR` 时，控制平面用内存任务仓库与
每机器人内存队列（测试与纯本地调试）；mTLS 网关未配置证书时自动禁用。
e2e 证据见 `tests/e2e/test_fleet_cloud.py`（真实双 MuJoCo + 双 worker 的本地进程闭环，耗时以本次运行记录为准）。
