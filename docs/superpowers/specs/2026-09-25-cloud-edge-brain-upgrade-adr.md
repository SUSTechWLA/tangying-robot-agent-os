# 云端机群大脑与 Orin NX 边缘 Agent 升级规范（ADR，2026-09-25）

状态：**软件候选规范，待目标环境认证**。本文件保留升级时的约束、取舍、回滚点和验收判据，后续排障应引用具体 commit 和本文件，不改写旧版 [闭环语义升级 ADR](2026-09-10-closed-loop-semantic-upgrade-adr.md) 的历史结论。实际软件验收记录在 [云边升级验收](../../production/cloud-edge-upgrade-acceptance.md)。

## 目标与运行边界

- 云端 Fleet Agent 管机群任务、身份、审批、资源租约、共享世界与跨机器人协调，可连接云内大模型；系统任务的最终权威在 Fleet Coordinator/MySQL/WorldHub。
- Orin NX Local Agent 只管一台机器人，以本地 SQLite、Runtime 和量化模型完成单机闭环；复杂的意图、规划或恢复可显式调用云端**只读**推理工具。机器人动作的最终否决权在边缘 Guard/Runtime，不在模型。
- Fleet 模式的每台机器人运行 Edge Worker，云端编排的计划必须在边缘具象化、校验、编译、执行和新鲜观测核验。Local Agent 和 Edge Worker 不可同时控制同一 Runtime。
- `AGENT_INTENT_*`、`AGENT_PLANNING_*`、`AGENT_RECOVERY_*` 分别覆盖传统 `AGENT_*`。云端控制面的前两段可以独立选择大模型；Orin NX 三段可以独立选择本机量化模型、确定性路径或云端别名。恢复阶段只在 Local Agent 运行。

## 决策记录

### ADR-1：两个任务权威，两种部署模式

单机器人自治使用 `cmd/local-agent`；机群统一派单使用 `cmd/fleet-control-plane` + 每机 `cmd/edge-worker`。身份来自 Runtime `Info`，显式配置与 Runtime 不符时拒绝启动。两种边缘进程在同一主机上按 Runtime 地址取得进程锁，Orin systemd 单元共享锁目录；相同 RobotID、不同端口的独立仿真可并行。模式切换先冻结/完成任务，核对持物与未知物理终态，再停旧进程、启新进程。

不采用“同一台 Runtime 同时接收本地与云端任务，再由模型协调”的方案：两份任务账本没有原子提交关系，断网和未知物理结果无法安全合并。**同机锁不是跨主机 fencing，也不能识别同一 Runtime 的不同地址别名**；生产两个单元必须配置相同规范地址，跨主机误配置须由单一 Runtime 控制凭据、部署编排和现场验收阻止。

### ADR-2：每个模型调用点独立、显式、可追踪

模型路由由 `internal/modelroute` 解析。缺 URL/模型名的 OpenAI 兼容配置失败关闭，本地兼容端点允许无 API Key。Console 显示三个阶段的非秘密路由；保存默认模型后实时重建意图、规划与恢复的调用器，应用失败恢复原配置并返回错误，不能宣称已生效。**阶段 URL 改变时，默认 Key 与模型名都不继承**；必须填新模型名，按新端点需要显式填 Key，避免旧凭据泄露。三个调用器的响应限制为 1 MiB，规划候选仍受既有 Guard/Skill catalog 校验。

云端 `/v1/assist/chat/completions` 接受机器人专属设备身份或独立的 Assist 权限身份，不接受操作员会话代替设备。`FLEET_ASSIST_DEVICE_CREDENTIALS` 的 token 只能调用该固定模型接口，不能取队列、上报遥测或修改任务；不同机器人、不同权限范围的 token 禁止复用。Fleet 把 `cloud-assist` 或 `cloud-intent`、`cloud-planning`、`cloud-recovery` 别名映射为其自己的模型配置；设备无法指定真实上游模型或带入上游密钥。接口仅转发有界文本请求，不开放流式、多模态 URL 或任意扩展字段；请求 64 KiB、响应 1 MiB、输出 token 和全局/单机并发均有上限。仅记录机器人 ID、模型别名与结果，不记录任务正文/令牌。

不采用“把云端 GPU 模型 API Key 下发到 Orin NX”的方案；设备只持有自己的 Fleet Assist 凭据。也不把云端建议直接解释为 Runtime 命令。

### ADR-3：部署与网络失败关闭

Orin NX 两个 Linux arm64 二进制由 `make edge-orin-build` 交叉编译。两套 systemd 单元启动前执行无运动 `--check-config`，验证身份、Runtime mTLS、Fleet TLS 文件和推理路由基本合法性；真正的连接、延迟、证书主机名与模型协议仍要在目标环境验证。Edge Worker 只允许 HTTPS Fleet URL 或开发机 loopback HTTP；CA 不可读/无效时拒绝启动，HTTP 重定向不携带设备令牌。

云端大规模 roster 使用一个 Redis 客户端连接池承载每机器人 Stream，而非每机器人一个连接池。未登记的机器人入队和取队列明确失败。千台身份的队列隔离测试仅验证路由逻辑；单主 WorldHub、MySQL/Redis、推理池与网络仍需按目标负载认证和分片/高可用设计。

## 兼容与回滚

- 不设新变量时，传统 `AGENT_*` 和确定性路径继续运行；Fleet Assist 不配置则接口返回 503。
- `cloud-assist` 是默认别名，要求 `FLEET_ASSIST_MODEL`；阶段别名仅在相应 `FLEET_ASSIST_*_MODEL` 配置后可用。未知别名返回 400，不回退到设备指定模型。
- 新版 Edge Worker 拒绝远端明文 Fleet URL 和错误 CA；升级前必须修正旧部署。旧版 Local Agent 未使用同机锁，混合版本切换仍需运维停旧进程。
- 回滚先冻结派单和运动，核对 Runtime journal、任务 revision、World checkpoint 与现场状态，再回退二进制/模型配置；不能回滚 fencing token、重放未知物理命令或删除证据抹平冲突。

## 验收门禁与可追溯材料

软件门禁：Go/前端/部署合同测试、race 重点包、Linux arm64 交叉编译、云端 Assist 的 TLS 设备认证与上游别名测试、千台逻辑隔离、配置错误/CA/重定向拒绝、模型失败的确定性退路、计划 Guard/Runtime 闭环以及格式/文档检查。每次记录命令、commit、退出码和跳过项。

目标环境门禁：Orin NX 上本机量化模型真实推理内存与 p95 延迟、JetPack/CUDA 兼容、断网/重启/降级、mTLS 与运行用户权限；GPU 服务器上真实模型协议、别名与输出 token 行为、并发负载和故障回退；真实机器人机械/感知/急停/持物恢复；数百至数千台目标观测频率下的 MySQL/Redis/WorldHub/网关容量、长稳与故障恢复。**通过软件门禁不能替代这些目标环境签字，也不能称为无人值守生产认证。**

定位问题时优先关联：Git commit、部署模式、RobotID、模型阶段/别名、任务与 revision、Runtime journal command ID、Fleet 事件/World revision、配置哈希（不收集密钥）、模型权重/量化格式与设备规格。保留原始失败及复现命令，后续修复新增记录而不覆盖本 ADR。
