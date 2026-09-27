# 数字版1.0.0历史恢复

1.1.0已直接更新当前正文。本目录保存1.0.0的原edition.json、原RELEASE.md及24个正文源文件与合订本SHA-256清单，不复制一整套正文。

## 两个历史身份

- 书籍正文快照：c02ed6f137aec0da9c54a0800478d9c29c8d636f。该提交在1.1.0编辑之前保存了完整1.0.0正文。
- 当时代码复核快照：2edd1c1ff07634765c1a671b6d803c679b3b7a5f。不能将上述书籍快照误写为旧版代码复核身份。

## 恢复阅读

在仓库根目录读取原合订本：

~~~bash
git show c02ed6f137aec0da9c54a0800478d9c29c8d636f:book/book.md > /tmp/robot-agentos-book-1.0.0.md
~~~

重新构建旧出版包时，在单独checkout中使用上述书籍提交的edition.json、全部分章、脚本与固定依赖，再运行make book-setup及make book-release。原路径为artifacts/book/1.0.0；新版输出到1.1.0，不覆盖旧输出。历史正文恢复的哈希由tests/book验证，source-manifest.json不能用新版正文重新生成。

原实验、规范、research和审校记录保留原日期；历史内容不作为当前实现说明。当前书籍入口见[README](../../README.md)。
