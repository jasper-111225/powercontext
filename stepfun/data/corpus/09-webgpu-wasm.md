# WebGPU 与 WASM 降级

WebGPU 是浏览器的新一代图形与计算接口，能把 ONNX 计算图映射到 GPU 的 compute shader。它比 WebGL 更适合通用计算，支持存储缓冲区和工作组，矩阵乘法可以并行到数千个线程。

检测 WebGPU 要分两步。第一步看 `navigator.gpu` 是否存在，第二步调用 `requestAdapter()` 获取适配器；有些环境暴露了 API 但没有可用适配器，只判断第一步会误判。适配器的 info 里可以看到 vendor、architecture 和 description，例如软件渲染器会显示 swiftshader，集成显卡会显示厂商名。把这些信息展示在界面上，有助于判断性能数据的来源。

拿到适配器不等于模型一定能跑。ONNX Runtime Web 的 WebGPU 执行器需要算子支持；如果模型里有权重为 INT8 的量化算子，GPU 可能不支持，需要回退到 CPU 或换用 FP16。另一个常见问题是 FP16 支持：Transformers.js 在 WebGPU 上加载 fp16 模型前会检查 `shader-f16` 特性，不支持就直接报错。因此“自动选择精度”要按实际特性判断，而不是假设 GPU 一定能跑 fp16。

WASM 是保底路径。ONNX Runtime Web 提供 SIMD 和多线程版本，多线程依赖 SharedArrayBuffer，而 SharedArrayBuffer 需要跨源隔离。开启跨源隔离后，`crossOriginIsolated` 为 true，WASM 线程数可以设到 4；未开启时只能单线程，吞吐大约减半。应用应把这两个状态显示出来，方便解释性能差异。

降级策略应该是链式的：先尝试 WebGPU，失败则切 WASM，而不是直接放弃。失败原因要收集起来，在控制台或界面上给出可读的提示。用户手动切换后端的能力也很重要：调试、对比性能、以及在某些驱动上 WebGPU 反而不如 WASM 时，可以强制使用 WASM。

验证降级路径需要构造无 WebGPU 的环境。最简单的办法是加一个 URL 参数，例如 `?backend=wasm`，初始化时直接跳过 WebGPU；再用无头浏览器或禁用 GPU 的浏览器打开，确认模型能加载、检索能完成、界面提示正确。只测试成功路径的降级逻辑，等于没有测试。
