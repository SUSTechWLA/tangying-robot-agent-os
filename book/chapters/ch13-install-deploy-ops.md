# 第 13 章 安装、部署与运维

> **本章的核心命题**
>
> 这套系统把"**能不能装**"和"**有没有发现问题**"都变成了**可验证的**。
>
> 而它验证的方式，是**两个只读的工具**和一个**只观测、不注入**的故障注入器。

---

## 13.1 三个角色，不是一个角色

先纠正一个常见的误解：**`install.sh` 只有 3 个角色，不是 4 个。**

`cloud` 已被移除并硬报错（`install.sh:41-44`）：

```
error: cloud role was removed; install the local role on the user's laptop
exit 2
```

**实测**：`./install.sh cloud` → 上述错误，`[exit=2]`。

**但四个角色同时出现在预检脚本里**（`scripts/precheck.sh:338` 的白名单是 `sim|local|robot-pi|cloud`）——

**这正说明预检与安装是两个不同的工具**（11.6 详述）。

### `install.sh` 的接口

**共 94 行。**

| 项 | 事实 |
| --- | --- |
| 角色 | **位置参数**，**没有 `--role` flag**（传 `--role` 会命中 `:63-67` 的 `unknown argument`） |
| flag | `--yes`（`:45-47`）、`--dry-run`（`:48-50`）、`--version VERSION`（`:51-58`）、`-h/--help`（`:59-62`） |
| 骨架 | `:82` source `scripts/install/common.sh` → `:84` `detect_platform` → `:85` `validate_role_platform` → `:86` `print_plan_header` → `:88-92` source 角色脚本 → `:94` `install_role` |

### `--dry-run` 的真实机制

`common.sh:24-35` 的 `run()`：

```bash
在 DRY_RUN=1 时打印 DRY-RUN + 每个参数 shell-quote（printf %q）后直接 return 0
```

`run_in_root()` 打印 `DRY-RUN cd <root> && …`；`confirm_mutation()` 在 dry-run 下**直接放行、不问也不检查 tty**；`write_receipt()` 打印 `DRY-RUN write receipt <dest>`。

**仍会真实执行的是平台探测与版本比对**——因为"**这台机器是什么**"本身是只读的。

**实测输出**（macOS 26 / arm64）：

```
$ ./install.sh sim --dry-run
PLAN role=sim os=darwin distro=macos version=26 arch=arm64 release=v0.7.0
==> preparing simulation development stack
DRY-RUN brew install go python@3.11 protobuf
DRY-RUN cd <root> && python3.12 -m venv .venv
DRY-RUN cd <root> && .venv/bin/pip install -e .\[dev\]
DRY-RUN cd <root> && go build -ldflags -X\ main.version=v0.7.0 -o bin/robot-agent ./cmd/robot-agent
DRY-RUN cd <root> && go build -o bin/local-agent ./cmd/local-agent
==> simulation installed; run: ./bin/robot-agent demo
```

**教学要点**：**`--dry-run` 打印的是 shell-quote 后的真实命令**，不是一句描述。

| 做法 | 用户能得到什么 |
| --- | --- |
| 打印"将安装依赖" | **什么都不能验证** |
| **打印 `printf %q` 后的完整命令** | **可以复制出来手动跑，也可以审查** |

**这是"预演"和"描述"的区别。**

### 三个角色各装什么

| 角色 | 脚本 | 装什么 |
| --- | --- | --- |
| **`sim`** | `scripts/install/sim.sh`（32 行） | macOS：`brew install go python@3.11 protobuf`；Ubuntu 22.04：`add-apt-repository ppa:deadsnakes/ppa` + `apt-get install python3.11{,-venv,-dev} protobuf-compiler …` 并设 `ROBOT_AGENT_PYTHON=python3.11`；`ensure_go`；`pip install -e '.[dev]'`；`build_go_binaries`；写回执。**不装任何服务单元** |
| **`local`** | `scripts/install/local.sh`（62 行） | macOS：`brew install go`；Linux：`apt-get install ca-certificates curl git openssl` + `ensure_go`；`build_go_binaries`；**仓库快照安装**（`git archive` + `tar`，**不是 clone**）；`install -m 0755 bin/robot-agent /usr/local/bin/robot-agent`；`local.env` 权限 **0600 且已存在则保留**；`state_dir/certs` 0700；macOS 渲染 launchd plist 并 `launchctl bootstrap gui/<uid>`，Linux 装 systemd user unit 并 `enable`（**不带 `--now`，故意不启动**） |
| **`robot-pi`** | `scripts/install/robot-pi.sh`（133 行） | `useradd --system --create-home --groups dialout --shell /bin/bash tangying-robot`；`git clone https://github.com/Vector-Wangel/XLeRobot.git /opt/XLeRobot` + **`checkout --detach 3d14695e…`**；仓库快照到 `/opt/tangying-robot-agent-os`；**隔离 venv**（direct 模式**不加** `--system-site-packages`）+ `pip install -e ".[robot-pi]"` + `pip check` + 复制 pinned XLeRobot 双轮集成到 `lerobot.robots`；`robot-pi.env`（0600，chown tangying-robot）；`certs` 0700 / `calibration` 0750；**跳过 ROS2 workspace 构建**（direct 模式）；装 systemd unit + udev 规则 |
| **`cloud`** | — | **`install.sh` 硬拒绝**；实际入口是 `./scripts/fleet-up.sh up` |

### 三条值得注意的细节

**① `local` 用仓库快照，不是 clone。**

```bash
git archive | tar
```

**为什么？** 因为安装的目标是**一个确定的版本**，不是"一个会跟着上游变的仓库"。一个 `git clone` 装出来的东西，明天 `git pull` 就会变。

**② 装服务单元但故意不启动。**

`local` 角色 `enable` 但**不带 `--now`**，收尾提示：

> Local Agent installed but not started; run robot-agent configure, then robot-agent start local

`robot-pi` 角色同样：

> Robot Edge installed but stopped pending certificates, serial devices, calibration, and safety checklist

**教学要点**：**安装完成 ≠ 可以运行。**

一个装完就自动启动的服务，会在配置、证书、标定都还没就绪时启动——而它**可能动硬件**。

（这与 `PluginBackend` 的"构造一个适配器从不需要连接、解锁或移动硬件"是同一条原则。）

**③ `robot-pi` 的 venv 是隔离的。**

direct 模式**不加** `--system-site-packages`。

**为什么？** 因为机器人端要和一个固定的 LeRobot 版本共存，而 `--system-site-packages` 会让系统 Python 的包泄漏进来——**破坏版本的可复现性**。

### 平台白名单

`common.sh:119-131`：

| 角色 | 支持 |
| --- | --- |
| `sim` / `local` | macOS（任意版本）+ Ubuntu 22.04 / 24.04 |
| **`robot-pi`** | **仅 `linux:ubuntu:24.04:arm64`** |
| 其余 | `die "unsupported platform for $role: …"` |

**固定版本**（`common.sh:8-9`）：Go **1.26.2**、XLeRobot 提交 **`3d14695e…`**。

---

## 13.2 `robot-agent doctor`：六步，其中一步会转派

**`cmd/robot-agent/main.go` 只有 19 行**，所有子命令实现都在 `internal/robotagent/app.go`（512 行）。

### `doctor` 的检查项

| # | 检查 | 判据 | 失败提示 |
| --- | --- | --- | --- |
| 0 | **读安装回执** `<state>/install.json` | 存在、JSON 合法、`role ∈ {sim,local,robot-pi}` | `read installation receipt: …`；**直接 return，后续一项都不跑** |
| 1 | 打印 `PASS receipt role=%s version=%s platform=%s/%s` | 已过检查 0 | — |
| 2 | 配置文件存在 `<config>/<role>.env`（`role != sim`） | `os.Stat` | `FAIL config <path>: <err>` |
| 3 | **配置文件权限不含 group/other 位** | `info.Mode().Perm() & 0o077 == 0` | `FAIL config permissions <path>: %o` |
| 4 | 打印 `PASS config=%s permissions=%o` | — | — |
| 5 | `role == robot-pi` → **转派** `bash scripts/robot-pi-preflight.sh <config>/robot-pi.env` | 见下 | 见下 |
| 6 | `sim` / `local` → **到此为止** | — | — |

### 第 0 步是硬门禁

`app.go:390` 调 `a.receipt()`，**读不到 `install.json` 就 `fmt.Errorf("read installation receipt: %w", err)`**。

**所以 `doctor` 在一台未安装的机器上必然报错退出。**

**教学要点**：这是一个**刻意的顺序设计**。

| 做法 | 后果 |
| --- | --- |
| 先查配置、再查回执 | 用户得到一堆"配置文件不存在"——**而他还没装** |
| **先查回执** | **一句话说明"你还没装"** |

**第 0 步失败就 return，是为了让错误信息准确地指向真正的原因。**

