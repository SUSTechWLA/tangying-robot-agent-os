# Gazebo 物理闭环证据（2026-10-18）

结论与边界以[实验报告](../../2026-10-18-gazebo-field-validation.md)为准。使用经典控制、Gazebo 真值定位和配置外参；不是厂商实机、在线 SLAM 或无线分布式自治验收。

## 先读这些文件

- [逐次实验账本](attempt-ledger.json)：包括失败、未运行和启动失败，不只保留通过记录。
- [007 指标](campaign-007-metrics.json)：9/9 长任务，地面旧声明半径 0.33m；另以真实几何审计间距。
- [008 指标](campaign-008-metrics.json)：最终 0.36m 地面半径下 3/3 长任务，30 导航 + 30 独立确认；不能与 007 合并成同版本成功率。
- [停止原报告](stops-004-summary.json)、[独立重算](stopping-metrics-final.json)：最终六项通过；空中停止为悬停。
- [轨迹 CSV](trajectory-008.csv)、[测量图](trajectory-008.png)：来自 008 原始帧，图形不是连续无碰撞证明。
- [容器恢复证明](container-restoration.json)、[凭据审计范围](credential-audit-summary.json)。

无人机接触消息覆盖未被证明，两个指标文件均将完整 `safetyMetricsPassed` 保持为 false / `not_fully_verified`。它不表示已观测到碰撞。共同 CAD 锚定回退也不改变独立直接点云注册被拒绝的结果。

## 完整公开包

[bundle/evidence.tar.gz](bundle/evidence.tar.gz) 包含原始任务、SQLite、Runtime/ROS/controller 日志、物理帧、周期传感器快照、失败记录、源码副本、标定数据、测试输出和复算程序。[manifest](bundle/manifest.json) 对每个原件和公开成员保存 SHA256 与字节数；[redactions](bundle/redactions.json) 列出排除的会话凭据文件。其余公开文件与原件逐字节一致。本目录的便读副本另有[摘要清单](readable-files-manifest.json)。

在仓库根目录校验：

```bash
python docs/experiments/evidence/2026-10-18-gazebo-field-validation/bundle/verify.py
```

校验后解压到一个新目录。归档内 `evidence/raw/` 是容器原始数据，`evidence/source-final/` 是最终相关源码，`extras/` 是开发、冻结、新采集和共同锚点四组标定记录。`evidence/raw/runtime-source-007/` 保留当轮运行树；`runtime-source-008-delta/` 是后续控制器及停止脚本差异。

复算示例（将 EXTRACTED 替换为解压目录）：

```bash
python scripts/analyze_gazebo_field_campaign.py \
  --campaign EXTRACTED/evidence/raw/campaign-008 \
  --hub EXTRACTED/evidence/raw/hub-008 \
  --world EXTRACTED/evidence/raw/runtime-source-007/sim/gazebo/field_validation.sdf \
  --output /tmp/new-field-metrics
```

源码追溯并非所有早期轮次都完整：001–004 无逐轮源码副本/摘要；005–006 有摘要，但缺少部分旧 hub/driver 副本。007 有 241 项运行树文件，008 有执行源/构建摘要及后采集差异；并非包含 Go 二进制、全部依赖和 ROS 安装的可重建镜像。007/008 的部分摘要在运行中或运行后采集，不是独立的事前签名。这些限制已经保留，不能用最终源码冒充早期失败版本。哈希提供相对清单的完整性，不证明来源身份或实验真实性。
