# 数字版 3.1.0 档案

正文、插图、精确提示词和生成器固定在 `0b42b87d729b9c9adcd192d563450fd342583560`。本目录保存出版说明与逐文件 SHA-256；从该提交建立独立 checkout 后运行原构建命令，不要覆盖旧出版目录。

```bash
git worktree add --detach /tmp/agentos-book-3.1.0 0b42b87d729b9c9adcd192d563450fd342583560
cd /tmp/agentos-book-3.1.0
make book-setup
make book-release
```
