# 2026-09-26 生产就绪审计与实机验证交接

## 判定与适用范围

**当前结论：可进入受控现场集成准备；尚不能宣称实机生产上线。** 本文审计的代码基线为 `45758e8b658b` 加本轮尚未发布的工作区变更。正式验证时必须重新记录实际 Git commit、镜像 digest、模型和标定哈希；历史测试结果不自动覆盖新制品。本文是新增审计记录，保留 [云边升级规范](../superpowers/specs/2026-09-25-cloud-edge-brain-upgrade-adr.md)、[角色 Harness 规范](../superpowers/specs/2026-09-25-role-specific-agent-harness-docker-adr.md)及原验收记录，便于回溯。

| 层级 | 当前判断 | 还缺什么 |
| --- | --- | --- |
| 软件合同与安全门禁 | 候选；以本轮测试日志为准 | 当前提交的完整 CI、目标镜像重建与签名 |
| Orin NX 边缘部署 | 待目标机验证 | JetPack/容器运行时、量化权重、内存、推理时延、证书/文件权限、断网行为 |
| GPU 云端模型 | 待目标服务器验证 | 真实模型协议、工具调用、并发、限流、故障恢复及费用/资源预算 |
| 单机器人实机动作 | 未放行 | 真实标定、物理急停、停止距离、负载、观测、策略、结果未知处置及现场签字 |
| 多机器人生产 | 未放行 | 目标规模压测、World/Coordinator 单写、数据库/队列容灾、隔离与恢复演练 |

云端 Compose 当前是单主控制面。`FLEET_ROBOTS` 登记数和千台逻辑路由测试不能证明数百或数千机器人长期生产容量。Local Agent 与 Fleet Worker 对同一 Runtime 的本机控制锁只覆盖同地址、同主机进程；跨主机切换需要独立的任务冻结和控制权 fencing。

## 本轮软件加固与验证

- 云端 Compose 对 MySQL、operator、JWT 和设备凭据要求显式值；`FLEET_PRODUCTION=1` 在控制面开放监听之前检查存储模式与关键凭据强度。`scripts/fleet-up.sh` 仍负责首次生成私有 `.env`；直接运行 Compose 不再使用示例口令。
- 意图、规划、系统决策及 HTTP 策略客户端拒绝 HTTP 重定向，避免把模型密钥、任务内容或机器人观测送往未配置的目的地。测试覆盖 307 拒绝及客户端配置不被修改。
- 本机执行的 `make test`、`make lint`、`make generate-check`、`make build`、Compose 配置和 arm64 构建结果见下表；这些检查不连接真实机器人，也不验证目标 JetPack 或 GPU 驱动。

| 检查 | 结果/日志位置 | 负责人/日期 |
| --- | --- | --- |
| 本轮工作区 `make test` | 2026-09-26 本机通过；另跑相关 Go 包及部署/文档合同回归均通过 | 本机审计 |
| `make lint`、`make generate-check`、`make build`、`make edge-orin-build` | 2026-09-26 本机通过；后者仅交叉编译 Linux arm64 | 本机审计 |
| 云端/边缘 Compose 解析及缺凭据拒绝 | 2026-09-26 本机通过；云端使用本机私有 `.env`，边缘使用占位 GID 模板；缺必填变量均拒绝解析 | 本机审计 |
| 统一 Agent 镜像构建和非 root 身份 | 2026-09-26 本机 `linux/arm64` 构建成功，镜像 ID `sha256:eddd9f5f943c465de1a988e6f7bd5969649e6d980450bc4a6c6497686e684238`；容器 `id` 为 UID/GID 65534。此 ID 仅对应本机候选构建，不是推送仓库 digest | 本机审计 |
| Orin NX JetPack/GPU/量化模型运行与机器人连接 | 未执行，待目标机 | 待现场负责人 |

## 现场阶段 A：无运动预检

