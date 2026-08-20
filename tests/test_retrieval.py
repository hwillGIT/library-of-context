from __future__ import annotations

import inspect
import math
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest.mock import patch

from context_cache.embeddings import HashingEmbedder
from context_cache.engine import ContextCache
from context_cache.models import ContextRecord, SearchHit
from context_cache.retrieval import RetrievalPolicy, rank_records


class _CountingEmbedder(HashingEmbedder):
    def __init__(self) -> None:
        super().__init__(64)
        self.calls = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return super().embed(texts)


class _FakeHotCache:
    query_ttl = 37

    def __init__(self) -> None:
        self.queries: dict[tuple[str, str], list[SearchHit]] = {}
        self.published_records: list[str] = []
        self.closed = False

    def generation(self, _namespace: str) -> int:
        return 0

    def get_query(self, namespace: str, cache_key: str) -> list[SearchHit] | None:
        return self.queries.get((namespace, cache_key))

    def put_query(self, namespace: str, cache_key: str, hits: list[SearchHit]) -> None:
        self.queries[(namespace, cache_key)] = hits

    def put_record(self, record: ContextRecord) -> None:
        self.published_records.append(record.id)

    def close(self) -> None:
        self.closed = True


def _record(
    record_id: str,
    *,
    embedding: list[float],
    importance: float,
    updated_at: float,
    accessed_at: float,
    metadata: dict[str, str] | None = None,
) -> ContextRecord:
    return ContextRecord(
        id=record_id,
        namespace="test",
        text=f"record {record_id}",
        embedding=embedding,
        metadata=dict(metadata or {}),
        importance=importance,
        updated_at=updated_at,
        accessed_at=accessed_at,
    )


class RetrievalRankingTests(unittest.TestCase):
    def test_rank_records_computes_the_hybrid_score(self) -> None:
        now = 20 * 86400.0
        aligned = _record(
            "aligned",
            embedding=[1.0, 0.0],
            importance=0.8,
            updated_at=now - 86400.0,
            accessed_at=now - 2 * 86400.0,
        )
        lexical = _record(
            "lexical",
            embedding=[0.0, 1.0],
            importance=0.4,
            updated_at=now - 4 * 86400.0,
            accessed_at=now - 3 * 86400.0,
        )
        policy = RetrievalPolicy(
            vector_weight=0.50,
            lexical_weight=0.25,
            importance_weight=0.15,
            recency_weight=0.10,
            recency_half_life_days=2.0,
        )

        hits = rank_records(
            [lexical, aligned],
            [1.0, 0.0],
            {"aligned": 0.2, "lexical": 1.0},
            filters=None,
            minimum_score=0.0,
            top_k=2,
            policy=policy,
            now=now,
        )

        expected_recency = math.exp(-math.log(2.0) * 86400.0 / (2 * 86400.0))
        expected_score = 0.50 * 1.0 + 0.25 * 0.2 + 0.15 * 0.8 + 0.10 * expected_recency
        self.assertEqual([hit.record.id for hit in hits], ["aligned", "lexical"])
        self.assertAlmostEqual(hits[0].vector_score, 1.0)
        self.assertAlmostEqual(hits[0].lexical_score, 0.2)
        self.assertAlmostEqual(hits[0].importance_score, 0.8)
        self.assertAlmostEqual(hits[0].recency_score, expected_recency)
        self.assertAlmostEqual(hits[0].score, expected_score)
        self.assertEqual(aligned.accessed_at, now - 2 * 86400.0)

    def test_filters_threshold_and_updated_at_tie_breaking(self) -> None:
        older = _record(
            "older",
            embedding=[1.0],
            importance=0.5,
            updated_at=10.0,
            accessed_at=10.0,
            metadata={"project": "a"},
        )
        newer = _record(
            "newer",
            embedding=[1.0],
            importance=0.5,
            updated_at=20.0,
            accessed_at=20.0,
            metadata={"project": "b"},
        )
        policy = RetrievalPolicy(
            vector_weight=1.0,
            lexical_weight=0.0,
            importance_weight=0.0,
            recency_weight=0.0,
        )

        hits = rank_records(
            [older, newer],
            [1.0],
            {},
            filters={"project": ["a", "b"]},
            minimum_score=1.0,
            top_k=2,
            policy=policy,
            now=30.0,
        )
        self.assertEqual([hit.record.id for hit in hits], ["newer", "older"])
        filtered = rank_records(
            [older, newer],
            [1.0],
            {},
            filters={"project": "a"},
            minimum_score=1.01,
            top_k=2,
            policy=policy,
            now=30.0,
        )
        self.assertEqual(filtered, [])


