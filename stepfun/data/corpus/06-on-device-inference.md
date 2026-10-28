# 浏览器端侧推理

端侧推理指模型在用户设备上执行，输入数据不离开设备。对知识库应用来说，这意味着用户导入的文档只在本地被编码，不会上传到云端；即使应用部署在公网，推理仍然发生在浏览器里。

在浏览器里跑 Transformer 模型有两种主流方案。Transformers.js 提供与 Python transformers 类似的 pipeline API，封装了分词、模型加载、池化和归一化；ONNX Runtime Web 更底层，直接执行 ONNX 计算图。Transformers.js 内部就用 ONNX Runtime Web 作为执行引擎，所以两者可以混用：用 Transformers.js 处理文本，用 ORT 的 session options 调优执行后端。

执行后端主要有 WebGPU 和 WASM。WebGPU 把矩阵运算交给 GPU，适合较大的 batch；WASM 用 CPU 执行，兼容性最好，但吞吐较低。正确的策略是“优先 WebGPU，失败自动降级 WASM”，并把当前后端明确展示给用户，而不是静默切换。

模型文件通过 fetch 加载，ONNX 权重通常有几十 MB。加载时间包括网络传输、解析计算图和初始化执行后端。首次加载明显慢于后续加载，因此性能报告要区分首次与预热后。浏览器 HTTP 缓存、Cache Storage 和 Service Worker 都可以减少重复下载，但要注意模型更新后的缓存失效。

推理时的批大小需要权衡。批越大，单条平均耗时越低，但首条结果的等待时间越长，显存或内存占用也越高。交互式应用通常用 8 到 16 的小批量，并在导入时按批推进进度条；查询时 batch 固定为 1，优先保证低延迟。

线程数也会影响 WASM 性能。多线程 WASM 需要 SharedArrayBuffer，而 SharedArrayBuffer 要求页面开启跨源隔离，即响应头包含 `Cross-Origin-Opener-Policy: same-origin` 和 `Cross-Origin-Embedder-Policy: require-corp`。不开这两个头，ORT 只能单线程运行。开启后还要确认所有静态资源都带正确的 CORP 头，否则会被浏览器拦截。

最后是降级路径的验证。只在支持 WebGPU 的机器上测试是不够的，必须在不支持 WebGPU 的环境里跑一遍完整流程：模型能加载、向量能生成、检索能返回结果、界面明确提示当前使用 WASM。
