# 阿里云 Fleet 部署

当前云端画像是 `deploy/cloud/docker-compose.yml`：MySQL、Redis、Fleet 与 nginx。当前 V1 仍需现场和生产环境验收，见[V1 状态](../production/v1-release-status.md)。旧 Cloud/PostgreSQL 安装不适用。

## 1. 准备主机

准备 Linux ECS、Docker 与 Compose 插件、Git、OpenSSL、可访问的域名和 TLS 证书。按用户/机器人规模评估容量；本仓库没有给出已压测的通用容量承诺。首次 SSH 人工核对主机指纹。

安全组只向管理来源开放 SSH，向批准来源开放 HTTPS 443 和机器人 mTLS 8444。8080、8443、MySQL 与 Redis 保持容器内部；18080 只供宿主机 loopback 调试。

## 2. 在 ECS 检出与配置

```bash
git clone https://github.com/SUSTechWLA/tangying-robot-agent-os.git
cd tangying-robot-agent-os
# 检出经审阅的 release/commit，并记录版本
./scripts/fleet-up.sh up
./scripts/fleet-up.sh status
```

脚本首次创建私有 `deploy/cloud/.env`、证书与 nginx 来源白名单。上线前按[配置与安全](../production/configuration-and-security.md)设置真实域名、正式 TLS 证书、机器人列表、独立设备凭据和最小来源范围，再重新加载服务。开发自签证书与默认地址只供受控测试。

```bash
# 只在受控 SSH 终端查看；不要复制进日志、截图或工单
./scripts/fleet-up.sh env
```

本页的手工路径使用仓库根目录的 `fleet-up.sh`。也可从本地使用修复后的 `scripts/deploy-alicloud.sh`：本地需 Go，目标机需 Docker/Compose 和所需 sudo 权限；首次 SSH 需人工核对并保存 known_hosts。脚本使用 `StrictHostKeyChecking=yes`，只打包已提交 HEAD 和该版本生成的 Go vendor，拒绝未提交的 tracked 改动，保留远端已有配置，并从仓库根目录调用 Fleet 启动。未跟踪的本地配置不会进入包。

```bash
# 先审阅并提交交付版本，再从该干净 tracked 工作树执行
ALICLOUD_SSH_HOST=fleet.example ALICLOUD_SSH_USER=ubuntu \
  ALICLOUD_SSH_KEY=/absolute/path/to/private-key bash scripts/deploy-alicloud.sh
```

这会写入远端并启动服务，不是只读预览。部署后仍需独立验证域名、证书、白名单、迁移、持久卷和设备身份；上传成功不等于生产验收。

## 3. 验证和接入

用实际 HTTPS 域名访问 `/healthz` 和 Console。部署到公网必须由可信证书验证，不用 `-k` 消除 TLS 错误。机器人端按[配置参考](../production/configuration-and-security.md)设置 Fleet mTLS、Runtime mTLS 和每台设备独立身份；不要把操作员 JWT 当设备令牌。

先用[双机器人仿真](../robocasa-handoff.md)或受限预生产环境验证注册、观测、任务、停止与恢复，再接真实设备。真实设备的 onboarding 和阶段证据见[购机后上手](../sim2real/README.md)。

当前内置身份区分 operator/device，尚未提供 Viewer/Approver/Administrator 等完整细粒度角色管理；多租户或组织级权限需额外实现与验证。部署单主世界快照时，只有一个 Fleet 进程可写同一快照；不能通过复制容器获得 HA。

## 4. 运维与升级

按[异常运维](../production/operations-and-failures.md)与[部署容量](../production/deployment-and-capacity.md)备份数据库、世界快照、证书和每台 Runtime journal。升级前冻结派发、确认安全状态、备份并记录版本；恢复后等待新观测，不能用旧快照直接证明现场成功。

服务端 LLM 使用 `AGENT_PROVIDER`、`AGENT_BASE_URL`、`AGENT_MODEL`、`AGENT_API_KEY`。默认确定性模式不需要 LLM；模型只负责理解/规划，不授予硬件动作权限。
