from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest import mock

from context_cache.embeddings import HashingEmbedder, estimate_tokens
from context_cache.indexing import OutboxIndexer
from context_cache.models import ContextEvent, ContextRecord, SearchHit
from context_cache.rings import RecentEventRing
from context_cache.scheduler import DeskScheduler
from context_cache.scopes import ContextScope, ThreadKey
from context_cache.store import OutboxLeaseLost
from context_cache.swapper import ContextSwapper
from library_of_context import LibraryOfContext, RuntimeSettings


class _BarrierEmbedder:
    dimensions = 32

    def __init__(self, parties: int = 2) -> None:
        self._barrier = threading.Barrier(parties)
        self._delegate = HashingEmbedder(self.dimensions)

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._barrier.wait(timeout=2.0)
        return self._delegate.embed(texts)


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


class _InterleavingCache:
    namespace = "default"
    redis = None

    def __init__(self) -> None:
        self.slow_started = threading.Event()
        self.release_slow = threading.Event()

    @staticmethod
    def _hit(focus: str) -> SearchHit:
        record = ContextRecord(
            id=f"record-{focus}",
            namespace="default",
            text=f"context for {focus}",
            embedding=[],
            token_count=4,
        )
        return SearchHit(record, 1.0, 1.0, 0.0, 0.5, 0.5)

    def retrieve(self, focus: str, **_: object) -> list[SearchHit]:
        if focus == "slow-old":
            self.slow_started.set()
            if not self.release_slow.wait(2.0):
                raise TimeoutError("slow retrieval was not released")
        return [self._hit(focus)]

    def get(self, *_: object, **__: object) -> ContextRecord | None:
        return None


class _StaleHotCache:
    query_ttl = 60

    def __init__(self, stale: ContextRecord) -> None:
        self.record = stale
        self.deleted: list[tuple[str, str]] = []

    def get_record(self, _namespace: str, _record_id: str) -> ContextRecord | None:
        return self.record

    def put_record(self, record: ContextRecord) -> None:
        self.record = record

    def delete_record(self, namespace: str, record_id: str) -> None:
        self.deleted.append((namespace, record_id))
        self.record = None  # type: ignore[assignment]

    def close(self) -> None:
        pass


class _GateLock:
    def __init__(self, *, thread_name: str, gate_at_entry: int) -> None:
        self._lock = threading.RLock()
        self._thread_name = thread_name
        self._gate_at_entry = gate_at_entry
        self._entries = 0
        self.blocked = threading.Event()
        self.release = threading.Event()

    def __enter__(self) -> _GateLock:
        if threading.current_thread().name == self._thread_name:
            self._entries += 1
            if self._entries == self._gate_at_entry:
                self.blocked.set()
                if not self.release.wait(2.0):
                    raise TimeoutError("gated lock acquisition was not released")
        self._lock.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self._lock.release()


def _event(sequence: int, *, content: str | None = None) -> ContextEvent:
    text = content or f"event {sequence}"
    return ContextEvent(
        event_id=f"event-{sequence}",
        namespace="default",
        session_id="thread",
        sequence=sequence,
        role="user",
        content=text,
        token_count=estimate_tokens(text),
        record_id=f"record-{sequence}",
        created_at=float(sequence),
    )


def _advance_scheduler(
    scheduler: DeskScheduler,
    clock: _ManualClock,
    seconds: float,
) -> None:
    clock.advance(seconds)
    with scheduler._condition:
        scheduler._condition.notify_all()


