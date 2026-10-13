# 当前系统评估与追加任务实验

日期：2026-10-13（北京时间）。源码基线 `76b516a18a78402d257f624b11f46f475caada8c`，软件 Runtime 自报 0.7.0；书籍版本 2.0.0。实验协议见[预登记及条件修正](2026-10-13-system-evaluation-protocol.md)。本轮结果与历史 2026-10-10 run-007 分开，不将旧记录算作新执行。

## 1. 系统评价

系统已具备开展有明确任务、风险边界和证据规则的机器人 Agent 仿真实验的基础，尚不能视为实机生产系统。本轮最重要的发现是：**Task自报成功不等于整体任务正确；自然语言子请求的语义重复仍可能穿过冻结审批。** 长程L01实际多做一轮抓放，被独立验收拒绝；修复后短程P01通过，完整长程未重跑。

核心价值是把“模型认为应该做什么”与“被批准且有证据的实际执行”分开：目标经目录和 schema 校验后冻结；任务、版本、机器人、步骤与命令贯穿观测、异常和恢复；物理完成由动作后的观测核验；UNKNOWN 不因摘要或模型建议变成成功。冻结与命令幂等保证执行与计划一致，却不能独自保证计划与用户语义一致；本轮缺陷正说明这两类问题需要分开评测。

| 维度 | 当前实现与证据 | 本轮结论的边界 |
|---|---|---|
| 自然语言到能力计划 | 注册目录、GOAL 只读规划循环、冻结调用/参数、审批、逐能力核验 | 已配置模型时所有目标走 GOAL；离线语法不是该配置下的规划来源；不同采样可能产生不同调用数量 |
| 物理闭环 | Robot Profile、Runtime、命令 journal、动作后新鲜观测、到位/抓取/放置判据 | 本轮仅 Gazebo home_furnished 同源 XLeRobot；不能推断实体精度、停止距离或通用抓放 |
| 多 Agent | 注册发布身份、`agent.event.v1`、topic 权限、task/revision/robot/step/command 及恢复因果链 | Task/Ops/Recovery 是实际责任角色；不等于三个独立 LLM，也不等于异构机器人自动分工 |
| 故障恢复 | 对目录内只读传输故障做调查、独立复验、持久预算内最多一次原读重试 | F01提供真实注入实例；未知物理动作、抓取滑落、机体损伤和一般代码自修复仍未由本轮证明 |
| 上下文 | 权威状态与模型投影分离；从完整账本生成热层、guard 与冷归档；SHA/作用域约束回读 | 本轮区分实际压缩与实际回读；单有归档引用不能证明模型使用了冷历史 |
| 云边与跨域 | Basis、资源 lease/fence、边缘 checkpoint、精确 completion receipt、pending/finalize 持久收尾 | 本轮现场链路是 Local Brain；云边 HTTP/SQLite故障契约测试不能冒充本轮 Fleet/Gazebo 实跑 |
| 异构扩展 | 机器人能力与观测契约、适配器边界；历史有独立双机交接 | 当前顺序意图/单 Task 单 Adapter；没有本轮异构机群匹配、并行任务图、自动换机成功数据 |
| 科研可用性 | 原始输入、模型上下文、事件、失败、观测与独立重验可保存 | 单场景、少样本、背景服务未完全隔离；适合机制和失败案例分析，尚不足以证明普适成功率 |

可进入受控实机接入准备，但实机放行仍须另做目标硬件、标定、实体急停/停止距离、断网未决动作、载荷、持续运行与多机交接实验。当前结论基于代码边界及本轮证据，不以通过 CI 替代机器人现场成功。

## 2. 环境与复现身份

独立实验根：`artifacts/experiments/system-20261013/`。Console `127.0.0.1:8907` → 三角色 Local Agent → 故障代理 `127.0.0.1:50262` → Gazebo Runtime `127.0.0.1:50261` → ROS 2 / Nav2 / RGB-D / Gazebo。导航只读侧车端口 18831。Robot ID `gazebo-home_furnished`。