class RetrievalCacheTests(unittest.TestCase):
    def test_public_retrieve_signature_matches_contract(self) -> None:
        parameters = inspect.signature(ContextCache.retrieve).parameters
        self.assertEqual(
            list(parameters),
            [
                "self",
                "query",
                "top_k",
                "namespace",
                "filters",
                "minimum_score",
                "vector_weight",
                "lexical_weight",
                "importance_weight",
                "recency_weight",
                "recency_half_life_days",
            ],
        )
        self.assertEqual(parameters["top_k"].default, 8)
        self.assertEqual(parameters["vector_weight"].default, 0.60)
        self.assertEqual(parameters["lexical_weight"].default, 0.25)
        self.assertEqual(parameters["importance_weight"].default, 0.10)
        self.assertEqual(parameters["recency_weight"].default, 0.05)
        self.assertEqual(parameters["recency_half_life_days"].default, 14.0)
        for name in list(parameters)[2:]:
            self.assertEqual(parameters[name].kind, inspect.Parameter.KEYWORD_ONLY)

    def test_local_query_cache_skips_embedding_and_storage_lookup(self) -> None:
        embedder = _CountingEmbedder()
        with tempfile.TemporaryDirectory() as directory:
            with ContextCache(
                Path(directory) / "library.sqlite",
                redis_url="",
                embedder=embedder,
            ) as cache:
                cache.put("Alpha cache record.", record_id="alpha")
                embedder.calls = 0
                cache.ram.clear()
                with patch.object(
                    cache.store,
                    "list_records",
                    wraps=cache.store.list_records,
                ) as list_records:
                    first = cache.retrieve("alpha", top_k=3)
                    second = cache.retrieve("alpha", top_k=3)

                self.assertIs(first, second)
                self.assertEqual(embedder.calls, 1)
                list_records.assert_called_once_with("default")
                self.assertEqual(cache.query_ram.stats()["hits"], 1)
                self.assertGreaterEqual(cache.ram.stats()["items"], 1)
                stored = cache.store.get("default", "alpha")
                assert stored is not None
                self.assertEqual(stored.accessed_at, first[0].record.accessed_at)

    def test_ranking_policies_have_distinct_cache_entries(self) -> None:
        embedder = _CountingEmbedder()
        with tempfile.TemporaryDirectory() as directory:
            with ContextCache(
                Path(directory) / "library.sqlite",
                redis_url="",
                embedder=embedder,
            ) as cache:
                cache.put("Policy-specific cache record.", record_id="policy")
                embedder.calls = 0

                vector_ranked = cache.retrieve(
                    "policy cache",
                    vector_weight=1.0,
                    lexical_weight=0.0,
                    importance_weight=0.0,
                    recency_weight=0.0,
                    recency_half_life_days=1.0,
                )
                lexical_ranked = cache.retrieve(
                    "policy cache",
                    vector_weight=0.0,
                    lexical_weight=1.0,
                    importance_weight=0.0,
                    recency_weight=0.0,
                    recency_half_life_days=30.0,
                )

                self.assertIsNot(vector_ranked, lexical_ranked)
                self.assertEqual(embedder.calls, 2)
                self.assertEqual(cache.query_ram.stats()["items"], 2)
                repeated = cache.retrieve(
                    "policy cache",
                    vector_weight=0.0,
                    lexical_weight=1.0,
                    importance_weight=0.0,
                    recency_weight=0.0,
                    recency_half_life_days=30.0,
                )
                self.assertIs(repeated, lexical_ranked)
                self.assertEqual(embedder.calls, 2)

    def test_embedder_configuration_is_part_of_the_cache_identity(self) -> None:
        embedder = _CountingEmbedder()
        embedder.model = "model-a"
        embedder.base_url = "http://127.0.0.1:11434"
        with tempfile.TemporaryDirectory() as directory:
            with ContextCache(
                Path(directory) / "library.sqlite",
                redis_url="",
                embedder=embedder,
            ) as cache:
                cache.put("Configured embedder record.", record_id="configured")
                embedder.calls = 0
                first = cache.retrieve("configured embedder")
                embedder.model = "model-b"
                second = cache.retrieve("configured embedder")

                self.assertIsNot(first, second)
                self.assertEqual(embedder.calls, 2)
                self.assertEqual(cache.query_ram.stats()["items"], 2)

    def test_shared_query_cache_is_used_for_lookup_and_publication(self) -> None:
        embedder = _CountingEmbedder()
        hot_cache = _FakeHotCache()
        with tempfile.TemporaryDirectory() as directory:
            with ContextCache(
                Path(directory) / "library.sqlite",
                redis_url="",
                embedder=embedder,
            ) as cache:
                cache.put("Shared cache record.", record_id="shared")
                cache.redis = hot_cache  # type: ignore[assignment]
                embedder.calls = 0

                first = cache.retrieve("shared cache", top_k=2)
                self.assertEqual(embedder.calls, 1)
                self.assertEqual(hot_cache.published_records, ["shared"])
                self.assertEqual(len(hot_cache.queries), 1)

                cache.query_ram.clear()
                second = cache.retrieve("shared cache", top_k=2)
                self.assertIs(second, first)
                self.assertEqual(embedder.calls, 1)
                self.assertEqual(cache.query_ram.stats()["items"], 1)
        self.assertTrue(hot_cache.closed)

    def test_empty_results_are_published_to_the_query_cache(self) -> None:
        embedder = _CountingEmbedder()
        with tempfile.TemporaryDirectory() as directory:
            with ContextCache(
                Path(directory) / "library.sqlite",
                redis_url="",
                embedder=embedder,
            ) as cache:
                cache.put("Only stored record.", record_id="record")
                embedder.calls = 0
                first = cache.retrieve("missing", minimum_score=2.0)
                second = cache.retrieve("missing", minimum_score=2.0)

                self.assertIs(first, second)
                self.assertEqual(first, [])
                self.assertEqual(embedder.calls, 1)
                self.assertEqual(cache.query_ram.stats()["items"], 1)
                self.assertEqual(cache.query_ram.stats()["hits"], 1)


if __name__ == "__main__":
    unittest.main()
