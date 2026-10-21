# 2026-10-22 · 教材 3.1.0：跨域协议、模型输入、上下文与一致性

## 需求与交付范围

将教材重点讲透：跨域传什么、模型看什么、怎样使用 KV cache、长程上下文如何管理、分布式一致性在哪些边界成立。要求给出实例、图示、数学推导、替代方案及代价；允许 imagegen；完成后跳过流水线，提交并直接合入远程 main。

本次交付是教材与出版工具，不更改机器人运行时。19 章、9 附录、57 道 B/E/R 题的总体结构保留，重点深化第 8、17、18 章，并同步第 12、19 章、导读、参考文献、课程脚本与解答。保留历史实验原身份，未新增或改写实验成绩。

## 最重要的源码核对结果

| 问题 | 实现位置 | 本书如何解释 |
| --- | --- | --- |
| 模型具体输入 | `internal/actionloop/llmdecider.go` 的 messages/chatRequest | managed 路径两条 messages；tools 独立；动态 Goal 在 system；最终请求正文与 SHA 发送前归档 |
| 缓存现状 | 同文件 chatRequest/chatResponse | 无显式 cache 参数与 usage 解析；后端隐式缓存可能存在，现有 trace 无法证实命中 |
| 批准前能力与 scope | `internal/capabilityagent/planner.go` | 只读 provider/PlanningFields、提案是计划数据、真实 robot scope，不伪造尚不存在的 task/revision |
| 长程状态 | `internal/edgeagent/host.go`、`core/agentcontext/managed.go` | Host 完整账本与最近 8 项/8000 字节进度；managed 热冷投影是另一接线，保护状态放不下则拒绝 |
| 精确冷读 | `internal/actionloop/context_read.go` | scope 捕获、源文本/哈希与分页，不让模型自行跨 robot 改作用域 |
| 执行一致性 | `internal/edgeagent/journal.go`、`middleware/sqlite/fleet.go` | CAS、事务内状态/事件/Outbox/检查点；物理世界在事务外 |
| 两种 Basis | `core/contextcontract/basis.go`、`core/exchangecontract/contract.go` | 执行依据含权限与命令；空间消息依据不授予动作 |
| 同行采纳 | `edge/exchange/coverage.go` | 身份、序号、TTL、空间与任务版本、采集年龄、目标集合；结果仍为 advisory |

实现链接继续固定 `9e6a58f6ad48c6d9441e95511ddb4d503ea6449e`，配套文档链接推进到 `e1c6e353c3df7397fbe229a4542459ba9251ac46`。本次从工作树核对相关实现；两快照间只有上一轮教材变更，没有将建议伪装为运行时能力。

## 内容变化与教学用途

第 17 章增加六条路径的最小信息表、信息域定义、点云与语义带宽算例、执行/空间 Basis 比较、坐标变换与协方差线性化、仓库消息时间线、三种通信架构取舍。跨域大对象引用协议与误差融合明确属于扩展设计。

第 18 章从真实请求开始，逐步解释三种记忆、候选准入、保护投影约束与背包抽象、两条当前上下文路径、精确冷归档、不一致裁决；再推导 Q/K/V、显存容量、云边缓存迁移下界、模型与任务延迟收益、前缀布局/失效、隔离与接入步骤、压缩回本。所有数值都标明教学假设。

第 8 章增加一致性对象表、双写者 CAS 推演、Outbox 崩溃窗口、网络分区下的权威条件、因果偏序与向量时钟教学例子、集合合并边界和三条执行不变量。明确当前不是全局共识、完整向量时钟或通用 CRDT 地图系统。

第 12 章给出投影×缓存 2×2 对照、冷暖定义、请求与 token 两种命中分母、usage 缺失、TTFT 与非流式总耗时区别，以及任务级统计。第 19 章将消息、模型、故障与指标串成五张设计表。附录补四份工作纸与答案、两次核心专题课，并增加一手文献。

## 图形、发布与旧版追溯

新增五幅可再生成 SVG：`08-cas-outbox`、`17-domain-contracts`、`17-version-cut`、`18-model-request`、`18-kv-prefix`。保留原 16 幅，共 21 幅技术图。

