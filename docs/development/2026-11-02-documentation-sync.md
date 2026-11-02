# 2026-11-02 文档与教材 3.3.0 同步记录

实现基线：`27cbbbaf96e2d76e1a17a1ea1befc8a8d8db14ce`。本次按当前源码更新操作、接口、架构、学习和研究资料，并出版教材 3.3.0。机器人运行代码、现场配置与在运行进程没有在本次改动中升级；此前软件、模型和 Gazebo 实验继续引用各自原版本。

## 1. 同步方法与范围

扫描根 README、`docs/`、`book/` 和相关源码 README 中的版本、session/会话、上下文、recipe、KV、恢复和导航就绪描述，人工核对受影响当前资料。历史日期报告、旧版出版清单、原实验数据及原设计档案按原时点保留；“全部文档同步”不意味着机械改写所有历史文件。

稳定入口调整为[当前系统速读](../learning/current-system.md)、[自动会话操作手册](../operations/automatic-edge-sessions.md)、[消息与上下文专题](../architecture/edge-agent-communication-and-context.md)，实现与测试来源仍见[会话变更](2026-11-01-automatic-edge-sessions.md)和[原验证报告](../experiments/2026-11-01-automatic-edge-sessions.md)。

| 问题 | 文档中的修正 |
| --- | --- |
| 旧架构建议不建立 Agent session | 区分访问 token、intake session/turn、Task/revision、command/journal 与模型视图 |
| “重启恢复”容易被理解为自动续跑 | 精确说明下次同 robot/adapter 的 Begin 查账，存在 task 验证绑定，无 task 记 interrupted，不重放 |
| 历史与当前目标混写 | request 独立保留，历史为 reference，按 source version/SHA 核对后经预算投影与有界回查 |
| recipe 与会话背景冲突 | 默认 Local 含会话时只给提示；未接会话的 Worker 精确复用仍按原规则重验证 |
| KV 被当作会话数据库或节省保证 | session、经验与供应商计算复用分别说明；真实 usage、缺失与零分开统计 |
| 本地与云端路径混淆 | 自动会话接 Local tasks.Create；objectives、Worker 和云边批准合同保持各自入口 |
| 目录锁与升级方式不清 | 正常停止旧进程，保留数据目录与锁文件；单机 flock 不是分布式租约，多用户隔离未实现 |
| 旧巡检失败与后续成功被混淆 | 第 19 章引用独立 Gazebo 续测，保留失败、人工介入、场景和源码差别 |

核心事实核对入口：`cmd/local-agent/main.go`、`tasks/creation_session.go`、`internal/agentsession/manager.go`、`internal/capabilityagent/planner.go` 和 `recipe_memory.go`，以及原 HTTP/SQLite、scope、spill 和 pending 测试。

## 2. 教材版次与恢复身份

3.3.0 继续四篇、19 章、9 附录、57 道分层题；正文吸收自动会话和长历史成本补充，课程/实验/工程模板同时对齐。新增 `18-auto-session.svg`、`18-cost-stack.svg`，合计 25 幅 SVG + 2 幅 PNG；两幅新图由标准库确定性生成，不是实验数据。

3.2.0 原始出版源冻结于 `e3a1a369a3a6058369c58c205eb3bc8234c2d5f7`，已保存[恢复清单](../../book/editions/3.2.0/README.md)：31 个出版源、合订本、原生成器/依赖、README 和资产均有 SHA-256。新版使用独立 `artifacts/book/3.3.0/`，不覆盖旧目录。实现快照固定为本页基线；项目文档快照在教材 `edition.json` 独立登记，避免把未提交文件伪装成固定远程来源。

## 3. 验证与交付

检查包括当前文档链接/命令、架构依赖、书稿清单与图形生成一致性、HTML/EPUB 离线资源与导航、旧版可恢复性。出版器保留来源清单、输出 SHA256SUMS，并拒绝不同来源覆盖同版次目录。执行结果、变更清单和核对范围在本记录的最终验证节登记。

没有新增机器人功能实验。此前 539 个顶层软件测试、4 包补丁复测和家庭巡检结果只按原报告引用，不能算作本次新采样或新版部署成功。

## 4. 后续维护规则

当前指南用稳定入口解释操作；日期记录回答当时怎么实现和验证；教材按版次固定来源；实验保留原始失败与统计单位。新增认证主体、跨渠道 Router、HTTP 请求幂等或新的召回策略后，应同时更新这四层，不能只修改总 README。

## 5. 最终验证结果

当前资料路径/关键词扫描清单覆盖 387 份 Markdown，其中 80 份更新、307 份保留。保留包括无相关变更的指南和历史材料，不表示逐句重新认证其历史结论；文件范围见[机器可读清单](2026-11-02-documentation-sync.json)。运行代码基线为 `27cbbbaf9`，教材主要配套文档冻结在 `82c1cee03ad60801972e1bbdfcd7aa8db15a3e43`；本节最终验证记录随后追加，出版物来源由自身 provenance 固定。

| 检查 | 实际结果 |
| --- | --- |
| Python 当前文档与仓库约定 | `pytest -q tests/docs tests/test_repository.py`：38 passed |
| Go 文档及依赖架构 | `go test ./tests/docs ./tests/architecture`：两包通过 |
| 出版与既有教学实验 | `.venv-book/bin/python -m pytest -q -o addopts='' tests/book`：45 passed，含新增 3.2.0 恢复身份检查 |
| 书稿与图形生成 | `make book-check`：31 个出版源、25 幅 SVG 一致 |
| 新增课堂会话命令 | 附录 F 的会话筛选命令（race）：命令通过；四包实际选择测试，core/agentsession 在该正则下仅编译无匹配测试 |
| 离线 HTML/EPUB | 独立预览与正式 3.3.0 构建成功，资源/导航结构检查通过，输出 SHA256SUMS 全部匹配 |
| 历史恢复 | 3.2.0 出版源及原生成器测试通过，全部归档资产摘要另逐项核对 |
| 新图目视 | Quick Look 渲染两幅 SVG 后检查，文字与箭头可读 |

测试执行日志保存在本机 `artifacts/book/qa-3.3.0/verification/`；3.3.0 正式产物位于 `artifacts/book/3.3.0/`。它们属于本地产物，不声称已随 Git 分发；源文件、构建方法与出版测试均在仓库中，可按版次重建。

保留的核验问题：首次文档扫描因本记录尚未创建而出现一条缺失链接；README 首屏回归要求家庭任务入口，调整导读与版本记录顺序后通过；冻结文档前构建拒绝引用未进快照的 3.2.0 档案，先提交配套指南再固定文档快照后通过。浏览器自动化因既有测试 profile 被占用无法启动，未改动该 profile，使用系统 SVG 渲染核对；本轮没有浏览器交互验收。
