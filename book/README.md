# 《深入理解分布式机器人 Agent 系统》

**基于Tangying Robot AgentOS的原理、实现与云边部署 · 数字版1.2.0 · 2026-10-11**

本版同步软件v0.7.0及main复核快照11db0adb，讲清从自然语言、批准计划、Agent通讯、世界证据到机器人执行与云边收尾的完整系统。面向会使用命令行、能阅读Go/Python的机器人、后端与Agent工程师。

新增精确事件/恢复因果链、长历史热guard与冷归档、模型实际请求留存、执行Basis、边缘checkpoint与v2完成回执；19章和6份附录涵盖异构接入、多机物理交接、长程任务复盘及可检查实验。出版可用不等于实体安全或机群规模放行。

## 先读哪里

- [前言](front/00-preface.md)：一条任务贯穿系统，按读者选择路线。
- [合订本](book.md)：自动生成的完整Markdown与目录。
- [第17章：云边职责、通讯与协作](chapters/ch17-cloud-edge-agent-harness.md)。
- [第18章：长程上下文与一致性](chapters/ch18-long-horizon-context.md)。
- [第19章：真实19分钟任务复盘](chapters/ch19-long-horizon-walkthrough.md)。
- [附录F：分级学习实验](appendix/F-learning-labs.md)：环境、命令、检查产物与答案。

| 学习部分 | 章节 |
| --- | --- |
| 物理约束与总图 | [1](chapters/ch01-physical-constraints.md)、[2](chapters/ch02-architecture-evolution.md) |
| 完成与世界事实 | [3](chapters/ch03-closed-loop-contract.md)、[4](chapters/ch04-world-model.md) |
| 工具、Agent与规划 | [5](chapters/ch05-tool-layer.md)、[6](chapters/ch06-multi-agent-runtime.md)、[7](chapters/ch07-orchestration.md) |
| 故障、持久化与单机 | [8](chapters/ch08-distributed-fault-assumptions.md)、[9](chapters/ch09-local-single-machine.md) |
| 实机接入与诊断 | [10](chapters/ch10-sim2real.md)、[11](chapters/ch11-observability-recovery.md) |
| 评测、运维与建设 | [12](chapters/ch12-evaluation-training.md)、[13](chapters/ch13-install-deploy-ops.md)、[14](chapters/ch14-build-from-zero.md) |
| 边界与工程对照 | [15](chapters/ch15-future-directions.md)、[16](chapters/ch16-coding-agent-contrast.md) |
| 当前完整机制与案例 | [17](chapters/ch17-cloud-edge-agent-harness.md)、[18](chapters/ch18-long-horizon-context.md)、[19](chapters/ch19-long-horizon-walkthrough.md) |
| 导航与检查资料 | [源码A](appendix/A-source-map.md)、[检查B](appendix/B-checklists.md)、[实验C](appendix/C-experiment-index.md)、[术语D](appendix/D-glossary-and-assumptions.md)、[参考E](appendix/E-references.md)、[实验F](appendix/F-learning-labs.md) |

第一小时读前言→2→3→17→19；后端/Agent读5–9、11、18并做实验1–4；机器人集成读1、3–5、10、13并做实验5–6；全系统按第14章建设顺序完成实验1–8。每章当前学习段含自测与答案，历史长片段用于进一步追因，不当作当前完整接口。

## 版本、历史与本版修订

[出版说明](front/01-publication-notes.md)区分代码、书稿、运行来源和证据。[RELEASE](RELEASE.md)记录本版修改与验证，[逐章审校记录](../docs/development/2026-10-11-book-current-system-review.md)保存变更依据与边界。

正式软件v0.7.0标签固定到30df0602，书稿复核main固定11db0adb；本版编辑提交另由Git记录。实验按原日期和制品引用；不把单机长程、双机交接及异构协议测试拼成异构实机机群成功。

[旧版1.1.3恢复说明](editions/1.1.3/README.md)保留完整正文/合订本哈希。1.0.0–1.1.2恢复目录和research写作历史继续保留，research不进入出版正文。大图、数据库、模型key和现场私密数据不进入书或发布包。

## 构建与阅读

```bash
# 无出版依赖也能核对源/链接/合订本
make book-check
# 更新分章后重新生成，不能手改book.md
python3 scripts/build_book.py
# 独立出版环境，不安装机器人运行依赖
make book-setup
make book-release
```

发布文件位于`artifacts/book/1.2.0/`，不进入Git，CI提供同版次完整包：

| 文件 | 用途 |
| --- | --- |
| index.html | 离线浏览、包内章节导航、窄屏表格横滚与打印 |
| book.epub | EPUB3电子书、导航与许可 |
| book.md | 合订Markdown |
| LICENSE | 分发保留项目许可 |
| provenance.json | 代码快照、正文/工具/依赖/渲染器哈希 |
| SHA256SUMS | 发布文件完整性校验 |

HTML/EPUB无需外部字体、脚本或CDN。源码链接固定到GitHub快照，网络访问不影响包内阅读。导出格式展开练习答案，保留原文中的图表、示意和历史案例。构建相同正文/依赖不加入当前时间或机器路径，可比较哈希。

维护时先改分章，再生成合订本，运行出版回归和EPUB校验；保留旧版与原失败，不手工修改生成结果。MIT License · [项目仓库](https://github.com/SUSTechWLA/tangying-robot-agent-os)。