class RecordConcurrencyContractTests(unittest.TestCase):
    def test_conflicting_visibility_writes_have_one_atomic_winner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
                embedder=_BarrierEmbedder(),
            ) as library:

                def write(
                    scope: ContextScope,
                ) -> ContextRecord | Exception:
                    try:
                        return library.shelve(
                            f"{scope.value} value",
                            book_id="shared-id",
                            scope=scope,
                            owner_session_id=(
                                "private-thread"
                                if scope is ContextScope.THREAD
                                else None
                            ),
                        )
                    except Exception as exc:
                        return exc

                with ThreadPoolExecutor(max_workers=2) as executor:
                    outcomes = list(
                        executor.map(
                            write,
                            (ContextScope.PROJECT, ContextScope.THREAD),
                        )
                    )

                winners = [item for item in outcomes if isinstance(item, ContextRecord)]
                failures = [item for item in outcomes if isinstance(item, Exception)]
                self.assertEqual(len(winners), 1)
                self.assertEqual(len(failures), 1)
                self.assertIsInstance(failures[0], ValueError)
                stored = library.store.get("default", "shared-id")
                self.assertIsNotNone(stored)
                assert stored is not None
                self.assertEqual(stored.scope, winners[0].scope)
                self.assertEqual(stored.owner_session_id, winners[0].owner_session_id)
                self.assertEqual(stored.text, winners[0].text)

    def test_concurrent_same_boundary_writes_leave_cache_equal_to_storage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
                embedder=_BarrierEmbedder(),
            ) as library:

                def write(text: str) -> ContextRecord:
                    return library.shelve(text, book_id="shared-id")

                with ThreadPoolExecutor(max_workers=2) as executor:
                    returned = list(executor.map(write, ("alpha value", "beta value")))

                stored = library.store.get("default", "shared-id")
                cached = library.ram.get(library._ram_key("default", "shared-id"))
                visible = library.get("shared-id")
                self.assertIsNotNone(stored)
                self.assertIsNotNone(cached)
                self.assertIsNotNone(visible)
                assert stored is not None and cached is not None and visible is not None
                self.assertIn(stored.text, {record.text for record in returned})
                self.assertEqual(cached.text, stored.text)
                self.assertEqual(cached.content_hash, stored.content_hash)
                self.assertEqual(visible.text, stored.text)
                self.assertEqual(visible.content_hash, stored.content_hash)


