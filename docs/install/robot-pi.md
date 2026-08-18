# 树莓派 Robot Edge 安装与硬件准备

## 先理解硬件

STS3215 是使用 12 V 供电、通过 Feetech 串行总线通信的舵机，不是需要用户刷写应用的 STM32 开发板。XLeRobot 的两块 USB 舵机控制板承担 USB 与舵机总线之间的转换：

```text
树莓派 USB -> 两块舵机控制板 -> 12 V STS3215 总线舵机
```

舵机 ID、机械零位、12 V 极性、总线接线和实体急停属于硬件装配，不能被一键安装安全替代。接插舵机、电源或控制板前必须断开 12 V 执行器电源。

## 系统与软件安装

只支持 Raspberry Pi 4/5 上的 Ubuntu Server 24.04 arm64：

```bash
# 树莓派
uname -m
. /etc/os-release && echo "$ID $VERSION_ID"
gh auth login
gh repo clone SUSTechWLA/tangying-robot-agent-os -- --branch v0.1.0-rc.2
cd tangying-robot-agent-os
./install.sh robot-pi --dry-run --yes
./install.sh robot-pi --yes
```

预期平台为 `aarch64`、`ubuntu 24.04`。安装器会：

- 安装 ROS 2 Jazzy、Go、Python 环境和 rosdep；
- 把 XLeRobot 固定到提交 `3d14695e40c9c68229c0aacffca6053c75cd3eb6`；
- 安装 LeRobot 0.4.1 与 XLeRobot two-wheel 模块；
- 构建 ROS 2 workspace；
- 安装 `tangying-xlerobot.service` 和 `tangying-robot-edge.service`；
- 保持服务停止，等待串口、标定、证书和安全检查。

## 建立稳定串口别名

断开 12 V 执行器电源，只连接两块 USB 控制板。先列出设备：

```bash
# 树莓派
ls -l /dev/serial/by-id/ /dev/ttyACM* 2>/dev/null
udevadm info --query=property --name=/dev/ttyACM0 | grep -E 'ID_SERIAL(_SHORT)?='
udevadm info --query=property --name=/dev/ttyACM1 | grep -E 'ID_SERIAL(_SHORT)?='
```

根据物理接线确认哪块控制板是左臂/头部总线、哪块是右臂/底盘总线。编辑规则，分别替换两个占位符：

```bash
# 树莓派
sudoedit /etc/udev/rules.d/99-tangying-xlerobot.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=tty
ls -l /dev/tangying-left /dev/tangying-right
```

目标权限是 `root:dialout 0660`；不要用 `chmod 666`。若控制板没有稳定的序列属性，改用 `/dev/serial/by-id/...` 并通过下面命令写入配置：

```bash
sudo robot-agent configure robot-pi \
  XLEROBOT_PORT1=/dev/serial/by-id/LEFT_BOARD \
  XLEROBOT_PORT2=/dev/serial/by-id/RIGHT_BOARD
```

## 交互式标定（会运动）

先逐项完成[硬件安全检查表](../safety-checklist.md)，清空工作区，让急停触手可及，并从低风险姿态开始。标定会启用/禁用扭矩并要求人工移动关节，不能无人值守运行。

```bash
# 树莓派；确认安全后才运行
sudo -u tangying-robot /opt/tangying-robot-agent-os/.venv/bin/python \
  /opt/tangying-robot-agent-os/scripts/calibrate_xlerobot.py \
  --acknowledge-hardware-motion
```

成功文件必须为：

```text
/var/lib/tangying-robot-agent-os/calibration/tangying-xlerobot.json
```

标定脚本会读取 `robot-pi.env` 中的串口、标定目录和 `XLEROBOT_MAX_RELATIVE_TARGET`。服务运行时固定使用这个文件并自动接受恢复，不会在 systemd 后台启动交互式重新标定。改变舵机 ID、机械结构、控制板映射或维修关节后必须重新标定。

## 配对、无动作预检和启动

先从笔记本运行配对，再回到树莓派：

```bash
# 树莓派；doctor 不连接舵机、不启用扭矩
sudo robot-agent doctor robot-pi
sudo robot-agent start robot-pi
sudo robot-agent status robot-pi
sudo robot-agent logs robot-pi --follow
```

`doctor` 检查稳定串口可读写、校准 JSON 可解析、服务端证书至少还有 7 天有效期、XLeRobot 与 Gateway 可导入，并执行 `scripts/xlerobot_preflight.py` 做 XLeRobot 驱动参数与 blocker 检查。任何失败都会阻止“预检通过”结论。

首次物理动作前继续完成 [XLeRobot 实验前检查](xlerobot-experiment.md)，不要跳过急停演练。

## 停止、升级与恢复

```bash
# 树莓派
sudo robot-agent stop robot-pi
sudo systemctl stop tangying-xlerobot.service tangying-robot-edge.service
sudo journalctl -u tangying-xlerobot.service -u tangying-robot-edge.service -n 200
```

软件升级前先停服务、保留校准和证书，再重跑相同角色安装器：

```bash
git fetch --tags
git checkout v0.1.0-rc.2
sudo robot-agent stop robot-pi
./install.sh robot-pi --dry-run --yes
./install.sh robot-pi --yes
sudo robot-agent doctor robot-pi
```

安装器不会删除 `/var/lib/tangying-robot-agent-os`。出现异常运动时先按实体急停并断开 12 V 执行器电源，再检查日志，不能用重启反复试错。
