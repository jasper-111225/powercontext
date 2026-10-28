# 任务 03 · seekdb SQL 客户端证据

- 时间：2026-10-28 19:00
- 截图：`docs/evidence/screens/qoder-03-output.png`（2880×1740，补充图；原 `qoder-03.png` 保留不动）
- seekdb：OceanBase seekdb **1.4.0.0**，`SELECT VERSION()` 返回 `5.7.25-OceanBase seekdb-v1.4.0.0`。
- 部署位置：VirtualBox Ubuntu22 虚拟机内以系统服务运行；Windows 侧通过 NAT 转发连接
  `127.0.0.1:2881`。官方 v1.4.0 没有 Windows 安装包（Windows MSI 最新为 1.3.0.0），
  所以截图里的 1.4 版本号来自实际连接结果，不是文字标注。
- 客户端：MySQL 5.7 命令行客户端
  `C:\Program Files\MySQL\MySQL Server 5.7\bin\mysql.exe`，`--default-character-set=utf8mb4`。
- 库：`judian`，9 篇资料 / 138 个片段（既有已灌库数据，未为本截图重灌）。
- 查询向量：由虚拟机内 Ollama 的 `qwen3-embedding:0.6b` 生成（1024 维）。
  为让 SQL 可读，向量写入辅助表 `judian_scratch.qvec`（id=3），检索 SQL 通过 join 引用；
  `qvec` 只用于证据复现，不参与应用逻辑。

## 复现步骤

```sql
CREATE TABLE IF NOT EXISTS judian_scratch.qvec (
  id INT PRIMARY KEY,
  question VARCHAR(255) NOT NULL,
  v VECTOR(1024) NOT NULL
);
-- v 由 http://127.0.0.1:11434/v1/embeddings（qwen3-embedding:0.6b）
-- 对问题「seekdb 怎么查看系统日志？」生成后写入
```

```bash
mysql -h 127.0.0.1 -P 2881 -u root --default-character-set=utf8mb4 \
  --table -vv < docs/evidence/logs/task-03-hybrid.sql
```

## 截图内容

1. `SELECT VERSION();` → `5.7.25-OceanBase seekdb-v1.4.0.0`
2. `SHOW CREATE TABLE judian.chunks\G`
   - `embedding VECTOR(1024)`
   - `FULLTEXT KEY ft_chunk (text) WITH PARSER ik`
   - `VECTOR KEY vi_chunk (embedding) WITH (DISTANCE=COSINE, TYPE=HNSW, LIB=VSAG, ...)`
   - `ORGANIZATION HEAP`
3. 混合检索：向量 top-30 + 全文 top-30，按 `1/(60+vec_rank) + 1/(60+ft_rank)` 计算 RRF，
   展示 cosine 相似度、全文得分、两路排名与融合分。

## 一致性核对

截图中的 5 行结果与 `app/db.py` 的 `hybrid_search`（RRF k=60）逐行一致：

| chunk | cosine | ft | vec_rank | ft_rank | rrf |
|---|---|---|---|---|---|
| 93 | 0.7646 | 6.54 | 1 | 2 | 0.0325 |
| 92 | 0.7214 | 5.85 | 4 | 3 | 0.0315 |
| 120 | 0.7553 | 5.15 | 2 | 6 | 0.0313 |
| 96 | 0.6645 | 7.20 | 9 | 1 | 0.0309 |
| 99 | 0.7006 | 5.61 | 5 | 5 | 0.0308 |

原始输出：[`task-03-hybrid.out`](task-03-hybrid.out)；
SQL：[`task-03-hybrid.sql`](task-03-hybrid.sql)。