Host 为 macOS arm64、10 个物理 CPU、16 GiB 内存，Python 3.11.9、Go 1.26.2；Docker 虚拟机资源及精确镜像/二进制摘要见 `provenance/runtime-build.json`。仿真使用软件渲染。本轮没有独占主机，Docker 启动时两个既有 Redis/MySQL 背景容器自动恢复，未操作它们；用时仅描述该运行，不作绝对吞吐或跨机器性能结论。

私有已有路由为 `https://api.deepseek.com` / `deepseek-flash`，由 Console 的不含秘密的路由状态确认。供应商未返回/未留存的权重摘要、tokenizer、解码种子、费用与 token 用量标为未测量。输入预算默认 32768 UTF-8 字节、完整请求 65536 JSON 字节、输出4096 tokens；字节数不当作输入 token 数。

新建世界 episode，标号13仅标识 episode，不能宣称物理确定性种子。复用停机部署中的同场景工作流地图：273文件、73,073,092字节，逐文件 SHA-256 留存；另复制匹配 `3007f73be75ead89/head-97634ababe40068d/rtabmap.db`，1,494,724,608字节，SHA-256 `2ddabee0a1aa6d53e9bf7dbb6039ebd4dd723a082deb4fdea3473ee0dcec6824`。没有复制旧任务库、导航命令库或机器人命令 journal。

最初工作流地图不完整，定位启动被拒绝，记录 `logs/startup-001-incomplete-map.log`；补齐数据库后才建立正式 episode。初始 Runtime ready=true、无 blocker，Nav2 localized，地图 `scan-2c72804716ad`，revision `ea419bbf785ac6c19834243c0d1d00463758c73654ad9dd6cedbca87caf31f5f`，标定 `68c581e7c5e70c1b1909e66ee3e68560b2d5c51eee276f684ed22fe9be80f58c`。完整原响应保留，不只摘录 ready 字段。

## 3. 任务与结果

完整原句与预算见协议、每运行的 `run-config.json` 和[机器索引](2026-10-13-system-evaluation.json)。elapsedSeconds 包含规划、审批后等待、执行、settle与证据收集，不能解释为纯运动耗时。

| 任务 | 结果 | 用时秒 | 能力/子步骤/到位 | 独立重验 |
|---|---|---:|---|---|
| R01：只读基线，误设 deterministic | 草案为llm；批准前取消，未执行 | 23.793 | 0已核验 | 记录实验条件错误，非任务成功 |
| N01：两地点基线，误设 deterministic | 草案为llm；批准前取消，未执行 | 13.694 | 0已核验 | 记录实验条件错误，非导航失败 |
| R02：按A1修正的只读基线 | SUCCEEDED | 27.235 | 4 / 0 / 0 | 通过，27文件、11,286,978字节 |
| N02：按A1修正的两地点导航 | SUCCEEDED | 73.471 | 4 / 8 / 2 | 通过，101文件、49,911,980字节 |
| F01：一次只读传输故障 | SUCCEEDED，原步骤自动恢复 | 17.796 | 1 / 0 / 0；原读重试1次 | 通过，30文件、5,663,592字节 |
| L01：完整长程与暂停 | Task SUCCEEDED，**执行验收与独立重验失败** | 1104.659 | 13项能力事件 / 子步骤整体验收不成立 / 10次到位；2组抓放 | 文件/账本/观测检查通过，路线基数检查拒绝；1158文件、1,411,485,996字节 |
| G01：阳台开窗浇水 | HTTP422，未创建执行任务 | 39.310 | 0新Runtime命令 | 原始拒绝及6轮模型输入留存，人工复核原因与目录缺失相符 |
| G02：去那个房间 | HTTP422，未创建执行任务 | 6.028 | 0新Runtime命令 | 原始拒绝及2轮模型输入留存，指代无唯一先行词 |
| P01：修复后独立抓放 | SUCCEEDED，恰好一对pick/place | 197.226 | 1 / 13 / 2 | 通过，239文件、93,384,480字节 |

这些不同任务、准备错误与边界请求不混成一个系统成功率。R02/N02 是明示条件修正后的新任务，不能追改 R01/N01 的原始报告。

