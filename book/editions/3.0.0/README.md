# 数字版 3.0.0 档案

正文、16 幅 SVG、两份课堂实验和生成器固定在 `e1c6e353c3df7397fbe229a4542459ba9251ac46`。本目录保存出版说明与逐文件 SHA-256；恢复时从该提交建立独立 checkout，在其中执行原版构建命令。不要用新生成器覆盖旧出版目录。

```bash
git worktree add --detach /tmp/agentos-book-3.0.0 e1c6e353c3df7397fbe229a4542459ba9251ac46
cd /tmp/agentos-book-3.0.0
make book-setup
make book-release
```
