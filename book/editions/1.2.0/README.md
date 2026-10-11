# 数字版1.2.0恢复说明

完整27份正文、合订本、README和出版脚本固定于Git提交`00676236742f6503f1c487cea42be1d579bb4bb8`。本目录保存原清单、RELEASE和逐文件SHA，教材重整不会倒写旧版或原实验。

```bash
git show 00676236742f6503f1c487cea42be1d579bb4bb8:book/book.md > /tmp/tangying-book-1.2.0.md
git show 00676236742f6503f1c487cea42be1d579bb4bb8:scripts/build_book.py > /tmp/tangying-build-book-1.2.0.py
```

合订本SHA-256：`225b72cf5419723c8eff81e0563457a2b31cb2b3ac203c3e6dab0142e3d68bfa`。复制旧版分章重新出版时也应恢复该提交的清单、生成器与固定依赖；直接读取Git中的合订本可恢复原文。
