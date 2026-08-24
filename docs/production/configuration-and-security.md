# 配置与安全手册

## 1. 配置清单

权威示例：`deploy/cloud/.env.example`、`deploy/config/local.env.example`、`deploy/config/robot-pi.env.example`。生产 `.env` 不进入 Git。

### Fleet 云端

| 变量 | 默认/示例 | 秘密 | 校验与重启影响 |
| --- | --- | --- | --- |
| `FLEET_OPERATOR_USER` | `admin` | 否 | 非空；改后重新登录 |
| `FLEET_OPERATOR_PASSWORD` | 生成随机值 | 是 | 禁止示例值；轮换使旧密码失效 |
| `FLEET_AUTH_MODE` | `required` | 否 | 仅允许 `required`/`demo`；生产必须为 `required`，改后重启 |
| `FLEET_AUTH_SECRET` | 启动生成 | 是 | HMAC 强随机；轮换使现有 JWT 失效 |
| `FLEET_DEVICE_CREDENTIALS` | `robot-id:token,...` | 是 | robot 唯一；轮换对应 Edge |
| `FLEET_ROBOTS` | `robot-1,robot-2` | 否 | 与证书/设备目录一致 |
| `FLEET_HTTPS_PORT` | 443 | 否 | 1–65535；代理重启 |
| `FLEET_GRPC_PORT` | 8444 | 否 | mTLS passthrough；网关重启 |
| `FLEET_ALLOWED_CIDRS` | 私网/loopback | 否 | 生产最小白名单 |
| `FLEET_WORLD_ID` | `fleet-default` | 否 | 变更相当于新世界，不能混用历史 |
| `FLEET_WORLD_FRESHNESS` | `1s` | 否 | 必须小于安全容忍窗口 |
| `FLEET_WORLD_DELTA_RETENTION` | 512 | 否 | 影响 WS gap/resync 内存 |
| `FLEET_HANDOFF_MAX_AGE` | `5s` | 否 | 超时后 Harness 不采信 |
| `FLEET_LEADER_LEASE` | `15s` | 否 | 大于续租周期；改后 Coordinator 重启 |
| `FLEET_RESOURCE_LEASE` | `2m` | 否 | 物体持有上限；过期需恢复流程 |

### 存储与 Agent

| 变量 | 说明 |
| --- | --- |
| `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_ROOT_PASSWORD` | Task/Event/Outbox；两个密码均为秘密 |
| `REDIS_ADDR`, `REDIS_PASSWORD`, `REDIS_STREAM`, `REDIS_GROUP` | 可执行流；消费组不能在同一环境随意改名 |
| `AGENT_PROVIDER` | `deterministic` 或 `openai` |
| `AGENT_BASE_URL`, `AGENT_MODEL`, `AGENT_ORCHESTRATION_SAMPLES` | LLM endpoint/model/采样数 |
| `AGENT_API_KEY` | 秘密；只在 Agent 进程，不发往机器人/浏览器状态 |

### Edge 与 Runtime

| 变量 | 说明 |
| --- | --- |
| `EDGE_FLEET_GRPC`, `EDGE_MTLS_CA`, `EDGE_MTLS_CERT`, `EDGE_MTLS_KEY`, `EDGE_MTLS_SERVER_NAME` | FleetGateway mTLS |
| `EDGE_RUNTIME_ADDR`, `EDGE_RUNTIME_CA`, `EDGE_RUNTIME_CERT`, `EDGE_RUNTIME_KEY` | Runtime mTLS |
| `EDGE_HEARTBEAT_INTERVAL`, `EDGE_LEASE`, `EDGE_WORLD_POSE`, `EDGE_OBSERVATION_SEQUENCE_BASE` | 心跳、lease、地图 pose、重启序列基线 |
| `ROBOT_GRPC_LISTEN`, `ROBOT_SERVER_KEY`, `ROBOT_SERVER_CERT`, `ROBOT_CLIENT_CA` | Runtime server |
| `ROBOT_RUNTIME_JOURNAL` | 幂等/终态 journal，目录必须持久化 |
| `XLEROBOT_PORT1`, `XLEROBOT_PORT2`, `XLEROBOT_CALIBRATION`, `XLEROBOT_CALIBRATION_ROOT`, `XLEROBOT_UPSTREAM_ROOT` | 实机串口与标定 |
| `XLEROBOT_MAX_RELATIVE_TARGET`, `XLEROBOT_MAX_ACTION_CHUNK_LENGTH` | 动作安全上限 |
| `ROBOT_ENTITY_PROVIDER`, `ROBOT_VERIFIER_PROVIDER` | 可选感知/验证插件入口 |
| `ROBOCASA_ENV_NAME`, `ROBOCASA_PORT_1`, `ROBOCASA_PORT_2` | 仿真环境和两个 Runtime 端口 |
| `SIM_STACK_SIM_PORT`, `SIM_STACK_AGENT_PORT` | Local 仿真端口 |

