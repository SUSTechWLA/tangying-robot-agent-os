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