1. 冻结 Git commit、镜像 digest、Orin 模块/载板序列号、JetPack/Jetson Linux、容器运行时版本，以及量化模型权重/Tokenizer 哈希。NVIDIA 的 [Jetson Linux 指南](https://docs.nvidia.com/jetson/archives/r36.5/DeveloperGuide/IN/QuickStart.html)区分开发套件与生产模块；交付清单需写明实际硬件类型。GPU 容器如需访问设备，按 [NVIDIA 容器设置指南](https://docs.nvidia.com/jetson/orin-nano-devkit/user-guide/latest/setup_docker.html)核对目标 JetPack 对应的 Container Toolkit，不把开发机 Compose 解析当成 GPU 可用证明。
2. 确认 Runtime 未 arm、机械臂被支撑、底盘禁用、实体急停可触及。先记录驱动/标定、相机内外参、动作上限、地图版本与安全 profile；不要使用仿真豁免配置。
3. 在 Orin NX 按 [安装指南](../install/edge-orin.md)设置私有 env、证书和同机锁卷，执行 `docker compose ... config -q` 与容器内 `--check-config`。检查 UID 65534 真实可读证书和 env；只运行 `edge` 或 `fleet` 一个 profile。`--check-config` 只检查配置与文件，不能代替 TLS 主机名、模型 API 或 Runtime 实连接验收。
4. 云端用受控 secret store 注入强凭据，验证 `docker compose -f deploy/cloud/docker-compose.yml config -q` 与 HTTPS/mTLS；在反向代理外部验证 8080、MySQL、Redis 不可直连。备份 MySQL、Redis、World checkpoint 和 Runtime journal，并演练只读恢复核对。
5. 对每个模型阶段分别发无动作请求，记录响应 schema、错误映射、模型版本、p50/p95/p99、峰值内存与断网/超时/429/重定向行为。边缘云端 Assist 的令牌必须与 Worker 数据面令牌不同；云端工具不可批准或执行动作。

## 现场阶段 B：受监护实机与故障

由现场负责人先给出**设备专属**停止距离、延迟、负载、观测新鲜度和成功率阈值，再开始动作。先单关节/夹爪空载、再限定物体和工位；逐次保存命令 ID、TaskRevision、动作前后原始观测、Runtime journal、模型/策略版本及失败记录。独立验证实体急停、断网、相机/TF 陈旧、重复命令、进程重启、持物恢复和 `PHYSICAL_OUTCOME_UNKNOWN` 阻断。至少 30 次试验与 soak 记录是试点资料门槛，**不会自动授予** `PHYSICAL_GO`。现场签字、风险评审和每型号策略/标定验收仍必需。

## 现场阶段 C：目标规模与上线

按实际机器人数量、心跳/观测频率、任务混合和模型并发压测 MySQL、Redis、WorldHub、Gateway 与 GPU 推理池。记录队列 pending/outbox lag、世界新鲜度、任务 p99、停止响应、丢包、重连风暴、资源使用与成本。当前单主/单写架构若达不到容量或恢复目标，应先设计分片、HA 和跨主 fencing，再重新验收；不能靠复制 Compose 副本获得 HA。

上线决策材料应包含：需求范围及明确排除项、阈值及实测原始日志、失败与处理、恢复演练、回滚版本和制品哈希、现场负责人签名。任何模型权重、标定、地图、安全限幅、驱动、证书/身份、Fleet 或 Runtime 版本变化，都要评估并重跑受影响的门槛。回滚前冻结派单、确认持物及未知结果，保留 journal、事件和 World 版本；不能删除状态来绕过冲突。

## 本次交接记录模板

| 字段 | 现场填写 |
| --- | --- |
| 站点、日期、负责人、受限任务范围 |  |
| Git commit / 镜像 digest / SBOM 或构建记录 |  |
| Orin 模块与载板、JetPack、Container Toolkit、模型/Tokenizer 哈希 |  |
| GPU 服务器、模型版本、上游 API 与并发限制 |  |
| RobotID、Runtime 地址、硬件/标定/策略/地图哈希 |  |
| 证书身份、有效期、轮换与设备令牌发放记录（不填写秘密原文） |  |
| 实体急停、停止距离、断网、重复命令、持物与未知结果证据路径 |  |
| trial/soak/容量/恢复证据路径与事先设定的阈值 |  |
| 结论：继续集成 / 受监护试点 / 生产放行 / 拒绝及理由 |  |

可复用的逐次证据结构和现场签字边界见 [Sim2Real kit](../sim2real/README.md)与[生产就绪判定](../operations/production-readiness.md)。
