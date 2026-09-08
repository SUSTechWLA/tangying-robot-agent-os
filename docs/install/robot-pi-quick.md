# 树莓派快捷实机部署

适用于已完成组装、运行 Ubuntu Server 24.04 arm64 的 Raspberry Pi 4/5。第一次购买与安装先读[购机后 Sim2Real 上手](../sim2real/README.md)，确认设备与固定 two-wheel 集成版本匹配。默认运行 ROS2-free direct backend，移动底盘禁用。

## 1. 安装

```bash
git clone --branch v0.2.0 https://github.com/SUSTechWLA/tangying-robot-agent-os.git
cd tangying-robot-agent-os
# 记录正式版本对应的实际提交
./scripts/robot-pi-quick-deploy.sh --dry-run
./scripts/robot-pi-quick-deploy.sh
```

安装器通过 `.[robot-pi]` 安装共同兼容的 Runtime、LeRobot 与 Feetech SDK，执行 `pip check` 后保持服务停止。它不会完成硬件兼容验证、标定、配对或动作授权；依赖与相机 SDK 范围见[完整安装指南](robot-pi.md)。

## 2. 串口与标定

执行器断电时确认左右控制板与稳定序列号，按[完整安装指南](robot-pi.md)设置 `/dev/tangying-left`、`/dev/tangying-right` 和 `dialout` 权限。不要依赖会交换编号的 `ttyACM0/1`。

标定可能使能扭矩或要求人工移动关节。完成[物理安全检查表](../safety-checklist.md)，清空工作区并由现场人员操作：

```bash
sudo -u tangying-robot /opt/tangying-robot-agent-os/.venv/bin/python \
  /opt/tangying-robot-agent-os/scripts/calibrate_xlerobot.py \
  --acknowledge-hardware-motion
```

## 3. 配对与无动作预检

```bash
# 笔记本：使用树莓派上已能 SSH 登录且具备所需 sudo 权限的账户
ssh ubuntu@xlerobot.local
robot-agent pair xlerobot.local --ssh-user ubuntu
robot-agent doctor local
```

首次 SSH 人工核对主机指纹。`tangying-robot` 是安装器创建的服务用户，不应假定它已经具备 SSH 登录凭据。

```bash
# 树莓派
sudo robot-agent doctor robot-pi
# 启动会连接并禁用现有扭矩/配置总线；先支撑机械臂
sudo robot-agent start robot-pi
sudo journalctl -u tangying-robot-edge.service -n 50 --no-pager
```

`readiness: READY` 仅表示能力配置检查通过；默认服务不自动授权动作。连接、现场 arm、provider 和第一次最小动作按[实验前检查](xlerobot-experiment.md)及[Sim2Real 上手](../sim2real/README.md)继续。重启不是急停复位。
