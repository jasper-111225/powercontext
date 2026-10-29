# 据典固定题集评测

- 生成时间：2026-10-29T20:08:31
- 向量模型：`qwen3-embedding:0.6b`
- 语料：9 篇 / 135 片段
- 题集：20 道可答 + 3 道资料外

## 指标

| 指标 | 数值 |
|---|---:|
| Recall@5 | 100% （20/20） |
| 引用命中率 | 100% （20/20） |
| 拒答准确率 | 100% （3/3） |
| 中位耗时 | 70 ms |
| 平均耗时 | 86 ms |

## 逐题结果

| # | 问题 | 类型 | 模式 | Recall@5 | 引用 | 拒答 | 耗时 |
|---|---|---|---|---|---|---|---:|
| a01 | Release 模式的编译产物在哪个目录？ | 可答 | extractive | 命中 | 有 | — | 381 ms |
| a02 | Sanity 二进制的编译产物在哪里？ | 可答 | extractive | 命中 | 有 | — | 84 ms |
| a03 | 首次编译 seekdb 时，用于准备依赖的 Release 初始化命令是什么？ | 可答 | extractive | 命中 | 有 | — | 122 ms |
| a04 | 完成初始化后，Release 模式的增量编译命令是什么？ | 可答 | extractive | 命中 | 有 | — | 68 ms |
| a05 | 怎样用 obd.sh 部署并启动一个本地 seekdb 实例？ | 可答 | extractive | 命中 | 有 | — | 70 ms |
| a06 | seekdb 仓库固定使用哪个 Rust 版本？ | 可答 | extractive | 命中 | 有 | — | 82 ms |
| a07 | 在 Windows 上编译 seekdb，Visual Studio 需要选择哪个工作负载？ | 可答 | extractive | 命中 | 有 | — | 82 ms |
| a08 | 分析 core dump 时应该使用什么命令？ | 可答 | extractive | 命中 | 有 | — | 75 ms |
| a09 | 如何把 syslog 日志级别调整为 DEBUG？ | 可答 | extractive | 命中 | 有 | — | 66 ms |
| a10 | syslog 的 I/O 带宽限制默认是多少？ | 可答 | extractive | 命中 | 有 | — | 69 ms |
| a11 | 每种 syslog 日志文件默认最多保留几个？ | 可答 | extractive | 命中 | 有 | — | 59 ms |
| a12 | memory_budget 设置为 0M 时，自动值以多少为目标？ | 可答 | extractive | 命中 | 有 | — | 67 ms |
| a13 | memory_budget 显式设置为非零值时不得小于多少？ | 可答 | extractive | 命中 | 有 | — | 63 ms |
| a14 | ObArenaAllocator 的设计特点是什么？ | 可答 | extractive | 命中 | 有 | — | 59 ms |
| a15 | 为什么 seekdb 禁止使用 STL 容器？ | 可答 | extractive | 命中 | 有 | — | 59 ms |
| a16 | 单入口单出口对函数返回有什么要求？ | 可答 | extractive | 命中 | 有 | — | 62 ms |
| a17 | ObString 自己管理内存吗？ | 可答 | extractive | 命中 | 有 | — | 72 ms |
| a18 | 如何列出可用的单元测试模块目标？ | 可答 | extractive | 命中 | 有 | — | 69 ms |
| a19 | 哪个目标可以一次编译全部单元测试模块？ | 可答 | extractive | 命中 | 有 | — | 68 ms |
| a20 | 如何运行全部 mysqltest 测试？ | 可答 | extractive | 命中 | 有 | — | 74 ms |
| u01 | seekdb 支持 MongoDB 协议吗？ | 资料外 | insufficient | — | — | 已拒答 | 74 ms |
| u02 | seekdb 如何启用 GPU 加速？ | 资料外 | insufficient | — | — | 已拒答 | 87 ms |
| u03 | 如何用 Kubernetes Operator 部署 seekdb？ | 资料外 | insufficient | — | — | 已拒答 | 77 ms |
