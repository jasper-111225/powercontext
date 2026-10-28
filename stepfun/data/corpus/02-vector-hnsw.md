# 向量检索与 HNSW 索引

向量检索的基本流程是：把文本、图片或其他对象编码成固定维度的稠密向量，查询时把查询对象编码成同维度向量，然后在向量集合里找距离最近的前 K 个结果。向量维度由 embedding 模型决定，例如 bge-small-zh-v1.5 输出 512 维，all-MiniLM-L6-v2 输出 384 维。

seekdb 用 `VECTOR(n)` 类型保存向量，n 是维度。建表时可以声明向量索引，例如：

```sql
CREATE TABLE chunks (
  id VARCHAR(64) PRIMARY KEY,
  document TEXT,
  embedding VECTOR(512),
  metadata JSON,
  VECTOR KEY idx_vec (embedding) WITH (DISTANCE=COSINE, TYPE=HNSW)
);
```

HNSW 是分层可导航小世界图（Hierarchical Navigable Small World）。它把向量组织成多层图：顶层稀疏、底层稠密。查询从顶层的一个入口点开始做贪心搜索，找到局部最近点后下降到下一层继续搜索，直到最底层得到候选集合。相比全量扫描，HNSW 用少量召回损失换来了数量级的延迟下降。

影响 HNSW 的关键参数包括 M（每个节点的邻居数）、ef_construction（建索引时的候选队列长度）和 ef_search（查询时的候选队列长度）。M 越大，图连接越密，召回越高但内存和构建时间也越大；ef_search 越大，查询越准但越慢。实际调优时先在固定数据集上测 Recall@K，再逐步降低 ef_search，找到质量与延迟的平衡点。

距离度量要和 embedding 的训练方式匹配。bge 系列和大多数句向量模型使用余弦相似度，因此建索引时选择 COSINE。若模型输出已经归一化，余弦相似度与内积排序等价，但显式声明 COSINE 更不容易出错。l2 适合未归一化的特征向量。

向量索引是异步维护的。新写入的行先进入增量结构，只有执行刷新（refresh index）后才会合并进可检索的索引。很多“写完立刻查不到”的问题都源于忽略了这一步。应用应在一次导入结束后显式刷新，或者按固定时间窗口批量刷新，兼顾写入吞吐和查询可见性。

在 seekdb-js 中，`collection.query({ queryEmbeddings, nResults })` 会执行向量近邻查询，返回 ids、documents、metadatas 和 distances。distances 的含义取决于距离度量：cosine 距离越小越相似，换算成相似度为 `1 - distance`。
