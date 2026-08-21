from __future__ import annotations

import threading
import unittest
from unittest import mock

from context_cache.models import ContextRecord, SearchHit
from context_cache.scheduler import DeskScheduler
from context_cache.scopes import ThreadKey
from context_cache.swapper import ContextSwapper


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


class _FakeCache:
    namespace = "project"
    redis = None

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._queries: list[str] = []
        self.stale_started = threading.Event()
        self.release_stale = threading.Event()

    def retrieve(self, query: str, **_: object) -> list[SearchHit]:
        with self._condition:
            self._queries.append(query)
            self._condition.notify_all()
        if query == "stale":
            self.stale_started.set()
            if not self.release_stale.wait(2.0):
                raise TimeoutError("stale retrieval was not released")
        record = ContextRecord(
            id=f"record-{query}",
            namespace=self.namespace,
            text=f"context for {query}",
            embedding=[],
            token_count=4,
        )
        return [SearchHit(record, 1.0, 1.0, 0.0, 0.5, 0.5)]

    def get(self, *_: object, **__: object) -> ContextRecord | None:
        return None

    def clear_queries(self) -> None:
        with self._condition:
            self._queries.clear()

    def wait_for_queries(self, count: int) -> bool:
        with self._condition:
            return self._condition.wait_for(
                lambda: len(self._queries) >= count,
                timeout=2.0,
            )

    def queries(self) -> list[str]:
        with self._condition:
            return list(self._queries)


def _advance(
    scheduler: DeskScheduler,
    clock: _ManualClock,
    seconds: float,
) -> None:
    clock.advance(seconds)
    with scheduler._condition:
        scheduler._condition.notify_all()