### 检查 3 的权限判据

```go
info.Mode().Perm() & 0o077 == 0
```

即**不许 group 和 other 有任何权限位**。

**为什么？** 配置文件里有 `AGENT_API_KEY`、设备令牌、证书路径。

**一个 `0644` 的配置文件，在同机多用户的环境下等于把密钥公开了。**

（这与 `local.env` 装成 0600、`state_dir/certs` 0700 是同一条纪律的多个落点。）

### `scripts/robot-pi-preflight.sh`（55 行）的 20 项检查

```
配置可读
→ 六个必填键（XLEROBOT_PORT1/XLEROBOT_PORT2/XLEROBOT_CALIBRATION/
              ROBOT_SERVER_KEY/ROBOT_SERVER_CERT/ROBOT_CLIENT_CA）
→ 两个串口是字符设备且可读写
→ 标定文件 tangying-xlerobot.json 非空
→ 三份 mTLS 文件可读
→ 服务端证书 ≥ 7 天有效期（openssl x509 -checkend 604800）
→ .venv/bin/python 可执行
→ 能 import lerobot.robots.xlerobot_2wheels 与 tangying_robot_gateway
→ 跑 xlerobot_preflight.py
```

失败输出到 stderr 并 `exit 1`；成功打印：

```
PASS no-motion Robot Edge preflight complete
```

**"no-motion"** 这个词是关键——**这个预检不产生任何运动**。

### `--dry-run` 在 install 与 doctor 里的差别

| | 支持 `--dry-run` 吗 |
| --- | --- |
| `install.sh` | ✅ 支持 |
| **`robot-agent doctor`** | ❌ **不支持**（`app.go:386-389` 只接受 0 或 1 个参数且必须是 `sim|local|robot-pi`） |

**实测**：`./bin/robot-agent doctor --dry-run` → `error: unknown doctor option or role "--dry-run"`，`[exit=1]`。

**为什么 doctor 不需要 `--dry-run`？**

> **doctor 本身是只读的。**

**教学要点**：**一个只读的工具不需要预演模式。**

`install.sh` 需要 `--dry-run`，因为它会改机器；`doctor` 不需要，因为它不改。

**"需不需要 dry-run"这个问题本身，就是一个工具危险程度的指标。**

---

## 13.3 一键配对：不是 SSH，也不是二维码

### 发现：UDP 广播，端口 45871

**不是 mDNS。**

| 项 | 值 |
| --- | --- |
| 广播端口 | **45871** |
| 兜底广播地址 | `255.255.255.255` |
| 协议版本 | `1` |
| 载荷标识 | `topic = "tangying.robot.announce"` |
| 单包上限 | 2048 字节 |
| 公告间隔 | **5 秒** |
| 列表保留 | 3 个周期 = **15 秒** |
| 排序 | `open` 优先 → LastSeen 倒序 → RobotID |

**监听失败非致命，只 log**（`Listener.StartInBackground` `:302-315`）——

> **不能因为发现服务起不来就让控制台起不来。**

### 广播明文且不认证是刻意的

`announcement.go:9-20`：

> 两端**还没有共享密钥**，建立密钥正是配对要做的事；
> 因此载荷**只携带"同网段任何设备本来就看得见"的信息**，**配对码永不进广播**。

**教学要点**：这是一个"**在正确的阶段使用正确的安全等级**"的例子。

用加密广播来"保护"发现阶段，是没有意义的——**因为发现阶段的目的是让双方找到彼此，而它们此时还没有密钥。**

**正确的做法是：广播只带公开信息，真正的认证留给配对协议。**

### 一处文档只写了单向：loopback 的处理

| 侧 | 行为 |
| --- | --- |
| **Python 侧** `beacon.py:247` | **起手就是** `[("127.0.0.1", ANNOUNCEMENT_PORT)]` |
| **Go 侧** `announcement.go:263-269` | **显式 `continue` 跳过 loopback** |

Go 侧的理由：

> announcing on it would put the robot in the agent's own discovery list when both run on one machine
> — **which is how the simulator is deployed**.

**合起来的行为**：

> **机器人会向本机广播，但 Agent 不会因为本机广播而自我发现。**

**教学要点**：**"发"和"收"的策略可以不同，而且应该不同。**

一个在单机上跑的仿真栈，如果 Agent 发现了自己，就会出现"我的机器人列表里有一台我自己"——**而这会让仿真和真机混在一个列表里**。

### 配对协议：裸 TCP 45872 + 长度前缀 JSON + 预共享码加密

| 项 | 值 |
| --- | --- |
| 传输 / 端口 | TCP / **45872**（紧邻广播 45871） |
| topic | 请求 `tangying.robot.enroll`，应答 `tangying.robot.enroll.result` |
| 帧格式 | **4 字节大端长度 + JSON**，单帧上限 64 KiB |
| 密钥派生 | **HKDF-SHA256**(配对码规范化为 ikm, salt=32B 随机, info=`tangying.robot.enroll.v1`) → 32 字节 |
| 加密 | **AES-256-GCM**，**AAD = robotId**（防跨机器人重放） |
| 请求载荷 | 三份 PEM：`ca` / `serverCert` / `serverKey` |
| 应答载荷 | `{"status":"paired"\|"refused","detail":"…"}`，用**同一密钥**封装——**这就是认证** |
| 码比较 | 规范化后**常量时间**；**空码不匹配任何东西** |
| 机器人侧窗口 | `DEFAULT_WINDOW_SECONDS = 900`、`MAX_ATTEMPTS = 5` |
| 超时 | 客户端 30 s，控制台侧 45 s |

### 「不需要 SSH」是怎么做到的

两台机器**没有共享秘密**，因此用**机器人打印出来的一次性配对码**作为唯一预共享值。

```
Agent  用配对码派生的密钥加密请求  → 证明自己知道码
机器人 用同一密钥封装应答        → 反过来证明自己知道码
```

**双向认证在同一次交换里完成**，而**证书材料的投递不再经 SSH/scp**，而是**密封在 enroll 请求里**。

控制台侧注释直接写着 **"with no SSH anywhere"**。

**教学要点**：这是一个"**用协议替代工具**"的例子。

| 做法 | 问题 |
| --- | --- |
| 用 SSH 投递证书 | 要求用户**先**配好 SSH——**而这本身是另一个配对问题** |
| **用一次性的配对码** | 用户只需要**读出机器人屏幕上的一串字符** |

**而两步认证在同一次交换里完成**，因为加密本身就是一个"我知道这个码"的证明。

### 三条证书纪律

`internal/pairing/authority.go`：

| 项 | 值 |
| --- | --- |
| CA 有效期 | **10 年** |
| 叶子有效期 | **90 天** |
| 续期告警 | **7 天** |
| 算法 | ECDSA P-256 |
| **CA 私钥** | **永不出本机** |
| 已存在 CA | **不静默替换** |
| 只剩一半 | **停下报缺哪个文件** |
| 签发后 | **立刻用本端 CA 验一遍** |

**"已存在 CA 不静默替换"和"只剩一半就停下报缺哪个文件"这两条值得单独说**：

一个"自动修复"的 CA 管理会**替换掉已有的 CA**——而这会让**所有已签发的证书失效**。

**所以它选择停下报错，而不是"帮"用户修。**

（这与第 10 章那条"限定在它被测量过的场景里，而不是提升为共享默认值"是同一种纪律。）

### HTTP 端点与错误码

| 端点 | 作用 |
| --- | --- |
| `GET /v1/robots/discovered` | 本机听到过的广播列表 |
| `POST /v1/robots/pair` | 发起配对 |

**请求体**：`{robotId, address, code, enrollmentPort?}`

**错误码映射**：

| 码 | HTTP |
| --- | --- |
| `PAIRING_CODE_REQUIRED` | 400 |
| `PAIRING_TARGET_REQUIRED` | 400 |
| `PAIRING_UNAVAILABLE` | 503 |
| `PAIRING_CODE_REJECTED` | **401** |
| `ROBOT_NOT_PAIRING` | 409 |
| `ROBOT_REFUSED_PAIRING` | 409 |
| `PAIRING_FAILED` | 502 |

**七个不同的错误码**——每一个对应**不同的下一步**。

### `Pair` 的五个步骤

| 步 | 内容 |
| --- | --- |
| ① | **机器人必须在本机听到过的广播列表里**（否则提示"没有发现叫 %s 的机器人正在广播"） |
| ② | 载入或创建本机 CA |
| ③ | 调配对客户端 |
| ④ | **机器人接受之后才写本机那一半**（`local-agent.crt` 0644 / `local-agent.key` **0600**，并更新 `local.env` 的五个键，**其余键原样保留**） |
| ⑤ | 返回 `restartRequired: true` |

**第 ① 步和第 ④ 步的顺序值得单独说。**

