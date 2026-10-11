# 数字版 1.1.3 恢复说明

旧版完整分章、合订本和 README 固定到 Git 提交 `11db0adb2be753e5ea04f6582032d925be8b95bb`；代码复核快照为 `4355038692eec9ea2e6bb2ea1cc7ed37246c257b`。本目录保存原版清单、发布记录和逐文件 SHA-256，新版修订不能倒写成旧版已实现/已验收能力。

```bash
git show 11db0adb2be753e5ea04f6582032d925be8b95bb:book/book.md > /tmp/tangying-book-1.1.3.md
git show 11db0adb2be753e5ea04f6582032d925be8b95bb:book/README.md > /tmp/tangying-book-1.1.3-readme.md
```

`source-manifest.json` 记录 24 份正文与合订本哈希；出版回归逐份读取上述 Git 快照核对。合订本 SHA-256：`942a3c9101f20f6f18c0c8e5f4c488a20b362576cd2a2ee93c6b41d405e4bccb`。
