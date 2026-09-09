# 躺营 · 小红书图文发布包（v0.2 家庭场景版）

准备日期：2026-09-09。正文见 `copy.md`，图片按 `01 → 05` 的顺序使用。

1. `01-cover.png`：项目封面，使用仿真机载 RGB-D 图像，明确标注为仿真验证与实机待接入。
2. `02-task-loop.png`：限定工位中的自然语言任务拆解与结果核对，展示 18 步参考任务的闭环结构。
3. `03-evidence.png`：同源 RGB-D 输入、历史观测和恢复语义，强调机器人只能根据已采集证据判断。
4. `04-sim2real-career.png`：统一任务工具 / MCP、robot profile、MuJoCo、ROS 2 和真实机器人适配层，以及求职和共建诉求。
5. `05-home-slam.png`：最新家庭场景版本，包含客厅、走廊、厨房、卧室和卫生间标签，以及当前 RTAB-Map 视觉就绪和未知区域安全门禁。

项目主页：

- https://github.com/SUSTechWLA/tangying-robot-agent-os

当前能力所在分支：

- https://github.com/SUSTechWLA/tangying-robot-agent-os/tree/codex/v0.2-release

当前提交：`5ad6d6f`。

## 发布时必须保留的事实边界

- 图片中的工位和家庭画面来自仿真 RGB-D 采集，不是客户实机照片。
- 限定工位参考任务完成过 18 步闭环；家庭场景目前完成视觉就绪、客厅段导航和到达确认验证。
- 家庭跨房间任务遇到尚未覆盖的地图区域会被 Nav2 安全拒绝；这不是跨房间实机成功率声明。
- 真实 XLeRobot 仍需完成相机标定、TF / 里程计、底盘驱动、速度限幅、急停、失联归零和现场地图验收。
- 求职文案只表达希望寻找机器人 Agent、具身智能系统和机器人软件相关机会，没有虚构学历、工作经历或实机交付。

## 编辑与再生成

全部卡片使用可编辑 SVG 和 `build-cards.cjs` 生成，原始 RGB-D 素材保存在 `inputs/`。安装 Node.js 与 `sharp` 后运行：

```bash
SHARP_MODULE=/path/to/node_modules/sharp node build-cards.cjs
```

卡片采用 3:4 竖版比例，适合小红书图文发布。SVG 中的标题、事实说明、项目地址和视觉词统计都可以继续更新。