**第 ① 步**：配对一个**没有在广播的**机器人会被拒绝——因为如果它不在广播，说明它可能不在配对窗口里，或者地址写错了。

**第 ④ 步**：**机器人接受之后才写本机那一半**——不是"写完再问"。

**教学要点**：**写本地状态的时机，必须在对方确认之后。**

如果先写本地再发请求，而请求失败了，本地就留下一个**指向不存在的配对**的配置。

### SSH 路径仍然存在，是回退

`robot-agent pair HOST --ssh-user USER [--new-ca]` → `scripts/pair-robot.sh`（305 行）。

硬检查 `openssl`/`ssh`/`scp`，`scp` 到 `/tmp/tangying-robot-pair-$RANDOM` 再远端 `install -o tangying-robot …`。

**两条路径产出等价。**

### 没有二维码

全仓未找到 QR 生成/扫描实现。配对码是**纯文本**（启动时打印进日志 / 印在标签上，由人读出并手输）。

**教学要点**：这是一个"**没有做的东西**"。

二维码会更好用——但它需要一套生成 + 扫描的实现。**而"打印一串字符"零依赖。**

**在可用性和复杂度之间，这个项目选了后者。**

### 「配置热生效」的真实链路

```
PUT /v1/config/llm
  → 落盘 local.env（0600，临时文件 + rename）
  → 写盘成功后调用 onChange(status)
  → 回调里重新读文件（不信请求体）→ 构造新 parser → service.SetParser(...)
     （tasks/service.go:120-131 的注释：
       "SetParser replaces how requests are understood, without a restart."）
```

**两个细节值得写**：

**① 回调里重新读文件，不信请求体。**

**为什么？** 因为**磁盘上的才是权威**。如果回调直接用请求体构造 parser，那么"写盘成功但回调收到的是另一个值"这种不一致就无法被发现。

**② 只有回调真的应用成功，才把 `RestartRequired` 置 false。**

回调没装或应用失败时，**界面如实显示"需要重启"**。

**对照**：`TANGYING_AGENTS`（启用的 Agent 集合）**只在启动时读，不热生效**。

**教学要点**：**"能热生效"和"不能热生效"都要被明确声明。**

一个既不是"已经生效"也不是"需要重启"的中间状态，会让用户反复试。

---

## 13.4 端口：8787 vs 8897 不是两个服务

**文档写得最清楚**（`why-distributed.md:183-185`）：

> 本地单机文档写的是 `8787`，而 `home-furnished` 显式传 `8897`。**两个都对**——
> 前者是 `sim-stack.sh` 的默认，后者是家庭场景目标的显式选择。
> 照文档敲 `127.0.0.1:8787` 打不开**不是 bug**，是文档里已标注的差异。

### 证据链

| 位置 | 内容 |
| --- | --- |
| `scripts/sim-stack.sh:10` | `AGENT_PORT="${SIM_STACK_AGENT_PORT:-8787}"` |
| `Makefile:113-114` | `bash scripts/furnished-home-demo.sh start --sim-port 50161 --agent-port 8897` |
| `Makefile:110-112` 注释 | **"One canonical port, stated once"**，否则 "README sends a first-time reader to a port nothing listens on" |
| `docs/operations/fresh-deployment.md:84,123` | 把"打开 8787 是空白 → 家庭场景在 8897"写进**故障表** |

### 根本原因是端口隔离

| 场景 | sim 端口 | agent 端口 |
| --- | --- | --- |
| 默认工位 | 50051 | **8787** |
| **家庭演示** | **50161** | **8897** |

**两者可以同时存在**，而且**不删原工作台数据**。

**教学要点**：**同一个软件用两个端口，是因为两份数据要同时存在。**

一个"统一成一个端口"的做法会要求用户**先停掉一个**——而"我今天想同时看工位和家庭场景"是一个真实需求。

### 本地单机的进程拓扑

```
make home-furnished (Makefile:113-114)
 └─ scripts/furnished-home-demo.sh start --sim-port 50161 --agent-port 8897
      └─ exec bash scripts/sim-stack.sh start … --scene home_task --perception rgbd
           ├─ 进程 1  .venv/bin/python -m tangying_sim.server --listen 127.0.0.1:<SIM_PORT>
           └─ 进程 2  bin/local-agent --dev-insecure --robot-safety-profile desktop_standard
                      --listen 127.0.0.1:<AGENT_PORT> --robot 127.0.0.1:<SIM_PORT>
```

**两个进程。**

**注意 `--robot-safety-profile desktop_standard` 是显式传的** —— 第 9 章讲过，Local Agent **必须显式配置安全档位**（`safety_profile_test.go:9-18` 断言"隐式安全档位……运行时策略必须自己选默认值"）。

### 端口全表

| 端口 | 用途 |
| --- | --- |
| **8787** | 本地单机控制台（**家庭场景 8897**） |
| **50051** | 仿真 Runtime（**家庭场景 50161**） |
| 18790 / 18791 | 机器人端导航容器 |
| 443 + 8444 | 云端 |
| 18080 | 云端**仅回环** |
| 3306 + 6379 | 云端**仅容器网** |

### 8787 的绑定安全策略

| 项 | 事实 |
| --- | --- |
| 默认绑定 | `127.0.0.1:8787` |
| **非环回绑定被拒绝** | 除非显式 `--allow-remote-console` 或 `LOCAL_ALLOW_REMOTE=1` |
| `loopbackListen` 的判定 | 对 `:8787`、`0.0.0.0:8787`、`[::]:8787`、`10.0.0.5:8787` **一律返回 false** |

**教学要点**：这个判定**不看主机名，只看绑定地址**。

`localhost` 会被解析成什么取决于 hosts 文件、DNS、甚至攻击者的配置。**所以它只接受明确的环回地址。**

---

## 13.5 服务单元：systemd 与 launchd

| 单元 | 关键字段 |
| --- | --- |
| `deploy/local/tangying-robot-local-agent.service`（18 行，Linux 用户单元） | `ExecStart=/usr/local/bin/tangying-local-agent --config %h/.config/… --data-dir %h/.local/share/…`；**`Restart=on-failure`、`RestartSec=2`**；加固 `NoNewPrivileges` / `PrivateTmp` / **`ProtectSystem=strict`** / `ReadWritePaths=…`。**端口不在 unit 里**，来自 `local.env` 的 `LOCAL_LISTEN` |
| `deploy/local/com.tangying.robot-agent.plist`（17 行，macOS） | `RunAtLoad=true`、**`KeepAlive=true`**；stdout/stderr 到 `…/logs/local-agent.log` 与 `.error.log`；`__HOME__` 由安装时 `sed` 渲染 |
| `deploy/robot/raspberry-pi/tangying-robot-edge-direct.service`（30 行，**current**） | `User/Group=tangying-robot`、`SupplementaryGroups=dialout`；`EnvironmentFile=/etc/…/robot-pi.env`；`Restart=on-failure`、`RestartSec=1`、`TimeoutStopSec=10`、**`KillSignal=SIGINT`**。注释：**"Service startup never enables torque."** |
| `deploy/robot/raspberry-pi/tangying-robot-edge.service`（27 行，ROS 2 路径，**当前不可达**） | `Requires=tangying-xlerobot.service`；`ROS_DOMAIN_ID=73`、`ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST` |

### ⚠️ 两处必须标注

**① 一处语义差异没有解释**

| 平台 | 重启策略 |
| --- | --- |
| launchd | **`KeepAlive=true`**（**正常退出后也会拉起**） |
| systemd | `Restart=on-failure`（**只在非零退出时拉起**） |

**两者不等价，仓库里没有解释这个差异的注释。**

**（推断）** 这可能是有意的（macOS 上用户手动退出后被拉起是一个已知的 annoying 行为）或者只是平台默认值不同造成的疏漏。**书里如实报告，不推测。**

**② ROS 2 路径当前不可达**

`scripts/install/robot-pi.sh:84-86` 的 `direct_edge()` **硬编码 `return 0`**，所以：

> ROS 2 Jazzy 安装分支与 `colcon build` 是**死代码**。

**写书时若要提"树莓派会装 ROS 2"，必须注明这一前提。**

**教学要点**：**一个不可达的单元文件比没有更危险**——它会让读者以为存在 ROS 2 路径。

---

## 13.6 两个只读工具：预检与故障注入

**这一节是本章的核心，也是最值得学的一部分。**

### `scripts/precheck.sh`：让"能不能装"可验证

**它证明什么**（`:8-19`）：

> Why this exists: `install.sh` validates the platform and dies with
> 'unsupported platform for <role>', **which tells you that you failed but not what this machine has
> or how far off it is.** On a fresh machine that is the difference between
> **one command and an afternoon**.
>
> * **It only reads.** It installs nothing, changes no configuration, and starts no service.
>   **A precheck that fixes things cannot be run to find out.**
> * **A missing optional tool is a WARN, not a FAIL.** Docker is only needed for the cloud stack,
>   and a robot build is not a cloud build.
>   **Reporting optional gaps as failures is how a check stops being read.**

