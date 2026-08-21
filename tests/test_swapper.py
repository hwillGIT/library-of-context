from __future__ import annotations

import threading
import unittest

from context_cache.embeddings import estimate_tokens
from context_cache.models import ContextRecord, SearchHit, WorkingSet
from context_cache.scopes import ContextScope
from context_cache.swapper import ContextSwapper


def _record(record_id: str, text: str = "fixture context") -> ContextRecord:
    return ContextRecord(
        id=record_id,
        namespace="project",
        text=text,
        embedding=[1.0],
        source="fixture",
    )


def _hit(record_id: str, score: float, text: str = "fixture context") -> SearchHit:
    return SearchHit(
        record=_record(record_id, text),
        score=score,
        vector_score=score,
        lexical_score=0.0,
        importance_score=0.5,
        recency_score=0.5,
    )


class _CacheStub:
    namespace = "project"
    redis = None

    def __init__(
        self,
        hits: list[SearchHit],
        records: dict[str, ContextRecord] | None = None,
    ) -> None:
        self.hits = hits
        self.records = records or {}
        self.retrieve_calls: list[tuple[str, int, str, dict[str, object] | None]] = []
        self.get_calls: list[tuple[str, str]] = []

    def retrieve(
        self,
        focus: str,
        *,
        top_k: int,
        namespace: str,
        filters: dict[str, object] | None,
        scopes: list[ContextScope],
        session_id: str,
        team_ids: tuple[str, ...],
    ) -> list[SearchHit]:
        self.assert_scope = (scopes, session_id, team_ids)
        self.retrieve_calls.append((focus, top_k, namespace, filters))
        return list(self.hits)

    def get(
        self,
        record_id: str,
        *,
        namespace: str,
        scopes: list[ContextScope],
        session_id: str,
        team_ids: tuple[str, ...],
    ) -> ContextRecord | None:
        self.assert_get_scope = (scopes, session_id, team_ids)
        self.get_calls.append((record_id, namespace))
        return self.records.get(record_id)


class _GateRedis:
    def __init__(self, working_set: WorkingSet) -> None:
        self.value = WorkingSet.from_dict(working_set.to_dict())
        self.first_get_entered = threading.Event()
        self.release_first_get = threading.Event()
        self._lock = threading.Lock()
        self._get_count = 0

    def get_working_set(self, _namespace: str, _session_id: str) -> WorkingSet:
        with self._lock:
            self._get_count += 1
            first = self._get_count == 1
            snapshot = WorkingSet.from_dict(self.value.to_dict())
        if first:
            self.first_get_entered.set()
            if not self.release_first_get.wait(2.0):
                raise TimeoutError("Redis snapshot read was not released")
        return snapshot

    def put_working_set(self, working_set: WorkingSet) -> None:
        with self._lock:
            self.value = WorkingSet.from_dict(working_set.to_dict())


