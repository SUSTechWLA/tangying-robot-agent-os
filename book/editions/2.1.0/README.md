# 数字版 2.1.0 恢复说明

31 份出版源、合订本、README、出版脚本与依赖固定于 `821659dfd6782927d02924916ced5474a8142d74`。本目录保存原清单、RELEASE 和逐文件 SHA；2.2.0 更新不覆盖旧书或旧实验。

```bash
git show 821659dfd6782927d02924916ced5474a8142d74:book/book.md > /tmp/tangying-book-2.1.0.md
git show 821659dfd6782927d02924916ced5474a8142d74:scripts/build_book.py > /tmp/tangying-build-book-2.1.0.py
```

完整出版需在独立目录恢复该提交的31份分章、清单、生成器、固定依赖与LICENSE，按source-manifest.json逐项核对SHA；勿用新版脚本覆盖旧版制品。2.1.0代码复核身份仍为 `9e5aebaec9199aa93d86e4561858ba5ef1fa5b82`，不随2.2.0变更。