## 2. 网络端口

| 端口 | 绑定 | 协议 | 生产要求 |
| --- | --- | --- | --- |
| 443 | 公网/受控网 | HTTPS Console/API | TLS、CIDR/WAF/限流 |
| 8444 | 公网/机器人网 | mTLS gRPC | 双向证书、TLS 1.3 |
| 18080 | loopback | 本地 Fleet Console | 不公开 |
| 8787 | loopback | Local Brain | 不公开；局域网暴露需反代鉴权 |
| 50051/51051/51052 | 私网/loopback | Runtime gRPC | mTLS；仿真可显式 dev-insecure |

## 3. RBAC 与身份

至少区分 Viewer、Operator、Approver、SafetyOperator、Administrator。Viewer 只读；Operator 可建任务/更新；Approver 可批准；SafetyOperator 可取消/急停；Administrator 管用户、证书和配置。生产禁止共用 admin。机器人身份按设备独立，不能用操作员 JWT 代替设备凭证，也不能用设备 token 调操作员接口。

## 4. mTLS、JWT 与密钥轮换

- CA/私钥保存在受限 secret store 或 root-only 路径；证书包含明确 server/client usage。
- 轮换顺序：加入新 CA 信任 → 签发新证书 → Edge/Runtime 验证重连 → 撤销旧证书 → 删除旧私钥。
- `FLEET_AUTH_SECRET` 轮换会使 JWT 与 WS ticket 失效，应在维护窗口执行。
- `FLEET_DEVICE_CREDENTIALS` 按单 robot 轮换；避免全 fleet 同时离线。
- LLM key 只进入 Agent；配置状态 API 返回“已配置”而不是值。
- `scripts/fleet-up.sh up` 生成 `deploy/cloud/.env` 并 chmod 600；示例中的 `change-this-*` 绝不是密码。

`scripts/robocasa-fleet.sh` 会显式写入 `FLEET_AUTH_MODE=demo`，只为 loopback RoboCasa 页面签发短期 `demo-operator` 会话，因此页面不显示账号表单。这个模式不会放开机器人数据面：Edge 仍须使用每台机器人独立凭据，设备路由也拒绝操作员 token。通用 `scripts/fleet-up.sh up` 和 Compose 默认始终是 `required`，即使之前运行过 RoboCasa，再次用通用命令启动也会恢复账号认证；生产必须保持该值，并通过 `./scripts/fleet-up.sh env` 在受控终端读取生成的随机凭据。

## 5. 浏览器安全

Console 使用 CSP：脚本/样式/GLB/请求同源；资产有 SHA-256 和 model revision；WebSocket 使用一次性 ticket；DOM 只用 `textContent` 渲染服务端字符串；专业证据默认折叠。不要把 token 放在 URL、截图、日志或浏览器持久存储。视觉资产失败只降级视觉，不改变权威世界状态。

## 6. 备份、恢复与保留

- MySQL：每日全备 + binlog/PITR；每季度恢复演练；备份加密并与主账户隔离。
- Redis：队列可由 Outbox 重建，但在恢复前冻结派发，避免双消费。
- Runtime journal、calibration、地图、transform revision 和证书：按设备备份。
- 领域事件、审批、急停、Harness verdict、catalog/adapter 版本按监管周期保留；原始图像按最小必要和隐私规则保留。
- 签名 RoboCasa 证据包可进入发布制品，不包含 bearer/private key；candidate session 必须销毁。

## 7. 生产加固检查

禁用 dev-insecure；关闭非必要端口；最小容器权限；文件系统只读化；数据库最小权限；时间同步；日志脱敏；告警覆盖 lease/freshness/custody/outbox/磁盘；证书到期监控；依赖与镜像固定 digest；定期运行 `make test`、`make robocasa-acceptance` 和恢复演练。详细故障动作见[异常运维](operations-and-failures.md)。

## 8. 策略服务安全

`EDGE_POLICY_MODE=deterministic` 仅用于仿真，实机必须使用经过晋级的 HTTP Provider。`EDGE_POLICY_ENDPOINT` 应位于 loopback 或受控服务网格；限制请求/响应大小、并发和超时，不向模型容器提供机器人设备、Fleet 数据库或密钥。监控 `EDGE_ROBOT_MODEL`、`EDGE_TRANSFORM_REVISION`、`EDGE_CALIBRATION_REVISION` 与 PolicyManifest 漂移。模型制品按 SHA-256 固定，训练数据和 evaluation pack 有独立访问控制；任务日志和浏览器不得保存原始 action chunk。详见[学习型策略工具](policy-tools.md)。
