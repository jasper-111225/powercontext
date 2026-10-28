# seekdb-js 数据层接入

seekdb-js 是官方 Node.js 客户端，支持嵌入式和服务端两种连接方式。服务端模式只需要 host、port、user、password 和 database：

```js
import { SeekdbClient } from "seekdb";
const client = new SeekdbClient({
  host: "127.0.0.1", port: 2881, user: "root", password: "", database: "stepfun_kb"
});
```

创建集合时要显式传入 `embeddingFunction: null`。这是因为 SDK 默认会寻找名为 default-embed 的嵌入函数；本项目的向量全部在浏览器里算好，服务端不应该再加载一个模型。把维度写在 configuration 里，SDK 会据此创建 `VECTOR(512)` 列和 HNSW 索引。

写入使用 `collection.add` 或 `collection.upsert`。upsert 的语义是“存在则覆盖、不存在则插入”，非常适合增量导入：分块 ID 由文档内容哈希和序号生成，同一个文件重复导入不会产生重复数据。写入支持批量，一次几十到几百条比逐条写入快得多，因为减少了网络往返和事务开销。

`collection.refresh_index()` 是让新数据立即可检索的关键。它会把增量数据合并进索引。刷新本身有成本，所以策略上应该“批量写完再刷新”，而不是每写一条刷新一次。本项目每次导入提交后刷新一次，并在界面上单独展示写入耗时和刷新耗时。

查询接口主要有三个。`collection.query` 只做向量近邻；`collection.hybridSearch` 做全文加向量的融合；`collection.get` 按 ID 或过滤条件取回记录。所有查询都支持 `include` 参数控制返回字段，只取需要的字段可以减少序列化开销。

集合元数据保存在数据库内部的元数据表里，SDK 通过 collectionId 定位实际的数据表。数据表名通常形如 `c$v2$<uuid>`，因此不要硬编码表名；要用 DDL 证据时，可以查 information_schema 里的表名，再执行 SHOW CREATE TABLE 和 SHOW INDEX。

连接池和超时也值得注意。服务端模式底层使用 mysql2，长时间运行的 Node 进程应复用同一个 client，而不是每个请求新建连接。查询超时可以通过构造参数设置，避免慢查询把请求堆积住。