### R02：标定、地图与语义状态

任务 `task-e8cf2539463e4acfa8f550a9`，模型计划 `calibration.get → mapping.status → mapping.inventory → semantic.locations`，4项均经生产 Provider 独立读取核验，未产生物理子步骤。5轮真实模型输入的精确字节和上下文对应通过重验；0份压缩上下文、0页归档回读。不能把只读核验通过解释为重新标定、建图或物理导航完成。

### N02：两个地点的顺序导航

任务 `task-ed3d1028897574b49cebc763`，走廊/客厅到位分别对应不同命令后观测。位置误差2.302mm、0.868mm；朝向误差0.028500rad、0.026329rad。这是该场景的仿真判据结果，不是实体机器人标定精度。6轮实际模型输入、8项物理/观测子步骤通过重验，0份压缩上下文、0页回读。

### F01：真实只读故障与自动续跑

任务 `task-ec9ff667348c244334d47003`，UTC `2026-10-13T00:15:55.714937Z` 在原请求 `task-ec9ff667348c244334d47003/rev-1-cap-01` 上注入一次 `navigation.status` / `UNAVAILABLE`，`forwarded=false`。代理进程生命周期故障预算为1，规划时未注入；实际注入JSONL与任务失败时间/身份核对。

原步骤失败后出现 Ops 异常、Recovery只读 `execution.read-history` 计划、恢复执行器的独立复验、原命令一次重读和最终核验；任务继续成功。10条版本化事件通过 schema与精确 task/revision/robot/step/command/source/anomaly/plan/execution 绑定，原读重试预算为1。2份实际上下文投影触发压缩，实际冷归档回读为0页；不得声称已验证现场冷历史检索。传输故障在转发前返回，因此本例不测“已经产生物理副作用后丢响应”的自动恢复。

### L01：终态达成与语义验收失败

任务 `task-8ac867c460fedcb80719219d`，8轮模型输入生成13项能力调用，457个账本事件、996次任务快照、1个不可变版本。新地图 `scan-b0591a4b9ab9`，revision `b8bd5a9cfb1ffdf7e5a076a80aeceb52ed8b624c90675ebe090fabb6ce0b92e4`；23.484691m、233帧、218次配准、2回环、71,980点。最终只读状态为同一地图版本、Nav2 localized、ready=true。这些结果不抵消整体验收失败。

冻结的厨房子句由模型表述为“从客厅出发，去厨房拿杯子，把杯子放进收纳盘，然后回到客厅”。旧 `homeTransfers` 把“把杯子”再次加入待搬运列表，产生两个 `home_manipulation` 子意图。实际有 `task01-pick/place` 与 `task02-pick/place` 两组派发；10次导航与4次抓放派发各有不同命令身份，**不是同一命令重复投递**。Schema有效、冻结审批和单命令幂等都未阻止这种语义层额外动作。执行验收及冻结原检验器的独立重验均在路线合并检查以 `arrival count differs from this frozen capability route` 拒绝，不据Task SUCCEEDED计成功。

操作员暂停的账本时间间隔为65.494709秒。暂停发生在下一子任务的观察完成边界，产生 `UNKNOWN_OUTCOME` 分类、5条Ops/Recovery事件及只读调查；由操作员resume继续，不计自动任务修复。这种控制流分类的噪声留作后续改进项，未在本轮放宽UNKNOWN规则。

补充 `L01-partial-audit.json` 在冻结原始manifest上单独检查模型输入、压缩、暂停与事件schema：10份managed投影、8份模型请求、2份压缩、0页归档回读；基本版本化事件检查与完整自动读恢复链分开。源preimage未证明的字段保留false。该补充不能使完整验收变为通过，也不把共享代理中早于L01的F01故障计入L01。

该语义缺陷由外部实验验收和开发过程定位、修复；机器人系统内Ops/Recovery没有识别重复搬运。本轮F01的自动只读续跑不能外推为生产系统自动修复解析代码或任意任务语义错误。

### G01/G02：正确拒绝的有限样本

