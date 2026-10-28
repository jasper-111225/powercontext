# bge-small-zh-v1.5 模型

bge-small-zh-v1.5 是 BAAI 发布的中文小模型，属于 BGE（BAAI General Embedding）系列。它的隐藏层维度是 512，输出 512 维句向量，参数量约 2400 万，FP32 ONNX 体积约 95MB。相比 bge-base 和 bge-large，small 版本更适合端侧：推理更快、内存占用更低，检索质量在中文短文本场景仍然够用。

BGE 系列使用 CLS 池化，也就是取最后一层 [CLS] 位置的隐藏状态作为句向量，而不是对所有 token 做平均池化。Transformers.js 的 feature-extraction pipeline 支持 `pooling: "cls"`，同时用 `normalize: true` 做 L2 归一化。归一化之后，余弦相似度可以直接用内积计算，排序也更稳定。

BGE v1.5 建议给查询加指令前缀，中文的推荐前缀是“为这个句子生成表示以用于检索相关文章：”。文档侧不加前缀。这个前缀会让查询向量靠近“检索意图”的表示空间，对短查询提升明显。前缀必须在查询和文档两侧保持一致的使用方式，否则训练和推理分布不匹配。

分词器是 BERT 的 WordPiece，中文按字切分，英文按子词切分，最大长度 512。我们的分块控制在 420 字符左右，加上前缀后仍远小于 512 个 token，因此不需要截断，也不会浪费太多填充。

选择中文模型而不是 all-MiniLM-L6-v2 的原因是语料以中文为主。all-MiniLM 在英文上很强，但对中文的语义区分度有限，容易出现“看起来都差不多”的向量。bge-small-zh 在中文语义检索上的召回明显更好，同时模型体积与 MiniLM 接近，适合作为端侧默认模型。

模型来源必须写清楚：Hugging Face 上的 `Xenova/bge-small-zh-v1.5` 是官方权重的 ONNX 转换版，包含 fp32、fp16、int8 等多种精度；ModelScope 上有同一仓库的镜像。本项目从镜像下载 FP32 权重，再用 ONNX Runtime 自行量化，因此量化方法和体积数据都可以复现。
