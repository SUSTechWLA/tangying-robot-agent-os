# 全新部署：新机器 / 新机器人 / 新云服务器

这份文档是**冷启动的单一入口**：一台什么都没有的机器、一台还没接过的机器人、一台空云服务器，从这里开始。

已有文档解释"为什么这样分"（[部署目标与代码归属](deployment.md)）；这份只回答"我现在敲什么"。两者的关系是：**归属判据以 `deployment.md` 为准，操作步骤以本文为准。**

---

## 0. 先跑预检（唯一一步不能跳过的）

```bash
git clone https://github.com/SUSTechWLA/tangying-robot-agent-os.git
cd tangying-robot-agent-os
./scripts/precheck.sh
```

它会告诉你**这台机器能不能装、缺什么**，并且**只读**——不装任何东西、不改任何配置、不起任何服务。所以它可以放心反复跑。

```bash
./scripts/precheck.sh local        # 只查某个角色
./scripts/precheck.sh sim robot-pi # 查两个
```

输出分三类，**只有 FAIL 会挡住你**：

| | 含义 | 处理 |
| --- | --- | --- |
| `PASS` | 已满足 | — |
| `FAIL` | **缺必需的**，装了也跑不起来 | 必须处理 |
| `WARN` | 缺可选的，或只是提醒 | 可以先忽略 |

退出码：`0` 可以继续，`1` 有必需的没满足，`2` 参数写错。

> 为什么要有这一步：`install.sh` 只在失败时说一句 `unsupported platform for <role>`，它告诉你不满足，但不告诉你这台机器有什么、差多远。新机器上这正是"一条命令"和"一个下午"的区别。

---

## 1. 选路线