G01失败规划 `planning-attempt-16f5df057efe5e99e42429491ab71105` 明确指出现用地图没有可导航阳台，工具目录没有开窗/浇水执行器。G02 `planning-attempt-99c04ca00dedb53739e079b755d7afc6` 指出多个可导航房间且“那个房间”无唯一先行词。两项均为HTTP422、未批准任务、Runtime command ID窗口无增加；完整原始错误正文、规划输入和前后journal保存。脚本的passed只评价创建拒绝和命令观察窗口，`correctSemanticRefusal=null`；上述原因由本文人工复核，不把任意422或模型服务故障自动当成正确拒绝，也不推断一般歧义准确率。

### P01：冻结失败之后的具体修复复验

同一episode、同一任务库/Runtime journal，重启重新构建的Local Agent；补丁及二进制hash见 `provenance/fix.patch` / `fix-build.json`。P01 `task-6e6f32d2d24a05fa39161bf3` 的初态为L01返程后的客厅、杯子已在盘中，因此不是独立重置初态的取物样本。

先保留旧代码失败回归，再修复“把/将 + 已待拿取物体 + 放入容器”的完整引用：同类别、属性及同数量列表只生成已有的一轮搬运；冲突/缺项要求澄清；真正明确的多个对象或后续第二次操作仍保留。新增验收预算 `--expected-home-transfers 1` 在批准前检查冻结子意图数量，执行后按不同步骤与命令核对实际pick/place，不能靠全局去重掩盖真正的多动作。

P01实际1项能力、13项子步骤、恰好一次pick和一次place；两次到位误差2.670mm/1.866mm，朝向0.027498rad/0.028716rad。4轮模型请求、0份压缩、0页回读；独立重验通过。此次仅证明具体解析修复与该短程仿真任务成立，**没有修复后的完整长程重跑结果**。次数门禁目前是实验验收参数，不能据此宣称生产GOAL已具备通用语义次数预算。

## 4. 证据组织与独立验证

每任务目录包括 `run-config.json`、`draft-plan.json`、`approved-plan.json`、`terminal-task.json`、`final-task.json`、`revisions.json`、`response-*.json/.bin`、`report.json` 和 `manifest.json`，实际字段以该运行是否到达相应阶段为准。失败创建另有 `planning-attempt.json`；拒绝实验脚本不批准意外返回的草案，保存错误正文和 Runtime command ID观察窗口。

`*-revalidation.json` 使用冻结原始 manifest 做离线重验，不发机器人请求、不修改原数据。它检查摘要、冻结计划、事件因果、动作后观察、物理判据、精确上下文和模型请求字节。`sourcePreimageVerified=false` 不隐藏：只保存身份hash而没有源preimage时，不声称已证明完整旧账本。

原始数据根在本地，Git提交的是报告、协议、机器索引和复现工具。未上传的图像、数据库、地图与大文件不能被远程 Git 链接替代。引用论文数据时应同时保存原始证据、冻结检验器及索引摘要。

机器索引同时提供[JSON](2026-10-13-system-evaluation.json)和[CSV](2026-10-13-system-evaluation.csv)。所有9项尝试均核对原manifest字节摘要；空值表示未测或不适用，不填造0。实验结束后只停止本轮拥有的Agent/代理/Gazebo进程；同库备份在写者关闭且无WAL/SHM后复制，SQLite integrity_check=ok、原件/副本SHA一致。初次SQLite backup产生的0字节失败文件和错误记录保留，不能把它当成有效数据库。

辅助观察另有一次CLI截断文本和完整只读protobuf/JSON状态采样；它们不推动机器人，也不用于整体成功判据。每次任务报告与原manifest没有覆盖。原验收器/修复后验收器分别冻结在 `provenance/validators` / `validators-fixed`，不能以新代码回写旧成功标签。

## 5. 软件回归与复现命令

修复后13个Go包race检查通过：547个顶层测试；含子测试共805个pass事件，不能当作805次机器人实验。421项选定Python回归全部通过、0跳过，含124项长程验收/重验测试，两者有包含关系，不相加。Go定向解析/规划三包也通过；修复前3项新回归失败日志保留。新增批准门禁测试首轮将草案fixture错误配置为终态，修正fixture后124项通过，初次日志同样保留。详细命令、开始/结束时刻、退出码和JUnit/Go JSON输出见机器索引的软件验证引用。

