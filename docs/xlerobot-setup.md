# XLeRobot 集成边界

## 固定版本

首版适配器针对 XLeRobot two-wheel 上游提交：

```text
3d14695e40c9c68229c0aacffca6053c75cd3eb6
```

树莓派安装器把上游放在 `/opt/XLeRobot`，安装 LeRobot 0.4.1，并把 `xlerobot_2wheels` 与 `model` 模块放进隔离虚拟环境。适配器不修改上游仓库。

官方参考：[XLeRobot 物料清单](https://github.com/Vector-Wangel/XLeRobot/blob/main/docs/en/source/hardware/getting_started/material.md)、[组装文档](https://xlerobot.readthedocs.io/en/latest/hardware/getting_started/assemble.html)。硬件文档可能随上游变化；本发布始终以固定提交为准。

## 为什么没有 STM32 端

STS3215 是 12 V 串行总线舵机。用户不在每个舵机或单独 STM32 上安装本系统：

```text
Raspberry Pi
  USB
两块舵机控制板
  Feetech serial bus + 12 V actuator power
STS3215 servos
```

控制板承担 USB/串行总线转换；树莓派运行 ROS 2、Robot Gateway、Safety Supervisor 和 XLeRobot LeRobot 驱动。舵机 ID、供电、控制板左右映射和机械装配按官方设计完成。

## 运行时接口

适配器使用上游：

```text
XLerobot2WheelsConfig
XLerobot2Wheels.get_observation()
XLerobot2Wheels.send_action()
XLerobot2Wheels.stop_base()
XLerobot2Wheels.disconnect()
```

固定配置包括：

- robot id：`tangying-xlerobot`
- port1：`/dev/tangying-left`
- port2：`/dev/tangying-right`
- calibration：`/var/lib/tangying-robot-agent-os/calibration/tangying-xlerobot.json`
- `XLEROBOT_MAX_RELATIVE_TARGET` 默认 `8.0`
- `XLEROBOT_MAX_ACTION_CHUNK_LENGTH` 默认 `64`
- 桌面模式拒绝任何 `x.vel` 与 `theta.vel` 键，拒绝非有限值或超出 `[-100, 100]`（夹爪 `[0, 100]`）的动作值，并且失败关闭而不是静默裁剪
- 驱动停止后本地锁存，直到操作员检查并通过 `SafetySupervisor.clear_local(operator_present=True)` 复位；常规服务停止后建议直接重启 `tangying-robot-edge.service`
- 仿真模型按官方 IKEA RÅSKOG 置物推车版增加 `ikea_cart` 与 `cart_depth` 顶部深度相机；真实模型若使用相同版本，可直接把上游 mesh 替换到 `sim/mujoco/assets/xlerobot` 保持视觉一致

上游在发现已有校准文件时会询问是否恢复。systemd 没有交互终端，因此适配器只在预检确认固定校准文件存在后自动选择“恢复已有标定”，绝不会在后台开始标定。

## 失败关闭门禁

任何物理动作前必须同时满足：

1. `/opt/XLeRobot` 与固定集成模块存在；
2. 两个稳定串口设备存在并可由 `tangying-robot:dialout` 读写；
3. 固定命名的校准 JSON 非空，且上游机器人报告 `is_calibrated`；
4. mTLS 服务端密钥/证书与客户端 CA 有效；
5. 实体急停已测试；
6. 本地感知提供场景实体；
7. 本地策略提供有界 `action_chunk`；
8. 每个高层物理技能具备审批、deadline、lease 和幂等键。

缺少策略动作时返回 `POLICY_ACTION_CHUNK_REQUIRED`；策略、感知、验证 provider 抛异常分别返回 `POLICY_PROVIDER_FAILED`、`ENTITY_PROVIDER_FAILED` 语义异常或 `VERIFIER_FAILED`；串口、集成或校准缺失返回对应 blocker，不得回退成模拟成功。

`robot-agent doctor robot-pi` 现在额外执行 `scripts/xlerobot_preflight.py`：实例化 XLeRobotDriver 并检查驱动参数、串口、固定集成和校准 JSON，但**绝不调用 `connect()`、`send_action()` 或启用扭矩**。任何动作仍必须在完成 [物理安全检查表](safety-checklist.md) 后按 [XLeRobot 实验前检查](install/xlerobot-experiment.md) 执行。

具体操作见[树莓派安装与硬件准备](install/robot-pi.md)和[物理安全检查表](safety-checklist.md)。