class ContextSwapperTests(unittest.TestCase):
    def test_redis_hydration_cannot_replace_a_newer_local_snapshot(self) -> None:
        old = WorkingSet(
            session_id="thread",
            namespace="project",
            focus="old",
            hits=[_hit("old", 0.5)],
            context="old context",
            token_count=3,
            token_budget=100,
            refreshed_at=1.0,
        )
        cache = _CacheStub([_hit("current", 1.0)])
        redis = _GateRedis(old)
        cache.redis = redis
        swapper = ContextSwapper(cache)  # type: ignore[arg-type]
        hydrated: list[WorkingSet | None] = []

        reader = threading.Thread(target=lambda: hydrated.append(swapper.get("thread")))
        reader.start()
        self.assertTrue(redis.first_get_entered.wait(2.0))
        current = swapper.refresh(
            "thread",
            "current",
            token_budget=100,
            top_k=1,
        )
        redis.release_first_get.set()
        reader.join(2.0)

        self.assertFalse(reader.is_alive())
        self.assertEqual(hydrated[0].focus, "current")
        self.assertEqual(swapper.get("thread"), current)
        self.assertEqual(redis.value.focus, "current")
        swapper.close()

    def test_retrieval_orders_pins_then_ranked_hits_and_applies_exclusions(
        self,
    ) -> None:
        cache = _CacheStub(
            [_hit("ranked-pin", 0.8), _hit("excluded", 0.7), _hit("ranked", 0.6)],
            {"fetched-pin": _record("fetched-pin")},
        )
        swapper = ContextSwapper(cache)  # type: ignore[arg-type]

        ordered = swapper._retrieve_and_order_hits(
            "topic",
            session_id="thread",
            namespace="project",
            top_k=2,
            filters={"kind": "fact"},
            pinned_record_ids=["fetched-pin", "ranked-pin"],
            exclude_record_ids=["excluded", "fetched-pin"],
            team_ids=(),
        )

        self.assertEqual(
            [hit.record.id for hit in ordered],
            ["fetched-pin", "ranked-pin", "ranked"],
        )
        self.assertEqual(ordered[0].score, 1.0)
        self.assertEqual(
            cache.retrieve_calls,
            [("topic", 6, "project", {"kind": "fact"})],
        )
        self.assertEqual(cache.get_calls, [("fetched-pin", "project")])
        self.assertEqual(cache.assert_scope[1:], ("thread", ()))
        self.assertEqual(cache.assert_get_scope[1:], ("thread", ()))

    def test_candidate_expansion_respects_the_public_result_bound(self) -> None:
        cache = _CacheStub([])
        swapper = ContextSwapper(cache)  # type: ignore[arg-type]

        for requested in (34, 100):
            with self.subTest(requested=requested):
                swapper._retrieve_and_order_hits(
                    "topic",
                    session_id="thread",
                    namespace="project",
                    top_k=requested,
                    filters=None,
                    pinned_record_ids=None,
                    exclude_record_ids=None,
                    team_ids=(),
                )

        self.assertEqual(
            [call[1] for call in cache.retrieve_calls],
            [100, 100],
        )
        swapper.close()

    def test_packing_preserves_order_limit_and_estimated_token_budget(self) -> None:
        ordered = [
            _hit("first", 0.9, "x" * 400),
            _hit("second", 0.8, "second context"),
        ]

        selected, context = ContextSwapper._pack_hits(
            ordered,
            token_budget=32,
            top_k=2,
        )

        self.assertEqual([hit.record.id for hit in selected], ["first"])
        self.assertIn('id="first"', context)
        self.assertNotIn('id="second"', context)
        self.assertLessEqual(estimate_tokens(context), 32)

        selected, _ = ContextSwapper._pack_hits(
            ordered,
            token_budget=200,
            top_k=1,
        )
        self.assertEqual([hit.record.id for hit in selected], ["first"])

    def test_swap_diff_preserves_previous_and_selected_order(self) -> None:
        previous = WorkingSet(
            session_id="thread",
            namespace="project",
            focus="old",
            hits=[_hit("a", 0.9), _hit("b", 0.8), _hit("c", 0.7)],
            context="",
            token_count=0,
            token_budget=100,
            refreshed_at=0.0,
        )

        swapped_in, swapped_out, retained = ContextSwapper._swap_diff(
            previous,
            [_hit("c", 0.9), _hit("b", 0.8), _hit("d", 0.7)],
        )

        self.assertEqual(swapped_in, ["d"])
        self.assertEqual(swapped_out, ["a"])
        self.assertEqual(retained, ["c", "b"])

    def test_refresh_returns_working_set_payload_and_swap_state(self) -> None:
        cache = _CacheStub([_hit("a", 0.9), _hit("b", 0.8)])
        swapper = ContextSwapper(cache)  # type: ignore[arg-type]

        first = swapper.refresh("thread", "alpha", token_budget=100, top_k=2)
        cache.hits = [_hit("b", 0.9), _hit("c", 0.8)]
        second = swapper.refresh("thread", "beta", token_budget=100, top_k=2)

        self.assertEqual(first.swapped_in, ["a", "b"])
        self.assertEqual(first.swapped_out, [])
        self.assertEqual(first.retained, [])
        self.assertEqual(second.swapped_in, ["c"])
        self.assertEqual(second.swapped_out, ["a"])
        self.assertEqual(second.retained, ["b"])
        self.assertEqual(second.session_id, "thread")
        self.assertEqual(second.namespace, "project")
        self.assertEqual(second.focus, "beta")
        self.assertEqual(second.token_count, estimate_tokens(second.context))
        self.assertEqual(swapper.get("thread"), second)
        self.assertIsNot(swapper.get("thread"), second)


if __name__ == "__main__":
    unittest.main()
