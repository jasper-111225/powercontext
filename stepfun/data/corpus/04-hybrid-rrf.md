# 混合检索与 RRF 融合

混合检索把向量近邻和全文匹配两路结果合并成一个排名。常见的融合方式有两种：加权分数融合和排名融合。加权分数融合需要把余弦距离和 BM25 分数映射到同一量纲，调参成本高；排名融合只使用名次，天然不受分数量纲影响，其中 RRF（Reciprocal Rank Fusion，倒数排名融合）最常用。

RRF 的公式是：对每个候选文档，累加每一路里的 `1 / (k + rank)`，其中 rank 从 1 开始，k 是平滑常数，通常取 60。两路都排在前面的文档得分最高；只在一路出现的文档也能获得基础分。k 越大，排名差异被抹得越平；k 越小，头部结果的优势越明显。

在 seekdb 中，混合检索由数据库函数 `DBMS_HYBRID_SEARCH` 完成。调用方把查询描述写进会话变量 `search_parm`，再用 `GET_SQL` 生成实际查询语句。search_parm 里包含 query（全文条件）、knn（向量条件）、rank（融合方式）和 size（返回条数）。数据库在一次查询里完成两路召回与融合，客户端拿到的已经是最终排名。

使用 seekdb-js 时不需要手写这些 SQL，调用：

```js
collection.hybridSearch({
  query: { whereDocument: { $contains: "混合检索" }, nResults: 120 },
  knn: { queryEmbeddings: vector, nResults: 120 },
  rank: { rrf: { rankWindowSize: 120, rankConstant: 60 } },
  nResults: 10,
  include: ["documents", "metadatas", "distances"]
});
```

`rankWindowSize` 决定每一路进入融合的候选数量，它应该大于最终返回条数，否则融合空间太小。经验上取 `max(60, topK * 8)`。`rankConstant` 对应公式里的 k。

为什么强调“在数据库内融合”？如果客户端分别取两路结果再自己拼合，会引入三个问题：两路的分页和过滤条件容易不一致；候选集大小受网络往返限制；无法利用数据库的过滤下推。把融合放进数据库，才能保证同一事务视图、同一过滤条件和稳定的排名。

评估混合检索时，要分别记录向量路、全文路和融合后的指标。融合通常能提升 Recall@K 和 nDCG，但如果两路质量差距过大，融合也可能被弱的一路拖累，这时应调整候选窗口或降低弱路的权重。
