# 评测结果

- 运行时间：2026-10-28T07:07:27+08:00
- 生成模型：`qwen2.5:0.5b`；向量模型：`qwen3-embedding:0.6b`；模式：`full`
- 语料：138 个片段；题目：23（可答 20 / 不可答 3）

| 指标 | 数值 |
|---|---:|
| Recall@5 | 100% |
| 引用命中率 | 100% |
| 拒答准确率 | 100% |
| 中位耗时（p50） | 176 ms |
| 平均耗时 | 2643 ms（含一次模型重载） |

| # | 问题 | 期望来源 | Top-5 | 引用 | 模式 |
|---|---|---|---|---|---|
| q01 | 首次编译 seekdb 前需要初始化依赖吗？ | build-and-run.md | ✓ | ✓ | extractive |
| q02 | Release 模式的编译产物在哪个目录？ | build-and-run.md | ✓ | ✓ | extractive |
| q03 | build.sh 支持 Debug 模式吗？ | build-and-run.md | ✓ | ✓ | extractive |
| q04 | 增量编译使用什么命令？ | build-and-run.md | ✓ | ✓ | extractive |
| q05 | 怎么启动一个本地 seekdb 实例？ | build-and-run.md | ✓ | ✓ | extractive |
| q06 | 怎么停止并清理本地实例？ | build-and-run.md | ✓ | ✓ | extractive |
| q07 | Debian 系统上要安装哪些工具链依赖？ | toolchain.md | ✓ | ✓ | extractive |
| q08 | 怎么用 GDB 调试 seekdb？ | debug.md | ✓ | ✓ | extractive |
| q09 | 可调试二进制怎么构建？ | debug.md | ✓ | ✓ | extractive |
| q10 | 日志级别怎么配置？ | logging.md | ✓ | ✓ | extractive |
| q11 | 系统日志默认写到哪里？ | logging.md | ✓ | ✓ | extractive |
| q12 | 单元测试的构建怎么配置？ | unittest.md | ✓ | ✓ | extractive |
| q13 | 怎么编译并运行全部模块的测试？ | unittest.md | ✓ | ✓ | extractive |
| q14 | mysqltest 怎么运行？ | mysqltest.md | ✓ | ✓ | extractive |
| q15 | ob_malloc 和 OB_NEWx 有什么区别？ | memory.md | ✓ | ✓ | extractive |
| q16 | ObArenaAllocator 适合什么场景？ | memory.md | ✓ | ✓ | extractive |
| q17 | 类名和成员变量怎么命名？ | coding-convention.md | ✓ | ✓ | extractive |
| q18 | 为什么禁止使用 STL 容器？ | coding-convention.md | ✓ | ✓ | extractive |
| q19 | ObIArray 和 ObArray 有什么区别？ | container.md | ✓ | ✓ | extractive |
| q20 | 运行时内存预算怎么设置？ | memory.md | ✓ | ✓ | extractive |
| u01 | seekdb 支持 MongoDB 协议吗？ | — | ✗ | ✗ | insufficient |
| u02 | 怎么开启 GPU 加速？ | — | ✗ | ✗ | insufficient |
| u03 | Kubernetes Operator 怎么部署 seekdb？ | — | ✗ | ✗ | insufficient |
