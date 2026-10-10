# 335 条真实事件后的恢复上下文预算修复

## 原始失败与证据范围

2026-10-10 Gazebo run-002 的任务为 `task-79f7e1765770f7b7de3e61db`。`artifacts/acceptance/long-horizon-20261010/run-002/final-task.json` 保存 335 条事件，SHA256 为 `b942bb7ca509d45402163e1b935eff80b795359586e6428a1ba7248e591eb3c7`；同目录 `revisions.json` 的 SHA256 为 `b6e01cf922e18caf92ade7535a2186d9175f5be590d6f300497ec6e643007640`。

任务在抓取返回 `GRASP_TARGET_UNREACHABLE` 后进入 `RECOVERABLE_FAILURE`。seq 326、333 的 `execution.read-history` 恢复回执记录 `required state 144991 > 32768 bytes`；seq 335 的 `observe.re-read` 为 `144929 > 32768`。三次诊断均未成功执行和复验。此前建图及只读故障恢复成立，但本任务没有完成抓放及剩余目标；本修复不能把原失败任务改判成功，也不重放未知物理动作。

## 根因

生产 `recoveryObserver` 已将任务、冻结计划、版本和事件历史交给 `tasks.ContextForRevisions`，当前 Observation 仅有任务状态和目标简述。失控的不是当前 Observation，而是阶段记忆的分类：每条完整动作生命周期记录都被标为不可归档的 `guard`。

run-002 的旧动作双发布路径存在参数和零 fence 编码差异，同 eventId 的内容冲突必须保守保留。335 条事件生成了 151 条动作状态来源；原 `ContextFor` 有 154 个 guard，序列化总计 134,879 字节。加上不可变版本来源、当前观察及工具后，必要状态超出 32 KiB。该统计与修复前重现保存在 `recovery-context-diagnosis.log`。镜像编码根因另见 `2026-10-10-agent-interaction-protocol.md`，不能通过忽略参数冲突来缩小上下文。

## 精确分层

`tasks/context_memory.go` 保持原生命周期归约规则和完整 `memory:action:N` 原文，包含 revision、robot、step、command、工具、状态、结果未知标记、冲突 eventId、所有 source_event_ids 和 evidence_ids。它们现在是可归档的 `verification` 来源记录，managed projection 按 SHA 保存完整 JSON，可用 `context_read` 的 item_id 精确回查。

热层 `memory:command:N` guard 按完整 `(revision, robot, step, command, tool)` 绑定聚合。它保留未决状态、unknown、identity conflict、禁止自动重放及全部 `source_record_ids`。任一冲突变体仍使该命令保持冲突；后续普通 `CONFIRMED` 不能覆盖它。缺失 revision、robot、step、command 或 tool 的记录不会与另一条记录合并。不同机器人、版本和命令也不会相互完成。

已完成动作仍有完整来源，热层保留历史计数、当前阶段、当前计划步骤状态和原约束；历史完成不被提升为当前物理事实。恢复工具的任务范围、权限、审批、未知物理结果门禁和查询范围均未改变。若精确未决命令边界本身仍超过预算，继续拒绝模型调用，不提高上限或截断内容。

## 第二轮读取回执也必须可归档

仅用小型成功 stub 会漏掉第二个问题。换成生产 `stepRunDetail` 的 35 条记录形态（stepId、status、capability、safetyLevel）后，真实 335 事件上下文首轮可运行，但第二轮仍因 37,581 字节失败。原 managed projection 无条件保留最后一条小于总预算三分之一的工具回执，即使它已无法放进剩余空间。

`core/agentcontext/managed.go` 现在先尝试归档较旧结果；仍超限时，也将最新业务回执的完整 Detail 归档。工具轮次 ID、参数和 verdict 保持原值。`context_read` 当前页继续保留，避免刚回读就再次归档的循环。没有摘要改写或部分截取；原回执可按 SHA 和 `round:N` 逐字节回查。

## 验证方式及边界

新增回归覆盖同命令多冲突变体后再出现完成回执、跨版本/机器人/命令隔离、缺失绑定不合并，以及 1,000 条事件中 500 个已完成动作的全部来源无损回查。自包含 335 事件 fixture 重现旧 required-state 溢出；独立测试验证小型最新回执的完整归档及 `context_read` 页保护。

`internal/recoveryexec/ledger_context_budget_test.go` 还可只读载入上述未修改的真实 Task 和 Revision 文件，重建同一生产上下文，再运行恢复 executor、确定性单工具 decider 和 `VerifyReadOnly`。执行历史读取使用 35 条生产字段形态，遥测读取使用生产成功回执形态；两个回调都是离线测试替身。每个诊断必须恰好读取两次（执行读取、独立复验），不能发物理命令，不能更改源 Document，也不能把诊断成功解释为原物理任务成功。

因此这项离线重放证明真实历史可承载恢复决策、回执和独立验证流程；它不声称修复后的二进制已经实际调用 Gazebo/真实 Runtime 的读取服务。部署后的真实补充任务及服务复验由本轮主报告单独记录。

```sh
TANGYING_RUN002_CONTEXT_FIXTURE="$PWD/artifacts/acceptance/long-horizon-20261010/run-002/final-task.json" \
  go test -race ./tasks ./core/agentcontext ./internal/recoveryexec ./internal/actionloop ./cmd/local-agent -count=1 -v
```

原始失败日志、首次替身过小的通过日志、使用真实回执形态复现的失败日志均保留；最终结果以 `artifacts/acceptance/long-horizon-20261010/recovery-context-final-race.log` 为准。完整失败任务及此前 seq 326/333/335 不改写。

最终五包 race 全部通过，包含显式启用的真实文件重放。真实源的执行历史诊断两轮输入分别为 31,634 / 32,530 字节，遥测诊断为 31,572 / 32,334 字节，均低于原 32,768 字节上限。两项诊断各执行一次读取和一次独立复验；原任务 UNKNOWN/冲突边界没有关闭。必要状态继续增长到无法容纳时仍会拒绝，这次通过不代表无限上下文容量。
