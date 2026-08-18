# 树莓派快捷实机部署

适合已经组装好 XLeRobot、写入 Ubuntu Server 24.04 arm64 并联网的树莓派。推荐默认使用 ROS2-free 直连后端，减少首次实验变量。

## 1. 在树莓派上一键安装

```bash
gh repo clone SUSTechWLA/tangying-robot-agent-os
cd tangying-robot-agent-os
./scripts/robot-pi-quick-deploy.sh --dry-run
./scripts/robot-pi-quick-deploy.sh
```

脚本等价于：

```bash
ROBOT_AGENT_DIRECT_EDGE=1 ./install.sh robot-pi --yes
```

安装完成后服务保持停止，等待串口、标定、证书和安全检查。不会自动驱动电机。

## 2. 配置稳定串口别名

```bash
udevadm info --query=property --name=/dev/ttyACM0 | grep -E 'ID_SERIAL(_SHORT)?='
udevadm info --query=property --name=/dev/ttyACM1 | grep -E 'ID_SERIAL(_SHORT)?='
sudoedit /etc/udev/rules.d/99-tangying-xlerobot.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=tty
ls -l /dev/tangying-left /dev/tangying-right
```

## 3. 标定（会运动）

先完成[物理安全检查表](../safety-checklist.md)，清空工作区并让急停触手可及：

```bash
sudo -u tangying-robot /opt/tangying-robot-agent-os/.venv/bin/python \
  /opt/tangying-robot-agent-os/scripts/calibrate_xlerobot.py \
  --acknowledge-hardware-motion
```

## 4. 笔记本配对

```bash
# 笔记本
robot-agent pair xlerobot.local --ssh-user tangying-robot
```

## 5. 树莓派预检并启动

```bash
# 树莓派
sudo robot-agent doctor robot-pi
sudo robot-agent start robot-pi
sudo journalctl -u tangying-robot-edge.service -n 50 --no-pager
```

看到 `readiness: READY` 后，继续按 [XLeRobot 实验前检查](xlerobot-experiment.md) 完成首次最小动作和急停演练。