| 你想做的事 | 路线 | 需要什么 |
| --- | --- | --- |
| **先在仿真里跑通家庭场景**（推荐从这里开始） | [§2](#2-路线-a仿真跑通家庭场景最新机器) | 一台开发机，无需硬件 |
| 单台真机，不接云端 | [§3](#3-路线-b本地单机接一台真机) | 开发机 + 机器人 |
| 多台机器人，统一派单与审计 | [§4](#4-路线-c云端--机器人端) | 云服务器 + 机器人 |

**平台支持矩阵**（`install.sh` 会强制执行，预检会提前告诉你）：

| 角色 | 支持平台 |
| --- | --- |
| `sim` | macOS（Intel/ARM）、Ubuntu 22.04 / 24.04（amd64/arm64） |
| `local` | 同上 |
| `robot-pi` | **仅** Ubuntu 24.04 arm64 |
| `cloud` | 不是 `install.sh` 角色，是容器栈；只要有 Docker |

---

## 2. 路线 A：仿真跑通家庭场景（最新机器）

目标：在没有任何硬件的情况下，看到控制台，并对机器人说一句中文完成一次任务。

### 2.1 装

```bash
make setup          # 建 .venv、装 Python 依赖、go mod download、npm ci
./scripts/precheck.sh sim local
```

`make setup` 用 `python3.11`（`Makefile` 里的 `PYTHON ?= python3.11`）。机器上没这个命令就显式指定：

```bash
make setup PYTHON=python3
```

### 2.2 起

```bash
make home-furnished
```

这一条会建二进制、准备家庭场景资产、启动仿真 + Local Agent，并打印地址。

打开 **`http://127.0.0.1:8897/`**。

> 端口是 `8897`，不是 `8787`。`sim-stack.sh` 默认给 agent 用 8787，而 `home-furnished` 显式传 `--agent-port 8897`。README、这份文档和各脚本用的是同一个端口。

### 2.3 验证

在控制台输入：

```
从客厅出发，去厨房拿杯子，放进收纳盘，然后回到客厅
```

预期能看到完整的闭环：观察 → 导航 → 到达确认 → 重新观察 → 解析目标 → 规划抓取 → 拿取 → 抓取确认 → 放置 → 放置确认 → 返回 → 到达确认。

然后在"任务记录"里打开这个任务，应能看到**每一步的证据与恢复状态**，以及（本轮新增的）"系统观察与诊断"一节。

命令行验收：

```bash
make home-accept    # 家庭移动操作验收
```

### 2.4 资产是怎么来的

第一次启动时 `scripts/furnished-home-demo.sh` 会检查资产是否齐全，缺了就用 `scripts/prepare_home_world.py` 与 `scripts/prepare_furnished_home.py` 生成：

| 资产 | 路径 | 用途 |
| --- | --- | --- |
| 家庭场景几何 | `artifacts/sim-assets/aws-small-house` | 房子本体 |
| 家具包 | `artifacts/sim-assets/furnished-home` | 可操作物体 |
| 仿真模型 | `artifacts/sim-assets/harmonic-models` | Gazebo/MuJoCo 模型 |
| 已建地图 | `artifacts/maps/furnished-home` | 导航用 |
| 标定 | `artifacts/calibration/furnished-home` | 与地图配对 |

**这些是仿真环境的一部分，请保留。** 其中 `artifacts/maps/furnished-home` 与 `artifacts/calibration/furnished-home` 是一对：地图里登记的 `calibrationRevision` 必须与标定文件一致，删掉任何一个都会让导航在启动时报"标定不匹配"。

### 2.5 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `make setup` 报找不到 python3.11 | 系统只有 `python3` | `make setup PYTHON=python3`（需 3.11+） |
| 打开 8787 是空白 | 端口用错了 | 家庭场景在 **8897** |
| 控制台报"需要审批" | 物理动作默认需要批准 | 在界面上点"批准物理动作"，这是设计如此 |
| 导航报标定不匹配 | 地图与标定不成对 | 不要单独删 `artifacts/calibration/furnished-home` |
| 家庭场景资产缺失 | 被清掉了 | 重新 `make home-furnished`，脚本会重新生成 |

---

## 3. 路线 B：本地单机（接一台真机）

单台机器人、不需要云端调度。控制台在 `http://127.0.0.1:8787/`。

### 3.1 开发机

```bash
./scripts/precheck.sh local
./install.sh local --dry-run --yes    # 先看它打算做什么
./install.sh local --yes
```

装完启动：

```bash
make build && ./bin/local-agent
```

不传 `--config` 也可以：默认读取安装器与配对脚本写入的 `local.env`（Linux 是
`~/.config/tangying-robot-agent-os/local.env`，macOS 是 `~/Library/Application Support/TangyingRobotAgent/local.env`），
`ROBOT_AGENT_CONFIG_DIR` 可覆盖。**以前不传 `--config` 会静默退回 `127.0.0.1:50051` 明文**，
把刚配对好的地址和证书全部忽略掉，而且什么都不说。

### 3.2 机器人端

先确认平台：**Ubuntu 24.04 arm64**，其它一律不支持。

```bash
./scripts/precheck.sh robot-pi
./install.sh robot-pi --yes
./scripts/robot-pi-preflight.sh        # 上电前的离线检查
```

`robot-pi-preflight.sh` 会检查串口设备、标定文件、mTLS 材料有效期，以及 XLeRobot 驱动能否导入——**全部是不动机器人的检查**，所以可以放心在通电前跑。

配置在 `/etc/tangying-robot-agent-os/robot-pi.env`，模板见 `deploy/robot/raspberry-pi/robot-pi.env.example`。

启动之后，机器人会每 5 秒在局域网广播一次自己。**先确认它被发现了，再配对**：

```bash
curl -s http://127.0.0.1:8787/v1/robots/discovered | python3 -m json.tool
```

| 看到什么 | 说明 |
| --- | --- |
| `robots` 里有条目 | 广播链路是通的，`address` 就是可以直接用的地址 |
| `listening: false` | **没人在听**，不是"网上没有机器人" |
| `mismatched > 0` | **有机器人在广播，但协议版本读不了**——机器人就在那儿 |
| `unreadable > 0` | 端口上有无关流量，属于噪声 |

### 3.3 配对（真机必须做这一步）

配对做三件事：在笔记本上建一个本地 CA（私钥永不离开笔记本）、给机器人签服务器证书、
给笔记本签客户端证书，然后把机器人的那份装到机器人上。**有两条路径，产出完全相同。**

**路径一：控制台一键配对（不需要 SSH，推荐）**

前提是 [§3.2](#32-机器人端) 里那条 `curl /v1/robots/discovered` 能列出机器人。机器人未配对时会在启动后
开放 15 分钟配对窗口，并在日志里打印配对码：

```
pairing: window-open {"attempts": 5, "port": 45872, "seconds": 900}
pairing: code 4F2K-9QW7 (single use, expires with the window)
```

在控制台「发现的机器人」面板里填入这个码即可；也可以用接口：

```bash
curl -s -X POST http://127.0.0.1:8787/v1/robots/pair \
  -H 'Content-Type: application/json' \
  -d '{"robotId":"xlerobot-0001","address":"192.168.50.73:50051","code":"4F2K-9QW7"}'
```

配对码**只能用一次**，用错 5 次窗口会自动关闭（在机器人本地重新生成才能再开）。成功后机器人会自己重启
以切换到 mTLS；响应里的 `restartRequired` 说明本机 Local Agent 也需要重启才生效。

**路径二：SSH 脚本（没有局域网广播时，或有现成 SSH 通道时）**

```bash
robot-agent pair xlerobot.local --ssh-user ubuntu
```

- 机器人端口不是 50051 时：`ROBOT_AGENT_PAIR_PORT=<port> robot-agent pair ...`
- **脚本会真的去连一次那个端口。** 连不上就以非零状态退出，并打印要检查什么
  （`systemctl status tangying-robot-edge.service`、`journalctl -u ...`、网络、防火墙），
  而不是打印一句让人误以为成功的 `pairing complete`。
- 明确不想验证时用 `ROBOT_AGENT_PAIR_VERIFY=0`，输出会写明 `unpaired (unverified)`——
  "没验证"和"验证通过"不会长得一样。
- 配对成功后脚本会 `systemctl enable` 机器人服务，所以**断电重启后它会自己回来**。
  在这之前它是"已安装但停着"的状态：过早 enable 会让未配对的机器人在每次开机时启动、
  因缺证书失败、被 systemd 反复重启，而且没人被告知。

证书有效期 90 天，续期 = 重跑同一条命令（会轮换叶子证书、保留 CA）。

### 3.4 后台常驻

| 平台 | 单元 |
| --- | --- |
| macOS | `deploy/local/com.tangying.robot-agent.plist` |
| Linux | `deploy/local/tangying-robot-local-agent.service` |

安装时会 `enable`（注册为开机自启）但**不启动**：装完还没有证书，起不来。启动由操作者决定，
之后断电重启会自己回来。

开机后想确认"现在到底能不能用"，读这个而不是 `/healthz`：

```bash
curl -s http://127.0.0.1:8787/v1/readiness | python3 -m json.tool
```

它会给出 `ready`、一条人话总结、`nextId`（先处理哪一项）以及每一项该做什么。
`/healthz` 只回答"进程活着吗"。详见[可用性自检](../architecture/readiness.md)。

> 配对需要人**读一个码**，这是设计上不能绕过的：你无法认证一个和你没有共享秘密的设备。
> 码由机器人打印、一次性、有窗口、有次数上限，而且永不进广播。
> 详见[机器人配对](../architecture/robot-pairing.md)。

---

## 4. 路线 C：云端 + 机器人端

多台机器人、统一派单与审计。**云端不直接驱动硬件**：它只下发命令与租约，动作始终由机器人端执行。

### 4.1 云服务器

只需要 Docker。控制面是一套 Compose：

```bash
./scripts/precheck.sh cloud
make fleet-build                # 构建 bin/fleet-control-plane 与 bin/edge-worker
./scripts/fleet-up.sh up        # 生成 .env、mTLS 证书、白名单，起 Compose，等就绪
./scripts/fleet-up.sh env       # 打印机器人端接入所需的凭据
```

`fleet-up.sh` 会自动生成密钥与证书，**你不需要手写 `.env`**。生产环境要按自己的域名和证书链替换，见 [配置与安全](../production/configuration-and-security.md)。

| 端口 | 用途 |
| --- | --- |
| `443` | 控制台 HTTPS（nginx 终止，带 mTLS） |
| `8444` | gRPC 透传（机器人端接入） |
| `127.0.0.1:18080` | 仅回环的调试入口 |

日常操作：

```bash
./scripts/fleet-up.sh status
./scripts/fleet-up.sh logs
./scripts/fleet-up.sh down
```

> 预检里"端口 443 已被占用"这条告警在 macOS 上很常见：那通常就是 Docker Desktop 自己。先确认是不是它。

### 4.2 机器人端

同 [§3.2](#32-机器人端)，但 `robot-pi.env` 里的服务器地址与凭据要指向你的云服务器（用 `fleet-up.sh env` 打印的那份）。

### 4.3 一次起全部（本地联调）

```bash
./scripts/start-all.sh up                          # 仿真 + Local Agent + 控制台
./scripts/start-all.sh up --with-cloud --with-navigation
./scripts/start-all.sh status
./scripts/start-all.sh down
```

`start-all.sh` 只是按顺序调用各组件已有的生命周期脚本，不重复实现启动逻辑。缺 Docker 时它会**明确说明跳过原因**，不会静默继续。

---

## 5. 这份文档不覆盖什么

诚实列出来，免得你在别处找不到：

| 事 | 在哪 |
| --- | --- |
| RoboCasa 仿真线（双机厨房） | `make robocasa-install` → 会下载约 4.2G 到 `datasets/`。**本项目已清理掉这份数据**，需要时重新下载 |
| 真实硬件放行 | [安全检查表](safety-checklist.md)、[发布检查清单](release-checklist.md)。**软件装成功不等于现场已验收** |
| 标定与建图流程 | [注册服务工作流](../guides/robot-service-workflow.md) |
| 生产容量与高可用 | [部署与容量](../production/deployment-and-capacity.md) |
| 排障 | [安装排障](../install/troubleshooting.md)、[异常运维](../production/operations-and-failures.md) |

**两条独立结论**：云端部署成功不代表机器人可以动；机器人端软件装成功也不代表现场已验收。实机动作仍需负责人完成制动、标定与监护验收。

---

## 6. 相关文档

- 归属判据（哪个目录装到哪台机器）：[部署目标与代码归属](deployment.md)
- 各角色安装细则：[Local](../install/local.md)、[树莓派](../install/robot-pi.md)、[树莓派快捷部署](../install/robot-pi-quick.md)、[阿里云](../install/alicloud-cloud.md)
- 家庭场景操作：[装修家庭演示指南](../guides/furnished-home-demo.md)
- 一次启动全部组件：[部署目标 §6](deployment.md#6-一次启动全部组件)
