# 2026-10-28 · 边缘大脑可靠性与教材 3.2.0

计划于 2026-10-27 开始，2026-10-28 完成核对。基线 `0b42b87d729b9c9adcd192d563450fd342583560`，分支 `codex/edge-brain-context-reliability`。先修复可复现的执行/通信问题，再将实际代码、不变量、代价与边界写回教材。两份已有营销文稿不属于本轮，保持未提交。

## 1. 从故障反例到实际修改

| 问题 / 复现 | 实现 | 回归判据 |
| --- | --- | --- |
| Host 检查批准后，Catalogue RPC 阻塞期间取消/换版/撤销批准/撤销外部控制，旧实现仍调用设备 | 目录先校验，随后重读任务权威、外部控制、context | 阻塞测试四种变化均 0 次物理 provider 调用；仍不声称跨 Runtime 原子性 |
| operation 查状态耗尽持久读预算，ErrNeedsInput 被当作临时失败反复轮询 | 立即进入已有 owned stop 路径 | 一次普通状态拒绝后停止监督；正确 owner/id 才允许取消，状态/停止不确定保持 unknown |
| peer agentId 相同但 robot/domain 改变，旧 outbox 队首被拒绝却继续堵住新消息 | SQLite PendingFor 在 FIFO/head 之前过滤当前完整 sender/recipient | 三类重配、同 stream、真实 SQLite 关闭重开，新消息可达且旧项不假 ACK |
| outgoing[C] 复用 incoming[B] 会把 B 的凭据发给 C | 收齐配置后校验所有跨 peer 凭据组合 | 配置顺序无关、不同文件相同值也拒绝，错误不输出秘密；同 peer 对称 token 兼容 |
| 书中缓存仍是建议，客户端没有可靠 usage | 固定 system 规则，目标留 user；GOAL/RECOVERY 显式 off/openai/vllm 提示，作用域派生 key；Projection.ModelCall | 默认无提示，身份/工具/模型变化换 key；零/缺失/部分/invalid usage 区分，SQLite 重开保持；无真实性能数值 |
| 无任务的机器人级恢复在缓存开启后没有 scope | 注入已 bindRuntimeIdentity 的 robotId | 只给真实 RobotID，无虚构 TaskID/revision；旧无 identity observer 仍兼容 |
| Recovery loop 中途失败丢掉已发生轮次；取消上下文又直接取消审计写入 | 错误返回前保存 Calls/Rounds/Reason；Record 使用独立 5 秒 cleanup context | 取消模型请求保留 exact request 和 canceled 分类；后续观察失败不抹掉先前调用；不把 cleanup context 用于工具执行 |

代码主要在 `internal/edgeagent`、`internal/capabilityagent`、`internal/actionloop`、`internal/recoveryexec`、`internal/modelroute`、`edge/exchange`、`middleware/sqlite` 和入口装配。模型配置及记录字段见[运行说明](../production/edge-brain-model-context.md)。没有修改数据库或 peer wire schema；新增 ModelCall 字段为可选。

## 2. 测试与证据

受影响的 14 个 Go 包以 `-race -count=1 -skip Campaign -timeout=120s` 验证通过，实际总耗时 44.662 秒。完整命令、输出和摘要分别在 [affected-packages.json](../experiments/2026-10-27-edge-brain-checks/affected-packages.json) 与 [原始日志](../experiments/2026-10-27-edge-brain-checks/affected-packages.log)。Campaign 单独通过固定 allowlist 重复采集，不将一次跳过的包检查当成全 campaign 完成。

[重复故障实验](../experiments/2026-10-27-edge-brain-reliability/README.md)保存每次命令、原始 Go JSON、来源哈希、任务结局与统计分母。它包括连续确认后重启、长前缀后的未知写、跨重启完成读配额、规划读/冷档/提案预算与 peer 重配。实际 10 轮、60/60 顶层和 110/110 leaf 通过；10 个 32 步目标完成，另 10 个目标各确认 24 步后因第 25 写未知停在 RECONCILIATION，均重启 0 写。577 个源文件在各轮前后保持相同摘要。采集结束后只增强了采集器的脚本留存/时区说明；准确运行版本另存 runner-source.py.txt，实验依据是来源清单而非事后脚本。软件 unknown 案例的断言通过表示拒绝重放，不表示任务完成；相同确定性输入的重复不是独立实体机器人可靠性样本。

