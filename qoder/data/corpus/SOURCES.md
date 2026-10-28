# 示例语料来源

`data/corpus/seekdb-dev/` 下的 Markdown 来自 **oceanbase/seekdb** 仓库的 `docs/developer-guide/zh/`：

- 仓库：<https://github.com/oceanbase/seekdb>
- 路径：`docs/developer-guide/zh/*.md`
- 许可：Apache-2.0（见上游 `LICENSE`）
- 获取时间：2026-10-28
- 改动：仅复制文件、在 `manifest.json` 中补充中文标题；正文未修改。

保留的 9 篇：

| 文件 | 主题 |
|---|---|
| `build-and-run.md` | 获取代码、编译并运行 |
| `toolchain.md` | 安装工具链 |
| `container.md` | C++ 容器库（ObIArray/ObArray/ObHashMap 等） |
| `debug.md` | 调试 seekdb（GDB/LLDB、日志、调用栈） |
| `memory.md` | 内存管理与内存预算 |
| `logging.md` | 系统日志与日志级别 |
| `unittest.md` | 编写与运行单元测试 |
| `mysqltest.md` | 使用 obd.sh 运行 mysqltest |
| `coding-convention.md` | C++ 命名与函数约定 |

## 为什么只保留 9 篇

演示语料的作用是让评审在 10 分钟内跑通并核对引用，不是复刻整个文档站。
9 篇覆盖「构建 → 调试 → 内存 → 日志 → 测试 → 编码」的完整学习路径，且向量化时间可控。
需要全量文档时，把上游 `docs/developer-guide/zh/` 整个目录复制进来，执行：

```bash
python scripts/seed.py --reset
```

## 评测集

`data/gold.json` 的 23 道题由作者根据上述文档手工编写，答案均可在这 9 篇中找到；
3 道「不可答」题（MongoDB 协议、GPU 加速、Kubernetes Operator）用于检验拒答，文档中确实没有这些内容。
