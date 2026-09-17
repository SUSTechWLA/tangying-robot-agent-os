# 机器人配对：不需要 SSH 的一键接入

本文说明把一台机器人接入 Agent 的协议、威胁模型，以及**为什么配对码必须由机器人打印出来**。

配套阅读：[机器人自动发现](robot-discovery.md)（配对之前）、[可用性自检](readiness.md)（配对之后）、[全新部署](../operations/fresh-deployment.md)（操作步骤）。

---

## 1. 它替掉的是什么

`scripts/pair-robot.sh` 做的是同一件事，而且做得对：本地 CA、90 天叶子证书、CA 私钥永不外传。但它要求操作者**知道机器人主机名**、**有 SSH 账号**、**配了免密登录**，并且**对面有 sudo**。这是一个技术员的流程，也是"上电就能用"和现实之间最后一道坎。

现在：机器人在局域网广播自己（[发现](robot-discovery.md)）→ 控制台列出它 → 输入机器人打印的配对码 → 完成。

```bash
curl -s -X POST http://127.0.0.1:8787/v1/robots/pair \
  -H 'Content-Type: application/json' \
  -d '{"robotId":"xlerobot-0001","address":"192.168.50.73:50051","code":"4F2K-9QW7"}'
```

两条路径产出的机器人**是同一台**：同样的三个文件（`server.key` 0600 / `server.crt` 0644 / `client-ca.crt` 0644），同样的目录，同样的 CA 本地持有方式。两条路径都必须如此，否则它们会漂移成"两种配对"。

## 2. 为什么配对码必须由机器人打印

这是整个设计里唯一不能绕过的一条：

> **你无法认证一个和你没有共享秘密的设备。**

配对码就是那个共享秘密，而且它必须是**人从机器人身上读到的**——印在标签上，或者服务启动时打进日志。只要这个前提成立，"谁能配对"就等于"谁能站到机器人旁边"，而这正是消费级产品需要的根信任。

推论：**配对码永远不进广播**。广播是未认证、明文的，任何同网段设备都能读；广播配对码等于把"必须有人在机器人旁边"这句话删掉。发现协议里带了 `pairingState`，但那是**状态**，不是凭证。

## 3. 协议

TCP，端口 **45872**（紧挨着广播端口 45871），4 字节大端长度前缀 + JSON。

```
agent → robot:
  {"topic":"tangying.robot.enroll","version":1,"robotId":"...",
   "salt":"<b64 32B>","nonce":"<b64 12B>","payload":"<b64>"}

robot → agent:
  {"topic":"tangying.robot.enroll.result","version":1,"robotId":"...",
   "nonce":"<b64 12B>","payload":"<b64>"}
```

- `payload` 用 **AES-256-GCM** 加密，密钥来自 **HKDF-SHA256(ikm=配对码, salt=salt, info="tangying.robot.enroll.v1")**；
- GCM 的附加认证数据（AAD）是 `robotId`，所以**一次为 A 机器人封装的请求无法在 B 机器人上重放**；
- 请求载荷是三份 PEM：CA 证书、机器人证书、机器人私钥；
- 应答载荷是 `{"status":"paired"|"refused","detail":"..."}`，用同一个密钥封装——**这就是认证**：只有知道配对码的一端才能产出一个本端会接受的密文。

## 4. 威胁模型（明说）

| 攻击者 | 结果 |
| --- | --- |
| 被动窃听 | **学不到东西**：载荷加密，握手不泄露明文 |
| 主动冒充机器人 | **答不出来**：不知道配对码 |
| 知道配对码的人 | **可以配对**，而且这是设计意图——配对码的含义就是"我站在这台机器人旁边" |
| 读到码并抢先配对 | **能赢**。缓解手段是适合这个威胁的那些：码一次性、有窗口、有次数上限 |

**不发明任何密码学原语**。HKDF 与 AES-GCM 都按标准使用，而且 HKDF 的实现**同时**被 RFC 5869 官方测试向量和 Python 的 `cryptography` 实现钉住——手写的密钥派生正是那种"错了也没人发现"的代码。

## 5. 跨语言契约

机器人侧是 Python，Agent 侧是 Go。契约由 `tests/contract/robot_pairing.json` 锁死：

- Python 断言 `encode_request()` 的输出与 fixture **逐字段相同**，派生密钥与记录值相同；
- Go 断言它能解开 fixture 里的请求载荷与应答载荷，并且**能解出与 Python 相同的派生密钥**。

派生密钥单独断言，是因为一个字节之差会产出"能编译、能运行、永远配不上"的实现，而症状只有"配对不工作"四个字。

