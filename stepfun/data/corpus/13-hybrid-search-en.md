# Hybrid search inside seekdb

seekdb is an AI-native database from OceanBase. It keeps relational data, vector indexes and full-text indexes in one engine, so an application can run semantic and keyword retrieval against the same table without a second datastore.

The vector column is declared as `VECTOR(n)` and indexed with HNSW. HNSW builds a multi-layer proximity graph: queries start at a sparse upper layer, greedily move toward the closest node, then descend layer by layer. Compared with a brute-force scan, it trades a small amount of recall for a large reduction in latency. The main tuning knobs are M (neighbour count), ef_construction (build-time candidate list) and ef_search (query-time candidate list).

The full-text index uses a parser. For Chinese text, the `ik` parser recognises compound words, while `ngram` splits text into fixed-length windows. The chosen parser is recorded in the table definition, for example `FULLTEXT KEY idx_fts (document) WITH PARSER ik`.

`DBMS_HYBRID_SEARCH` fuses the two retrieval paths. The caller stores a JSON search description in `search_parm`, then the function generates the SQL that runs the full-text query, the kNN query and the rank fusion. With the JavaScript SDK this is a single call:

```js
collection.hybridSearch({
  query: { whereDocument: { $contains: "vector index" }, nResults: 80 },
  knn: { queryEmbeddings: embedding, nResults: 80 },
  rank: { rrf: { rankWindowSize: 80, rankConstant: 60 } },
  nResults: 10
});
```

RRF, reciprocal rank fusion, scores each candidate as the sum of `1 / (k + rank)` across the two lists. It only uses ranks, so a cosine distance and a BM25 score never need to be normalised onto the same scale. The constant k is usually 60. A larger window gives the fusion more candidates and usually improves recall, at the cost of a little extra work.

Keeping the fusion inside the database matters for consistency. If the client fetched two lists and merged them itself, the two queries could see different snapshots, apply different filters, or paginate inconsistently. Running one database query keeps a single view of the data and lets the engine push filters down.

Index refresh controls visibility. Newly written rows go into an incremental structure and become searchable after `refresh_index()`. An import pipeline should write in batches, refresh once at the end, and only then tell the user that the new documents are searchable.

This knowledge-base demo keeps embeddings on the client: the browser encodes documents with a quantized ONNX model, sends vectors and text to a local Node service, and the service stores them in seekdb. Retrieval is one `hybridSearch` call, so the ranking shown in the UI is exactly the ranking produced by the database.
