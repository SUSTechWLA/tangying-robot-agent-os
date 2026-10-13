# 文档分类、学习路线与资料取用整理

日期：2026-10-13。整理基线为 `47b5739e32e84f8b4d853c2797f1bcc6bbd15fef`。本轮组织文档并修正已确认的指南冲突，没有修改机器人执行代码、重新运行机器人任务或改写历史实验结果。

## 原问题与组织方式

开始时 `docs/` 有209份 Markdown、34份配套文件。总索引混合操作、实现、设计、历史证据和论文贡献，且“新人第一小时”直接导向旧固定工位流程。书籍2.0.0有稳定课程主线，但固定代码快照与后续点云执行增量之间缺少清晰入口。

现改为“精选阅读路线 + 全量查找目录 + 原专题文档”三层：

| 入口 | 服务目标 | 主要内容 |
| --- | --- | --- |
| [总导航](../README.md) | 按问题找资料 | 四条路线、高频查询、版本/证据类型与仓库地图 |
| [学习](../learning/README.md) | 建立系统全貌 | 六阶段主线、不同专业路线、分级实践、书籍章节与系统增量对应 |
| [开发升级](README.md) | 定位和修改系统 | 模块→代码→接口→测试，当前说明/实现记录/历史方案/未验证范围 |
| [部署运维](../operations/README.md) | 安装、运行和交付 | 角色、标定/地图、任务运营、症状排障、恢复/升级/回滚与实机放行 |
| [研究论文](../research/README.md) | 选题、设计与复现 | 研究问题→方法→证据，JSON/CSV/原始包、失败保留和预登记 |
| [全量目录](../catalog.md) | 查找全部文件 | 原路径、标题、用途类别及配套数据/图像 |
| [维护规则](../maintenance.md) | 后续持续整理 | 存放位置、适用性判断、更新顺序、生成与验证命令 |

保留原文件路径、实验数据和发布身份，不搬动被书籍或外部引用的报告。根 README、书籍入口、生产手册与实验索引互相连接新导航。书籍正文、合订本和历史版次保持原内容。

## 确认并修正的指南冲突

| 原说明 | 修订 | 核对依据 |
| --- | --- | --- |
| 开发入门只提供 v0.3.0 固定工位与8787端口 | 当前首读改为 Gazebo 家庭/8897；原命令保留在明确的历史回归段落 | [Gazebo 指南](../guides/gazebo-default.md)、[Makefile](../../Makefile) |
| 兼容建图接口直接执行 `mapping.ensure` | 改为创建未批准 Task，旧参数按迁移规则拒绝 | [mapping_request.go](../../console/mapping_request.go)及对应兼容测试 |
| Local 没有接入 HTTP Policy | 改为 Local/Worker 已接通、默认禁用，设备策略仍需验收 | [Local policy 装配](../../cmd/local-agent/policy.go)、[策略说明](../production/policy-tools.md) |
| 没有图形化标定工具 | 明确已有注册服务调用/手工录入界面，算法与真机标定另行验证 | [工作流指南](../guides/robot-service-workflow.md)、[前端服务](../../web/robot_services.js) |
| 冷启动把 AWS 原房屋与 MuJoCo 标定目录当作默认 Gazebo 来源 | 分清同源导出资源、CameraInfo/commissioning、workflow与RTAB-Map数据；排障按实际引擎 | [Gazebo启动器](../../scripts/gazebo_process.py)、[标定工作流](../../robot/gateway/tangying_robot_gateway/gazebo_workflow.py) |
| 所有实验都有完整对照、可直接引用或本机复跑 | 区分协议、Harness、工程个例、对照和摘要；引用前核对数据可得性与统计单位 | [实验索引](../experiments/README.md)、对应原报告 |
| 统一要求清空旧 SLAM；接地验证 B1/A5 被描述为缺响应 | 改为按实验登记复位/地图复用、保留旧证据；按原报告标明实际模型结果与共享轨迹回放 | [接地验证原报告](../experiments/2026-09-21-grounded-verification.md) |
| 当前成熟度/新发布验证一律指向 V1 状态页 | 历史快照保留；新增结果按提交进入实验和发布记录 | [开发原则](principles.md)、[生产手册入口](../production/README.md) |

这些是本轮确认的入口及表述问题，不代表已对所有旧文逐句完成实现审计。原始 ADR 与历史记录中的“当前”继续按原时间解释。

## 可维护性与验证

新增[目录生成器](../../scripts/build_docs_catalog.py)，只生成分类目录，不改写源文件；`--check` 在目录与文件集合/标题不一致时返回失败。用途分类采用保守标签，设计档案不会被自动标为已实现。

- `python3 scripts/build_docs_catalog.py --check`：生成内容一致，全部目录项指向保留的原文件。
- `.venv/bin/python -m pytest -q tests/docs tests/test_repository.py tests/book`：55项通过，涵盖当前文档本地链接/Make目标、仓库约束、出版源和历史版次。
- `go test ./tests/docs`：通过。
- `make book-check`：31份出版源与合订 Markdown 一致；本轮不重新出版 HTML/EPUB。
- `.venv/bin/ruff check scripts/build_docs_catalog.py`、`git diff --check`：通过。
- 建图兼容接口定向回归通过：`go test ./console -run 'TestLegacyMappingEntryCreatesUnapprovedUnifiedTask|TestMappingCompatibility' -count=1`。

检查日志在本机 `artifacts/documentation/20261013-navigation/`。本地文件存在不等于所有网页和标题锚点都有效；新增导航锚点另做定向检查，按仓库书籍标题规则与显式 HTML 锚点，定向检查18份新增/修改页面的39个片段链接，未发现缺失目标；历史 ADR 的旧内部路径按原版本解释。测试过程没有接入真实机器人或重新估计系统可靠性。
