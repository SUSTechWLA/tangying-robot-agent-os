# 云端安装、启动与恢复

## 支持范围

- Ubuntu 22.04/24.04 或 Debian 12
- amd64 或 arm64
- 有 sudo 权限、能访问 Docker 与 Go 下载源
- 至少 2 CPU、4 GB 内存、20 GB 可用磁盘用于首版试用

## 全新安装

```bash
# 云主机
gh auth login
gh repo clone SUSTechWLA/tangying-robot-agent-os -- --branch v0.1.0-rc.2
cd tangying-robot-agent-os
./install.sh cloud --dry-run --yes
./install.sh cloud --yes
curl -fsS http://127.0.0.1:8080/healthz
sudo robot-agent doctor cloud
```

安装器把发布内容放到 `/opt/tangying-robot-agent-os`，配置放到 `/etc/tangying-robot-agent-os/cloud.env`，回执放到 `/var/lib/tangying-robot-agent-os/install.json`。第一次安装会把示例 PostgreSQL 密码替换为随机十六进制密码；重跑不会覆盖现有配置。

## 网络边界

默认：

```text
Cloud API  127.0.0.1:8080
PostgreSQL 127.0.0.1:54329
```

不要把当前 API 直接暴露公网。推荐让反向代理完成 HTTPS 和身份认证，或使用只对三端开放的私网；然后配置笔记本：

```bash
# 用户笔记本
robot-agent configure local CLOUD_URL=https://robot-cloud.example.com
```

短期调试可使用持续 SSH 隧道：

```bash
# 用户笔记本，保持终端运行
ssh -N -L 18080:127.0.0.1:8080 ubuntu@cloud-host
robot-agent configure local CLOUD_URL=http://127.0.0.1:18080
```

## 日常操作

```bash
# 云主机
sudo robot-agent start cloud
sudo robot-agent stop cloud
sudo robot-agent restart cloud
sudo robot-agent status cloud
sudo robot-agent logs cloud --follow
```

## 升级

数据库备份后再切换发布：

```bash
# 云主机，仓库检出目录
git fetch --tags
git checkout v0.1.0-rc.2
./install.sh cloud --dry-run --yes
./install.sh cloud --yes
curl -fsS http://127.0.0.1:8080/healthz
```

安装器保留现有配置和 PostgreSQL volume。它不会代替生产备份策略。

## 恢复

```bash
sudo robot-agent status cloud
sudo robot-agent logs cloud --follow
sudo docker compose --env-file /etc/tangying-robot-agent-os/cloud.env \
  -f /opt/tangying-robot-agent-os/deploy/docker-compose.yml ps
curl -v http://127.0.0.1:8080/healthz
```

若修改了 `POSTGRES_PASSWORD`，已有 PostgreSQL volume 内的数据库密码不会自动同步；应恢复原配置或按 PostgreSQL 流程更新数据库角色，不能只改环境文件。
