# 分布式异常与快速排障手册

## 1. 排障总原则

先保证人和设备安全，再恢复一致性，最后恢复吞吐。任何时候都遵守安全不变量：旧 revision 不覆盖新 revision；旧 fencing token 不驱动物理资源；观测不新鲜不判成功；软件急停不替代实体急停；不要手工改 Task 历史、World revision、source sequence、custody token、Harness evidence 或签名摘要。

统一检查顺序：用户看到的 WORLD/VISUAL/任务提示 → `/healthz` 与设备 lease → Task/Revision/事件 → Coordinator/Outbox/Redis → Runtime/catalog → World sources/freshness/frame → Harness verdict/evidence。

HTTP `/healthz` 成功只说明 Web 进程可响应，不能证明 Robot Runtime 已监听或已获得可执行的场景。启动演示/集成测试时，应先等待 Runtime 身份与能力查询成功、新鲜观测及所需实体已投影，再创建和批准任务；“来源已登记”也不能替代来源 FRESH。

自然语言问题先区分三层：创建时 422 `UNSUPPORTED_INTENT`（修改时 400 `REVISION_FAILED`）是未完整理解或不支持；`grounding absent`（这一帧没有匹配物体/区域/起点）、`grounding ambiguous`（匹配到多个候选）与 `grounding source mismatch` 是观测不符；Runtime 拒绝则检查能力和资源授权。绑定失败的消息同时给出机器人、adapter、观测数量、可见实体和场景切换命令，先按它区分“场景不对”和“相机或识别不对”。保留原输入，按具体原因处理，不能删除约束后自动重试。固定正反例见[语言评测](../development/natural-language-evaluation.md)、现场排查步骤见[统一故障排查](../install/troubleshooting.md#任务一开始就失败找不到物体)。

## 2. 账号、网络与基础设施

单机器人 Local 的操作路径另见[单机器人闭环](../development/single-robot-loop.md)：先 GET `/v1/tasks/{id}/recovery`，用户安全暂停等待 `PAUSED`；只重启 Local Agent 后仍需显式 resume。已完成物理步骤保留不重放；开始后没有可信终态的动作返回 `PHYSICAL_OUTCOME_UNKNOWN`，不可在网页强制跳过。不要重置仿真/真实现场后声称验证了持物恢复。

相机诊断先看原始 RGB/depth、采集时间、来源和标定，再看重建与动作后关系。当前图像接口 404 表示缺失、415 表示格式异常、503 表示不可用/过期；历史观测不套用实时新鲜度，410 表示保留预算已清理原始内容。Local SQLite 原始证据最多保留 512 条和 256 MiB，元数据继续累积，需要部署者制定数据库备份与归档；这不是全速视频录像。证据保存失败有 `OBSERVATION_EVIDENCE_FAILED`，完成工具不能补造图像编号。

| 场景 | 现象 | 检查 | 安全不变量 | 自动/人工恢复 | 防止复发 |
| --- | --- | --- | --- | --- | --- |
| 登录/JWT 失败 | 401、页面回登录 | Fleet 日志、系统时钟、`FLEET_AUTH_SECRET`、用户状态 | 不绕过鉴权 | 重新登录；若轮换则结束旧 session；确认后重启 auth | NTP、密钥轮换、operator/device 权限审计 |
| 证书过期/CA 错误 | Edge TLS handshake fail、设备 OFFLINE | `openssl x509 -dates`、证书 SAN/usage、CA 链 | 禁止 `-k`/dev-insecure 进入生产 | 先加入新 CA，再签发/滚动重连，最后撤旧 CA | 到期 30/14/7 天告警 |
| DNS/路由/CIDR | Console 或 gRPC 不通 | DNS、443/8444、nginx allowlist、双向 traceroute | 不扩大到 `all` 作为永久修复 | 临时隔离后修正 DNS/防火墙；验证 mTLS | 基础设施即代码、连通性探针 |
| 时钟漂移 | token 早过期、观测 stale、证据时间倒退 | `date`/NTP、observed/received 时间差 | 不手改证据时间 | 暂停派发，同步时钟，Runtime 重注册并使用新 sequence | chrony/NTP 告警 |
| MySQL 不可用 | 创建/审批 5xx，已有机器人可能仍运行当前命令 | DB 连接、磁盘、主从、连接池 | 不从 Redis 反写 Task 真值 | 停止新派发；恢复主库/PITR；校验 Event/Outbox | HA、备份恢复演练 |
| Redis 不可用/重复 | 队列积压或重复领取 | Redis、consumer group、pending entries、Outbox lag | 至少一次必须由幂等吸收 | 恢复 Redis；从 Outbox 重放；不要清空未审计 PEL | 持久化、lag 告警、幂等测试 |
| Outbox 卡住 | Task approved=true 但无执行 | DB outbox 状态、publisher 心跳、Redis stream | 不直接把 Task 改 RUNNING | 重启 publisher；按 event ID 重放 | 指标和死信 runbook |
| 磁盘/内存/CPU | 延迟、OOM、证据写失败 | 容量、GC、队列、帧存储、进程限制 | 证据不完整不能签名成功 | 限流/停止新任务；扩容；安全重启 | 配额、容量预测、日志轮转 |

## 3. 协调、任务与队列

| 场景 | 现象 | 检查 | 安全不变量 | 自动/人工恢复 | 防止复发 |
| --- | --- | --- | --- | --- | --- |
| leader lease 丢失 | Coordinator 停止领取 | leader token、续租时间、DB/Redis 延迟 | 失去 lease 后不发新命令 | 新实例取得更高 epoch 后重放；核对旧实例停止 | lease 与提交同存储 fencing、chaos test |
| 重复事件/命令 | UI 重复活动或机器人疑似重复动作 | event/command/idempotency ID、Runtime journal | 相同 key 不重复副作用 | 投影去重；Runtime 返回既有终态 | 全写接口幂等键 |
| 倒序事件 | 状态回退尝试 | aggregate version、WS revision、source sequence | 旧事实丢弃 | REST resync；重放缺失事件 | 单调序列测试 |
| revision gap | UI 停止更新并请求快照 | 当前/收到 revision、delta retention | 不跨 gap 猜状态 | GET `/v1/world` 或 task experience，替换 socket generation | retention/重连指标 |
| 更新 CAS 冲突 | 409 `REVISION_CONFLICT` | 当前 revision、expectedRevision/expectedCurrentRevision、幂等 key | 不覆盖别人更新 | 拉取历史，重新生成预览并让用户再次确认 | UI 保留原输入、显式冲突提示 |
| WAITING_SAFE_POINT 过久 | 更新轨道持续等待 | 当前工具 cancellable、held/custody、checkpoint event | 不硬中断持物/不可逆动作 | 等工具边界；必要时人工安全放置后确认 | 工具设计更细检查点、超时告警 |
| 任务/intent lease 超时 | 步骤停在 CLAIMED/RUNNING | worker heartbeat、lease、command terminal | 未确认旧命令停止前不重派同资源 | fencing token 增加后重领；Harness 重验世界 | 恢复场景测试、合理 lease |
| 进程崩溃 | 当前任务中断 | journal、EventLog、Outbox、command status | 重启不自动重放副作用 | 重放投影，查询 Runtime journal/世界，再决定继续或补偿 | supervisor、崩溃恢复测试 |

## 4. 机器人、工具与观测

| 场景 | 现象 | 检查 | 安全不变量 | 自动/人工恢复 | 防止复发 |
| --- | --- | --- | --- | --- | --- |
| 机器人离线 | 设备 OFFLINE、步骤等待 | Edge/Runtime、mTLS、heartbeat/lease、电源 | 不向离线机器人分派 | 网络恢复自动注册；持物时现场处置后提高 fencing | 双链路/UPS、离线演练 |
| Runtime/adapter mismatch | `RUNTIME_MISMATCH` | software/protocol/runtime/adapter version | 不降级猜协议 | 回滚匹配版本或升级 Fleet，重新注册 | 兼容矩阵、固定镜像 digest |
| tool/catalog mismatch | `CATALOG_MISMATCH` | command 与 Register catalog revision | 不调用未知工具 | 等 Edge 重注册；重新规划当前 revision | catalog 合约测试 |
| 工具超时且世界未变 | 活动超时、Harness 不满足 | SkillEvent、deadline、关节/实体观测、blocker | 不因返回超时/成功直接改世界 | Cancel；观察安全终态；允许策略化重试或人工恢复 | timeout/可取消/检查点设计 |
| 工具成功但 Harness 失败 | UI 显示“动作已结束，环境未确认” | evidence IDs、后置条件、source freshness | 任务保持未完成 | 重新观察；必要时补偿动作 | 传感器覆盖与条件设计 |
| stale observation | WORLD STALE、模型冻结/去饱和 | source last seen、budget、sequence | STALE 不恢复为 FRESH，除非新 revision/sequence | 修复传感器/网络；收到新观测后恢复 | freshness SLO 与告警 |
| 矛盾观测 | custody/held `CONFLICT` | robot held、entity relation、resource owner、时间/frame | 不投票决定资源所有权 | 停止动作；校准/刷新；人工确认并产生新事实 | 多源一致性测试 |
| 地图/transform mismatch | 机器人/物体错位、Harness frame 拒绝 | world/frame/transform revision、外参 | 不混用不同 revision | 暂停；重新定位/标定；以新 transform 重发 | 地图版本管理 |
| source sequence 回退 | 重启后观测全被丢弃 | sequence baseline/journal | 不接受倒序 | 从持久化 baseline+1 启动或注册新 source generation | 持久化 sequence |
| custody/fencing 冲突 | 409、红色 conflict 标记 | owner/token/lease/held 三源 | 旧 token 永不复活 | 安全停止双方；确认物理持有；以新 token 完成恢复 saga | 原子 custody 迁移、故障注入 |
| Harness timeout | 步骤等待验证 | required sources、world age、evidence parse | 无证据不成功 | 重新观察；修复 provider；人工只能取消/恢复，不能伪造 SATISFIED | 覆盖率与延迟预算 |
| emergency stop | 急停锁存、动作停止 | 实体/软件急停、Runtime blocker、电源 | 不自动解除 | 现场排险；实体复位；显式软件复位；低速再验收 | 定期急停演练 |

异构适配器报 profile/reconstruction 错误时，先保存经过脱敏的 robotId、adapterId、sourceId、sequence、observationId、采集时刻和 transformRevision。对照 `RuntimeInfo.robot_profile` 与实际采样：单位必须为 m、实体和点必须已在 world、四元数为 wxyz，来源与标定版本必须完全匹配。身份或 profile 生命周期内漂移时，停止任务并完成配置更新与重登记；不能删除 profile 或改成 legacy 来恢复动作。

`EXECUTION_OUTCOME_UNKNOWN` 表示不能确信物理执行结果，包括驱动已接收命令后异常或非法返回。Runtime 会停止并持久锁存；先用现场观测与 journal 对账，再按本地恢复流程处理，不直接重试命令、不删除 journal。合法的 `TARGET_UNREACHABLE` 等已知失败需要修正具体原因，不能一律当作设备已发生未知运动。

旧帧要区分相机停止采集、机器时钟差和 Edge 轮询频率。`EDGE_TELEMETRY_INTERVAL` 默认 2 秒，新来源若声明更短 `maxAgeMs`，应按实际采样、网络延迟和多源轮询周期缩短该间隔；禁止把 observedAt 改为发送时刻。关节缺失时核对 `state_provider.joints` 的原生关节名与 profile 声明，不能填零冒充测量。MCP 的 `OUTCOME_UNKNOWN` 先在控制台查询任务或设备状态，避免再次提交同一动作意图。接口和复现命令见[适配器手册](../development/robot-adapters.md)。

## 5. 浏览器与数字孪生

若 REST `/v1/world` 持续有新版本而预览页延迟，检查 WS 握手 Origin/Host；当前预览已保留浏览器 Host，不应改成内部端口或删除 Origin。若 Task 已完成但步骤仍停在准备状态，核对实际加载的 `app.js`：Experience 完整快照当前没有 cursor，同 revision/aggregateVersion 仍要刷新；World 的同版本事实规则不能用于拦截任务进展。

| 场景 | 现象 | 检查 | 安全不变量 | 自动/人工恢复 | 防止复发 |
| --- | --- | --- | --- | --- | --- |
| WebSocket 断开 | WORLD STALE/CONNECTING | ticket、close code、revision、REST | 旧 socket 回调失效 | 新 ticket+退避；REST resync | socket generation 回归测试 |
| GLB/hash/model mismatch | VISUAL DEGRADED | manifest、同源 URL、SHA-256、modelHash | WORLD 状态不被视觉篡改 | 重建 `make robocasa-web-assets`；部署包含四个脚本的十角色一致版本 | 确定性 bundle/资产签名 |
| CSP/外部请求 | 资源拒绝或验收网络失败 | 浏览器控制台、CSP、page-assets inventory | 不放宽到任意域 | 移除外联，改同源固定资产 | CI 网络闭包测试 |
| Canvas fallback | WORLD LIVE / VISUAL DEGRADED | WebGL context、GPU、资产 | 语义实体/路径/状态仍可用 | 重试视觉或换浏览器；任务数据继续 | context-loss 测试 |
| 浏览器性能低 | 卡顿、display rAF 低 | rAF、renderer submission mean/p90/p95、DPR/backing | 不伪造 FPS | 关闭非必要标签/路径仅作诊断；升级 GPU/浏览器 | 实机可见 display rAF 独立验收 |
| 刷新后任务空白 | revision/experience 请求失败 | 当前 task ID、JWT、旧 fetch generation | 旧响应不能覆盖新选择 | 重新选择任务/登录；检查 API | 刷新与 race 测试 |

## 6. 灾难恢复

1. 触发实体安全停机，冻结新任务和资源转移。
2. 保存数据库、Outbox、Redis PEL、WorldHub 快照、Runtime journal、地图/标定、证书和日志快照。
3. 从受信备份恢复 MySQL；校验事件连续性和 aggregate version。
4. 重建 Redis/投影；不要从 UI 状态反推权威事件。
5. 轮换可能泄露的操作员/设备/JWT/mTLS 密钥。
6. Edge 逐台重注册；World sources 全 FRESH 且 transform 匹配后才允许 dry-run。
7. 对所有持物资源人工核对物理状态，以更高 fencing token 重新建立 custody。
8. 运行单机器人受限验收，再恢复双机器人和生产任务。

升级给研发时携带：时间范围、task/revision/command/event/source IDs、版本、日志、世界 revision、custody token、是否持物/急停；不得携带密码、JWT、设备 token、私钥或未脱敏图像。

## 7. 学习策略异常

策略异常分为 OBSERVATION_WAIT、POLICY_RETRY、POLICY_BLOCKED、EXECUTION_RECONCILE、SAFE_RECOVERY 和 SAFETY_STOP。前两类只有在 Runtime 尚未接收动作时才允许有界自动重试；清单/机器人/地图/标定不匹配和越界动作直接阻断；连接在物理结果返回前中断时只能读取 Runtime journal 与环境观测对账。具体检查项、公开技术码和恢复动作见[学习型策略工具](policy-tools.md#8-恢复状态机)。

## 8. 单主世界快照故障

设置 `FLEET_WORLD_SNAPSHOT_PATH` 后，损坏/世界身份不符或被另一 writer 锁定会阻止启动，保存失败会阻止世界推进。检查路径、所属用户、磁盘和是否存在另一 Fleet 进程；不要删除文件绕过。先冻结派发并核对现场，再恢复受信 checkpoint，重新注册并获取新观测。Compose 数据位于 `fleet-world` 卷；delta 环形缓存不跨重启，浏览器需重新 GET `/v1/world`。

实机出现 `ROBOT_NOT_CONNECTED` / `ROBOT_NOT_ARMED` 时核对现场授权流程，不自动连接/arm 重试。急停的现场复位和重新 arm 按[Sim2Real 上手](../sim2real/README.md)执行；服务重启不解除锁存。

## 9. 仿真回合结束后的再次执行

RoboCasa 当前按单回合单向交接实现。完成后要求反向搬运可能返回 `FENCING_TOKEN_STALE`；还需核对场景允许的 robot/destination，不能一律归因于网络。重跑开发演示按[RoboCasa profile](../robocasa-handoff.md)重启自己的仿真栈；独立语言评测则停止自己的 `--keep-running` 进程，使用新输出目录重新运行。网页切换视图不会重置回合。

完成后通用重新授权和反向动作尚未实现；不得删除 journal、降低 token 或扩大 Runtime 方向范围来伪造支持。这是目前需要补齐的能力预检与执行生命周期工作，不能把该仿真重启流程用于实机恢复。