class IsolationAndExpiryContractTests(unittest.TestCase):
    def test_public_records_hits_and_working_sets_are_deeply_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            ) as library:
                returned = library.shelve(
                    "mutation isolation sentinel",
                    book_id="isolated",
                    catalog={"nested": {"labels": ["original"]}},
                )
                with self.assertRaises(FrozenInstanceError):
                    returned.text = "replacement"  # type: ignore[misc]
                returned.embedding.append(99.0)
                returned.metadata["nested"]["labels"].append("caller")

                fetched = library.get("isolated")
                self.assertIsNotNone(fetched)
                assert fetched is not None
                self.assertEqual(fetched.metadata["nested"]["labels"], ["original"])
                self.assertNotIn(99.0, fetched.embedding)
                fetched.metadata["nested"]["labels"].append("get-caller")
                fetched.embedding.clear()

                hits = library.retrieve("mutation isolation sentinel")
                self.assertEqual([hit.record.id for hit in hits], ["isolated"])
                hits[0].record.metadata["nested"]["labels"].append("hit-caller")
                hits[0].record.embedding.clear()
                hits[0].score = -1.0
                hits.clear()

                cached_hits = library.retrieve("mutation isolation sentinel")
                self.assertEqual([hit.record.id for hit in cached_hits], ["isolated"])
                self.assertEqual(
                    cached_hits[0].record.metadata["nested"]["labels"],
                    ["original"],
                )
                self.assertTrue(cached_hits[0].record.embedding)
                self.assertGreaterEqual(cached_hits[0].score, 0.0)

                working = library.runtime.swapper.refresh(
                    "thread",
                    "mutation isolation sentinel",
                    token_budget=100,
                )
                working.focus = "caller focus"
                working.hits[0].record.metadata["nested"]["labels"].append(
                    "desk-caller"
                )
                working.hits.clear()
                working.swapped_in.clear()

                cached_working = library.runtime.swapper.get("thread")
                self.assertIsNotNone(cached_working)
                assert cached_working is not None
                self.assertEqual(cached_working.focus, "mutation isolation sentinel")
                self.assertEqual(
                    [hit.record.id for hit in cached_working.hits], ["isolated"]
                )
                self.assertEqual(
                    cached_working.hits[0].record.metadata["nested"]["labels"],
                    ["original"],
                )
                self.assertEqual(cached_working.swapped_in, ["isolated"])

    def test_source_replacement_is_limited_to_one_visibility_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            ) as library:
                project = library.shelve_document(
                    "shared source text",
                    source="shared-source",
                )[0]
                alpha = library.shelve_document(
                    "shared source text",
                    source="shared-source",
                    scope=ContextScope.THREAD,
                    owner_session_id="alpha",
                )[0]
                beta = library.shelve_document(
                    "shared source text",
                    source="shared-source",
                    scope=ContextScope.THREAD,
                    owner_session_id="beta",
                )[0]
                self.assertEqual(len({project.id, alpha.id, beta.id}), 3)

                replacement = library.shelve_document(
                    "alpha replacement text",
                    source="shared-source",
                    replace_edition=True,
                    scope=ContextScope.THREAD,
                    owner_session_id="alpha",
                )[0]

                self.assertIsNotNone(library.store.get("default", project.id))
                self.assertIsNone(library.store.get("default", alpha.id))
                self.assertIsNotNone(library.store.get("default", beta.id))
                self.assertIsNotNone(library.store.get("default", replacement.id))
                alpha_records = library.source_records(
                    "shared-source",
                    scopes=(ContextScope.THREAD,),
                    session_id="alpha",
                )
                self.assertEqual(
                    [record.id for record in alpha_records], [replacement.id]
                )

    def test_returned_event_mutation_cannot_change_durable_or_recent_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            ) as library:
                governor = library.open_context_governor(
                    "thread",
                    start_worker=False,
                )
                returned = governor.record(
                    "developer",
                    "immutable event content",
                    metadata={"nested": {"labels": ["original"]}},
                    event_id="event",
                )

                with self.assertRaises(FrozenInstanceError):
                    returned.content = "caller replacement"  # type: ignore[misc]
                with self.assertRaises(FrozenInstanceError):
                    returned.protected = False  # type: ignore[misc]
                returned.metadata["nested"]["labels"].append("caller")

                durable = library.store.get_thread_event(
                    "default",
                    "thread",
                    "event",
                )
                self.assertIsNotNone(durable)
                assert durable is not None
                self.assertEqual(durable.content, "immutable event content")
                self.assertTrue(durable.protected)
                self.assertEqual(durable.metadata["nested"]["labels"], ["original"])
                with library.runtime.thread_states.lease(governor._key) as state:
                    recent = state.recent.snapshot()[0]
                self.assertEqual(recent.content, "immutable event content")
                self.assertTrue(recent.protected)
                self.assertEqual(recent.metadata["nested"]["labels"], ["original"])

    def test_expired_query_and_reading_desk_entries_are_not_returned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            ) as library:
                record = library.shelve(
                    "short lived query sentinel",
                    book_id="expiring",
                    shelf_life_seconds=0.1,
                )
                self.assertEqual(
                    [
                        hit.record.id
                        for hit in library.retrieve("short lived query sentinel")
                    ],
                    [record.id],
                )
                working = library.runtime.swapper.refresh(
                    "thread",
                    "short lived query sentinel",
                    token_budget=100,
                )
                self.assertEqual([hit.record.id for hit in working.hits], [record.id])

                assert record.expires_at is not None
                time.sleep(max(0.0, record.expires_at - time.time()) + 0.03)

                self.assertEqual(library.retrieve("short lived query sentinel"), [])
                self.assertIsNone(library.runtime.swapper.get("thread"))


