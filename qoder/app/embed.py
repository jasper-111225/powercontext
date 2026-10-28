"""向量化：调用 Ollama 的 OpenAI 兼容 /v1/embeddings，带磁盘缓存。

缓存的目的很实际：重新灌库 / 改 chunk 策略时，没变的文本不会重复算。
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Callable, Iterable

import httpx

from .config import settings


class EmbedError(RuntimeError):
    pass


class Embedder:
    def __init__(self, s=settings) -> None:
        self.s = s
        self._cache: dict[str, list[float]] = {}
        self._dirty = 0
        self._load_cache()

    # ---------------------------------------------------------------- cache
    def _key(self, text: str) -> str:
        digest = hashlib.sha1()
        digest.update(self.s.embed_model.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(text.encode("utf-8"))
        return digest.hexdigest()

    def _load_cache(self) -> None:
        path: Path = self.s.embed_cache
        if not path.exists():
            return
        try:
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    vector = row.get("v")
                    key = row.get("k")
                    if key and isinstance(vector, list) and len(vector) == self.s.embed_dim:
                        self._cache[key] = vector
        except OSError:
            pass

    def _append_cache(self, rows: list[dict]) -> None:
        if not rows:
            return
        self.s.embed_cache.parent.mkdir(parents=True, exist_ok=True)
        with self.s.embed_cache.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps({"k": row["k"], "v": row["v"]}, ensure_ascii=False))
                fh.write("\n")
        self._dirty = 0

    # ----------------------------------------------------------------- call
    def _request(self, texts: list[str]) -> list[list[float]]:
        payload = {"model": self.s.embed_model, "input": texts}
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=180.0) as client:
                    response = client.post(f"{self.s.embed_base}/embeddings", json=payload)
                    response.raise_for_status()
                    data = response.json()
                vectors = [item["embedding"] for item in data["data"]]
                if len(vectors) != len(texts):
                    raise EmbedError(f"向量条数不匹配：{len(vectors)} != {len(texts)}")
                for vector in vectors:
                    if len(vector) != self.s.embed_dim:
                        raise EmbedError(
                            f"向量维度 {len(vector)} 与配置 JUDIAN_EMBED_DIM={self.s.embed_dim} 不一致"
                        )
                return vectors
            except Exception as exc:  # noqa: BLE001 - 统一重试
                last_error = exc
                time.sleep(1.5 * (attempt + 1))
        raise EmbedError(
            f"调用向量模型失败（{self.s.embed_base}/embeddings, model={self.s.embed_model}）：{last_error}"
        )

    def embed(
        self,
        texts: Iterable[str],
        *,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[list[float]]:
        items = [text if text.strip() else "（空）" for text in texts]
        keys = [self._key(text) for text in items]
        missing: list[tuple[int, str]] = [
            (i, text) for i, (text, key) in enumerate(zip(items, keys)) if key not in self._cache
        ]

        total_missing = len(missing)
        done = 0
        for start in range(0, total_missing, self.s.embed_batch):
            batch = missing[start : start + self.s.embed_batch]
            vectors = self._request([text for _, text in batch])
            rows = []
            for (index, text), key, vector in zip(batch, [keys[i] for i, _ in batch], vectors):
                self._cache[key] = vector
                rows.append({"k": key, "v": vector})
                done += 1
                if progress:
                    progress(done, total_missing)
            self._append_cache(rows)
        return [self._cache[key] for key in keys]

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    @property
    def cached(self) -> int:
        return len(self._cache)
