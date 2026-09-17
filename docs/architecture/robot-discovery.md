# 机器人自动发现：上电联网就能被找到

本文说明"机器人上电 → 局域网广播 → Agent 自动发现"这条链路的协议、设计取舍，以及**明确没有做的事**。

配套阅读：[恢复 Agent](recovery-agent.md)（发现问题之后）、[全新部署](../operations/fresh-deployment.md)（操作步骤）、[Agent 运行时配置](../operations/agent-runtime-config.md)。

---

## 1. 它解决什么

在此之前，把 Agent 接到一台机器人上，第一步是**人**要知道机器人的主机名，并且那台机器人要开着 SSH、操作者要有 `sudo`：

```bash
robot-agent pair xlerobot.local --ssh-user ubuntu   # 主机名是打字打进去的
```

机器人自己从不声明"我在这里"。对一个买回家插上电就想用的产品，这是第一道也是最硬的一道门槛。

现在机器人主动广播，Agent 被动监听，控制台把听到的列出来：

```bash
curl -s http://127.0.0.1:8787/v1/robots/discovered
```

```json
{
  "listening": true,
  "robots": [{
    "robotId": "xlerobot-0001", "hostname": "xlerobot.local",
    "address": "192.168.50.73:50051", "adapter": "xlerobot",
    "sourceIp": "192.168.50.73", "pairingState": "open",
    "needsPairing": true, "pairingOpen": true, "capabilityCount": 12
  }],
  "unreadable": 0, "mismatched": 0
}
```

## 2. 协议

UDP 广播，端口 **45871**，每 **5 秒**一次，载荷是 JSON：

| 字段 | 含义 |
| --- | --- |
| `topic` | 恒为 `tangying.robot.announce`，用来把无关广播和机器人区分开 |
| `version` | 协议版本，当前 `1` |
| `robotId` | 机器人稳定身份，发现列表按它去重 |
| `hostname` | 机器人自认的名字，仅供显示与证书 SAN |
| `address` | **对端可连的** `host:port`（见 §4） |
| `adapter` | 硬件适配器名，例如 `xlerobot` |
| `pairingState` | `unpaired` / `open` / `paired` |
| `capabilityCount` | 能力数量，只是摘要，目录仍以机器人发布为准 |
| `sentAt` | 机器人时钟的 RFC3339 时间 |

Agent 侧保留 **3 个广播周期**（15 秒）：一台关机、拔线或被搬走的机器人会自己从列表里消失，不需要谁去清理。`3` 这个数字是取舍——短于它，丢一个包就会让机器人闪烁；长于它，列表就不再描述"现在"。

## 3. 为什么广播是明文且不认证的

必须如此：两端**还没有共享密钥**——建立密钥正是配对要做的事。所以载荷里只能放"同一网段上任何设备本来就能看到"的东西：身份、地址、适配器、是否正在等待配对。

由此得到一条硬规则：**配对码永远不进广播**。广播配对码会让"需要有人在机器人旁边"这个前提彻底失效，同一网段上任何设备都能抢先配对。

`pairingState` 是机器人的**自述且未经核实**。Agent 不得把它当作授权：它只决定怎么显示。一台机器人能不能被配对，由机器人在收到配对请求时决定。

## 4. `0.0.0.0` 是可以绑定的地址，也是不能广播的地址

机器人默认 `--listen 0.0.0.0:50051`。告诉对端"连到 0.0.0.0"等于让它连自己。所以广播前必须替换成**真正能到达网络的那个接口地址**（`resolve_address()`），并且 `validate()` 拒绝广播 `0.0.0.0`——这一条不是格式检查，是"发出去也永远不会成功"的检查。

## 5. 跨语言契约用 fixture 钉住

机器人侧是 Python，监听侧是 Go。两边的编译器都不会替对方检查，所以线格式由一份**共享 fixture** 锁死：

```
tests/contract/robot_announcement.json
```

