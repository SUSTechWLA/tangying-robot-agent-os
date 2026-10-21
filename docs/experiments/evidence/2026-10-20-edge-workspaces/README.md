# 边缘工作空间软件验收证据

日期：2026-10-20。对应[变更与验证记录](../../../development/2026-10-20-edge-robot-workspaces.md)。本目录记录工作台、代理、数据隔离与任务回溯的软件验收；**没有执行 Gazebo 或实机运动，不是机器人任务成功率实验**。

## 文件

| 文件 | 内容 |
| --- | --- |
| `summary.json` | 基线提交、分支、工具版本、实际命令、结果数量与验证边界 |
| `go.jsonl.gz` | Go 功能测试原始 JSONL：4 包、383 个测试/子测试通过 |
| `go-race.jsonl.gz` | Console / Local Agent race 原始 JSONL：2 包、368 个测试/子测试通过 |
| `web.log.gz` | 最终 `npm test` 原始输出：529 通过 |
| `python.log.gz` | 会话工具、文档与仓库检查原始输出：48 通过 |
| `source-manifest.json` | 本轮受影响代码、测试与脚本的 SHA-256；基线文件由基线提交确定 |
| `SHA256SUMS` | 本目录证据及浏览器留存文件的校验值，路径相对仓库根目录 |

浏览器[检查结果](../../../frontend/assets/edge-workspaces-20261020/browser-checks.json)记录 Runtime 离线读历史、返回恢复上次任务、缺少执行证据提示、Agent 离线无回退，以及两个页面的三个宽度检查。截图同目录保存。样例服务器来源为 `console/testdata/workspace-preview/main.go`，executor 明确拒绝执行；测试结束已停止临时服务。

## 解释与复现

按 `summary.json` 中命令在同一源代码版本运行。Go `Action=pass` 中带 `Test` 的记录包括父测试和子测试；race 重复执行了功能用例，不能把两个数相加作独立样本量。真实 SQLite 集成验证同任务编号在不同 Agent 的内容/事件隔离、重启后历史、会话轮换和 WebSocket 释放；UI 使用内存样例，不产生实机遥测。

首次验证暴露的文档入口、提取式前端测试依赖及成功任务空历史断言问题均已修正；这里保留最终通过日志。缺少执行历史时页面现在告警，不会为了测试通过而伪造工具步骤。完整修复说明见变更记录。

从仓库根目录校验：

```bash
shasum -a 256 -c docs/experiments/evidence/2026-10-20-edge-workspaces/SHA256SUMS
```

本轮未修改持久化任务 schema，也未对现有机器人服务热切换 Runtime；部署方式与可扩展边界见[操作指南](../../../operations/edge-robot-workspaces.md)。
