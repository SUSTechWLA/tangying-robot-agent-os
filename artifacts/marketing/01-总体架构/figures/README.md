# 第 01 期图件说明

本期的图**不是**手写 HTML + SVG 渲染的信息图（那是第 02、03 期的做法），而是由会话内脚本生成的**数据图**：

| 文件 | 是什么 | 数据来源 |
| --- | --- | --- |
| `稠密点云地图.png` | 发布地图的稠密点云重绘 | 地图产物的 `cloud/lod4.bin` |
| `导航栅格.png` | 导航栅格重绘 | 地图产物的 `navigation/map.pgm`、`trajectory.geojson` |
| `关键帧扫描示例.png` | 关键帧原始预览帧 | `slam-keyframes.json` |
| `任务验收汇总.png` | 任务验收成绩排版 | `summary.json` |
| `SLAM精度.png` | SLAM 精度指标排版 | `metrics.json` |

**它们不是界面截图。** 真实界面截图（11 张）在共享目录 [`../../_assets/screens/`](../../_assets/screens/)，仿真渲染在 [`../../_assets/renders/`](../../_assets/renders/)。

重做方法记录在 [`../素材来源与数字出处.md`](../素材来源与数字出处.md) §2 的表格里（每张图对应哪个数据源、怎么排版）。第 02、03 期的信息图渲染管线见 [`../../tools/README.md`](../../tools/README.md)。
