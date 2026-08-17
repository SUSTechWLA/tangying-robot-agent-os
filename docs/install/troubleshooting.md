# 统一故障排查

## 通用顺序

1. 物理异常先按实体急停并切断执行器 12 V 电源。
2. 运行本机 `robot-agent doctor ROLE`。
3. 查看 `robot-agent status ROLE` 与 `robot-agent logs ROLE --follow`。
4. 核对三端都在相同 `robot-agent version`/Git 标签。
5. 先修复最靠近硬件的一端：树莓派 -> 笔记本 -> 云端。

## 安装器中断

安装器按角色设计为可重跑，现有配置、证书、数据库和校准不会被覆盖：

```bash
./install.sh ROLE --dry-run --yes
./install.sh ROLE --yes
```

平台不支持时会在任何变更前退出。不要通过修改测试环境变量绕过平台门禁；`ROBOT_AGENT_TEST_*` 只有同时设置 `ROBOT_AGENT_TEST_MODE=1` 时才生效，并且只供自动测试。

## 云端不健康

```bash
sudo robot-agent status cloud
sudo robot-agent logs cloud --follow
sudo docker compose --env-file /etc/tangying-robot-agent-os/cloud.env \
  -f /opt/tangying-robot-agent-os/deploy/docker-compose.yml ps
curl -v http://127.0.0.1:8080/healthz
```

检查端口冲突、磁盘空间、PostgreSQL volume 和密码是否一致。不要删除 volume 作为第一步。

## 笔记本领取不到任务

```bash
robot-agent doctor local
robot-agent logs local --follow
curl -v "$CLOUD_URL/healthz"
```

确认任务已审批、Local Agent 的 `AGENT_ID` 稳定、云端 URL 能由后台服务访问、SSH 隧道没有退出。

## mTLS 连接失败

```bash
# 笔记本
robot-agent pair xlerobot.local --ssh-user ubuntu
openssl x509 -in "$HOME/Library/Application Support/TangyingRobotAgent/certs/server.crt" -noout -dates 2>/dev/null || true
```

Ubuntu 笔记本的证书在 `~/.local/share/tangying-robot-agent-os/certs`。hostname/IP 变化、证书过期或 `ROBOT_SERVER_NAME` 不匹配时重新配对；不要先轮换 CA。

## 树莓派预检失败

```bash
sudo robot-agent doctor robot-pi
ls -l /dev/tangying-left /dev/tangying-right
sudo -u tangying-robot test -r /var/lib/tangying-robot-agent-os/calibration/tangying-xlerobot.json
sudo journalctl -u tangying-xlerobot.service -u tangying-robot-edge.service -n 200
```

错误含义：

| 错误 | 处理 |
| --- | --- |
| `SERIAL_PORTS_UNAVAILABLE` | 修复 udev 序列属性、左右板映射或 dialout 权限 |
| `CALIBRATION_REQUIRED` | 停服务并按树莓派 runbook 重新标定 |
| `XLEROBOT_LEROBOT_INTEGRATION_MISSING` | 重跑 `./install.sh robot-pi --yes` |
| `POLICY_ACTION_CHUNK_REQUIRED` | 接入已验证的本地策略；不能把高层 pick/place 直接当舵机动作 |
| `MOBILE_BASE_DISABLED` | 桌面配置禁止底盘运动；不要绕过 |

## 停止总是优先

配置损坏时生命周期命令仍不解析业务配置：

```bash
robot-agent stop local
sudo robot-agent stop robot-pi
sudo robot-agent stop cloud
```

若软件停止无效，使用实体急停切断执行器电源。
