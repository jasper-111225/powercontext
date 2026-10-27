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

"""HTTP cross-encoder reranking for coarse Memory search candidates."""

from __future__ import annotations

import math
from typing import TypeAlias

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, ValidationError

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage

HTTP_RERANK_POLICY_ID = "http-cross-encoder-v1"

ScoreValue: TypeAlias = StrictInt | StrictFloat


class _ScoreEntry(BaseModel):
    """One service score bound to its global candidate rank."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rank: StrictInt = Field(ge=1)
    score: ScoreValue


class _ScoresResponse(BaseModel):
    """The only accepted response body: an exact score list."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scores: tuple[_ScoreEntry, ...]


class HTTPMemoryReranker:
    """Score coarse candidates through one batched HTTP cross-encoder service."""

    policy_id: str

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = HTTP_RERANK_POLICY_ID,
    ) -> None:
        if batch_size < 1:
            raise ValueError("reranker batch_size must be positive")  # noqa: TRY003
        if not endpoint:
            raise ValueError("reranker endpoint must not be empty")  # noqa: TRY003
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Score every candidate once and select the globally best ``limit`` ranks."""

        _validate_bounds(len(candidates), limit)
        scores: dict[int, float] = {}
        requests = 0
        for start in range(0, len(candidates), self._batch_size):
            batch = candidates[start : start + self._batch_size]
            ranks = tuple(range(start + 1, start + 1 + len(batch)))
            response = await self._client.post(
                self._endpoint,
                json={
                    "query": query,
                    "candidates": [
                        {"rank": rank, "text": candidate.text} for rank, candidate in zip(ranks, batch, strict=True)
                    ],
                },
            )
            requests += 1
            response.raise_for_status()
            scores.update(_parse_scores(response, ranks))
        ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
        return MemoryRerankDecision(
            selected_ranks=tuple(rank for rank, _score in ordered),
            usage=InferenceUsage(requests=requests),
        )


def _parse_scores(response: httpx.Response, expected_ranks: tuple[int, ...]) -> dict[int, float]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("reranker response is not valid JSON") from exc  # noqa: TRY003
    try:
        parsed = _ScoresResponse.model_validate(payload)
    except ValidationError as exc:
        raise ValueError("reranker response does not match the score schema") from exc  # noqa: TRY003

    scores: dict[int, float] = {}
    for entry in parsed.scores:
        score = float(entry.score)
        if not math.isfinite(score):
            raise ValueError("reranker score must be finite")  # noqa: TRY003
        if entry.rank in scores:
            raise ValueError(f"reranker score repeats rank {entry.rank}")  # noqa: TRY003
        scores[entry.rank] = score
    if set(scores) != set(expected_ranks):
        raise ValueError("reranker scores must cover every batch rank exactly once")  # noqa: TRY003
    return scores


def _validate_bounds(candidate_count: int, limit: int) -> None:
    if candidate_count < 1:
        raise ValueError("rerank requires at least one candidate")  # noqa: TRY003
    if limit < 1 or limit > candidate_count:
        raise ValueError("rerank limit must be between one and the candidate count")  # noqa: TRY003


__all__ = ["HTTP_RERANK_POLICY_ID", "HTTPMemoryReranker"]
