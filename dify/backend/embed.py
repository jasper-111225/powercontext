"""OpenAI 兼容的向量化客户端。

默认指向本机 Ollama（`ollama pull qwen3-embedding:0.6b`），
也可以换成任何提供 `/v1/embeddings` 的服务（如 Xinference、vLLM、云厂商）。
"""

from __future__ import annotations

import httpx

from .config import Settings


class EmbeddingError(RuntimeError):
    """向量化服务不可用或返回不合法。"""


class Embedder:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # Ollama 原生 /api/embed 支持 keep_alive=0：用完立即卸载，给 7B 模型腾内存
        self._native = settings.embed_native
        base = settings.embed_base_url
        if self._native and base.endswith("/v1"):
            base = base[:-3]
        self._client = httpx.Client(
            base_url=base,
            timeout=settings.embed_timeout,
            headers={
                "Authorization": f"Bearer {settings.embed_api_key}",
                "Content-Type": "application/json",
            },
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._native:
            payload = {
                "model": self.settings.embed_model,
                "input": texts,
                "keep_alive": 0,
            }
            endpoint = "/api/embed"
        else:
            payload = {"model": self.settings.embed_model, "input": texts}
            endpoint = "/embeddings"
        try:
            resp = self._client.post(endpoint, json=payload)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise EmbeddingError(
                f"向量化请求失败（{self.settings.embed_base_url}，{'/api/embed' if self._native else '/embeddings'}，"
                f"模型 {self.settings.embed_model}）：{exc}"
            ) from exc
        except ValueError as exc:
            raise EmbeddingError(f"向量化服务返回的不是 JSON：{exc}") from exc

        if self._native:
            vectors = data.get("embeddings")
            if not isinstance(vectors, list) or len(vectors) != len(texts):
                raise EmbeddingError(f"Ollama /api/embed 返回条数不匹配：{data!r:.300}")
            result: list[list[float]] = []
            for vector in vectors:
                if not isinstance(vector, list) or len(vector) != self.settings.embed_dim:
                    raise EmbeddingError(
                        f"向量维度不匹配：期望 {self.settings.embed_dim}，实际 {len(vector)}"
                    )
                result.append([float(x) for x in vector])
            return result

        items = data.get("data")
        if not isinstance(items, list) or len(items) != len(texts):
            raise EmbeddingError(f"向量化返回条数不匹配：{data!r:.300}")

        vectors: list[list[float]] = []
        for item in sorted(items, key=lambda x: x.get("index", 0)):
            vector = item.get("embedding")
            if not isinstance(vector, list) or not vector:
                raise EmbeddingError("向量化返回缺少 embedding 字段")
            if len(vector) != self.settings.embed_dim:
                raise EmbeddingError(
                    f"向量维度不匹配：期望 {self.settings.embed_dim}，实际 {len(vector)}。"
                    " 请检查 ECHO_EMBED_MODEL / ECHO_EMBED_DIM 是否一致。"
                )
            vectors.append([float(x) for x in vector])
        return vectors

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    def close(self) -> None:
        self._client.close()
