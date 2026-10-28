# 据典固定题集评测

- 生成时间：2026-10-28T22:52:54
- 向量模型：`qwen3-embedding:0.6b`
- 语料：9 篇 / 138 片段
- 题集：20 道可答 + 3 道资料外

## 指标

| 指标 | 数值 |
|---|---:|
| Recall@5 | 95% （19/20） |
| 引用命中率 | 95% （19/20） |
| 拒答准确率 | 100% （3/3） |
| 中位耗时 | 209 ms |
| 平均耗时 | 372 ms |

## 逐题结果

| # | 问题 | 类型 | 模式 | Recall@5 | 引用 | 拒答 | 耗时 |
|---|---|---|---|---|---|---|---:|
| a01 | Release 模式的编译产物在哪个目录？ | 可答 | extractive | 命中 | 有 | — | 17 ms |
| a02 | Sanity 二进制的编译产物在哪里？ | 可答 | extractive | 命中 | 有 | — | 289 ms |
| a03 | 首次编译 seekdb 时，用于准备依赖的 Release 初始化命令是什么？ | 可答 | extractive | 命中 | 有 | — | 1573 ms |
| a04 | 完成初始化后，Release 模式的增量编译命令是什么？ | 可答 | extractive | 命中 | 有 | — | 203 ms |
| a05 | 怎样用 obd.sh 部署并启动一个本地 seekdb 实例？ | 可答 | extractive | 命中 | 有 | — | 195 ms |
| a06 | seekdb 仓库固定使用哪个 Rust 版本？ | 可答 | extractive | 未命中 | 无 | — | 200 ms |
| a07 | 在 Windows 上编译 seekdb，Visual Studio 需要选择哪个工作负载？ | 可答 | extractive | 命中 | 有 | — | 345 ms |
| a08 | 分析 core dump 时应该使用什么命令？ | 可答 | extractive | 命中 | 有 | — | 215 ms |
| a09 | 如何把 syslog 日志级别调整为 DEBUG？ | 可答 | extractive | 命中 | 有 | — | 176 ms |
| a10 | syslog 的 I/O 带宽限制默认是多少？ | 可答 | extractive | 命中 | 有 | — | 240 ms |
| a11 | 每种 syslog 日志文件默认最多保留几个？ | 可答 | extractive | 命中 | 有 | — | 165 ms |
| a12 | memory_budget 设置为 0M 时，自动值以多少为目标？ | 可答 | extractive | 命中 | 有 | — | 176 ms |
| a13 | memory_budget 显式设置为非零值时不得小于多少？ | 可答 | extractive | 命中 | 有 | — | 2680 ms |
| a14 | ObArenaAllocator 的设计特点是什么？ | 可答 | extractive | 命中 | 有 | — | 304 ms |
| a15 | 为什么 seekdb 禁止使用 STL 容器？ | 可答 | extractive | 命中 | 有 | — | 219 ms |
| a16 | 单入口单出口对函数返回有什么要求？ | 可答 | extractive | 命中 | 有 | — | 303 ms |
| a17 | ObString 自己管理内存吗？ | 可答 | extractive | 命中 | 有 | — | 200 ms |
| a18 | 如何列出可用的单元测试模块目标？ | 可答 | extractive | 命中 | 有 | — | 112 ms |
| a19 | 哪个目标可以一次编译全部单元测试模块？ | 可答 | extractive | 命中 | 有 | — | 277 ms |
| a20 | 如何运行全部 mysqltest 测试？ | 可答 | extractive | 命中 | 有 | — | 199 ms |
| u01 | seekdb 支持 MongoDB 协议吗？ | 资料外 | insufficient | — | — | 已拒答 | 29 ms |
| u02 | seekdb 如何启用 GPU 加速？ | 资料外 | insufficient | — | — | 已拒答 | 209 ms |
| u03 | 如何用 Kubernetes Operator 部署 seekdb？ | 资料外 | insufficient | — | — | 已拒答 | 244 ms |