使用内置 imagegen 生成一幅仓库概念插画，原图作为 `book/assets/domains-warehouse.png` 入库；未改图。最终提示词与生成方式见[插图记录](../../book/assets/ILLUSTRATION.md)。这不是仿真截图，不表达控制协议或实验结果。

发布器保持本地被动 SVG 校验，新增本地 PNG 签名/路径检查、二进制 ZIP 写入及 EPUB image/png MIME；图像均纳入 provenance 与 SHA256SUMS。现有出版测试的版次/资源类型预期同步更新，但本次没有运行测试套件。

上一版 3.0.0 的 31 份正文和 16 幅图的身份固定在 e1c6e353；[档案](../../book/editions/3.0.0/README.md)包含原 edition、RELEASE、源/图片/生成器 SHA 与恢复步骤。旧出版物目录保持原样。

## 本地编辑核对结果

按用户要求，**不运行 CI/GitHub Actions、pytest、Go 测试、课堂程序、Gazebo、ROS 2 或 LLM 性能实验**。未因教材发布启动机器人或更改部署。

实际运行的出版命令：

```bash
python3 scripts/build_book_figures.py
.venv-book/bin/python scripts/build_book.py --release --output /tmp/agentos-book-3.1-preview-a
.venv-book/bin/python scripts/build_book.py --release
python3 scripts/build_book_figures.py --check
python3 scripts/build_book.py --check
python3 scripts/build_docs_catalog.py
git diff --check
```

发布器完成源清单、篇章覆盖、代码围栏、固定快照链接、XHTML/XML、EPUB 内部资源与锚点检查。另以 Python 标准库核对实际出版物和 SHA、HTML ID/引用、EPUB 图像 MIME/字节一致、旧版源身份，并重算教学公式。四个改动的 Python 文件仅用 ast.parse 检查语法，不执行测试函数。

结果见[机器可读编辑核对](evidence/book-3.1.0-editorial-check.json)：31 份正文；22 幅图（21 SVG + 1 PNG）；295 个唯一 HTML ID、380 个内部引用；27 个产物校验项；EPUB 与 HTML 资源字节一致；旧版 31 份正文、16 幅图的哈希匹配。

图形通过本地 Sharp 渲染（不使用浏览器或网络），逐张目视检查五幅新增 SVG 的字、框、箭头与关系。imagegen 原输出已目视核对；这里没有宣称跨阅读器实测或整本浏览器视觉验收。

算例复算：点云 16,000,000 bytes/s；语义 4096 bytes/s；比值 3906.25；KV 1,073,741,824 bytes=1 GiB；理想 1 Gbit/s 迁移下界 8.589934592 s；模型等待降 24%，含 60 s 运动的任务降 0.96%；压缩 15 回合打平、16 回合开始正收益。两维旋转后点为 (2,2)，误用零旋转偏差 √2。它们不是缓存/机器人实测数据。

出版物位置：`artifacts/book/3.1.0/`（HTML/EPUB/Markdown/assets/provenance/SHA256SUMS），完整离线分享包为 `artifacts/book/tangying-agentos-book-3.1.0-offline.zip`。生成物依仓库规则不提交；书稿、图源、提示词、生成器与核对记录提交。

## 合入方式与保留事项

遵照本次直接合入 main 的要求，教材提交携带 `[skip ci]`。本地已完成无冲突整合，内容树与工作分支完全一致；第一次原子推送被 GitHub 的 GH006 拒绝，原因是 main 要求 release-gate，两个远程引用均未改变。已确认当前仓库权限为 ADMIN，后续沿现有 PR #30 改为 main 目标，并使用管理员合并通道执行用户明确授权的跳过检查合入。保持分支保护和工作流定义，不伪造状态检查，不重写远程历史。合并提交同样携带 `[skip ci]`，最终 SHA 与结果以 PR/Git 历史为准。

合入范围包含当前开发分支此前的 10 次系统/教材提交和本次教材增量；本次出版核对不重新认证那些运行时升级。PR 说明将分别列出当前教材核对与已有实验记录。

原工作区两份 `artifacts/marketing/` 编辑与本次无关，保持原样且不纳入本次提交。未运行的运行时与硬件检查不能从出版校验推断通过；缓存接入仍是后续实现课题。
