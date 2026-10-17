# 冻结软件实验与验证证据

`campaign/` 是 spatial-exploration-20261018-002 的全量原件副本，包含最终日志写入容量保护。`pre-byte-capacity-campaign/` 保留修复前的 001 原件，`pre-byte-capacity-figure/` 保留其图表。两次使用相同配置、配对种子与几何，复测不扩大独立样本量；最终结果以 002 为准。

`validation/` 保存实际命令、结果、初次失败和后续修正日志。全量 Python 门禁在最后日志写入容量保护前通过，最终代码再次通过 96 项聚焦测试、lint 和 30 个 episode 完整复测与回放。详细时序见实验报告。

`master-manifest.json` 固定本目录各文件的字节数与 SHA-256。无实机或 Gazebo 新验收主张。
