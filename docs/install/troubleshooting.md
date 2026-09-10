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

检查 loopback 端口冲突、本地状态目录权限和配置语法。LLM API 故障不会让 Console 下线；完整已知请求优先确定性解析，不依赖 LLM；其余表达的模型解析失败会返回解析错误。含未解决约束的请求需要澄清，不通过模型绕过。修改 LLM 配置后若状态显示 `restartRequired`，重启 Local Agent。

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
sudo journalctl -u tangying-robot-edge.service -n 200
```

| 错误 | 处理 |
| --- | --- |
| `SERIAL_PORTS_UNAVAILABLE` | 修复 udev 映射或 dialout 权限 |
| `CALIBRATION_REQUIRED` | 停服务后按 runbook 重新标定 |
| `XLEROBOT_LEROBOT_INTEGRATION_MISSING` | 重跑树莓派安装 |
| `UPSTREAM_SOURCE_UNSUPPORTED` / `LEROBOT_VERSION_UNSUPPORTED` | 核对固定版本和实际导入文件，使用经测试的内部兼容层，不删除版本检查 |
| `ROBOT_NOT_ARMED` / `ROBOT_NOT_CONNECTED` | 按 Sim2Real 现场流程显式连接/授权，不在后台自动重试 |
| `ENTITY_PROVIDER_REQUIRED` | 接入实体感知 provider |
| `POLICY_ACTION_CHUNK_REQUIRED` | 接入已验证的有界动作策略 |
| `VERIFICATION_UNAVAILABLE` | 接入结果 verifier |
| `MOBILE_BASE_DISABLED` | 桌面配置禁止底盘运动，不得绕过 |

## 任务一开始就失败：找不到物体

固定工位任务在批准后几秒内进入 `RECOVERABLE_FAILURE`，最后一条 `STATE_CHANGED` 类似：

```text
ground subtask 1: grounding absent: objects=0 destinations=0; robot=... adapter=mujoco observed=0; this scene commissions no pickable objects, ...
```

`grounding absent` 表示这一帧没有匹配项，`grounding ambiguous` 表示匹配到多个候选，两者处理不同。

先看错误里的 `observed=` 与 `visible=`，再按层排查，不要先改解析器或放宽物体匹配：

| 现象 | 含义 | 处理 |
| --- | --- | --- |
| `observed=0` | 相机这一帧没有可见的已配置物体 | 先确认场景是否为固定工位（下表），再看相机画面是否正常 |
| `observed>0` 但 `visible=` 里没有目标颜色或类别 | 场景不是这句话对应的世界 | 切换到该任务已配置的场景，或改用该场景支持的说法 |
| `visible=` 里有两项同类物体 | 场景里有多个候选，系统按设计要求澄清 | 说得更具体，不能靠重复发送绕过 |

`make rgbd-start`、`make rgbd-restart` 显式使用 `--scene tabletop`。直接调用生命周期脚本时，**省略 `--scene` 会沿用上一次记录的场景**，因此刚跑过家庭路线后，只写 `--perception rgbd` 可能重新打开不配置桌面物体的 `home` 场景，桌面任务必然找不到物体。脚本现在会打印提示：

```text
sim-stack: reusing recorded scene 'home' from stack.env
sim-stack: the 'home' scene commissions no tabletop objects; pass '--scene tabletop' (or '--scene home_task') to switch explicitly
```

按提示显式切换并重建现场：

```bash
make sim-stop
bash scripts/sim-stack.sh restart --perception rgbd --scene tabletop
```

物体在场景中存在却观测不到时，先确认相机与新鲜度，而不是改物体名或颜色：

```bash
bash scripts/sim-stack.sh status
curl -s "http://127.0.0.1:8787/v1/telemetry?adapter=mujoco&limit=1" | head -c 400
```

需要确认“这句话到底被绑定成哪个物体/终点”时，查看任务的事件与证据：

```bash
curl -s "http://127.0.0.1:8787/v1/tasks/TASK_ID" | grep -o '"message":"[^"]*"'
curl -s "http://127.0.0.1:8787/v1/tasks/TASK_ID/observations"
```

`objects=0 destinations=1` 这类逐项计数说明只有一个引用没绑上；此时物体没有被移动，任务可安全重试。恢复语义见[单机器人 V1](../production/single-robot-v1.md)。

## 停止总是优先

配置损坏时仍可执行：

```bash
robot-agent stop local
sudo robot-agent stop robot-pi
```

软件停止无效时立即使用实体急停切断执行器电源。

急停锁存不会因 `restart` 清除，禁止删除 Runtime journal 绕过。现场复位、重新连接/arm 与重新验收见[购机后上手](../sim2real/README.md)。
