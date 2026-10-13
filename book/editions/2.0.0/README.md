# 数字版 2.0.0 恢复说明

31 份出版源、合订本、README、出版脚本与依赖固定于 `56c16c485efa2bd8037ab75bcb6be2d3643c3f5a`。本目录保存原清单、RELEASE 和逐文件 SHA；2.1.0 更新不覆盖旧书或旧实验。

```bash
git show 56c16c485efa2bd8037ab75bcb6be2d3643c3f5a:book/book.md > /tmp/tangying-book-2.0.0.md
git show 56c16c485efa2bd8037ab75bcb6be2d3643c3f5a:scripts/build_book.py > /tmp/tangying-build-book-2.0.0.py
```

恢复完整出版时，在单独目录恢复该提交的分章、清单、生成器与固定依赖，勿使用新版脚本覆盖旧版制品。