**这两条自我约束是本节最重要的东西。**

**第一条：预检不能修东西。**

> **"一个会修东西的预检，就不能被用来'探明情况'。"**

**为什么？** 因为用户跑预检的**目的**是"我想知道差多远"。如果它会改机器，那么：

1. 用户不敢在有状态的机器上跑它；
2. 跑完之后，**状态变了**，你无法再问"刚才是什么情况"。

**第二条：可选工具缺失是 WARN，不是 FAIL。**

> **"把可选的缺口报成失败，是一个检查被停止阅读的方式。"**

**这与第 3 章"从不消失的告警会被忽略"、第 12 章"数据不足报成崩溃会被忽略"是同一条原则。**

### 三态与退出码

`set -uo pipefail` 且**刻意不加 `-e`**；三态 **PASS / FAIL / WARN**；颜色仅在 `[ -t 1 ]` 且 `NO_COLOR` 未设时启用。

| 退出码 | 含义 |
| --- | --- |
| `0` | **每个必需检查通过**（WARN 不影响） |
| `1` | 至少一个必需检查失败 |
| `2` | 用法错误 |

### 检查项

**全局平台报告**（`:87-98`）：打印 `os/distro/version/arch`；平台不在支持矩阵内 → **WARN** + note：

> "install.sh will refuse it. That is a supported-platform decision, not a toolchain problem."

**"那是一个支持平台的决定，不是一个工具链问题"** —— 这句话防止用户去升级工具链。

**工具探针**：

| 探针 | PASS 判据 | 缺/旧时 |
| --- | --- | --- |
| `probe_go` | `go version` ≥ **1.26** | **FAIL** + 安装指引 |
| `probe_python` | ≥ **3.11** | **FAIL** |
| `probe_node` | 仅 `command -v node` | **WARN**：`only needed for the frontend tests, not to run the product`（**不查版本**） |
| `probe_docker` | `docker info` 成功 | **WARN**（未装 / 守护进程不通**两种情况分开**） |
| `probe_openssl` | `command -v openssl` | **FAIL**（robot 与 cloud 角色要生成和检查证书） |

**四个角色各自的检查项**：

| 角色 | 项数 | 内容 |
| --- | ---: | --- |
| `sim` | 7 | 平台 → Python → Go → Node → `artifacts/sim-assets`（WARN）→ `artifacts/maps/furnished-home`（WARN，"run the mapping pass before a navigation task"）→ `.venv/bin/python`（WARN） |
| `local` | 4 | 平台 → Go → Python → **checkout 可写**（"the build writes `bin/` and `.venv/`"） |
| `robot-pi` | 6 | 平台（**仅 `linux:ubuntu:24.04:arm64`**）→ Python → openssl → `robot-pi.env` 可读（WARN）→ **preflight 脚本是否存在（只报存在性，不执行）** → `/dev/tangying-left\|right`（WARN，"run only after installing the udev rule"） |
| `cloud` | 5 | 平台检查**恒返回 0** → Docker → openssl → compose 文件 → `.env`（WARN）→ **端口占用**（`lsof`，只警告不阻挡） |

**`robot-pi` 的"只报存在性，不执行"值得注意**（`:291-295`）：

> "The serial devices and calibration are what `scripts/robot-pi-preflight.sh` checks;
> **this only reports whether that check can run at all.**"

**教学要点**：**预检不嵌套另一个预检。**

| 做法 | 问题 |
| --- | --- |
| `precheck.sh` 调用 `robot-pi-preflight.sh` | 两层检查的输出混在一起，**失败原因难以定位** |
| **只报"那个检查能不能跑"** | **职责清晰** |

### 版本比较器：一个写在注释里的历史缺陷

`probe_*` 用的版本比较器（`:116-143`）是本工具**最关键的可测单元**，而它的历史缺陷被写在源码注释里（`:104-115`）：

> The expansion version of this function was written first and was **wrong**:
> inside `${x%%[0-9]*}` the alternation is a trap, and it **silently classified 1.26.2 as older than 1.26**.
>
> **A precheck that misjudges is worse than no precheck, because it sends someone to fix a machine
> that was already fine**, so the parsing is now explicit and has its own unit test.

**"一个判断错误的预检比没有预检更糟，因为它会让人去修一台本来就好的机器。"**

**修法**：改为 awk 精确提取，支持 `go1.26.2` / `Python 3.11.9` / `v24.14.1` / `1.26.2-rc1`。

**三条规则**：

| 规则 | 效果 |
| --- | --- |
| 缺 minor/patch **按 0 计** | 所以 `1.26` **不早于** `1.26.0` |
| **没有任何前导数字的一律 `unknown`** | **永不放行** |

**教学要点**：这是本章最好的一条经验。

**这类 bug 的危险在于它是静默的**——`${x%%[0-9]*}` 是一个看起来对的展开，而它在某些输入下给出错误答案。

**而它的后果是"让人去修一台没坏的机器"** ——一个**假阳性**。

（这与第 10 章"守卫之守卫"是同一条：**一个永远通过的检查比没有检查更糟**；而这里是**一个误判的检查比没有检查更糟**。）

### 实测

```bash
./scripts/precheck.sh sim local
# → passed: 11 check(s) passed, 0 warning(s)   exit 0
# 输出含 python3 3.11.9、go go1.26.2、node v24.14.1
```

### `scripts/inject_faults.py`：让"有没有发现问题"可验证

**它证明什么**（文件自述 `:6-15`）：

> the Go tests cover the rules with **synthetic findings**, and the live check earlier covered
> **one fault by hand**. **Neither answers the question an operator actually has,
> which is 'when this robot is broken in this particular way, does the agent say the right thing'.**
>
> The three faults below are the ones the simulator is able to **prove**, and it publishes only those —
> **a fault list that cries wolf is worse than a short one.**
> Everything this driver injects therefore arrives through the **same `robot.faults.v1` contract
> the runtime uses in production, not through a back door.**

**三个"不是"**：

| 之前有的 | 它不能回答的问题 |
| --- | --- |
| Go 测试用**合成 finding** | 真实故障链路 |
| **手工测过**一次 | 可重复性 |
| — | **"当这台机器人以这种特定方式坏掉时，agent 说得对吗"** |

### 只注入三个故障

| 场景 | 人类观察 | 期望 agent 故障码 | 期望消息子串 | 期望建议子串 |
| --- | --- | --- | --- | --- |
| `estop` | 急停锁存：机器人被停止，软件不会自动复位 | `ANOMALY_SAFETY_STOP` | — | `人工`、`急停` |
| `no_map` | 移动场景没有启用地图：导航能力被摘掉 | `ANOMALY_COMPONENT_FAULT` | `NAV_MAP_NOT_READY` | `地图` |
| `calibration` | 工位高度与标定不一致 | `ANOMALY_COMPONENT_FAULT` | `WORKCELL_CALIBRATION_MISMATCH` | `标定` |

**"a fault list that cries wolf is worse than a short one"** —— 这是第 3 章"从不消失的告警会被忽略"的另一个表述。

### 怎么注入的：一个诚实的"我不注入"

**`estop` 是真注入**，走正式 gRPC（`EmergencyStop`），`operator_id="fault-injector"`。

**`no_map` 与 `calibration` 不注入，只观测**（`:163-172`）：

> "The map and calibration faults are produced by **the runtime's own commissioning checks**.
> **They cannot be forced from outside**, so the driver **reports what the runtime is already publishing
> rather than pretending to inject them** — **a fabricated injection would prove nothing**."

标为 `result["injected"] = False` 并给出 N/A 结论。

**教学要点**：这是本节最值得学的一处。

| 做法 | 后果 |
| --- | --- |
| 假装注入了 | **它证明不了任何东西**——因为故障不是注入产生的 |
| **标为"无法注入，只观测"** | 输出里明确区分"注入了并检出"和"本来就存在并检出" |

**"a fabricated injection would prove nothing"** —— 一个伪造的注入什么都证明不了。

**没有使用的手段**（这也值得列）：不改配置文件、不杀进程、不断网、不改写模拟器状态、**不返回伪造错误码**。

**"不是走后门"** —— 所有注入都走**生产用的同一份 `robot.faults.v1` 契约**。

### 一个直接后果：急停没有解除 RPC

`clear_estop`（`:103-115`）按 `ClearEmergencyStop` / `ResetEmergencyStop` / `ReleaseEmergencyStop` 顺序探测，**全都不存在**。

全仓 grep 这三个名字**只命中脚本自己**；proto 的 `RobotRuntime` **只有 7 个 RPC**，被 `tests/contract/test_proto_schema.py:28` 钉死。

