from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Callable, Sequence

import httpx

from .config import Settings, openai_url


class EmbeddingError(RuntimeError):
    pass


class Embedder:
    """Ollama OpenAI-compatible embeddings with an on-disk cache."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.model = settings.embed_model
        self.dim = settings.embed_dim
        self._client = client
        self._lock = threading.Lock()
        self._cache: dict[str, list[float]] = {}
        self._cache_loaded = False

    @property
    def name(self) -> str:
        return self.model

    def _load_cache(self) -> None:
        if self._cache_loaded:
            return
        self._cache_loaded = True
        path = self.settings.embed_cache_path
        if not path.exists():
            # 仓库内置一份演示语料的向量缓存，首次 clone 无需等 CPU embedding；
            # 新导入的文本仍会实时调用模型并写回 .runtime/embed_cache.json。
            bundled = self.settings.root / "data" / "embed_cache.json"
            if bundled.exists():
                path = bundled
            else:
                return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if data.get("model") != self.model:
            return
        vectors = data.get("vectors")
        if isinstance(vectors, dict):
            self._cache = {
                str(key): [float(value) for value in vector]
                for key, vector in vectors.items()
                if isinstance(vector, list) and len(vector) == self.dim
            }

    def _save_cache(self) -> None:
        path = self.settings.embed_cache_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"model": self.model, "dim": self.dim, "vectors": self._cache}
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def _key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model}\0{text}".encode("utf-8")).hexdigest()

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def _post_batch(self, texts: Sequence[str]) -> list[list[float]]:
        url = openai_url(self.settings.embed_base, "embeddings")
        payload = {"model": self.model, "input": list(texts)}
        try:
            if self._client is not None:
                response = self._client.post(url, json=payload, headers=self._headers())
            else:
                with httpx.Client(timeout=self.settings.embed_timeout) as client:
                    response = client.post(url, json=payload, headers=self._headers())
            response.raise_for_status()
            body = response.json()
            data = sorted(body.get("data", []), key=lambda item: item.get("index", 0))
            if len(data) != len(texts):
                raise EmbeddingError(
                    f"向量模型返回 {len(data)} 条结果，期望 {len(texts)} 条"
                )
            vectors: list[list[float]] = []
            for item in data:
                vector = item.get("embedding")
                if not isinstance(vector, list):
                    raise EmbeddingError("向量模型返回的 embedding 不是数组")
                if len(vector) != self.dim:
                    raise EmbeddingError(
                        f"向量维度为 {len(vector)}，配置 JUDIAN_EMBED_DIM={self.dim}；"
                        "请换回原模型或修改维度后执行 seed.py --reset"
                    )
                vectors.append([float(value) for value in vector])
            return vectors
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(
                f"调用向量模型失败（{self.model} @ {self.settings.embed_base}）: {exc}"
            ) from exc

    def embed_texts(
        self,
        texts: Sequence[str],
        progress: Callable[[int, int], None] | None = None,
    ) -> list[list[float]]:
        self._load_cache()
        results: list[list[float] | None] = [None] * len(texts)
        pending: list[tuple[int, str, str]] = []

        with self._lock:
            for index, text in enumerate(texts):
                key = self._key(text)
                cached = self._cache.get(key)
                if cached is not None:
                    results[index] = cached
                else:
                    pending.append((index, text, key))

        total = len(texts)
        done = total - len(pending)
        if progress:
            progress(done, total)

        batch_size = max(1, self.settings.embed_batch)
        for start in range(0, len(pending), batch_size):
            batch = pending[start : start + batch_size]
            vectors = self._post_batch([item[1] for item in batch])
            with self._lock:
                for (index, _, key), vector in zip(batch, vectors):
                    self._cache[key] = vector
                    results[index] = vector
            done += len(batch)
            if progress:
                progress(done, total)

        if not all(result is not None for result in results):
            raise EmbeddingError("向量化结果不完整")
        with self._lock:
            self._save_cache()
        return [result for result in results if result is not None]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    def available(self) -> tuple[bool, str]:
        base = self.settings.embed_base.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        urls = [f"{base}/api/tags", openai_url(self.settings.embed_base, "models")]
        last_error = ""
        for url in urls:
            try:
                if self._client is not None:
                    response = self._client.get(url, timeout=2.0)
                else:
                    response = httpx.get(url, timeout=2.0)
                if response.status_code >= 500:
                    last_error = f"HTTP {response.status_code}"
                    continue
                if response.status_code >= 400:
                    last_error = f"HTTP {response.status_code}"
                    continue
                if url.endswith("/api/tags"):
                    models = response.json().get("models", [])
                    names = {str(item.get("name", "")) for item in models if isinstance(item, dict)}
                    if any(name == self.model or name.startswith(self.model + ":") for name in names):
                        return True, f"已安装 {self.model}"
                    return False, f"Ollama 可达，但未找到模型 {self.model}"
                return True, f"Ollama 可达"
            except Exception as exc:
                last_error = str(exc)
        return False, f"向量模型不可用: {last_error}"


def clean_cache(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
