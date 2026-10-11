# 附录 E · 理论文献、规范与项目依据

## E.1 外部一手资料

访问核验日期：2026-10-11。以下为原论文、规范或维护方文档；用途只限所述概念。文献不为项目背书，格式符合也不证明物理正确。动态项目文档注明所用分支，采用时可另固定提交。

| 编号 | 文献/出处 | 对应主题与限制 |
| --- | --- | --- |
| E1 | Leslie Lamport. *Time, Clocks, and the Ordering of Events in a Distributed System*. 1978. [作者论文](https://lamport.azurewebsites.net/pubs/time-clocks.pdf) | 第 8 章：因果偏序与墙钟区别；不声称本项目实现完整逻辑时钟算法 |
| E2 | Diego Ongaro, John Ousterhout. *In Search of an Understandable Consensus Algorithm*. 2014. [Raft 论文](https://raft.github.io/raft.pdf) | 第 8 章：复制日志共识；Redis 单写租约不等同于它 |
| E3 | R. Fielding, M. Nottingham, J. Reschke. *RFC 9110: HTTP Semantics*. 2022. [§9.2.2](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.2) | 第 8 章：幂等语义；不自动覆盖物理副作用 |
| E4 | SQLite maintainers. *Isolation In SQLite*. [官方文档](https://www.sqlite.org/isolation.html) | 第 9 章：读写隔离与快照；数据库外设备效果不在事务内 |
| E5 | ROS 2 documentation maintainers. *About tf2*. Jazzy 分支. [官方文档源码](https://raw.githubusercontent.com/ros2/ros2_documentation/jazzy/source/Concepts/Intermediate/About-Tf2.rst) | 第 4 章：时间与坐标变换；不证明标定质量 |
| E6 | ROS 2 documentation maintainers. *About Quality of Service settings*. Jazzy 分支. [官方文档源码](https://raw.githubusercontent.com/ros2/ros2_documentation/jazzy/source/Concepts/Intermediate/About-Quality-of-Service-Settings.rst) | 第 10 章：通信策略与兼容；可靠传输不是新鲜采集证明 |
| E7 | G. Klyne, C. Newman. *RFC 3339: Date and Time on the Internet: Timestamps*. 2002. [规范](https://www.rfc-editor.org/rfc/rfc3339.html) | 附录 D：时间表示；不保证跨机校时精度 |
| E8 | ISO. *ISO 10218-1:2025 — Robotics — Safety requirements — Part 1: Industrial robots*. [官方范围页](https://www.iso.org/standard/73933.html) | 第 10 章：工业机器人适用范围；仅核验公开范围，未引用付费全文条款 |
| E9 | ISO. *ISO 10218-2:2025 — Robotics — Safety requirements — Part 2: Industrial robot applications and robot cells*. [官方范围页](https://www.iso.org/standard/73934.html) | 第 10 章：应用与单元范围；不泛用于所有家庭/移动机器人或声称合规 |
| E10 | W3C. *EPUB 3.3*. [格式规范](https://www.w3.org/TR/epub-33/) | 数字出版：包、导航与阅读顺序；发布实际校验另记录 |
| E11 | markdown-it-py maintainers. *Using markdown-it-py*. [官方文档](https://markdown-it-py.readthedocs.io/en/latest/using.html) | 固定渲染器构建 HTML/XHTML；不是机器人设计依据 |

## E.2 项目实现与协议

出版链接固定到代码复核快照；历史报告的模型/场景身份仍按原记录。阅读源文件同时读测试，辨别接口、实际接线和已覆盖行为。

| 主题 | 项目资料 | 相关章 |
| --- | --- | --- |
| 整体架构 | [系统架构](../../docs/production/architecture.md)、[Fleet](../../docs/architecture/fleet-cloud.md) | 2、9、13、17 |
| 闭环与世界 | `core/closedloop/`、`core/harness/`、`core/worldmodel/` | 3、4 |
| 能力/本体 | [能力目标](../../docs/guides/unified-capability-goals.md)、[硬件接入](../../docs/guides/hardware-agent-integration.md)、[适配器](../../docs/development/robot-adapters.md) | 5、10、14 |
| Agent 协议 | [事件、权限与恢复 binding](../../docs/development/2026-10-10-agent-interaction-protocol.md) | 6、11 |
| 云边协议 | [Basis、检查点、完成与兼容](../../docs/development/2026-10-10-cloud-edge-protocol.md) | 8、17 |
| 长上下文 | [预算修复与原失败](../../docs/development/2026-10-10-context-budget-recovery.md)、[协作导读](../../docs/architecture/long-horizon-collaboration.md) | 18 |
| 运行治理 | [配置和安全](../../docs/production/configuration-and-security.md)、[Orin](../../docs/install/edge-orin.md)、[现场准备](../../docs/production/field-readiness-2026-09-26.md) | 10、13 |
| 研究与完整案例 | [实验索引](C-experiment-index.md) | 12、19 |

## E.3 引用、复现与延伸

引用本书注明书名、版次、日期、章节；引用代码注明完整提交与路径；引用实验注明原报告与数据/制品身份。研究方案应继续阅读相应领域原论文，不把本书的工程建议当作证明。

旧版包含更多历史统计与完整源码片段，恢复方式见[出版说明](../front/01-publication-notes.md)。本版用固定源码链接代替复制实现，减少正文与代码漂移；学习过程可在本地批注或保存自己的版本差异。