> **"急停没有解除 RPC"不是遗漏，是设计的直接后果。**

### 判定：三条，第三条来自一次真实缺陷

轮询 `GET {console}/v1/agent/alerts`，合并 `alerts + runnerAlerts`。

窗口 **25 秒**（注释说明 **agent 默认 5 s 一 tick，等更短会得到假阴性**），间隔 1.5 s。

**判定三条**：`active` 为真、`code` 相等、**`message` 含期望子串**。

**第三条的动机是一次真实暴露的缺陷**（`:54-59`）：

> **Two different module faults both report `ANOMALY_COMPONENT_FAULT`,
> so matching on the code alone would accept the wrong fault as evidence that the right one was found.**

**"只匹配 code，会把错误的故障当成'找到了正确的故障'的证据。"**

**教学要点**：**这是一个"断言强度"的问题。**

| 断言 | 强度 |
| --- | --- |
| `active == true` | 太弱——任何故障都满足 |
| `code == 期望` | 不够——**多个故障共用一个 code** |
| **`code` 相等 + `message` 含期望子串** | ✅ |

**结论分档**（`:187-203`）：

```
定位并给出可执行建议
定位了，但建议里缺少 {missing}
定位了，但没有给出建议
```

**三档**，而不是"通过/失败"。

### 退出码：四条，其中 3 是关键

| 退出码 | 含义 |
| --- | --- |
| `0` | **全部通过** |
| `1` | **有遗漏** |
| `2` | 用法错误或连不上 |
| **`3`** | **所有场景的故障当前都不存在** |

**第 3 条的用途**（提交正文原话）：

> **专门用来区分"注入没生效"和"都通过了"。**

**教学要点**：这是本章第三次遇到"**必须有第三个状态**"（前两次：预检的 WARN、门禁的 INCONCLUSIVE）。

| 两态 | 三态 |
| --- | --- |
| 通过 / 失败 | 通过 / 失败 / **"故障根本不存在"** |

**"故障不存在"和"故障存在但没检出"是完全不同的问题**：前者要改测试环境，后者要改 agent。

**（⚠️ 而 docstring `:22-23` 漏写了 `3`。**）

### 真实输出

```
[N/A ] calibration: 工位高度与标定不一致  →  当前不存在此故障（无法注入，运行时未上报）
[OK  ] estop: 急停锁存               →  定位: 机器人处于急停状态，所有依赖运动的能力都已不可用
                                        建议: 确认现场安全后，由人工复位急停按钮
[OK  ] no_map: 移动场景没有启用地图     →  定位: chassis 报告 NAV_MAP_NOT_READY：no active map is loaded
                                        建议: 按机器人给出的处置方式处理：先完成巡检建图并在控制台启用地图。
```

**注意 `no_map` 的建议**：

> "**按机器人给出的处置方式处理**"

**这不是 agent 自己编的建议，而是引用了机器人写的 `userInstruction`。**

（第 11 章讲过这条纪律：故障的行动项用**机器人自己写的** `userInstruction`，取不到时才退回"记下故障码，把日志发给支持人员"——**"那句不假装知道故障含义"**。）

### ⚠️ 必须标注的四条现状

**① 注入器没有任何执行级测试。**

`tests/install/test_precheck.py:150,175` 只是 `read_text()` 做**字符串断言**，**从不运行脚本**。

> "运行时那 3 个故障码是否真能被 agent 检出"**没有自动化覆盖**。

**② `expect_manual` 是死字段。**

定义 `:64` + 三处赋值，**零读取点**。

而作者写它的意图恰恰是防未来自动化（`:72-73`）：

> "Clearing an e-stop is a person's job.
> **If the advice ever stops saying so, someone will eventually try to automate it.**"

**③ 设想的登记制度尚未实现。**

`docs/architecture/agent-evaluation-system.md:327` 要求"故障注入必须登记注入位置、起止时间、强度、可观察性、独立注入回执、是否实际生效与解除条件……**注入未生效的案例按预注册规则判实验无效并留存，不伪装成系统检出失败**"。

**④ `precheck.sh` 不在 CI 里。**

**唯一入口是手敲与 `tests/install/test_precheck.py`。**

`Makefile:60-61` 的 `install-check` 目标跑 `pytest tests/install -q`，因此**测试**在 CI 内——但 `.github/workflows/ci.yml:36-39` 只跑 `make test` / `make lint` / `make generate-check` / `go test ./tests/docs`，**不跑 `make install-check`**，也**不跑 `precheck.sh` 或 `inject_faults.py`**。

### 三处数字/事实的小出入

| # | 事实 |
| --- | --- |
| 1 | 预检里 "the project pins 3.11.9" 与仓库不符：全仓**无** `.python-version` / `.tool-versions`，`pyproject.toml:8` 是 `requires-python = ">=3.11"`。**3.11.9 是参考环境版本，不是机器可读的 pin**（Go 侧则确实是 pin） |
| 2 | `probe_node` **不校验版本**，而测试用例里却有 `v24.14.1 ≥ 18.0`——**"node ≥ 18"这条要求没有任何地方真正执行** |
| 3 | 提交正文与归档 CHANGELOG 说"12 个用例"，实测版本比较器用例是 **7 + 6 = 13** |

### 两个工具证明了什么

提交正文原话：

> 冷启动预检 + 故障注入器：让"**能不能装**"和"**有没有发现问题**"都可验证

| 工具 | 把什么变成了可验证 |
| --- | --- |
| **precheck** | 把"**这台机器能不能装**"从"跑了才知道"变成"**一条只读命令先告诉你差多远**" |
| **注入器** | 把"**故障发生时 agent 会不会说对话**"从"靠人手测一次"变成"**可反复跑的脚本 + 退出码**" |

**教学要点**：**这两个工具各自关掉一类"无法验证"的缺口。**

而它们的共同点是：**都是只读的**（precheck）/ **都走生产契约**（注入器）。

---

## 13.7 三份检查清单

### 先说状态标记的实况

**三份文件里的 checkbox 全部是未勾选 `- [ ]`。仓库中没有任何 `- [x]`。**

| 文件 | checkbox 数 |
| --- | ---: |
| `safety-checklist.md` | **14** |
| `release-checklist.md` | **19** |
| `production-readiness.md` | **0（它本身没有 checkbox）** |

**这一点必须写进书里**：

> **这套系统把"愿望"和"已验收"分得很开——清单是待办，不是成绩单。**

### 15.7.1 「哪些结果可以证明什么」

`docs/operations/production-readiness.md:7-14` 的这张表，本身就是全书最好的"证据分级"教材：

| 证据 | 能证明什么 |
| --- | --- |
| MuJoCo / RoboCasa 测试通过 | 对应代码版本和**限定仿真场景**的行为 |
| `robot-agent doctor robot-pi` | **无动作**配置、文件、证书与驱动兼容预检 |
| **Runtime READY** | 当前软件能力检查状态，**不是现场动作许可** |
| `production-check` 的 offline READY | 已配置 provider 可导入，传统记录满足字段检查 |
| Sim2Real check / report | 版本化配置、制品与操作员记录**符合阶段要求** |
| **实际生产放行** | **现场负责人**验证风险、策略/感知质量、真实停止与恢复，并完成必要评审 |

**段落（`:16`）**：

> 以上结果**不能相互替代**；仓库**没有随附适配所购 XLeRobot 的已训练生产模型**；
> **默认 systemd 服务连接并保持扭矩关闭，不自动 arm；
> 连接前必须支撑机械臂，底盘禁用，急停锁存不随重启解除。**

### 15.7.2 「使用逐次证据而非预填通过」

这一节讲的是一条纪律，而且它有一句非常具体的警告（`:38-42`）：

> **"这些人工声明不校验事实，不能把它们当成开关预填为 true。
> 新用户不应从文档复制一个全 true JSON 来取得 READY。"**

**旧脚本的数值门槛**：

| 检查 | 门槛 |
| --- | --- |
| 硬件记录 | `completed_trials >= 30` 及三个故障演练布尔值（`< 30` 时报 `must be an integer >= 30`） |
| 安全记录 | 实体急停和现场操作员布尔值 |

**教学要点**：**一个"全 true JSON"能通过检查，是这个检查的设计缺陷。**

而这个项目**明确警告用户不要这么做**——并且把这句话写进了文档。

（更彻底的修法是 `sim2real.py` 的 `staleRecords` 机制：**改变接入包配置、模型、标定或资料后，旧记录会计入 `staleRecords`**。）

### 15.7.3 现场放行顺序（6 步）