```sh
go test -race -p 1 -count=1 -json ./agent/intent ./agentruntime \
  ./core/agentcontract ./core/agentcontext ./core/contextcontract \
  ./internal/capabilityagent ./internal/recoveryexec ./fleet/coordinator \
  ./edge/worker ./edge/cloudclient ./fleet ./tasks ./orchestration

# 本轮短程缺陷复验；指定新输出目录，批准后机器人会运动。
.venv/bin/python scripts/evaluate_long_horizon.py \
  --base-url http://127.0.0.1:8907 \
  --request '从客厅出发，去厨房拿杯子，把杯子放进收纳盘，然后回到客厅' \
  --output artifacts/experiments/my-new-P01 --timeout 900 \
  --required-source llm --expected-arrivals 2 --require-manipulation \
  --expected-home-transfers 1 --collect-revisions

# 只读重验；使用冻结检验器和新输出文件，不启动模型或机器人。
.venv/bin/python artifacts/experiments/system-20261013/provenance/validators-fixed/revalidate_long_horizon.py \
  --evidence artifacts/experiments/system-20261013/P01 \
  --output artifacts/experiments/my-new-P01-audit.json \
  --expected-arrivals 2 --require-manipulation --require-model-context
```

Gazebo启动及地图复用参照[原复现指南](long-horizon-harness.md)，必须同时准备匹配RTAB-Map数据库、工作流地图和标定绑定。故障F01必须在单次任务执行前arm、保留真实日志并核对完整链；P01不使用旧F01日志。Python选定回归的精确argv在 `python-tests-result.json` 中保存。本轮没有运行全仓完整CI或实体机器人实验。

本地证据归档为 `artifacts/experiments/system-20261013-paper-evidence.tar.gz`，含两个运行二进制、任务原始包、冻结检验器、日志、数据库与相关地图制品；完整RTAB-Map导入库仍独立本地留存、不打入该归档。精确归档SHA-256、字节数及逐文件校验结果见JSON索引；归档没有上传远程。SQLite失败备份文件等诊断记录单列，不混入任务分母。

## 6. 解释、局限与下一步论文实验

本轮任务不是独立随机抽样，来自开发期间已使用的家庭场景和措辞。任务之间共享世界状态与地图，同一自然语言不同模型采样也会产生不同计划，不满足跨候选严格配对条件。不能对这些异质单例做显著性检验或宣称某个高成功率。

任务协议是在本地执行前制定、随后明确增补A1/A2，保留会话与时间记录；没有第三方预注册、盲评或不可更改的外部登记。本文记录工程实验过程，不据“预登记”字样声称已满足完整学术预注册标准。

冻结检验器独立于模型与任务自报，但部分物理判据实现与Runtime共用工程代码，尚无第二实现或人工盲标的全面交叉验证。L01原始包约1.41GB，包含每秒全量任务快照；输入压缩有效不意味着数据库、网络和实验归档成本已被控制。本轮未测绝对模型成本、长期故障率、真实MySQL/Redis切主或大规模Fleet能力。脚本timeout主要约束批准后终态等待，规划和证据采集另有单请求期限，elapsed包含全过程，不能当成纯执行deadline。

论文中的机制论断可以引用“在已登记作用域内，一次转发前只读故障经过多角色因果链后完成有界重试”。暂不能引用“任意意外可自行修复”“异构机群自动完成长程目标”“仿真等价于实机”或“压缩后模型已使用冷历史”。

后续应事先固定场景/对象/语言分布、独立留出场景、重复次数和失败切片，再随机化或配对比较：无恢复与受限恢复；完整上下文与受控压缩；正确与陈旧/冲突跨域事实；断网/丢完成回执/多进程重启；物理抓取失败与UNKNOWN；不同Robot Profile的真实交接。分开报告目标完成、应拒绝、错误拒绝、UNKNOWN、人工介入、成本和证据覆盖，不以一律拒绝获得表面低风险。
