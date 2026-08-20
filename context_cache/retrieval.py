from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .models import ContextRecord, SearchHit

RANKER_VERSION = "hybrid-v1"


@dataclass(frozen=True, slots=True)
class RetrievalPolicy:
    vector_weight: float = 0.60
    lexical_weight: float = 0.25
    importance_weight: float = 0.10
    recency_weight: float = 0.05
    recency_half_life_days: float = 14.0

    @property
    def total_weight(self) -> float:
        return max(
            1e-9,
            self.vector_weight
            + self.lexical_weight
            + self.importance_weight
            + self.recency_weight,
        )

    @property
    def recency_half_life_seconds(self) -> float:
        return max(1.0, self.recency_half_life_days * 86400.0)

    def cache_identity(self) -> dict[str, float]:
        return {
            "vector_weight": self.vector_weight,
            "lexical_weight": self.lexical_weight,
            "importance_weight": self.importance_weight,
            "recency_weight": self.recency_weight,
            "recency_half_life_days": self.recency_half_life_days,
        }


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True))


def _metadata_matches(
    metadata: Mapping[str, Any], filters: Mapping[str, Any] | None
) -> bool:
    if not filters:
        return True
    for key, expected in filters.items():
        actual = metadata.get(key)
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def rank_records(
    records: Sequence[ContextRecord],
    query_vector: Sequence[float],
    lexical_scores: Mapping[str, float],
    *,
    filters: Mapping[str, Any] | None,
    minimum_score: float,
    top_k: int,
    policy: RetrievalPolicy,
    now: float,
) -> list[SearchHit]:
    """Rank records without reading or mutating storage or caches."""

    hits: list[SearchHit] = []
    total_weight = policy.total_weight
    recency_half_life = policy.recency_half_life_seconds
    for record in records:
        if not _metadata_matches(record.metadata, filters):
            continue
        cosine = max(-1.0, min(1.0, _dot(query_vector, record.embedding)))
        vector_score = (cosine + 1.0) / 2.0
        lexical_score = lexical_scores.get(record.id, 0.0)
        importance_score = max(0.0, min(1.0, record.importance))
        age = max(0.0, now - max(record.updated_at, record.accessed_at))
        recency_score = math.exp(-math.log(2.0) * age / recency_half_life)
        score = (
            policy.vector_weight * vector_score
            + policy.lexical_weight * lexical_score
            + policy.importance_weight * importance_score
            + policy.recency_weight * recency_score
        ) / total_weight
        if score >= minimum_score:
            hits.append(
                SearchHit(
                    record=record,
                    score=score,
                    vector_score=vector_score,
                    lexical_score=lexical_score,
                    importance_score=importance_score,
                    recency_score=recency_score,
                )
            )
    hits.sort(key=lambda hit: (hit.score, hit.record.updated_at), reverse=True)
    return hits[:top_k]