| # | 步 |
| --- | --- |
| ① | 匹配固定软件与所购硬件，完成接线、实体急停和稳定串口 |
| ② | 现场标定，校验关节、相机、坐标变换与安全限制 |
| ③ | **无动作预检**和 mTLS 配对；接入真实感知、经过评估的动作策略与 verifier |
| ④ | **显式连接、现场 arm**，先单关节/夹爪、空载 bench，再单机轻物任务 |
| ⑤ | 记录急停、断网、重复/未知命令与持物恢复；**失败同样保留** |
| ⑥ | 完成独立真实 trial 和 soak 门槛，再由**现场责任人**对受限试点作出决定 |

**两个细节**：

**"显式连接、现场 arm"** —— 连接不等于 arm（第 10 章：`PluginBackend` 构造不碰硬件）。

**"失败同样保留"** —— 与第 12 章"未完成的 run 既不上线也不丢弃"是同一条。

**结尾（`:53`）**：

> 至少 30 次 trial 或 pilot evidence 通过**不会自动生成 PHYSICAL_GO 或生产认证**。

### 15.7.4 安全检查清单（14 条）

**使用说明（`:3`）**：

> **仓库没有已完成的物理验收结果。软件 READY 不是移动硬件的许可。**

**14 条逐条（`safety-checklist.md:7-20`）**：

| # | 内容 |
| --- | --- |
| 1 | **独立的物理急停在不用软件的情况下切断执行器电源**，且整个试验期间可触及 |
| 2 | 工作区没有人员、宠物、线缆和易碎物；操作员站在**运动包络之外** |
| 3 | 所购硬件、控制器映射、固件、pinned 驱动与标定身份与**记录在案的套件**一致 |
| 4 | 串口有稳定名称与正确权限；**没有其它服务占用同一个控制器** |
| 5 | 标定是最新的；使能扭矩或执行标定需要**显式的现场监督** |
| 6 | **桌面 profile 拒绝所有 `x.vel` 与 `theta.vel` 动作键**；移动底盘操作在本 profile 之外 |
| 7 | 关节范围、相对目标、动作长度、速度与工作空间限制已为本设备选定并验证。**默认值 `8.0` 与 `64` 是软件默认，不是硬件认证的限制** |
| 8 | 启动/停止服务前机械臂已被支撑：**默认 systemd 启动是扭矩关闭连接，这会禁用现有扭矩并配置寄存器——连接本身不是物理上无动作的** |
| 9 | 本次进程的**显式现场 arm 授权**已完成；**重启的服务不会自动 arm** |
| 10 | 空载、低速 bench 运动与受控取消**在带载荷作业之前**成功 |
| 11 | 软件停止与物理急停都已测试，并记录了**实际响应时间** |
| 12 | 断网已按配置的命令租约/看门狗与硬件响应预算测试过；**不假设固定的 1 秒保证** |
| 13 | 过期、重复或结果不确定的命令**不会重复物理运动**；journal 与观测对账已验证 |
| 14 | 真实感知与验证与被观察到的结果一致；**第一次带载的物体是软的、轻的、非液体且非尖锐的** |

**四条的措辞值得单独引用**：

**第 8 条**："**连接本身不是物理上无动作的**" —— 这一条纠正了一个危险的直觉。

**第 12 条**："**不假设固定的 1 秒保证**" —— 明确的"不要假设"。

**第 14 条**：第一次带载用**软的、轻的、非液体且非尖锐的**物体 —— 这是一个**具体的、可执行的**降险措施。

**收尾（`:22`）**：

> 急停之后必须检查机器人与工作区，再经**本地显式**放行；
> **云端不能清除锁存；重启或删除 journal 不是恢复程序。**

**"重启或删除 journal 不是恢复程序"** —— 这与第 3 章"删除 `agent.db` 不是对账"是同一条。

### 15.7.5 发布检查清单（19 条）

**软件与合同（5 条）**：

```
make build / make generate-check / make lint / git diff --check 通过
PYTHONPATH=sim/mujoco pytest -q sim/mujoco/tests/test_home_scene.py 通过
  （确认五房间、双 RGB-D 和 verify_arrival 证据）
go test ./agent/intent ./skills/manipulation ./edge/robotclient ./edge/agent ./tasks 通过
pytest -q tests/install/test_navigation_stack.py … 通过
  （确认 --scene home 同时传到仿真/Compose/launch）
make test-web 通过（用户模式不显示底层动作输入，开发诊断仍可按
  task/revision/step/command/observation 定位）
```

**家庭仿真（6 条）**，其中三条最值得引用：

| # | 内容 |
| --- | --- |
| 1 | 用"从客厅出发，去厨房确认一下环境"创建任务，检查 `observe_scene → navigation.navigate → verify_arrival` 顺序与**同次底盘证据** |
| 2 | 在工具边界暂停、重启 Local Agent、显式继续；**确认已完成导航不重复发送，未确认的物理结果保持阻断** |
| 3 | 相机观测为空处**保持未知** |

**第 3 条是"不知道的地方不进去"在验收清单里的表达。**

**ROS 2 / RTAB-Map（4 条）**：

```
make navigation-restart NAVIGATION_ARGS='--build --mode mapping --scene home' 成功
实际覆盖五个房间并保存 rtabmap.db（容器内路径，宿主对应命名卷 tangying-navigation-maps）
用 --mode localization --scene home 从至少三个不同起点重复路线
丢失 RGB-D/TF/odom 或视觉质量时必须停止并保留冻结失败证据
逐项验证取消、断网、底部相机断流、急停、速度看门狗、地图版本漂移和进程重启；
不能自动重放未知物理命令
```

**实机放行（4 条）**：

| # | 内容 |
| --- | --- |
| 1 | profile、驱动、相机内参/外参、odom、工具 catalog 和策略制品**已绑定版本与哈希** |
| 2 | 头部与底盘 RGB-D 在**真实房屋照明、反光地面、窄门、低矮障碍和家具遮挡**下通过观测合同检查 |
| 3 | 完成低速软围栏、刹车距离、实体急停、断网归零、持物恢复和人工接管演练 |
| 4 | **至少 30 次**单机器人路线和一次长稳运行通过；记录成功率、定位丢失、停止延迟、失败证据和回滚版本 |

**收尾**：

> **现场负责人签字后**才能进入受监护试点；
> **无人值守生产、通用家务和多机器人协同另行验收。**

**教学要点**：**最后一句划了三条明确的"不包含"**。

一个"通过发布检查"的软件，**不是**一个可以无人值守的系统。

### 还有第四份清单

`docs/install/xlerobot-experiment.md:7-13`（**7 条 checkbox，同样全部未勾选**）。

---

## 13.8 与通用 coding agent 的部署差异

| 维度 | coding agent | 机器人 agent |
| --- | --- | --- |
| **安装后能跑吗** | 通常能 | **故意不能**——装完不启动，等配置/证书/标定 |
| **配置热生效** | 通常需要重启 | **部分热生效**（LLM provider），部分不（Agent 集合） |
| **密钥存储** | 环境变量 / `.env` | **0600 + 权限位检查** |
| **端口** | 一个 | **两个**（因为两份数据要同时存在） |
| **健康检查** | `/healthz` | **`/healthz` + `/v1/readiness` 分开**（见下） |
| **清单** | 可选的 | **三份，全部未勾选** |
| **放行** | 部署即上线 | **软件发布与实机放行是两项独立结论** |

### 最核心的一条差异：liveness 与 readiness 必须分开

第 11 章引用过 `console/readiness.go:15-34` 的那段论证，这里完整给出，因为它是**部署**层面最重要的一个设计：

> "**Liveness** asks 'should I restart this process' —
> a robot in an emergency stop is **a healthy process doing its job**,
> and restarting it would be an outage caused by a working safety feature.
>
> **Readiness** asks 'should I offer this to a person' — and being stopped is precisely a reason not to.
>
> **Folding them together is how a container ends up in a restart loop because its robot is safely stopped.**"

**"把两者合并，就是一个容器因为它的机器人安全地停着而进入重启循环的原因。"**

**教学要点**：这是一条**可以直接推广到很多领域**的部署原则。

| | 问题 | 停机后果 |
| --- | --- | --- |
| liveness | "**要不要重启这个进程**" | 重启一个正在正常工作的进程 |
| readiness | "**要不要把服务提供给人**" | 让用户看到一个不能用的服务 |

**在机器人上，这两者会同时出现在一个场景里**：急停锁存 + 无地图 + 有未知结果步骤的机器人——

| 端点 | 返回 |
| --- | --- |
| `/healthz` | **ok** |
| `/v1/readiness` | **不** |

**而这个"矛盾"是正确的**：进程是健康的（它在执行急停），但它不该被提供给用户。

**而重启它会让急停**……**（软件急停不随重启解除，但重启会中断其他服务）**。

---

## 13.9 教学要点

### 一道可以从两个工具下手的练习

给学生这两段自我约束：

```
precheck.sh:  "It only reads. It installs nothing, changes no configuration, and starts no service.
               A precheck that fixes things cannot be run to find out."

              "A missing optional tool is a WARN, not a FAIL. ...
               Reporting optional gaps as failures is how a check stops being read."
```

