# 通信与上下文审计证据

源码基线与实际结果见 [summary.json](summary.json)，逐文件摘要见 [manifest.json](manifest.json)。

- `go.jsonl.gz`：普通协议/执行/存储/协调回归。
- `harness.jsonl.gz`：模型、工具、恢复和任务执行回归。
- `race.jsonl.gz`：首次竞态检查，Host 长程 campaign 超时失败，保留原堆栈。
- `host-race.jsonl.gz` 与 `host-race-selection.json`：排除该 campaign 的明确测试选择和定向复核；不替代超时场景。
- `context-replay.jsonl.gz`：原 335 事件文件的只读上下文回放，包含原文摘要和投影字节指标。
- `python.log`：ROS 2 Profile/mobility/peer 适配测试。
- `process-*`：本轮四进程回环通信、Python 消费、故障注入与构建来源。
- `interop.log`：跨语言固定线样例的准确内容 ID。

JSONL 压缩只为减少仓库存储，读取例如 `gzip -dc race.jsonl.gz`。这些是测试结果，包含子测试与正常/环境缺失 skip，不是独立物理机器人样本。当前没有硬件、无线网络或新增 Gazebo 验收。私有配置、token 和数据库未复制到此目录。
