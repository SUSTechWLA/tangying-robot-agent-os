# 笔记本 Local Agent 安装、配对与恢复

## 支持范围

- macOS 13+（Intel/Apple Silicon）
- Ubuntu 22.04/24.04（amd64/arm64）
- 可访问用户选择的 LLM API（可选）
- 可经 SSH 完成首次配对，并直接访问树莓派 gRPC 地址

笔记本不需要 Docker、PostgreSQL 或 ROS 2。它是任务和用户数据的唯一业务状态权威。

## 安装与首次配置

```bash
./install.sh local --dry-run --yes
./install.sh local --yes
robot-agent configure local
robot-agent doctor local
```

Local Agent 默认只监听 `127.0.0.1:8787`。启动后打开该地址，在首次页面填写 LLM API Base URL、模型与密钥；保存配置后按页面提示重启服务。确定性模式无需 API Key。

配置、证书和 SQLite 状态在升级时保留；敏感后备配置权限为 `0600`。

## 配对树莓派

```bash
ssh ubuntu@xlerobot.local
robot-agent pair xlerobot.local --ssh-user ubuntu
robot-agent doctor local
```

首次 SSH 必须人工核对主机指纹。配对由笔记本 CA 签发机器人服务端证书和笔记本客户端证书；CA 私钥不离开笔记本。主机地址或证书变化时重新配对，只有主动轮换信任根时使用 `--new-ca`。

## 生命周期

```bash
robot-agent start local
robot-agent status local
robot-agent logs local --follow
robot-agent restart local
robot-agent stop local
```

macOS 使用 launchd，Ubuntu 使用 systemd user service；笔记本命令不要使用 sudo。Console 在 LLM 或机器人离线时仍可打开，并分别显示依赖状态。

## 升级与恢复

```bash
git fetch --tags
git checkout <release-tag>
./install.sh local --dry-run --yes
robot-agent stop local
./install.sh local --yes
robot-agent doctor local
robot-agent start local
```

安装器不会覆盖现有本地配置、证书或 SQLite 数据库。故障时先查看日志和 `doctor`；不要删除数据库来处理普通连接问题。
