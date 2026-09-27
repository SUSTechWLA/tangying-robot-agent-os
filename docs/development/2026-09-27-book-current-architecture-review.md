# Book 1.1.0当前架构同步报告

## 目标与身份

用户授权直接更新book正文，匹配较大架构升级。代码复核固定到295e5523529b68c8ba97f0a65373e92fd3c0546a（包含本轮发现的取消回执竞态修复，统一能力架构原合入为c02ed6f137aec0da9c54a0800478d9c29c8d636f）；出版日期2026-09-27。书籍编辑提交与代码身份分开，出版provenance保存全部章节哈希。

## 修改与依据

逐章矩阵见[book/RELEASE](../../book/RELEASE.md)。当前依据是core/capability、internal/capabilityagent、任务/Worker持久状态、Runtime服务契约、[实施规格](2026-09-27-capability-goal-implementation-spec.md)、[能力指南](../guides/unified-capability-goals.md)及[实际闭环](../experiments/2026-09-27-capability-goal-closure.md)。

主要修正：旧NL全部Intent的主入口、旧工具计数代表全部能力、Local无任何云I/O、Local固定robot-local、云Worker无SQLite、所有租约都被Local删除、自动恢复等同人工StepReconciliation、Room实体缺失等同语义能力缺失、旧未实现清单仍为当前路线图、出版CI硬编码1.0.0。

新正文明确：独立制品读回与物理观测分工；稳定operationId与分段sessionId不同；claim与operation lease不同；目录/机器人/参数审批冻结；未知写入不重放；取消读回；模型调用点独立；Dockerprofile互斥与持久卷；异构Provider责任；当前单机器人GOAL及实机/规模边界。

## 留存与验证

1.0.0原edition.json、RELEASE.md及24份正文/合订SHA-256保存在book/editions/1.0.0，原正文由c02ed6f恢复。新增出版回归验证真实Git内容和原文件字节，避免以新版覆盖历史。原规范、实验和research不改写。新版输出artifacts/book/1.1.0，与旧产物分离。

## 已完成的验证

| 检查 | 本地结果 |
| --- | --- |
| make book-check | 24个单元、链接/锚点/围栏与合订同步通过 |
| 出版回归 | 6通过；含旧版真实Git字节恢复、两次构建全部输出逐字节一致 |
| 文档测试 | 19通过 |
| EPUBCheck 5.4.0 | 0 fatals / 0 errors / 0 warnings / 0 infos |
| 浏览器抽查 | 1.1.0首页身份、第17章直接锚点打开、表格及正文可读；不是所有阅读器认证 |
| 本轮完整make test | Go全量通过；Python2432通过/40跳过，隔离Runtime2通过；前端486通过 |
| 取消修复后门禁 | 重新build/lint/generate-check/test-go全部通过；Python/前端源码未改变 |
| 取消竞态回归 | 旧代码overlay确定性失败；修复后包测试及HTTP取消100次通过 |
| Git whitespace | git diff --check通过 |

CI使用edition.json动态选择1.1.0，安装固定出版依赖、执行上述出版回归、EPUBCheck和上传完整包。最终远端门禁以该提交的GitHub release-gate记录为准，本报告不预报尚未结束的CI结果。

## 新版制品身份

输出目录artifacts/book/1.1.0；构建产物按项目约定不进入Git，由本地文件或CI发布包分发。旧1.0.0输出不覆盖。

- book.epub SHA-256：34908bd2601945efe715b7556b1223b7b8a23bfa3788a13efc384ca7537a3cad
- index.html SHA-256：b4d6891cbc6dfa9955de0ad7547d346163c6e6c8f7362081a7bd6773597afa85
- book.md SHA-256：ec42e5ae1fb647667838dfbf3c0fc243c9fb2ff380d83969da7c4f7cad5d2a3e
- provenance.json SHA-256：88b39ce9f3304498c9b0175950840b712f33eb2a01c9d52cd38db12d693bacb1

SHA256SUMS另含LICENSE哈希。书籍源provenance保存24单元与工具版本；书籍编辑提交由Git识别，代码导航固定295e552。此次同步出版与CI资源，并修复复核CI时实际发现的共享Executor取消回执竞态，见[独立修复报告](2026-09-27-capability-cancel-ack-race.md)。不重新运行硬件或模型实验。
