from __future__ import annotations

import threading
import unittest
from collections import Counter
from types import SimpleNamespace

from context_cache.models import ContextEvent
from context_cache.scopes import ThreadKey
from context_cache.thread_state import ThreadCapacityError, ThreadStateRegistry


class _ManualClock:
    def __init__(self) -> None:
        self._value = 0.0
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._value

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._value += seconds


class _EventStore:
    def __init__(self) -> None:
        self.events: dict[ThreadKey, list[ContextEvent]] = {}
        self.loads: Counter[ThreadKey] = Counter()

    def append(self, event: ContextEvent) -> None:
        key = ThreadKey(event.namespace, event.session_id)
        self.events.setdefault(key, []).append(event)

    def list_thread_events(
        self,
        namespace: str,
        session_id: str,
        *,
        limit: int | None = None,
    ) -> list[ContextEvent]:
        key = ThreadKey(namespace, session_id)
        self.loads[key] += 1
        events = list(self.events.get(key, []))
        if limit is None:
            return events
        return events[-limit:] if limit else []


def _event(key: ThreadKey, sequence: int) -> ContextEvent:
    return ContextEvent(
        event_id=f"event-{sequence}",
        namespace=key.collection,
        session_id=key.session_id,
        sequence=sequence,
        role="user",
        content=f"message {sequence}",
        token_count=1,
        record_id=f"record-{sequence}",
        created_at=float(sequence),
    )


def _registry(
    store: _EventStore,
    *,
    max_entries: int,
    idle_ttl_seconds: float = 60.0,
    recent_events: int = 8,
    clock: _ManualClock | None = None,
) -> ThreadStateRegistry:
    return ThreadStateRegistry(
        SimpleNamespace(store=store),
        max_entries=max_entries,
        idle_ttl_seconds=idle_ttl_seconds,
        recent_events=recent_events,
        recent_tokens=256,
        clock=clock or _ManualClock(),
    )


class ThreadStateRegistryTests(unittest.TestCase):
    def test_capacity_evicts_the_least_recently_used_idle_state(self) -> None:
        store = _EventStore()
        registry = _registry(store, max_entries=2)
        key_a = ThreadKey("project", "a")
        key_b = ThreadKey("project", "b")
        key_c = ThreadKey("project", "c")

        with registry.lease(key_a) as state_a:
            first_a = state_a
        with registry.lease(key_b) as state_b:
            first_b = state_b
        with registry.lease(key_a) as state_a:
            self.assertIs(state_a, first_a)
        with registry.lease(key_c):
            pass
        with registry.lease(key_b) as state_b:
            self.assertIsNot(state_b, first_b)

        self.assertEqual(store.loads[key_a], 1)
        self.assertEqual(store.loads[key_b], 2)
        self.assertEqual(registry.stats()["active"], 2)

    def test_idle_ttl_expires_state_at_the_declared_boundary(self) -> None:
        store = _EventStore()
        clock = _ManualClock()
        registry = _registry(
            store,
            max_entries=2,
            idle_ttl_seconds=5.0,
            clock=clock,
        )
        key = ThreadKey("project", "thread")

        with registry.lease(key) as state:
            first = state
        clock.advance(5.0)
        with registry.lease(key) as state:
            self.assertIsNot(state, first)

        self.assertEqual(store.loads[key], 2)
        self.assertEqual(registry.stats()["active"], 1)

    def test_leased_state_cannot_be_expired_or_displaced(self) -> None:
        store = _EventStore()
        clock = _ManualClock()
        registry = _registry(
            store,
            max_entries=1,
            idle_ttl_seconds=1.0,
            clock=clock,
        )
        held = ThreadKey("project", "held")
        waiting = ThreadKey("project", "waiting")

        with registry.lease(held):
            clock.advance(100.0)
            with self.assertRaises(ThreadCapacityError):
                with registry.lease(waiting):
                    self.fail("capacity admission must not evict a leased state")
            self.assertEqual(registry.stats()["leased"], 1)

        clock.advance(1.0)
        with registry.lease(waiting):
            pass
        self.assertEqual(registry.stats()["active"], 1)

    def test_evicted_state_rehydrates_the_recent_ring_from_durable_events(self) -> None:
        store = _EventStore()
        registry = _registry(store, max_entries=1, recent_events=2)
        key = ThreadKey("project", "thread")
        other = ThreadKey("project", "other")
        store.append(_event(key, 1))
        store.append(_event(key, 2))

        with registry.lease(key) as state:
            first = state
            self.assertEqual(
                [event.event_id for event in state.recent.snapshot()],
                ["event-1", "event-2"],
            )
        with registry.lease(other):
            pass
        store.append(_event(key, 3))

        with registry.lease(key) as state:
            self.assertIsNot(state, first)
            self.assertEqual(
                [event.event_id for event in state.recent.snapshot()],
                ["event-2", "event-3"],
            )

        self.assertEqual(store.loads[key], 2)

    def test_operation_lock_serializes_handles_for_the_same_thread(self) -> None:
        store = _EventStore()
        registry = _registry(store, max_entries=1)
        key = ThreadKey("project", "thread")
        first_entered = threading.Event()
        second_attempting = threading.Event()
        second_entered = threading.Event()
        release_first = threading.Event()
        failures: list[BaseException] = []
        state_ids: list[int] = []
        order: list[str] = []

        def first_handle() -> None:
            try:
                with registry.lease(key) as state:
                    state_ids.append(id(state))
                    with state.operation_lock:
                        order.append("first")
                        first_entered.set()
                        if not release_first.wait(2.0):
                            raise TimeoutError("first handle was not released")
            except BaseException as exc:
                failures.append(exc)

        def second_handle() -> None:
            try:
                if not first_entered.wait(2.0):
                    raise TimeoutError(
                        "first handle did not acquire the operation lock"
                    )
                with registry.lease(key) as state:
                    state_ids.append(id(state))
                    second_attempting.set()
                    with state.operation_lock:
                        order.append("second")
                        second_entered.set()
            except BaseException as exc:
                failures.append(exc)

        first_thread = threading.Thread(target=first_handle)
        second_thread = threading.Thread(target=second_handle)
        first_thread.start()
        second_thread.start()
        self.assertTrue(first_entered.wait(2.0))
        self.assertTrue(second_attempting.wait(2.0))
        self.assertEqual(order, ["first"])
        self.assertFalse(second_entered.is_set())
        self.assertEqual(registry.stats()["leased"], 2)

        release_first.set()
        first_thread.join(2.0)
        second_thread.join(2.0)

        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(order, ["first", "second"])
        self.assertEqual(len(set(state_ids)), 1)

    def test_ten_thousand_thread_churn_remains_within_capacity(self) -> None:
        store = _EventStore()
        registry = _registry(store, max_entries=37)

        for index in range(10_000):
            with registry.lease(ThreadKey("project", f"thread-{index}")):
                pass

        self.assertEqual(registry.stats()["active"], 37)
        self.assertEqual(sum(store.loads.values()), 10_000)

        newest = ThreadKey("project", "thread-9999")
        with registry.lease(newest):
            pass
        self.assertEqual(store.loads[newest], 1)
        self.assertEqual(registry.stats()["active"], 37)


if __name__ == "__main__":
    unittest.main()