问：**这两条约束各防的是什么失效？如果去掉任一条会发生什么？**

<details>
<summary>答案要点</summary>

**第一条防的是"预检不可用"**：

一个会修东西的预检，用户**不敢在有状态的机器上跑它**——因为跑了之后状态就变了。

**更根本的**：预检的目的**就是"探明情况"**。如果它会改，那"跑之前是什么情况"这个问题**永远没有答案**。

**第二条防的是"预检被忽略"**：

如果 Docker 没装就报 FAIL，那么在**一台不需要 Docker 的机器人构建机上**，预检**永远显示失败**。

而一个**永远显示失败**的检查，会被用户加进忽略列表——**然后真正的失败也被忽略了**。

**这是第 3 章、第 11 章、第 12 章都出现过的那条原则**：

> **一个从不消失的告警会被忽略，一个每天报错的构建会被加入忽略列表，一条永远不成立的检查等于没有检查。**

**加分点**：指出项目里有**四个**同类实例：

| 位置 | 失效 | 修法 |
| --- | --- | --- |
| 第 3 章 | 所有 `Freshness` 都写死 `"FRESH"`，过期规则是死代码 | 计算新鲜度 |
| 第 3 章 | 从不消失的告警会被忽略 | `Reconciled` 后不再报 |
| 第 12 章 | 数据不足报成崩溃会被忽略 | **退出码 1 是"收更多数据"，不是错误** |
| 第 13 章 | 可选缺口报成失败会被忽略 | **WARN 不是 FAIL** |

**再加分**：指出这两条约束**本身是可测试的**——`tests/install/test_precheck.py` 断言了平台行对齐，但没有断言"检查项集合一致"（这是一处已知缺口）。

</details>

### 五道练习题

**练习 1：为什么 `doctor` 在未安装的机器上必然报错，而且这是对的？**

<details>
<summary>答案要点</summary>

**机制**：`app.go:390` 调 `a.receipt()`，读不到 `install.json` 就 `fmt.Errorf("read installation receipt: %w", err)`——**直接 return，后续一项都不跑**。

**为什么这是对的**：

| 做法 | 用户得到什么 |
| --- | --- |
| 先查配置 | **一堆"配置文件不存在"**——而他还没装 |
| **先查回执** | **一句话说明"你还没装"** |

**第 0 步失败就 return，是为了让错误信息准确地指向真正的原因。**

**教学要点**：**检查的顺序是诊断质量的一部分。**

一组检查如果全部跑完再报告，用户会看到**一个症状列表**，而不是**一个根因**。

**加分点**：指出这也解释了为什么 `doctor` **不支持 `--dry-run`**——**它本身是只读的**。

**"需不需要 dry-run"这个问题本身，就是一个工具危险程度的指标。**

</details>

**练习 2：为什么故障注入器只注入一个故障，另外两个"只观测"？**

<details>
<summary>答案要点</summary>

**原文**：

> "The map and calibration faults are produced by **the runtime's own commissioning checks**.
> **They cannot be forced from outside**, so the driver reports what the runtime is already publishing
> rather than pretending to inject them — **a fabricated injection would prove nothing**."

**为什么"伪造注入"什么都没证明**：

| 做法 | 实验能得出的结论 |
| --- | --- |
| 真的注入 → 检出 | **"注入的故障被检出了"** ✅ |
| 假装注入（实际是运行时本来就有的）→ 检出 | **"一个本来就存在的故障被检出了"**——但**不是**"注入生效了" |
| 假装注入 → 没检出 | **无法区分**"注入失败"和"检出失败" |

**第三种情况最关键**：如果注入是假的，那么"没检出"的这个结果**无法归因**。

**教学要点**：

> **一个实验如果不能区分"处理无效"和"处理没生效"，它就不是一个实验。**

（这与第 12 章的"故障注入必须登记……是否实际生效"是同一个要求。）

**加分点**：指出退出码 **3**（所有场景的故障当前都不存在）**正是为了区分这两种情况**：

| 退出码 | 含义 |
| --- | --- |
| `0` | **故障存在，且被检出** |
| `1` | **故障存在，但没检出/建议不完整** |
| `3` | **故障根本不存在**（注入没生效 / 环境不对） |

**"故障不存在"和"故障存在但没检出"是完全不同的问题。**

</details>

**练习 3：为什么"两个不同的模块故障都报 `ANOMALY_COMPONENT_FAULT`"迫使注入器加了 message 断言？**

<details>
<summary>答案要点</summary>

**原文**：

> "**Two different module faults both report `ANOMALY_COMPONENT_FAULT`,
> so matching on the code alone would accept the wrong fault as evidence that the right one was found.**

**三层断言强度**：

| 断言 | 强度 | 问题 |
| --- | --- | --- |
| `active == true` | **太弱** | 任何故障都满足 |
| `code == 期望` | **不够** | **多个故障共用一个 code** |
| **`code` 相等 + `message` 含期望子串** | **足够** | — |

**这是一个"证据强度"的问题**：`ANOMALY_COMPONENT_FAULT` 是**一个类**，不是**一个具体的故障**。

**教学要点**：

> **一个断言，它的强度必须足以排除"错误的成功"。**

**项目里的同类实例**：

| 位置 | 弱断言 | 强断言 |
| --- | --- | --- |
| 第 3 章 | "有证据" | "证据的 `ObservationID` 非空 + 采集时间非零 + 晚于下发 + 来源非 STALE" |
| 第 5 章 | "handler 被调用" | **"handler 未被调用"**（`test_execution_admission.py:22-26`） |
| 第 11 章 | "诊断了" | **"诊断出了但账本里没有"**（`taskID=""`） |
| 本章 | `code` 相等 | `code` 相等 + `message` 子串 |

**加分点**：指出**这个强化是被一次真实缺陷逼出来的**——作者原本只断言 code，直到发现两个故障共用它。

**"断言强度"这个问题，通常只有在一次假阳性之后才会被认真对待。**

</details>

**练习 4：为什么 liveness 和 readiness 必须分开？**

<details>
<summary>答案要点</summary>

**原文**：

> "**Liveness** asks 'should I restart this process' — a robot in an emergency stop is
> **a healthy process doing its job**, and restarting it would be an outage
> **caused by a working safety feature**.
>
> **Readiness** asks 'should I offer this to a person' — and being stopped is precisely a reason not to.
>
> **Folding them together is how a container ends up in a restart loop because its robot is safely stopped.**"

**具体场景**：急停锁存 + 无地图 + 有未知结果步骤的机器人——

| 端点 | 返回 |
| --- | --- |
| `/healthz` | **ok** |
| `/v1/readiness` | **不** |

**这个"矛盾"是正确的**：进程健康（它在执行急停），但它不该被提供给用户。

**如果不分开**：

| 合并方式 | 后果 |
| --- | --- |
| 用 readiness 当 liveness | **重启循环**——一个正常执行急停的进程被反复重启 |
| 用 liveness 当 readiness | **用户看到一个不能用的机器人**，而且没有任何提示 |

**第一种更糟**：因为**重启会中断其他服务**，而急停是**在工作**。

**教学要点**：

> **"这个进程还活着吗"和"这个服务能用吗"是两个不同的问题，
> 而在机器人上它们会给出相反的答案。**

**加分点**：指出这种"两个健康检查给出相反答案"的情况**不是异常，是设计**。

**一个只能回答"是/否"的健康模型，无法表达"健康但不可用"。**

</details>

**练习 5：三份清单为什么一条都没勾？**

<details>
<summary>答案要点</summary>

**实测**：`safety-checklist.md` 14 条、`release-checklist.md` 19 条，**全部 `- [ ]`**，仓库中没有任何 `- [x]`。

**为什么这是对的**：

| 做法 | 含义 |
| --- | --- |
| 仓库里预勾几条 | 读者会以为**这些已经做过了** |
| **全部不勾** | **清单是待办，不是成绩单** |

**而文档明确写了这一点**（`production-readiness.md` 的前提）：

> 当前 V1 是仿真与集成候选版，**没有已完成的实机生产验收**。

**`safety-checklist.md:3` 更直接**：

> **仓库没有已完成的物理验收结果。软件 READY 不是移动硬件的许可。**

**教学要点**：

> **一份签过字的清单，它的价值在于"谁在什么情况下签的"，不在于"里面有什么"。**

**一个预填的清单会破坏这个价值** —— 而文档里有一句非常具体的警告：

> "**新用户不应从文档复制一个全 true JSON 来取得 READY。**"

**加分点**：指出项目用**四个机制**防止"预填通过"：

| 机制 | 作用 |
| --- | --- |
| **全部不勾** | 视觉上明确是待办 |
| **显式警告**（"不能把它们当成开关预填为 true"） | 命名了这个反模式 |
| **`staleRecords`** | **改变配置/模型/标定后，旧记录自动失效** |
| **现场负责人签字** | 把责任落到一个具体的人 |

