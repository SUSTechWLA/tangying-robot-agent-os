# 笔记本 Local Agent 安装、配对与恢复

## 支持范围

- macOS 13+，Intel 或 Apple Silicon
- Ubuntu 22.04/24.04，amd64 或 arm64
- 能访问云端 API和树莓派 `50051/tcp`
- 能通过 SSH 登录树莓派完成首次配对

笔记本不需要 ROS 2。它负责云端任务领取、本地 SQLite、场景落地/策略编排和到树莓派的 mTLS gRPC。

## 全新安装

```bash
# 用户笔记本
gh auth login
gh repo clone SUSTechWLA/tangying-robot-agent-os -- --branch v0.1.0-rc.2
cd tangying-robot-agent-os
./install.sh local --dry-run --yes
./install.sh local --yes
robot-agent configure local CLOUD_URL=https://robot-cloud.example.com AGENT_ID=my-laptop
robot-agent doctor local
```

macOS 状态与配置位于 `~/Library/Application Support/TangyingRobotAgent`；Ubuntu 配置位于 `~/.config/tangying-robot-agent-os`，状态位于 `~/.local/share/tangying-robot-agent-os`。私钥和配置使用 `0600`。

## 配对树莓派

```bash
# 用户笔记本
ssh ubuntu@xlerobot.local
robot-agent pair xlerobot.local --ssh-user ubuntu
robot-agent doctor local
```

如果 Raspberry Pi Imager 设置了别的用户名，把 `ubuntu` 换成实际用户名。首次 SSH 必须人工核对主机指纹；脚本不会使用 `StrictHostKeyChecking=no`。重新配对会更新叶证书但保留 CA；只有信任根泄露或主动轮换时才加 `--new-ca`。

## 启动和日志

```bash
# 用户笔记本，不使用 sudo
robot-agent start local
robot-agent status local
robot-agent logs local --follow
robot-agent restart local
robot-agent stop local
```

macOS 使用 launchd，Ubuntu 使用 systemd user service。配置错误时仍可随时执行 `robot-agent stop local`。

## 升级

```bash
# 用户笔记本，仓库检出目录
git fetch --tags
git checkout v0.1.0-rc.2
./install.sh local --dry-run --yes
robot-agent stop local
./install.sh local --yes
robot-agent doctor local
robot-agent start local
```

安装器不覆盖已有 `local.env`、证书或 SQLite 数据库。

## 恢复

```bash
robot-agent status local
robot-agent logs local --follow
robot-agent configure local
robot-agent version
```

常见问题：云端 URL 只能在浏览器访问但后台不能访问；树莓派 hostname 解析到了旧 IP；证书 SAN 与 `ROBOT_SERVER_NAME` 不一致；证书超过 90 天。网络地址变化后重新运行配对会签发包含新 IP 的服务端证书。
