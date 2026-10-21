# 2026-10-18 异构边缘验收证据包

结论见[实验总报告](../../2026-10-18-edge-swarm-readiness.md)。本目录保存正式结果、原始失败和冻结源码，软件、DDS 记录式控制器、回环 HTTP 与未知地图几何实验分别计数，不包含实机运动认证。

`raw-evidence.tar.gz` 的每个文件及摘要见 `archive-members.json`；外层所有公开文件由 `master-manifest.json` 校验。使用：

```bash
.venv/bin/python docs/experiments/evidence/2026-10-18-edge-swarm-readiness/verify.py
```

解包到一个新建的临时目录后，主要内容为：

| 目录 | 内容与判据 |
| --- | --- |
| `ros-final/` | 正式 attempt03：9 条固定自然语言路线、99 导航/独立到达、18 故障合同；完整可编译源在 `source-snapshot.tar.gz` |
| `ros-development/` | 相同场景开发 attempt02 原始任务记录；不计为额外独立样本，省略重复源归档 |
| `ros-preparation/`、`ros-index/` | 准备失败、编译来源、全部尝试索引、容器入口恢复与观测关联审计 |
| `software-04/` | 最终长任务/重启/预算 23 条软件场景记录、1003 条总记录 |
| `software-01/02/03/` | 为负载隔离人工中止的尝试，不计完成 |
| `trio-002/` | 最终三形态未知环境 fixture，3 个地图 × 正常/故障，共 6 个 partial 任务 |
| `trio-001/` 与 validation | 同输入的早期结果与校验器加强记录；不是另一组独立样本 |
| `peer-002/003/004/` | 同一场景三次四进程通信验收、原始收件与建议消费、对应源摘要与源码 |
| `peer-001/005/` | 注入时序失败、归档预检失败；005 未启动通信任务 |
| `peer-build-source/`、`peer-validation/` | 构建依赖/源归档与 Go/Python 检查 |
| `validation/`、`ros-harness-unit/` | 缺陷复现、修复回归、一次长 race 超时和后续独立验证 |

对 `trio-002` 可继续运行当前脚本的严格指标重算：

```bash
.venv/bin/python scripts/run_heterogeneous_exploration_fixture.py --verify /absolute/extracted/trio-002
```

ROS 目录有 **18 个临时 console-session 凭据文件被移除**（正式/开发各 9 个）；原始 manifest 完全保留，省略文件的摘要和原因见 `redactions.json`。因此公开解包目录不能直接通过要求所有原文件齐全的 ROS `--verify`，应使用本目录公开校验器和来源清单。原本完整的本地正式 ROS 目录已执行原校验器，通过 622 文件校验。原始 Linux 二进制未重复发布，保留其 SHA、工具链和实际完整构建源码。任务、观测、命令、数据库与原始结果没有被改写。

外层哈希验证回答“公开证据是否保持归档字节”；三机型 replay 回答“记录的观测能否重算指标”；二者都不重新驱动机器人，也不证明实验之外的物理性能。读论文数据时，以每个实验冻结的来源和证据层级为准，不能把整个工作树后续脚本增量倒推成旧二进制来源。

`post-review.tar.gz` 单独保存最终 Ops 独立复核的失败与修复记录、三类厂商绑定的 36 项离线测试和 CLI 输出、最终构建/lint/文档结果，以及本轮代码与配置增量的源码字节。成员清单为 `post-review-members.json`，同一 `verify.py` 同时验证两个归档。它不替换原始归档，后续构建也没有重跑旧 DDS 任务。`final-validation.json` 给出实际检查、源码和最终本机/Linux ARM64 二进制摘要；二进制本体仍留本地。