本地 darwin/arm64 和 Linux/arm64（CGO_ENABLED=0）的 local-agent / edge-worker / edge-exchange 共 6 个入口二进制构建成功；[格式、SHA-256 与命令](../experiments/2026-10-27-edge-brain-checks/builds.json)留档。只证明可构建，没有部署至设备。

开发中保留的事实：一次新 fixture 缺 Document.Role 导致校验失败，已补真实角色；另一次模型取消测试的 HTTP handler 未消费请求体导致测试清理等待，修复 fixture 后通过；一次全 edgeagent campaign 在约 110 秒时人为取栈终止，不能算通过。最终受影响包与明确选择的重复实验另行报告；旧 96 步 race 性能问题仍按历史记录保留。没有新跑完整 CI、Gazebo、ROS 2 设备或外部 LLM benchmark。

## 3. 教材升级及勘误

教材 3.2.0 增加两幅确定性 SVG、一幅内置 imagegen 三联插画，合计 23 SVG + 2 PNG。图像提示词记录于 `book/assets/EDGE-AUTONOMY-ILLUSTRATION.md`；插图不是实验截图。第 8 章用真实修复讲 TOCTOU、筛选和队首不交换、预算失败不能自愈；第 17 章的信封改为可通过实际校验的 digest，并给出时钟偏差界及小角度变换 Jacobian；第 18 章推导保护状态线性下界、缓存提示身份、模型等待与停止距离。第 12 章改正可观测样本分母，附录同步 worked examples 与源码入口。

3.1.0 的正文/资产/生成器身份已归档，旧出版目录不覆盖。3.2.0 固定本轮实现提交，并通过独立文档快照固定新增报告；历史实验继续引用各自版本。出版结构、链接、离线 HTML/EPUB、图形生成与教材小实验检查结果见出版说明和最终发行记录。

## 4. 部署与回退

默认不开缓存提示，现有直连服务请求仍兼容；配置开启前验证相应后端字段。INTENT/旧 PLANNING/SYSTEM 和当前 `/v1/assist` 不扩展提示支持；代理路径继续用 off。使用旧第三方 Journal 时仍保留身份拒绝，但要实现 PendingFor 才获得新队列活性。新凭据冲突规则可能拒绝原本可启动的危险配置，应重新配置 peer 专属 token，不能为启动关闭校验。

回退运行时可回到基线提交并删除新增 CACHE_MODE/NAMESPACE 配置；新增 trace 是可选字段，未做数据库迁移。升级后仍应按单机器人有界任务逐步验收：具体厂商 ROS 2 控制接口、标定、物理停止、无线分区和协作地图对齐尚需设备证据。当前更适合受控边缘集成与实机测试候选，不能据此宣称任意无人机/足式/AGV 已可无人值守上线。

## 5. 本地出版核对

首次 3.2.0 预览已构建至 `/tmp/agentos-book-3.2-preview-20261028`；`.venv-book/bin/python -m pytest -q -o addopts='' tests/book` 为 **44 passed in 4.26s**，包含可重复导出、EPUB 导航/图像、越界资源拒绝与两份教学实验。23 幅 SVG 的生成一致性检查通过，新增/改动图逐一被动渲染检查，三联 PNG 已目视核对。实验 26 份清单文件的 SHA-256 独立复核无误；Go 源文件均与十轮实验相同，只有采集器的留存/时区说明在实验后增强，其实际执行副本已保存。

实现提交：`63af99305fd8204c0a54d86145cb94d89b754326`。最终离线目录为 `artifacts/book/3.2.0/`，来源清单/生成器/图形哈希由 provenance.json 固定；SHA256SUMS 用于核对输出。最后的文档快照独立固定，代码快照不随排版改动漂移。
