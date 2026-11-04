# 数字版 3.2.0 档案

正文、图形、插画提示词与原出版器固定在 `e3a1a369a3a6058369c58c205eb3bc8234c2d5f7`。实现快照为 `63af99305fd8204c0a54d86145cb94d89b754326`，配套文档为 `021e99c6e8994db474c5bca6771357f6a1b3af03`。本目录保存原版次和逐文件 SHA-256，3.3.0 不覆盖原始书稿、实验或已有出版目录。

```bash
git worktree add --detach /tmp/agentos-book-3.2.0 e3a1a369a3a6058369c58c205eb3bc8234c2d5f7
cd /tmp/agentos-book-3.2.0
make book-setup
make book-release
```

在独立 checkout 中使用原命令恢复；不要将新生成器用于覆盖旧目录。`source-manifest.json` 包含 31 个出版源、合订本、README、生成器、依赖和全部资产的摘要。
