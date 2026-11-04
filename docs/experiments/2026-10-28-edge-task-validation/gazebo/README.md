# Gazebo 三机器人任务与停止证据

实测解释、失败轮次、判据和源码边界见[总实验报告](../README.md)。本目录从本地 `artifacts/acceptance/edge-task-validation-20261028-gazebo` 复制公开材料；未修改原始实验记录。

- `summaries/evidence/run-002/analysis/metrics.json`：3/3 路线、30 导航 + 30 确认的物理帧核验；严格安全覆盖仍 false。
- `summaries/evidence/run-002/stops-001/summary.json`：保留被锁存停止拒绝的旧失败，不计作六次实际制动。
- `summaries/evidence/run-003-stops/stops-001/summary.json`：独立新世界的 6/6 停止，UAV 为 hover。
- `public/evidence.tar.gz`：183 个成员，包含原始任务、SQLite、物理/传感器/控制账本及失败记录。
- `public/redactions.json`：整文件排除 6 份 console-session / model.env；对 3 个已知凭据做逐字节和成员名检查，不代表任意未知秘密检测。
- `frozen-source-manifest.json`、`patch-manifest.json`、`runner-source/`：基线源码身份、停止补丁与实际编排源码。runner 中的绝对容器路径是原始实验路径，复现时按独立目录调整。
- `environment.json` 与 `restoration.json`：仅复用一个原项目容器，入口原字节/权限恢复，实验结束后容器停止。

在根目录执行：

```sh
python3 docs/experiments/2026-10-28-edge-task-validation/gazebo/public/verify.py
```

归档大小为 6,855,340 字节，SHA256 为 `a1c5348001d70e0319e0dc2d574d1a404f720300953061fae813077a1ef764d3`；manifest SHA256 为 `2d47f8218420c9740e99920874910f12d0d367c634adcb99ac2f3dfb4ecd2077`。校验器不解压运行任何文件，检查成员名、字节与排除清单；哈希不能认证实验真实性。
