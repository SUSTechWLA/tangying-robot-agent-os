# tools · 全期共用的图件工具链

这一份是共享的，**全期只用这一份**。以前第 02、03 期各有一份重复副本，已合并到这里。

```text
tools/
├── render.mjs           图源 HTML → PNG（无头 Chromium）
└── theme/theme.css      深色科技风设计系统（颜色变量 + 基础组件）
```

---

## 渲染

在 `artifacts/marketing/` 目录下执行：

```bash
export PLAYWRIGHT_MODULE=/Users/wanglian/.npm/_npx/9833c18b2d85bc59/node_modules/playwright-core

node tools/render.mjs                  # 渲染所有期的所有图
node tools/render.mjs 03-工具调用        # 只渲染第 03 期
node tools/render.mjs 03-工具调用 04     # 只渲染第 03 期里文件名含 "04" 的图
node tools/render.mjs 04 05            # 所有期里文件名含 04 或 05 的图
```

约定每一期的形状：

```text
NN-主题/figures/src/*.html   ← 图源（改文案改这里）
NN-主题/figures/*.png        ← 产物（渲染生成，不要手改）
```

脚本会自动在本机 playwright 缓存里找可用的 Chromium，避免 npx 缓存的 playwright-core 与实际浏览器版本对不上。也可以用 `CHROME_PATH` 直接指定：

```bash
CHROME_PATH=/path/to/chrome-headless-shell node tools/render.mjs
```

输出为 **2× 分辨率**（1500px 宽 → 3000px PNG），直接适合发长图。

脚本会检查每张图**恰好有一个 `.fig` 元素**（截图对象是它，不是视口），并收集页面错误；任一图失败会以非零码退出。

---

## 设计系统

`theme/theme.css` 提供全期统一的视觉语言。色相沿用产品本身（teal `#167d72` 提亮为 `#2ad0bb`），所以宣传图和产品控制台是一套视觉。

### 可用变量

| 组 | 变量 | 用途 |
| --- | --- | --- |
| 底色 | `--bg` `--bg-2` `--panel` `--panel-2` `--panel-3` | 深色底与卡片层级 |
| 描边 | `--line` `--line-soft` `--line-strong` | 三级线宽，避免所有边框一样响 |
| 文字 | `--ink` `--ink-2` `--ink-3` `--ink-4` | 由亮到暗的四级文字 |
| 主色 | `--teal` `--teal-dim` `--teal-deep` `--teal-glow` | 安全 / 通过 / 我们的做法 |
| 危险 | `--danger` `--danger-dim` `--danger-glow` | 失败 / 拒绝 / 错误做法 |
| 警示 | `--warn` `--warn-dim` `--warn-glow` | 需要注意 / 待确认 |
| 第三色 | `--violet` `--violet-glow` | 特例 / 协议层 / 对比项 |
| 圆角 | `--radius` `--radius-sm` `--radius-xs` | 14 / 9 / 6 px |

### 可复用 class

- 结构：`.fig`（必须恰好一个）`.head` `.eyebrow`（配 `.dot` `.sep`）`h1`（配 `.hl`）`.sub` `.body` `.foot`（配 `.repo` `.tag`）
- 组件：`.card`（配 `.safe` `.warn` `.danger`）`.card-head` `.card-title` `.badge`（配 `.teal` `.danger` `.warn` `.violet` `.mute`）`.takeaway`（配 `.k`）`.mono` `code`

### 硬性规则

1. 恰好一个 `.fig`，宽度写死 `1500px`。
2. **不要外部资源**：无图片、无网络字体、无 JS。图示用内联 SVG 或 HTML/CSS 盒模型。这样输出是确定性的。
3. **不要覆盖 `:root` 变量，也不要写 `body{}` 规则**——本文件已处理深色底与居中。
4. 字号下限 12.5px，正文 14–16px。
5. 所有文字必须落在容器内；宁可换行也不要溢出。长英文标识符注意断行。

### 视觉约定（三期沉淀下来的）

- **一种颜色一个含义**：teal 永远是"通过/我们的做法"，danger 永远是"失败/反例"，不要混用。
- **每张图一个结论**：副标题里就把它说完。读者只看标题和副标题应该拿到结论。
- **对比优于陈述**：能画成"左边错、右边对"的就不要写成一段话描述。
- **页眉三行固定**：系列名 · 图号 · 视角；标题里用 `.hl` 强调一个词组；副标题一句话。
- **页脚固定两段**：仓库地址 + "从零到一入门机器人开发 · 第 NN 期 · 主题"。

起步模板见 [`../_模板/figures/src/example.html`](../_模板/figures/src/example.html)，注释里写清了变量、约束和页眉页脚的结构。
