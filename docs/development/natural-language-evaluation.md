# 躺营自然语言 Agent 评测与改进

2026-09-05，在当前主工作区编译的 Fleet、Edge 和真实 RoboCasa Runtime 进程上完成 13 项检查，全部符合预期。5 条正向指令完成仿真动作，6 条不支持或含糊的指令在创建时被拒绝，2 条场景条件不成立的指令在执行前失败且物体未移动。

这里的“13/13”是**理解、执行或拒绝符合预期**，不是 13 次成功搬运，也不是开放域语言理解准确率。使用确定性语义动作与仿真世界，没有调用线上大模型、训练新权重或连接实机；不构成真实抓取成功率、生产放行或实机性能证明。

## 实测结果

| 用例 | 自然语言输入 | 修复前 | 修复后 |
| --- | --- | --- | --- |
| canonical | 让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区 | 完成 | 完成，1.05 秒 |
| polite | 请帮我让一号机器人把红色方块放到交接区。 | 422，未理解 | 完成，0.53 秒 |
| colloquial | 麻烦 1 号机器人将红色积木移到交接点。 | 422，未理解 | 完成，0.53 秒 |
| pronoun | 先让一号机器人把红色方块放到交接区，然后让二号机器人把它放到右侧目标区。 | 422，未理解 | 双机交接完成，1.04 秒 |
| english | Robot 1, please move the red block to the handoff zone. | 422，未理解 | 完成，0.53 秒 |
| negation_zh | 把红色方块放到交接区是不允许的 | 错误生成肯定动作 | 422，要求澄清 |
| negation_en | Do not put the red block into the right bin | 错误生成肯定动作 | 422，要求澄清 |
| conditional | If the person leaves, put the red block into the right bin | 条件被忽略 | 422，不创建无条件动作 |
| multiple_objects | 把红色和蓝色方块放到交接区 | 随意选择颜色 | 422，要求每一步明确一个物体 |
| unknown_object | 让1号机器人把蓝色方块放到交接区 | 场景不存在蓝方块，执行前失败 | 仍在执行前失败，红方块未移动 |
| unknown_destination | 让1号机器人把红色方块放进冰箱 | 冰箱被误认成收纳箱 | 422，说明暂不支持家电操作 |
| unsupported | 帮我做晚饭 | 422 | 422 |
| source_mismatch | 让1号机器人把红色方块从右侧目标区放到交接区 | 本轮新增；旧绑定器未检查起点，回归测试已复现 | 执行前失败，红方块保持在左侧起点 |

对相同的前 12 项用例，修复前为 3/12，修复后为 12/12。基线中所有错误解析均在批准之前取消，没有执行否定或歧义任务。耗时是批准到读取终态的本次观测，排除编译、场景重启和解析时间；样本量不足以报告延迟分位数。

每条可执行用例先重启本评测拥有的 Runtime 与两个 Edge，等待新观测和新的世界版本，确认红方块回到左侧起点。成功同时检查任务 `SUCCEEDED`、目标区域关系、观测 `FRESH`、世界版本推进、意图数量，以及每个意图的 Harness `SATISFIED` 和非空证据 ID。

## 已修复的问题

- **完整解析、准确绑定。** `agent/intent/parser.go` 对动作、物体、起点和终点分别解析；中文机器人编号、礼貌用语、常用别称、中英文与同句后续代词均有回归测试。物体颜色不再被终点颜色覆盖。
- **不丢弃约束。** 已识别的否定、条件、停止表达，以及只理解了部分步骤的请求，返回可澄清的错误。`agent/agent.go` 优先保留完整确定性结果，阻止可选模型把明确的双机任务改写成其他动作。
- **检查起点。** `edge/robotclient/client.go` 要求起点唯一且物体观测关系相符；位置错误、未知、仍被持有、起点缺失或多义都失败。取到首次观测后取消订阅，避免遗留观测流。
- **任务更新也保留约束。** `tasks/service.go` 使用完整、有限的终点修改语法。“不要放到右侧目标区”“最后放到右侧目标区，然后关闭电源”等不会再被截取成肯定更新，也不会写入新版本。
- **执行反馈持续刷新。** HTTP Experience 没有事件游标时，同一版本仍需刷新步骤；前端现在接收完整快照，并丢弃先前并发请求的迟到响应。带游标的数据仍检查顺序。任务完成后，步骤与主状态统一显示完成，不再因版本仍为 `ACTIVE` 而显示正在执行。
- **预览实时连接。** 预览转发 WebSocket 保留浏览器访问的 Host，使后端可以正确验证同源 Origin；仍拒绝外部 Origin。修复原来 API 能读到新观测、场景推送却被拒绝的问题。

可选模型用受控 HTTP 响应测试验证“不重写已知指令”和“不绕过否定/条件”。没有评测任何线上模型的质量或延迟。运行中合法修改“最后放到右侧蓝色垫子上”的独立 RoboCasa 测试通过，保留发送方证据，等待安全点后更新接收方任务。

## 浏览器补测与未解决的边界

通过当前工作台额外执行了同句代词双机任务（`task-90a723fb2e3efa34255fdd16`），结果成功。反馈修复后，在页面确认两步均显示“已完成 / 环境已经确认这一步完成”，三维场景保持实时同步。