- Python 侧断言 `encode_announcement()` 的输出与 fixture **逐字节相同**；
- Go 侧断言它能读**这个文件本身**。

已验证：把 `robotId` 改成 `robot_identifier`，两侧测试同时失败。有一处细微差别值得记住——Go 的 JSON 匹配对字段名大小写不敏感，所以 `robotId → robotID` 这种改名只有 Python 的逐字节断言能拦住。这正是契约必须在两侧都断言的原因。

## 6. 身份必须是每台唯一的

出厂默认的 `ROBOT_ID` 是一个所有机器人共用的常量。发现列表以它为键，所以两台机器人会显示成**一台在移动的机器人**。

`default_robot_id()` 的回退顺序：`ROBOT_ID` 环境变量 → `/etc/machine-id` 前 8 位 → 主机名 → 历史常量。选 `machine-id` 而不是随机值或主机名，因为它是**跨重启稳定**的（否则每次上电都像一台新机器人），而且是每台系统唯一的（两台机器人可以都叫 `raspberrypi`）。身份来源会打进日志，因为"有人选过这个名字"和"跑在没人看过的回退值上"是两回事。

## 7. 一次广播要覆盖的两种部署

`broadcast_targets()` 返回每个可广播接口的广播地址，**外加 `127.0.0.1`**。

回环不是调试便利：模拟器把机器人运行时和 Agent 跑在同一台笔记本上，只广播外网接口的机器人会对身边的 Agent 隐身——于是"上电就出现"在真机上成立、在演示里失效，而演示恰恰是所有人的第一次体验。

## 8. 三态报告，而不是"有没有"

| 字段 | 含义 |
| --- | --- |
| `listening` | 到底有没有人在听。空列表既可能是"网上没有机器人"，也可能是"没人在听"，后者会把运维支使去检查一台好机器人 |
| `mismatched` | 有机器人在广播，但协议版本读不了。**这是最有用的一个数**：机器人就在那儿，它没进列表的原因只是版本不一致 |
| `unreadable` | 端口上读不出内容的报文。属于噪声，和版本不符分开计数，因为合并会把"你的机器人固件更新了"显示成"有什么东西在乱发包" |

## 9. 本版本没有做的事（诚实清单）

- **没有免 SSH 的一键配对**。发现链路已经通了，配对**投递**仍然走 `scripts/pair-robot.sh` 的 SSH 路径。机器人能广播 `pairingState=open`（要求 `ROBOT_PAIRING_WINDOW=1`），但**接受配对请求的引导通道没有实现**：还没有"未配对时开放一次性 enroll 端口 + 配对码校验 + 成功后关闭并切 mTLS"这套东西。所以现在的完整流程仍然是：**发现（自动）→ 配对（一次 SSH，需要有人操作）→ 使用**。
  把这一步说清楚，是因为"自动发现"很容易被读成"全自动接入"，而这两件事之间隔着一次真实的密钥投递。
- **没有 mDNS/DNS-SD**。广播够用且不需要额外守护进程；`_tangying-robot._tcp.local` 的设计描述仍在 `docs/superpowers/specs/` 里，未实现。
- **没有跨网段发现**。广播不出网段，这是特性不是缺陷：跨网段接入应该是云端注册表的事，不是让局域网协议穿透路由器。

## 10. 相关实现文件

| 文件 | 内容 |
| --- | --- |
| `robot/gateway/tangying_robot_gateway/beacon.py` | 机器人侧：身份推导、地址解析、广播线程 |
| `internal/discovery/announcement.go` | 协议定义、编解码、广播发送 |
| `internal/discovery/listener.go` | 监听、去重、老化、三态计数 |
| `console/discovery.go` | `GET /v1/robots/discovered` |
| `tests/contract/robot_announcement.json` | 跨语言契约 fixture |
| `internal/discovery/discovery_test.go` | Go 侧契约与监听行为 |
| `robot/gateway/tests/test_beacon.py` | Python 侧契约与广播行为 |