class DeskSchedulerTests(unittest.TestCase):
    def test_concurrent_start_and_stop_leave_no_periodic_callback(self) -> None:
        scheduler = DeskScheduler(worker_count=1, jitter_ratio=0.0)
        swapper = ContextSwapper(_FakeCache(), scheduler=scheduler)
        schedule_entered = threading.Event()
        release_schedule = threading.Event()
        stop_finished = threading.Event()
        errors: list[BaseException] = []
        stop_results: list[bool] = []
        original_schedule = scheduler.schedule

        def gated_schedule(*args: object, **kwargs: object) -> int:
            schedule_entered.set()
            if not release_schedule.wait(2.0):
                raise TimeoutError("periodic registration was not released")
            return original_schedule(*args, **kwargs)

        def start() -> None:
            try:
                swapper.start_periodic("thread", "focus", interval_seconds=60.0)
            except BaseException as exc:
                errors.append(exc)

        def stop() -> None:
            try:
                stop_results.append(swapper.stop_periodic("thread"))
            except BaseException as exc:
                errors.append(exc)
            finally:
                stop_finished.set()

        with mock.patch.object(scheduler, "schedule", side_effect=gated_schedule):
            starter = threading.Thread(target=start)
            stopper = threading.Thread(target=stop)
            starter.start()
            self.assertTrue(schedule_entered.wait(2.0))
            stopper.start()
            self.assertFalse(stop_finished.wait(0.05))
            release_schedule.set()
            starter.join(2.0)
            stopper.join(2.0)

        self.assertEqual(errors, [])
        self.assertEqual(stop_results, [True])
        self.assertEqual(swapper.status(), [])
        self.assertEqual(scheduler.status(), [])
        swapper.close()
        scheduler.close()

    def test_concurrent_starts_publish_one_matching_registration(self) -> None:
        scheduler = DeskScheduler(worker_count=1, jitter_ratio=0.0)
        swapper = ContextSwapper(_FakeCache(), scheduler=scheduler)
        first_schedule_entered = threading.Event()
        release_first_schedule = threading.Event()
        second_finished = threading.Event()
        errors: list[BaseException] = []
        original_schedule = scheduler.schedule
        calls = 0
        call_lock = threading.Lock()

        def gated_schedule(*args: object, **kwargs: object) -> int:
            nonlocal calls
            with call_lock:
                calls += 1
                call_number = calls
            if call_number == 1:
                first_schedule_entered.set()
                if not release_first_schedule.wait(2.0):
                    raise TimeoutError("first registration was not released")
            return original_schedule(*args, **kwargs)

        def start(focus: str, finished: threading.Event | None = None) -> None:
            try:
                swapper.start_periodic("thread", focus, interval_seconds=60.0)
            except BaseException as exc:
                errors.append(exc)
            finally:
                if finished is not None:
                    finished.set()

        with mock.patch.object(scheduler, "schedule", side_effect=gated_schedule):
            first = threading.Thread(target=start, args=("first",))
            second = threading.Thread(target=start, args=("second", second_finished))
            first.start()
            self.assertTrue(first_schedule_entered.wait(2.0))
            second.start()
            self.assertFalse(second_finished.wait(0.05))
            release_first_schedule.set()
            first.join(2.0)
            second.join(2.0)

        self.assertEqual(errors, [])
        self.assertEqual(calls, 2)
        self.assertEqual(len(scheduler.status()), 1)
        self.assertEqual(swapper.status()[0]["focus"], "second")
        swapper.close()
        scheduler.close()

    def test_fatal_callback_stops_and_degrades_the_scheduler(self) -> None:
        clock = _ManualClock()
        scheduler = DeskScheduler(worker_count=1, jitter_ratio=0.0, clock=clock)
        key = ThreadKey("project", "fatal")

        def fatal_refresh(_generation: int) -> None:
            raise SystemExit("fatal refresh")

        with mock.patch("threading.excepthook"):
            scheduler.schedule(
                key,
                interval_seconds=1.0,
                callback=fatal_refresh,
            )
            _advance(scheduler, clock, 1.0)
            worker = scheduler._workers[0]
            worker.join(2.0)

        health = scheduler.health()
        self.assertFalse(worker.is_alive())
        self.assertTrue(health["degraded"])
        self.assertEqual(health["workers_alive"], 0)
        self.assertIn("SystemExit", str(health["last_fatal_error"]))
        self.assertTrue(scheduler.status()[0]["degraded"])
        with self.assertRaisesRegex(RuntimeError, "closed"):
            scheduler.schedule(key, interval_seconds=1.0, callback=lambda _: None)
        scheduler.close()

    def test_repeated_focus_changes_coalesce_to_the_latest_refresh(self) -> None:
        clock = _ManualClock()
        scheduler = DeskScheduler(worker_count=1, jitter_ratio=0.0, clock=clock)
        cache = _FakeCache()
        swapper = ContextSwapper(cache, scheduler=scheduler)
        try:
            swapper.start_periodic("thread", "alpha", interval_seconds=10.0)
            swapper.update_focus("thread", "beta")
            swapper.update_focus("thread", "gamma")
            cache.clear_queries()

            status = scheduler.status()
            self.assertEqual(len(status), 1)
            self.assertEqual(status[0]["generation"], 3)
            _advance(scheduler, clock, 10.0)
            self.assertTrue(cache.wait_for_queries(1))
            self.assertEqual(cache.queries(), ["gamma"])
        finally:
            swapper.close()
            scheduler.close()

    def test_stale_generation_cannot_replace_a_current_snapshot(self) -> None:
        clock = _ManualClock()
        scheduler = DeskScheduler(worker_count=2, jitter_ratio=0.0, clock=clock)
        cache = _FakeCache()
        swapper = ContextSwapper(cache, scheduler=scheduler)
        key = ThreadKey("project", "thread")
        stale_finished = threading.Event()
        try:
            swapper.refresh("thread", "baseline")

            def stale_refresh(generation: int) -> None:
                swapper.refresh(
                    "thread",
                    "stale",
                    _publish_if=lambda: scheduler.is_current(key, generation),
                )
                stale_finished.set()

            stale_generation = scheduler.schedule(
                key,
                interval_seconds=1.0,
                callback=stale_refresh,
            )
            _advance(scheduler, clock, 1.0)
            self.assertTrue(cache.stale_started.wait(2.0))

            current_generation = scheduler.schedule(
                key,
                interval_seconds=1.0,
                callback=lambda _: None,
            )
            latest = swapper.refresh("thread", "current")
            self.assertFalse(scheduler.is_current(key, stale_generation))
            self.assertTrue(scheduler.is_current(key, current_generation))

            cache.release_stale.set()
            self.assertTrue(stale_finished.wait(2.0))
            self.assertEqual(swapper.get("thread"), latest)
            self.assertIsNot(swapper.get("thread"), latest)
            self.assertEqual(swapper.get("thread").focus, "current")
        finally:
            cache.release_stale.set()
            scheduler.close()

    def test_one_thread_has_at_most_one_refresh_in_flight(self) -> None:
        clock = _ManualClock()
        scheduler = DeskScheduler(worker_count=2, jitter_ratio=0.0, clock=clock)
        key = ThreadKey("project", "thread")
        first_started = threading.Event()
        second_started = threading.Event()
        release_first = threading.Event()
        counter_lock = threading.Lock()
        active = 0
        maximum_active = 0
        generations: list[int] = []

        def enter(generation: int) -> None:
            nonlocal active, maximum_active
            with counter_lock:
                active += 1
                maximum_active = max(maximum_active, active)
                generations.append(generation)

        def leave() -> None:
            nonlocal active
            with counter_lock:
                active -= 1

        def first_refresh(generation: int) -> None:
            enter(generation)
            first_started.set()
            try:
                if not release_first.wait(2.0):
                    raise TimeoutError("first refresh was not released")
            finally:
                leave()

        def second_refresh(generation: int) -> None:
            enter(generation)
            try:
                second_started.set()
            finally:
                leave()

        try:
            first_generation = scheduler.schedule(
                key,
                interval_seconds=1.0,
                callback=first_refresh,
            )
            _advance(scheduler, clock, 1.0)
            self.assertTrue(first_started.wait(2.0))

            second_generation = scheduler.schedule(
                key,
                interval_seconds=1.0,
                callback=second_refresh,
            )
            _advance(scheduler, clock, 100.0)
            self.assertFalse(second_started.is_set())
            self.assertTrue(scheduler.status()[0]["in_flight"])

            release_first.set()
            self.assertTrue(second_started.wait(2.0))
            self.assertEqual(generations, [first_generation, second_generation])
            self.assertEqual(maximum_active, 1)
        finally:
            release_first.set()
            scheduler.close()

    def test_stopping_periodic_refresh_retains_the_last_snapshot(self) -> None:
        clock = _ManualClock()
        scheduler = DeskScheduler(worker_count=1, jitter_ratio=0.0, clock=clock)
        cache = _FakeCache()
        swapper = ContextSwapper(cache, scheduler=scheduler)
        try:
            snapshot = swapper.start_periodic(
                "thread",
                "stable focus",
                interval_seconds=10.0,
            )
            self.assertTrue(swapper.stop_periodic("thread"))
            self.assertEqual(scheduler.status(), [])

            _advance(scheduler, clock, 100.0)
            self.assertEqual(cache.queries(), ["stable focus"])
            self.assertEqual(swapper.get("thread"), snapshot)
            self.assertIsNot(swapper.get("thread"), snapshot)
        finally:
            swapper.close()
            scheduler.close()

    def test_close_waits_for_active_work_and_stops_all_workers(self) -> None:
        clock = _ManualClock()
        scheduler = DeskScheduler(worker_count=1, jitter_ratio=0.0, clock=clock)
        key = ThreadKey("project", "thread")
        callback_started = threading.Event()
        release_callback = threading.Event()
        close_entered = threading.Event()
        close_finished = threading.Event()

        def callback(_: int) -> None:
            callback_started.set()
            if not release_callback.wait(2.0):
                raise TimeoutError("scheduler close did not release active work")

        def close_scheduler() -> None:
            close_entered.set()
            scheduler.close()
            close_finished.set()

        scheduler.schedule(key, interval_seconds=1.0, callback=callback)
        _advance(scheduler, clock, 1.0)
        self.assertTrue(callback_started.wait(2.0))
        workers = list(scheduler._workers)
        closer = threading.Thread(target=close_scheduler)
        closer.start()
        self.assertTrue(close_entered.wait(2.0))
        with scheduler._condition:
            self.assertTrue(
                scheduler._condition.wait_for(lambda: scheduler._stop, timeout=2.0)
            )
        self.assertFalse(close_finished.is_set())

        release_callback.set()
        closer.join(2.0)

        self.assertFalse(closer.is_alive())
        self.assertTrue(close_finished.is_set())
        self.assertIsNotNone(scheduler._coordinator)
        self.assertFalse(scheduler._coordinator.is_alive())
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        with self.assertRaises(RuntimeError):
            scheduler.schedule(key, interval_seconds=1.0, callback=lambda _: None)


if __name__ == "__main__":
    unittest.main()