随后补测“请让二号机器人把红色方块从右侧目标区放到交接区。”（`task-00029f5a7f8c2aad7850b4ab`）失败，Runtime 返回 `FENCING_TOKEN_STALE`，未抓取物体。源码确认此场景每回合的放置方向限定为 `robot-1 → handoff-zone`、`robot-2 → right-target-zone`；完成后 owner 为 environment，缺少新一轮通用授权流程。**这条额外探索用例没有算入上面的 13/13，也尚未被实现为可执行能力。**证据另存 `final/browser-probes.json`。

这意味着当前评测通过的是单回合定向交接，不支持在同一完成回合上任意往返搬运。后续应将场景方向与资源授权约束公开到能力预检，明确提示用户为何不能开始，并实现经过验证的回合重置或重新授权；不能通过放宽 fencing 校验获得表面成功。当前需要重新启动评测场景才能从初始状态再跑一次完整交接。

## 复现

先按[开发快速上手](getting-started.md)安装主 `.venv`、Go 和 RoboCasa 隔离环境。脚本使用随机本机端口，不接管现有 Docker 或实机服务；每次使用一个不存在的输出目录。

```bash
ROBOCASA_PYTHON=/absolute/path/to/tangying-robocasa/bin/python \
  .venv/bin/python scripts/evaluate_natural_language.py \
  --output artifacts/natural-language-eval/my-run

# 只复测某些用例
ROBOCASA_PYTHON=/absolute/path/to/tangying-robocasa/bin/python \
  .venv/bin/python scripts/evaluate_natural_language.py \
  --output artifacts/natural-language-eval/my-focused-run \
  --case pronoun --case source_mismatch

make test-go
ROBOCASA_PYTHON=/absolute/path/to/tangying-robocasa/bin/python \
  .venv/bin/python -m pytest -q tests/e2e/test_robocasa_task_updates.py
```

脚本显式选择 `AGENT_PROVIDER=deterministic`，不继承远程模型密钥。无 `--keep-running` 时退出即清理自有进程；添加该选项会在所有用例通过后重置到初始场景并保留本机仿真，直到 Ctrl-C。`ready.json` 记录当前 Fleet 地址和自有进程 PID；这是临时开发服务，使用测试夹具账号 `admin / admin123`，不能用作生产部署配置。

若要让当前前端使用这套仿真，先停止自己启动的旧预览进程，再将 `ready.json` 的 `baseURL` 填入：

```bash
CONSOLE_BACKEND_URL=http://127.0.0.1:YOUR_FLEET_PORT node scripts/preview-console.cjs
```

打开 `http://127.0.0.1:18130/#workspace`，确认“仿真环境”“场景已同步”，再测试任务。预览的 API 和模型资源始终来自同一个后端。已有其他检出的 Docker 服务不代表当前源码已生效；必须核对实际进程来源，不能用旧任务记录冒充本轮测试。

## 证据与验证范围

本机报告位于 `artifacts/natural-language-eval/baseline-verified/results.json` 和 `artifacts/natural-language-eval/final/results.json`。后者含本轮全部输入、解析结果、任务、世界快照、意图、Experience，以及 Fleet/Edge 二进制 SHA-256，可用 task/step/observation ID 回溯。报告不含操作员 token；整个制品目录因包含测试 mTLS 私钥已被 Git 忽略，不应打包发布。

本轮代表任务：

- 标准双机：`task-efc7d99c0b4f393edf903ba3`
- 同句代词：`task-3233b1c9e9cf5537c92ac5c2`
- 起点不符：`task-631ee98ada8a12aea5a0e366`

验证包括 `make test-go` 全部项目 Go 包、3 项任务更新测试（其中一项启动真实 RoboCasa 进程）、13 项任务评测、137 项 Web 测试、5 项文档检查，以及新增脚本 Ruff 检查。新增反馈修复后单独复测 `go test ./web/...`。单元测试先复现旧行为失败，再验证修复。上述结果是本次未提交工作区快照，发布候选仍需独立采集签名证据和现场验收。

## 后续能力优先级

1. **场景感知的澄清与预检。** 当前不存在的物体到实体绑定阶段才失败，单向场景与回合授权约束也未在批准前提示。下一步可在批准前显示候选物体、可达区域与当前可执行方向，区分“听不懂”“找不到”和“现在做不了”，由用户明确选择。
2. **明确的跨轮上下文。** 当前只支持同句后续“它”，以及版本化任务内的终点修改。跨任务“把刚才那个拿回来”需要记录对象身份、有效期和用户确认，不能直接沿用上次颜色。
3. **能力目录驱动的任务拆解。** “两台机器人协作收拾桌面”、条件等待、多个物体批量处理，需要显式依赖、分支条件、候选计划和验证规则；继续增加字符串别称不能替代这些设计。
4. **实机能力与评测。** 接入真实感知、策略模型、抓取验证和标定，再测多次重复、不同摆放与异常恢复。当前厨房搬运的确定性仿真闭环不能证明真实 XLeRobot 已具备通用家务能力。
