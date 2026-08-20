# 阿里云一键部署 Fleet Control Plane

## 1. 准备 ECS

- Ubuntu 22.04/24.04
- 安装 Docker Engine 与 Compose plugin
- 安全组开放 22 和 8080（正式环境建议只开放 443 反向代理）

## 2. 本地上传并部署

```bash
ALICLOUD_SSH_HOST=1.2.3.4 \
ALICLOUD_SSH_USER=root \
ALICLOUD_SSH_KEY=~/.ssh/id_rsa \
bash scripts/deploy-alicloud.sh
```

脚本会：

1. 打包当前仓库，排除 `.git`、`.venv`、`XLeRobot`、日志和产物；
2. 上传到 `/opt/tangying-robot-agent-os`；
3. 生成 `deploy/cloud/.env`；
4. 执行 `docker compose up -d --build`。

## 3. 组件

```text
fleet-control-plane :8080
mysql 8.4
redis 7
```

默认数据卷：

```text
fleet-mysql
fleet-redis
```

## 4. 验证

```bash
curl http://1.2.3.4:8080/healthz
```

```json
{"mode":"fleet","status":"ok"}
```

创建任务：

```bash
curl -fsS -X POST http://1.2.3.4:8080/v1/tasks \
  -H 'Content-Type: application/json' \
  --data '{"request":"把红色杯子放进右侧收纳盒","adapter":"mujoco"}'
```

## 5. 接入阿里云 LLM

编辑 `deploy/cloud/.env`：

```bash
AGENT_PROVIDER=openai
AGENT_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AGENT_API_KEY=sk-...
AGENT_MODEL=qwen-plus
AGENT_ORCHESTRATION_SAMPLES=3
```

然后：

```bash
docker compose up -d
```

## 6. 安全建议

- 不要把 8080 直接暴露公网；前面挂 Nginx/ALB 和 HTTPS。
- MySQL 密码、Redis 密码和 LLM API Key 必须修改。
- 生产环境开启安全组白名单。
- 机器人端继续通过 mTLS gRPC 接入 Edge Agent，不直接调用控制平面。

## 7. 生产安全加固（Fleet 版）

当前 Fleet 部署画像（`deploy/cloud/docker-compose.yml`）中 8080 **不再发布**到
宿主机：fleet-control-plane 只有 `expose`，公网唯一入口是 nginx（443 HTTPS 控制台
/ 数据面 + 8444 TCP 透传 mTLS gRPC 机器人通道）。完整架构、环境变量与 API 表见
[Fleet 云端控制平面](../../docs/fleet-cloud.md)。

### 入口与域名

| 项 | 说明 |
| --- | --- |
| 安全组 | 只开放 22 / 443 / 8444；8080 永不开放 |
| 域名直连 | 域名解析到 ECS，用域名证书替换 `deploy/cloud/certs/fleet-server.crt` / `.key`（同时更新 nginx 挂载与网关 mTLS 服务器证书） |
| ALB 终结 TLS | 443 → ALB（域名证书）→ 内网 nginx 80 → fleet 8080；安全组只对 ALB 来源开放 |

### 访问控制

- 白名单：`deploy/cloud/.env` 中 `FLEET_ALLOWED_CIDRS` 收紧为办公网段
  （`scripts/fleet-up.sh` 会据此生成 nginx allow/deny 规则）。
- mTLS：保持 `FLEET_GRPC_REQUIRE_CN=true`，机器人客户端证书 CN 必须等于 robot id。
- 口令：改掉 `.env` 中所有默认口令（`FLEET_OPERATOR_PASSWORD`、`MYSQL_*`），
  设备令牌用 `openssl rand -hex 32` 生成并妥善保管。
