# 附录 E · 参考资料与证据导航

## E.1 项目内的一手资料

项目源代码与报告的导航固定到出版说明中的复核快照。历史实验仍按原报告日期与注明的提交解释；这里的当前导航不改变历史数据来源。

| 主题 | 原始资料 | 使用边界 |
| --- | --- | --- |
| 闭环契约 | `core/closedloop/gate.go`、`core/closedloop/closedloop.go`、`core/harness/evaluator.go` | 具体代码路径和用例，不是所有设备因果保障证明 |
| 世界投影 | `core/worldmodel/`、`fleet/worldhub/` | 来源、版本与持久化规则 |
| 工具目录与准入 | `robot/gateway/tangying_robot_gateway/`、`core/skills/` | Python 与 Go/Runtime 目录有各自契约，跨版本需验证 |
| 事件运行时 | `agentruntime/`、`core/agentcontract/` | 观察者接线与事件机制 |
| 云边规范 | `docs/superpowers/specs/2026-09-25-cloud-edge-brain-upgrade-adr.md`、`docs/superpowers/specs/2026-09-25-role-specific-agent-harness-docker-adr.md` | 保留升级设计与决策历史 |
| 云边软件验收 | `docs/production/cloud-edge-upgrade-acceptance.md`、`docs/production/agent-harness-docker-acceptance.md` | 软件测试、容器和交叉编译，未认证目标设备 |
| 当前现场准备 | `docs/production/field-readiness-2026-09-26.md` | 当前风险、待实机验证范围与证据字段 |
| 安装与安全配置 | `docs/install/edge-orin.md`、`docs/architecture/fleet-cloud.md`、`docs/production/configuration-and-security.md` | 使用对应源码版本的环境模板，不复制书中的旧示例部署 |
| 证据门禁实验 | `docs/experiments/2026-09-21-grounded-verification.md` | 指定仿真和模型条件的比较 |
| 上下文实验 | `docs/experiments/2026-09-21-agent-context-evaluation.md` | 表达、信息完整性、模型与任务分布不能混为一因 |
| 全部实验索引 | [附录 C](C-experiment-index.md) | 原报告导航，非本版重跑结果 |

证据制品未提交或链接不可取时，不应将报告中的数字升级为独立复现结论。重跑必须重新记录模型服务、种子、数据哈希、环境、失败和排除项；旧报告与新报告分别保存。

## E.2 外部一手资料

以下是概念或格式依据，不是对本项目的背书或安全认证。访问核验日期：2026-09-26。

1. Leslie Lamport, **Time, Clocks, and the Ordering of Events in a Distributed System**, 1978。[作者提供的论文](https://lamport.azurewebsites.net/pubs/time-clocks.pdf)。用于区分事件因果与时钟排序；墙钟数值本身不建立 happens-before。
2. IETF, **RFC 3339: Date and Time on the Internet: Timestamps**。[规范](https://www.rfc-editor.org/rfc/rfc3339.html)。规定时间表示，不保证跨设备校时精度。
3. IETF, **RFC 9110: HTTP Semantics**，§9.2.2。[规范](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.2.2)。HTTP 方法的幂等语义不自动使机器人工具的物理效果幂等。
4. SQLite, **Isolation In SQLite**。[官方说明](https://www.sqlite.org/isolation.html)。用于理解数据库事务与读写隔离；这些性质不延伸到外部执行器。
5. W3C, **EPUB 3.3**。[出版格式规范](https://www.w3.org/TR/epub-33/)。本版导出遵循容器、包文档、导航和阅读顺序要求；实际导出仍须通过发布校验。
6. markdown-it-py, **Using markdown-it-py**。[项目文档](https://markdown-it-py.readthedocs.io/en/latest/using.html)。本版固定渲染器依赖，用于可重复构建 HTML/XHTML；不作为机器人设计依据。

## E.3 发布复现

`edition.json` 是版本和顺序清单；各分章文件是编辑源；`scripts/build_book.py` 生成合订本与发布包。发布报告保留章节哈希、源码快照、构建工具哈希和输出哈希。查错时应先确定读到的书籍版本，而不是用当前 main 的行号核对旧版。
