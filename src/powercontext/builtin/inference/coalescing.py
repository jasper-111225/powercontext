# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Coalesce concurrent Embedding requests and cache successful vectors."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel

DEFAULT_MAX_BATCH_SIZE = 64
DEFAULT_COALESCE_MS = 5.0
DEFAULT_CACHE_SIZE = 1024


@dataclass(frozen=True, slots=True)
class _BatchEntry:
    """One resolved vector together with the provider batch that produced it."""

    vector: EmbeddingVector
    usage: InferenceUsage
    batch: object


class CoalescingEmbeddingModel:
    """Share in-flight text embeddings across callers and cache successful vectors."""

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = DEFAULT_MAX_BATCH_SIZE,
        coalesce_ms: float = DEFAULT_COALESCE_MS,
        cache_size: int = DEFAULT_CACHE_SIZE,
    ) -> None:
        if max_batch_size < 1:
            raise ValueError("max_batch_size must be positive")  # noqa: TRY003
        if coalesce_ms < 0:
            raise ValueError("coalesce_ms must not be negative")  # noqa: TRY003
        if cache_size < 0:
            raise ValueError("cache_size must not be negative")  # noqa: TRY003
        self._model = model
        self.profile = model.profile
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = coalesce_ms / 1000.0
        self._cache_size = cache_size
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._futures: dict[str, asyncio.Future[_BatchEntry]] = {}
        self._claimed: set[str] = set()
        self._wakeup = asyncio.Event()
        self._dispatcher: asyncio.Task[None] | None = None
        self._workers: set[asyncio.Task[None]] = set()

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Embed texts, sharing pending work with concurrent callers."""

        values = tuple(texts)
        if not values:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))

        ordered = tuple(dict.fromkeys(values))
        entries: dict[str, _BatchEntry] = {}
        waiting: list[tuple[str, asyncio.Future[_BatchEntry]]] = []
        started = False
        for text in ordered:
            cached = self._cached(text)
            if cached is not None:
                entries[text] = cached
                continue
            future = self._futures.get(text)
            if future is None:
                future = asyncio.get_running_loop().create_future()
                self._futures[text] = future
                started = True
            waiting.append((text, future))

        if started:
            self._wakeup.set()
            self._schedule()
        if waiting:
            # ``asyncio.wait`` never cancels its futures, so cancelling this caller
            # leaves work shared with other callers running.
            await asyncio.wait([future for _text, future in waiting])
        for text, future in waiting:
            if not future.done():
                raise asyncio.CancelledError
            entries[text] = future.result()

        vectors = tuple(entries[text].vector for text in values)
        return EmbeddingResult(vectors=vectors, usage=_merge_usage(entries[text] for text in ordered))

    def _cached(self, text: str) -> _BatchEntry | None:
        vector = self._cache.get(text)
        if vector is None:
            return None
        self._cache.move_to_end(text)
        return _BatchEntry(vector=vector, usage=InferenceUsage(requests=0), batch=None)

    def _schedule(self) -> None:
        if self._dispatcher is None or self._dispatcher.done():
            self._dispatcher = asyncio.create_task(self._dispatch())

    async def _dispatch(self) -> None:
        while True:
            await self._wakeup.wait()
            self._wakeup.clear()
            if self._coalesce_seconds > 0:
                await asyncio.sleep(self._coalesce_seconds)
            else:
                # Give concurrently scheduled callers a chance to join this window.
                await asyncio.sleep(0)
            batch_texts = [text for text in self._futures if text not in self._claimed]
            if not batch_texts:
                continue
            for start in range(0, len(batch_texts), self._max_batch_size):
                chunk = tuple(batch_texts[start : start + self._max_batch_size])
                for text in chunk:
                    self._claimed.add(text)
                worker = asyncio.create_task(self._run_batch(chunk))
                self._workers.add(worker)
                worker.add_done_callback(self._workers.discard)

    async def _run_batch(self, texts: tuple[str, ...]) -> None:
        batch = object()
        try:
            result = await self._model.embed(texts)
        except BaseException as error:
            for text in texts:
                self._finish(text, error=error)
            return
        if len(result.vectors) != len(texts):
            error = ValueError("embedding model returned a mismatched vector count")
            for text in texts:
                self._finish(text, error=error)
            return
        for text, vector in zip(texts, result.vectors, strict=True):
            self._store(text, vector)
            self._finish(text, entry=_BatchEntry(vector=vector, usage=result.usage, batch=batch))

    def _finish(
        self,
        text: str,
        *,
        entry: _BatchEntry | None = None,
        error: BaseException | None = None,
    ) -> None:
        self._claimed.discard(text)
        future = self._futures.pop(text, None)
        if future is None or future.done():
            return
        if error is not None:
            future.set_exception(error)
        elif entry is not None:
            future.set_result(entry)

    def _store(self, text: str, vector: EmbeddingVector) -> None:
        if self._cache_size <= 0:
            return
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)


def _merge_usage(entries) -> InferenceUsage:
    requests = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    batches: set[int] = set()
    for entry in entries:
        if entry is None or entry.batch is None or id(entry.batch) in batches:
            continue
        batches.add(id(entry.batch))
        requests += entry.usage.requests
        if entry.usage.input_tokens is not None:
            input_tokens = (input_tokens or 0) + entry.usage.input_tokens
        if entry.usage.output_tokens is not None:
            output_tokens = (output_tokens or 0) + entry.usage.output_tokens
    return InferenceUsage(requests=requests, input_tokens=input_tokens, output_tokens=output_tokens)


__all__ = ["CoalescingEmbeddingModel"]