**最后一条最关键**：它把"通过"从一个**文件状态**变成了一个**人的判断**。

</details>

### 学生最容易误解的四个点

| # | 误解 | 纠正 |
| --- | --- | --- |
| 1 | "`install.sh` 有 4 个角色" | **只有 3 个**（`sim`/`local`/`robot-pi`）。`cloud` 已被移除并**硬报错**。四个角色同时出现在**预检**里——**这正说明预检与安装是两个工具** |
| 2 | "预检和 doctor 是一回事" | `precheck.sh` 是**安装前**、纯 bash、只读、覆盖 4 个角色；`doctor` 是**安装后**、Go、**第一步读安装回执**（所以未安装的机器上必然报错） |
| 3 | "故障注入器注入了三个故障" | **只注入一个**（`estop`）。另两个"**只观测，不注入**"——因为运行时自己的 commissioning 检查产生它们，**"a fabricated injection would prove nothing"** |
| 4 | "发布检查清单通过 = 可以无人值守" | 清单明写：**无人值守生产、通用家务和多机器人协同另行验收**；至少 30 次 trial **不会自动生成 PHYSICAL_GO** |

---

## 13.10 本章小结

1. **三个角色（不是四个）**，且装完**故意不启动**。`--dry-run` 打印 **shell-quote 后的真实命令**，不是描述。

2. **`doctor` 的第一步是读安装回执**，读不到就直接 return——**为了让错误信息准确指向真正的原因**。而它**不需要 `--dry-run`**，因为它本身是只读的。

3. **一键配对不用 SSH**：机器人**打印一次性配对码**（预共享的**唯一**秘密）→ HKDF 派生密钥 → 信封里装着证书材料 → **双向认证在同一次交换里完成**。**"with no SSH anywhere"**。

4. **8787 vs 8897 不是两个服务**，是**同一个 Local Agent 在两种场景下的端口**——因为**两份数据要同时存在**。

5. **两个只读工具各关掉一类"无法验证"**：`precheck.sh` 把"这台机器能不能装"变成**一条只读命令**；`inject_faults.py` 把"故障时 agent 说对话吗"变成**可反复跑的脚本 + 退出码**。

6. **两条自我约束**：**"一个会修东西的预检不能被用来探明情况"**、**"把可选的缺口报成失败，是一个检查被停止阅读的方式"**。

7. **必须区分三态**：`precheck` 的 WARN、门禁的 INCONCLUSIVE、注入器的退出码 **3**（**"故障根本不存在"** ≠ **"故障存在但没检出"**）。

8. **三份清单一条都没勾**。**"新用户不应从文档复制一个全 true JSON 来取得 READY。"**

---

## 13.11 源码索引

### 安装

| 内容 | 位置 |
| --- | --- |
| `cloud` 硬拒绝 | `install.sh:41-44` |
| flag 与骨架 | `install.sh:45-94` |
| `--dry-run` 机制 | `scripts/install/common.sh:24-35` |
| 固定版本 | `common.sh:8-9` |
| 平台白名单 | `common.sh:119-131` |
| `sim` 角色 | `scripts/install/sim.sh`（32 行） |
| `local` 角色 | `scripts/install/local.sh`（62 行） |
| `robot-pi` 角色 | `scripts/install/robot-pi.sh`（133 行） |
| **ROS 2 分支是死代码** | `robot-pi.sh:84-86` |

### doctor

| 内容 | 位置 |
| --- | --- |
| 检查项（六步） | `internal/robotagent/app.go:385-419` |
| **第 0 步读回执** | `app.go:390-394`；`app.go:122-135` |
| 权限位判据 | `app.go:405-407` |
| 转派 preflight | `app.go:410-417` |
| 20 项预检 | `scripts/robot-pi-preflight.sh:21-55` |
| 不支持 `--dry-run` | `app.go:386-389` |

### 配对

| 内容 | 位置 |
| --- | --- |
| 广播端口与载荷 | `internal/discovery/announcement.go:9-51` |
| **Go 侧跳过 loopback** | `announcement.go:263-269` |
| Python 侧含 loopback | `beacon.py:247` |
| 监听失败非致命 | `internal/discovery/listener.go:302-315` |
| 协议参数 | `internal/pairing/protocol.go:11-24,68-208,341-468` |
| 证书纪律 | `internal/pairing/authority.go:38-98,268` |
| 机器人侧窗口 | `robot/gateway/tangying_robot_gateway/pairing.py:79,85` |
| HTTP 端点与错误码 | `console/server.go:186-187`；`console/pairing.go:103-155` |
| `Pair` 五步 | `console/pairing.go:179-278` |
| SSH 回退 | `scripts/pair-robot.sh`（305 行） |
| 配置热生效链路 | `console/server.go:189,303-321`；`internal/localconfig/settings.go:66-171`；`cmd/local-agent/main.go:489-516`；`tasks/service.go:120-131` |
| `TANGYING_AGENTS` 不热生效 | `docs/operations/agent-runtime-config.md:5-9` |

### 部署与端口

| 内容 | 位置 |
| --- | --- |
| **8787 vs 8897 的解释** | `docs/architecture/why-distributed.md:183-185` |
| 端口来源 | `scripts/sim-stack.sh:10`；`Makefile:110-114` |
| 端口全表 | `docs/operations/deployment.md:127-136` |
| 环回绑定策略 | `cmd/local-agent/listen.go:19-65`；`listen_test.go:18-28` |
| systemd 用户单元 | `deploy/local/tangying-robot-local-agent.service` |
| launchd plist | `deploy/local/com.tangying.robot-agent.plist` |
| **"Service startup never enables torque"** | `deploy/robot/raspberry-pi/tangying-robot-edge-direct.service:15-16` |
| Fleet Compose | `deploy/cloud/docker-compose.yml`（109 行） |
| `fleet-up.sh` 顺序 | `scripts/fleet-up.sh`（225 行） |
| `start-all.sh` 顺序 | `scripts/start-all.sh`（321 行） |
| **liveness vs readiness** | `console/readiness.go:15-34` |

### 两个只读工具

| 内容 | 位置 |
| --- | --- |
| **两条自我约束** | `scripts/precheck.sh:8-19` |
| `set -uo pipefail` 不加 `-e` | `precheck.sh:24` |
| 平台报告 | `precheck.sh:87-98` |
| **版本比较器的历史缺陷** | `precheck.sh:104-115` |
| 工具探针 | `precheck.sh:145-199` |
| 四角色检查项 | `precheck.sh:230-330` |
| 不 source `common.sh` 的理由 | `precheck.sh:203-205` |
| **只报存在性不执行** | `precheck.sh:291-295` |
| **注入器的自我说明** | `scripts/inject_faults.py:6-15` |
| 三个场景 | `inject_faults.py:64-93` |
| **"只观测不注入"** | `inject_faults.py:163-172` |
| 急停无解除 RPC | `inject_faults.py:103-115`；`tests/contract/test_proto_schema.py:28` |
| **message 断言的动机** | `inject_faults.py:54-59` |
| 判定窗口 25 秒 | `inject_faults.py:42` |
| 结论三档 | `inject_faults.py:187-203` |
| **退出码四条（含 3）** | `inject_faults.py:266-271` |
| `expect_manual` 是死字段 | `inject_faults.py:64,72-73` |
| 真实输出 | `docs/architecture/supervision-verification.md:184-193` |
| 注入器无执行级测试 | `tests/install/test_precheck.py:150,175` |
| 故障注入登记制度（未实现） | `docs/architecture/agent-evaluation-system.md:327` |
| CI 不跑 `install-check` | `.github/workflows/ci.yml:36-39`；`Makefile:60-61` |

### 三份清单

| 内容 | 位置 |
| --- | --- |
| **证据分级表** | `docs/operations/production-readiness.md:7-14` |
| **"不能预填为 true"** | `docs/operations/production-readiness.md:38-42` |
| 现场放行六步 | `docs/operations/production-readiness.md:44-51` |
| 旧脚本数值门槛 | `scripts/xlerobot_production_check.py:75-77` |
| **安全检查清单 14 条** | `docs/operations/safety-checklist.md:7-20` |
| **"软件 READY 不是移动硬件的许可"** | `docs/operations/safety-checklist.md:3` |
| **发布检查清单 19 条** | `docs/operations/release-checklist.md:5-34` |
| 第四份清单 | `docs/install/xlerobot-experiment.md:7-13` |

---

**上一章**：[第 12 章 评测体系与后训练](../chapters/ch12-evaluation-training.md) · **下一章**：[第 14 章 从零搭建一套分布式机器人 Agent 系统](../chapters/ch14-build-from-zero.md) —— 施工图：分阶段、有验收标准、有"这一步做完了怎么证明"。
