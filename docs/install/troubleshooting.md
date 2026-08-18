# 统一故障排查

## 通用顺序

1. 物理异常先按实体急停并切断执行器 12 V 电源。
2. 在对应机器运行 `robot-agent doctor local` 或 `sudo robot-agent doctor robot-pi`。
3. 查看 `robot-agent status ROLE` 与 `robot-agent logs ROLE --follow`。
4. 核对笔记本和树莓派的软件版本。
5. 先修复靠近硬件的树莓派，再检查直连网络和笔记本。

安装器可安全重跑，已有配置、证书、SQLite 和标定不会被覆盖：

```bash
./install.sh ROLE --dry-run --yes
./install.sh ROLE --yes
```

## Local Agent 不健康

```bash
robot-agent doctor local
robot-agent logs local --follow
curl -v http://127.0.0.1:8787/healthz
```

检查 loopback 端口冲突、本地状态目录权限和配置语法。LLM API 故障不会让 Console 下线；支持的请求应回退确定性解析。修改 LLM 配置后若状态显示 `restartRequired`，重启 Local Agent。

## Robot Runtime 连接失败

```bash
robot-agent pair xlerobot.local --ssh-user ubuntu
robot-agent doctor local
```

检查笔记本能否解析/访问树莓派地址、证书 SAN、`ROBOT_SERVER_NAME`、证书有效期及两端时钟。地址改变或叶证书过期时重新配对；不要先轮换 CA，也不要为方便关闭 SSH 主机指纹检查。

## 树莓派预检失败

```bash
sudo robot-agent doctor robot-pi
ls -l /dev/tangying-left /dev/tangying-right
sudo journalctl -u tangying-xlerobot.service -u tangying-robot-edge.service -n 200
```

| 错误 | 处理 |
| --- | --- |
| `SERIAL_PORTS_UNAVAILABLE` | 修复 udev 映射或 dialout 权限 |
| `CALIBRATION_REQUIRED` | 停服务后按 runbook 重新标定 |
| `XLEROBOT_LEROBOT_INTEGRATION_MISSING` | 重跑树莓派安装 |
| `ENTITY_PROVIDER_REQUIRED` | 接入实体感知 provider |
| `POLICY_ACTION_CHUNK_REQUIRED` | 接入已验证的有界动作策略 |
| `VERIFICATION_UNAVAILABLE` | 接入结果 verifier |
| `MOBILE_BASE_DISABLED` | 桌面配置禁止底盘运动，不得绕过 |

## 停止总是优先

配置损坏时仍可执行：

```bash
robot-agent stop local
sudo robot-agent stop robot-pi
```

软件停止无效时立即使用实体急停切断执行器电源。