## 6. 三种防止配对码变成后门的设计

1. **一次性**。配对成功后码立即作废（文件删除、内存清空）。一个活得比配对更久的配对码就是机器人的永久钥匙。
2. **窗口**。未配对的机器人在启动后开放 `ROBOT_PAIRING_WINDOW_SECONDS`（默认 900 秒），然后自己关闭。永远开着的窗口意味着任何人都可以在任何时候试着认领它。
3. **次数上限**。默认 5 次错误后窗口关闭，必须在机器人本地重新打开（`PairingState.regenerate()`，需要有本机访问权）。码短到能读出声，所以猜必须不免费。

**已配对的机器人不会再开配对窗口。** 在已配对的机器人上开一个 enrollment 监听器，等于给出一条"不碰它就能换掉它证书"的网络路径；这条路径是被**拒绝**而不是被静默跳过的，而且会记录一条 `pairing.skipped`。

每一次尝试都会上报：`pairing.window-open` / `pairing.rejected`（带 peer 地址）/ `pairing.paired` / `pairing.window-closed`。配对窗口正是有人会来试的时候，所以"有人试过、不是我"必须是可见的。

## 7. 配对成功之后

机器人的 gRPC 监听器**仍然是启动时那个明文监听器**——一个正在运行的 TLS 监听器无法换掉自己的身份。所以配对成功后进程主动退出（退出码 1，`Restart=on-failure` 会把它拉起来），日志里写明原因。**不这样做就会"看起来配好了"**：证书在磁盘上，而机器人还在按老身份服务。

Agent 这一侧同样：机器人客户端是启动时用当时的证书构造的，所以控制台返回 `restartRequired: true`，并说明"重启本机 Local Agent 后生效"。这是本地动作，不是机器人侧的配置步骤。

写 Agent 自己的那一半时：

- 在机器人**接受之后**才写，失败绝不会留下一个指向"会拒绝自己"的机器人的 Agent；
- 保留配置文件里其它键（同一个文件也放模型配置，配对顺手把 API key 删掉会看起来像"配对弄坏了语言能力"）；
- `local-agent.key` 0600、`local.env` 0600。

## 8. 证书是 Go 原生签发的

`internal/pairing/authority.go` 用 `crypto/x509` 生成 CA 与叶子证书，不再 shell 调用 `openssl`。理由不是"少一个依赖"，而是**一个按钮没法合理地 shell 出一串需要临时目录和五个子进程的脚本**，而且失败时会在磁盘上留下签了一半的证书。进程内签发意味着一个函数要么产出整套材料，要么什么都不改。

形状与脚本一致：ECDSA P-256、CA 10 年、叶子 90 天、服务器叶子带 DNS + IP SAN。签发后会**立刻用本端 CA 验证一遍**——本端验不过的证书会在机器人那边以一句没人读得懂的 TLS alert 失败。

已存在的 CA 不会被静默替换（那会让所有已配对的机器人失效，是人的决定）；只剩一半 CA 时**停下并说明缺哪个文件**，而不是生成一个新的把另一半毁掉。

## 9. 配对之后：机器人不再接受明文

配对装上证书的意义在于**机器人从此不再接受明文连接、并要求客户端证书**。这一点有测试直接钉住：

- 没有凭据且没有显式 `allow_insecure` 时，`start_server` **直接报错**，不会静默退回明文；
- 有凭据时走 `add_secure_port`，并且 `require_client_auth=True`——这是"双向 TLS"里双向的那一半；
- 已配对的机器人在下一次启动时**不会重开配对窗口**（有测试）。

再加上配对成功后进程主动退出以切 mTLS，"配好了"意味着三件事同时成立：证书在磁盘上、明文端口关掉了、配对窗口不会再开。

## 10. 相关实现文件

| 文件 | 内容 |
| --- | --- |
| `internal/pairing/protocol.go` | 线格式、HKDF、AES-GCM 封装、帧、常量时间比较 |
| `internal/pairing/authority.go` | CA 与叶子证书的 Go 原生签发 |
| `internal/pairing/client.go` | Agent 侧：连接、发送、读应答 |
| `robot/gateway/tangying_robot_gateway/pairing.py` | 机器人侧：配对码、窗口、安装材料 |
| `console/pairing.go` | `POST /v1/robots/pair`、写 Agent 自己那一半 |
| `tests/contract/robot_pairing.json` | 跨语言契约 fixture |
| `internal/pairing/hkdf` 测试 | RFC 5869 A.1–A.3 官方向量 |
| `internal/pairing/crosslang_test.go` | Go Agent 与**真实 Python 机器人**在真 socket 上完成一次配对 |