class ReadingDeskOrderingContractTests(unittest.TestCase):
    def test_slow_older_on_demand_refresh_cannot_replace_current_focus(self) -> None:
        cache = _InterleavingCache()
        swapper = ContextSwapper(cache)  # type: ignore[arg-type]
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                slow = executor.submit(swapper.refresh, "thread", "slow-old")
                self.assertTrue(cache.slow_started.wait(2.0))
                current = executor.submit(
                    swapper.refresh,
                    "thread",
                    "fast-current",
                ).result(timeout=2.0)
                cache.release_slow.set()
                slow.result(timeout=2.0)

            stored = swapper.get("thread")
            self.assertIsNotNone(stored)
            assert stored is not None
            self.assertEqual(stored.focus, "fast-current")
            self.assertEqual(stored, current)
        finally:
            cache.release_slow.set()
            swapper.close()

    def test_generation_check_and_store_are_one_atomic_publication_step(self) -> None:
        cache = _InterleavingCache()
        swapper = ContextSwapper(cache)  # type: ignore[arg-type]
        gate = _GateLock(thread_name="old-refresh", gate_at_entry=4)
        swapper._lock = gate  # type: ignore[assignment]
        old_error: list[BaseException] = []

        def old_refresh() -> None:
            try:
                swapper.refresh("thread", "old-focus")
            except BaseException as exc:
                old_error.append(exc)

        old = threading.Thread(target=old_refresh, name="old-refresh")
        old.start()
        try:
            self.assertTrue(gate.blocked.wait(2.0))
            current = swapper.refresh("thread", "current-focus")
            gate.release.set()
            old.join(2.0)

            self.assertFalse(old.is_alive())
            self.assertEqual(old_error, [])
            stored = swapper.get("thread")
            self.assertIsNotNone(stored)
            assert stored is not None
            self.assertEqual(stored.focus, "current-focus")
            self.assertEqual(stored, current)
        finally:
            gate.release.set()
            old.join(2.0)
            swapper.close()


class RingAndSchedulerContractTests(unittest.TestCase):
    def test_recent_ring_projects_oversized_events_within_both_limits(self) -> None:
        content = "oversized-event " * 1000
        durable = _event(1, content=content)
        ring = RecentEventRing(max_events=2, max_tokens=64)

        ring.append(durable)

        snapshot = ring.snapshot()
        self.assertEqual(len(snapshot), 1)
        self.assertNotEqual(snapshot[0].content, content)
        self.assertEqual(durable.content, content)
        self.assertLessEqual(ring.stats()["events"], 2)
        self.assertLessEqual(ring.stats()["estimated_tokens"], 64)

    def test_old_idempotent_retry_cannot_reenter_the_recent_tail(self) -> None:
        ring = RecentEventRing(max_events=2, max_tokens=1024)
        first = _event(1)
        ring.append(first)
        ring.append(_event(2))
        ring.append(_event(3))

        ring.append(first)

        self.assertEqual(
            [event.sequence for event in ring.snapshot()],
            [2, 3],
        )

    def test_schedule_stop_churn_retains_only_bounded_live_state(self) -> None:
        scheduler = DeskScheduler(worker_count=1, max_tasks=1, jitter_ratio=0.0)
        try:
            for index in range(10_000):
                key = ThreadKey("project", f"thread-{index}")
                scheduler.schedule(
                    key,
                    interval_seconds=3600.0,
                    callback=lambda _generation: None,
                )
                self.assertTrue(scheduler.stop(key))

            self.assertEqual(scheduler.status(), [])
            self.assertEqual(len(scheduler._tasks), 0)
            self.assertEqual(len(scheduler._inflight), 0)
            self.assertLessEqual(scheduler._work.qsize(), scheduler.worker_count)
            self.assertFalse(hasattr(scheduler, "_generations"))
        finally:
            scheduler.close()

    def test_blocked_worker_does_not_create_an_unbounded_submission_queue(self) -> None:
        clock = _ManualClock()
        scheduler = DeskScheduler(
            worker_count=1,
            max_tasks=64,
            jitter_ratio=0.0,
            clock=clock,
        )
        started = threading.Event()
        release = threading.Event()

        def blocked(_generation: int) -> None:
            started.set()
            if not release.wait(2.0):
                raise TimeoutError("blocked scheduler callback was not released")

        try:
            for index in range(32):
                scheduler.schedule(
                    ThreadKey("project", f"thread-{index}"),
                    interval_seconds=1.0,
                    callback=blocked,
                )
            _advance_scheduler(scheduler, clock, 1.0)
            self.assertTrue(started.wait(2.0))
            with scheduler._condition:
                self.assertLessEqual(len(scheduler._inflight), 1)
                self.assertLessEqual(
                    scheduler._work.qsize(),
                    scheduler._work.maxsize,
                )
        finally:
            release.set()
            scheduler.close()

    def test_close_returns_within_the_declared_timeout_for_a_hung_callback(
        self,
    ) -> None:
        clock = _ManualClock()
        scheduler = DeskScheduler(
            worker_count=1,
            jitter_ratio=0.0,
            shutdown_timeout_seconds=0.05,
            clock=clock,
        )
        started = threading.Event()
        release = threading.Event()

        def blocked(_generation: int) -> None:
            started.set()
            release.wait()

        scheduler.schedule(
            ThreadKey("project", "thread"),
            interval_seconds=1.0,
            callback=blocked,
        )
        _advance_scheduler(scheduler, clock, 1.0)
        self.assertTrue(started.wait(2.0))
        try:
            before = time.monotonic()
            scheduler.close()
            elapsed = time.monotonic() - before
            self.assertLess(elapsed, 0.5)
        finally:
            release.set()
            for worker in scheduler._workers:
                worker.join(2.0)


class RecoveryAndOwnershipContractTests(unittest.TestCase):
    def test_stale_hot_record_is_rejected_against_the_sqlite_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            library = LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            )
            try:
                durable = library.shelve(
                    "authoritative value",
                    book_id="record",
                    catalog={"source": "sqlite"},
                )
                stale = replace(
                    durable,
                    text="stale value",
                    content_hash="stale-hash",
                    metadata={"source": "hot-cache"},
                )
                hot = _StaleHotCache(stale)
                library.redis = hot  # type: ignore[assignment]
                library.ram.clear()

                recovered = library.get("record")

                self.assertIsNotNone(recovered)
                assert recovered is not None
                self.assertEqual(recovered.text, "authoritative value")
                self.assertEqual(recovered.metadata, {"source": "sqlite"})
                self.assertEqual(hot.deleted, [("default", "record")])
            finally:
                library.close()

    def test_database_creation_supports_a_missing_nested_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "storage" / "library.sqlite"
            with LibraryOfContext(path, redis_url="") as library:
                library.shelve("nested parent", book_id="record")
                self.assertTrue(path.exists())

    def test_second_process_cannot_open_an_owned_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "library.sqlite"
            with LibraryOfContext(path, redis_url=""):
                program = "\n".join(
                    (
                        "from context_cache import ContextCache",
                        "try:",
                        f"    ContextCache({str(path)!r}, redis_url='')",
                        "except RuntimeError as exc:",
                        "    raise SystemExit(0 if 'owns database' in str(exc) else 2)",
                        "raise SystemExit(3)",
                    )
                )
                completed = subprocess.run(
                    [sys.executable, "-c", program],
                    cwd=Path.cwd(),
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                    check=False,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stdout + completed.stderr,
                )

    def test_transient_outbox_scan_failure_recovers_without_worker_restart(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            ) as library:
                library.runtime.indexer.close()
                library.store.append_thread_event(
                    namespace="default",
                    session_id="thread",
                    event_id="event",
                    role="user",
                    content="recoverable outbox event",
                    metadata={},
                    importance=0.5,
                    protected=False,
                    token_count=4,
                    record_id="record",
                    created_at=1.0,
                )
                indexer = OutboxIndexer(
                    library,
                    capacity=4,
                    poll_seconds=0.01,
                    worker_count=1,
                )
                original = library.store.claim_outbox_events
                calls = 0

                def flaky_claim(*args: object, **kwargs: object) -> object:
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        raise RuntimeError("transient scan failure")
                    return original(*args, **kwargs)

                try:
                    with mock.patch.object(
                        library.store,
                        "claim_outbox_events",
                        side_effect=flaky_claim,
                    ):
                        indexer.start(name="recovery-indexer")
                        deadline = time.monotonic() + 2.0
                        while (
                            library.store.get("default", "record") is None
                            and time.monotonic() < deadline
                        ):
                            time.sleep(0.01)

                    stored = library.store.get("default", "record")
                    self.assertIsNotNone(stored)
                    self.assertGreaterEqual(calls, 2)
                    self.assertTrue(indexer.is_alive)
                    self.assertIsNone(indexer.last_error)
                finally:
                    indexer.close()

    def test_terminal_event_allows_later_progress_and_explicit_retry(self) -> None:
        class SelectiveEmbedder:
            dimensions = 32

            def __init__(self) -> None:
                self.reject_poison = True
                self._delegate = HashingEmbedder(self.dimensions)

            def embed(self, texts: list[str]) -> list[list[float]]:
                if self.reject_poison and any("poison" in text for text in texts):
                    raise RuntimeError("poison event rejected")
                return self._delegate.embed(texts)

        embedder = SelectiveEmbedder()
        settings = RuntimeSettings(
            outbox_workers=1,
            outbox_poll_seconds=0.01,
            outbox_max_attempts=1,
        )
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
                embedder=embedder,
                runtime_settings=settings,
            ) as library:
                governor = library.open_context_governor("thread")
                poison = governor.record(
                    "user",
                    "poison event",
                    event_id="poison",
                )
                later = governor.record(
                    "user",
                    "later valid event",
                    event_id="later",
                )
                deadline = time.monotonic() + 2.0
                while (
                    library.store.get("default", later.record_id) is None
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)

                self.assertIsNotNone(library.store.get("default", later.record_id))
                self.assertIsNone(library.store.get("default", poison.record_id))
                self.assertEqual(
                    governor.status()["watermarks"]["failed_events"],
                    1,
                )
                self.assertFalse(governor.flush(timeout=0.1))

                embedder.reject_poison = False
                self.assertTrue(governor.retry_failed(poison.event_id))
                self.assertTrue(governor.flush(timeout=2.0))
                self.assertIsNotNone(library.store.get("default", poison.record_id))
                watermarks = governor.status()["watermarks"]
                self.assertEqual(watermarks["pending_events"], 0)
                self.assertEqual(watermarks["failed_events"], 0)
                self.assertEqual(watermarks["indexed_through"], 2)

    def test_replacement_owner_reclaims_an_expired_lease_without_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            ) as library:
                library.runtime.indexer.close()
                library.store.append_thread_event(
                    namespace="default",
                    session_id="thread",
                    event_id="event",
                    role="user",
                    content="lease reclaim",
                    metadata={},
                    importance=0.5,
                    protected=False,
                    token_count=2,
                    record_id="record",
                    created_at=1.0,
                )
                with mock.patch("context_cache.store.time.time", return_value=100.0):
                    first = library.store.claim_outbox_events(
                        "first-owner",
                        limit=1,
                        lease_seconds=1.0,
                    )[0]
                with mock.patch("context_cache.store.time.time", return_value=102.0):
                    second = library.store.claim_outbox_events(
                        "second-owner",
                        limit=1,
                        lease_seconds=1.0,
                    )[0]

                self.assertEqual(second.event_id, first.event_id)
                self.assertNotEqual(second.claim_token, first.claim_token)
                with self.assertRaises(OutboxLeaseLost):
                    library.store.mark_thread_event_indexed(
                        first.namespace,
                        first.session_id,
                        first.event_id,
                        indexed_at=102.0,
                        claim_token=first.claim_token,
                    )
                with self.assertRaises(OutboxLeaseLost):
                    library.store.fail_outbox_event(
                        first.namespace,
                        first.session_id,
                        first.event_id,
                        error="stale worker failure",
                        retry_after_seconds=1.0,
                        claim_token=first.claim_token,
                    )

    def test_release_updates_recent_event_and_indexed_record_protection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            ) as library:
                governor = library.open_context_governor("thread")
                event = governor.protect(
                    "retained instruction",
                    event_id="protected-event",
                )
                self.assertTrue(governor.flush(timeout=2.0))

                self.assertTrue(governor.release(event.event_id))

                durable_event = library.store.get_thread_event(
                    "default",
                    "thread",
                    event.event_id,
                )
                indexed_record = library.store.get("default", event.record_id)
                self.assertIsNotNone(durable_event)
                self.assertIsNotNone(indexed_record)
                assert durable_event is not None and indexed_record is not None
                self.assertFalse(durable_event.protected)
                self.assertIs(indexed_record.metadata["protected"], False)
                with library.runtime.thread_states.lease(governor._key) as state:
                    recent = {
                        item.event_id: item.protected
                        for item in state.recent.snapshot()
                    }
                self.assertIs(recent[event.event_id], False)

    def test_event_record_identity_is_reserved_by_the_durable_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            ) as library:
                library.runtime.indexer.close()
                governor = library.open_context_governor("thread")
                event = governor.record(
                    "user",
                    "authoritative event text",
                    event_id="reserved-record",
                )

                with self.assertRaisesRegex(ValueError, "reserved"):
                    library.shelve(
                        "caller overwrite",
                        book_id=event.record_id,
                        scope=ContextScope.THREAD,
                        owner_session_id="thread",
                    )

                durable = library.store.get_thread_event(
                    "default",
                    "thread",
                    event.event_id,
                )
                self.assertIsNotNone(durable)
                assert durable is not None
                self.assertEqual(durable.content, "authoritative event text")

    def test_thread_event_append_rejects_a_preexisting_record_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            ) as library:
                library.runtime.indexer.close()
                session_id = "collision-thread"
                event_id = "collision-event"
                record_id = hashlib.sha256(
                    f"default\x00{session_id}\x00{event_id}".encode("utf-8")
                ).hexdigest()[:24]
                library.shelve(
                    "unrelated project record",
                    book_id=record_id,
                )
                governor = library.open_context_governor(session_id)

                with self.assertRaisesRegex(ValueError, "collides"):
                    governor.record(
                        "user",
                        "event must not replace the record",
                        event_id=event_id,
                    )

                self.assertIsNone(
                    library.store.get_thread_event("default", session_id, event_id)
                )
                record = library.store.get("default", record_id)
                self.assertIsNotNone(record)
                assert record is not None
                self.assertEqual(record.text, "unrelated project record")

    def test_release_does_not_modify_an_unrelated_record_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            ) as library:
                library.runtime.indexer.close()
                unrelated = library.shelve(
                    "unrelated project record",
                    book_id="unrelated-projection",
                    catalog={"protected": "unrelated"},
                )
                governor = library.open_context_governor("thread")
                event = governor.protect(
                    "protected event",
                    event_id="protected-collision",
                )
                with library.store._lock, library.store._connection:
                    library.store._connection.execute(
                        """
                        UPDATE thread_events SET record_id = ?
                        WHERE namespace = ? AND session_id = ? AND event_id = ?
                        """,
                        (
                            unrelated.id,
                            "default",
                            "thread",
                            event.event_id,
                        ),
                    )

                self.assertTrue(governor.release(event.event_id))
                record = library.store.get("default", unrelated.id)
                self.assertIsNotNone(record)
                assert record is not None
                self.assertEqual(record.metadata["protected"], "unrelated")

    def test_release_before_index_completion_controls_derived_protection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            ) as library:
                governor = library.open_context_governor(
                    "thread",
                    start_worker=False,
                )
                event = governor.protect(
                    "retained until released",
                    event_id="release-before-completion",
                )
                claim = governor._indexer.queue.get(timeout=1.0)
                self.assertTrue(event.protected)
                self.assertTrue(governor.release(event.event_id))
                try:
                    governor._indexer._index(event, claim)
                finally:
                    governor._indexer.queue.complete(claim)

                durable_event = library.store.get_thread_event(
                    "default",
                    "thread",
                    event.event_id,
                )
                indexed_record = library.store.get("default", event.record_id)
                assert durable_event is not None and indexed_record is not None
                self.assertFalse(durable_event.protected)
                self.assertIs(indexed_record.metadata["protected"], False)


if __name__ == "__main__":
    unittest.main()
