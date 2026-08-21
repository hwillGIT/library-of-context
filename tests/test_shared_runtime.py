from __future__ import annotations

import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from context_cache.indexing import OutboxIndexer
from context_cache.scopes import ThreadKey
from context_cache.store import OutboxLeaseLost
from library_of_context import LibraryOfContext, RuntimeSettings


def _close_within(library: LibraryOfContext, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if library.close():
            return True
        time.sleep(0.01)
    return library.close()


class SharedRuntimeTests(unittest.TestCase):
    def test_attached_redis_close_failure_is_retried_before_completion(self) -> None:
        class FailingHotCache:
            def __init__(self) -> None:
                self.close_calls = 0
                self._lock = threading.Lock()

            def close(self) -> None:
                with self._lock:
                    self.close_calls += 1
                    if self.close_calls == 1:
                        raise OSError("injected Redis close failure")

        with tempfile.TemporaryDirectory() as directory:
            library = LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            )
            hot_cache = FailingHotCache()
            library.redis = hot_cache  # type: ignore[assignment]

            self.assertFalse(library.close())
            self.assertEqual(library._lifecycle, "closing")
            self.assertIn("redis", library._resource_close_errors)
            self.assertTrue(_close_within(library))
            self.assertEqual(hot_cache.close_calls, 2)

    def test_sqlite_close_failure_retains_ownership_until_retry_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "library.sqlite"
            library = LibraryOfContext(path, redis_url="")
            release_close = threading.Event()
            original_close = library.store.close

            def controlled_close() -> None:
                if not release_close.is_set():
                    raise OSError("injected SQLite close failure")
                original_close()

            with mock.patch.object(
                library.store, "close", side_effect=controlled_close
            ):
                self.assertFalse(library.close())
                self.assertEqual(library._lifecycle, "closing")
                self.assertIn("sqlite", library._resource_close_errors)
                with self.assertRaisesRegex(RuntimeError, "closing"):
                    library.shelve("must not be admitted")
                with self.assertRaisesRegex(RuntimeError, "another Library runtime"):
                    LibraryOfContext(path, redis_url="")

                release_close.set()
                self.assertTrue(_close_within(library))

            with LibraryOfContext(path, redis_url="") as replacement:
                self.assertEqual(replacement.stats()["lifecycle"], "open")

    def test_fatal_indexer_does_not_change_durable_commit_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            library = LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            )
            governor = library.open_context_governor("fatal-commit")
            try:
                with (
                    mock.patch.object(
                        library.runtime.indexer,
                        "_index",
                        side_effect=SystemExit("fatal indexing"),
                    ),
                    mock.patch("threading.excepthook"),
                ):
                    first = governor.record(
                        "user",
                        "first durable turn",
                        event_id="first",
                    )
                    worker = library.runtime.indexer._threads[0]
                    worker.join(2.0)

                self.assertFalse(worker.is_alive())
                second = governor.record(
                    "user",
                    "second durable turn",
                    event_id="second",
                )
                retried = governor.record(
                    "user",
                    "second durable turn",
                    event_id="second",
                )

                self.assertEqual(first.event_id, "first")
                self.assertEqual(second.sequence, retried.sequence)
                self.assertEqual(
                    library.store.count_thread_events("default", "fatal-commit"),
                    2,
                )
                self.assertGreater(
                    library.store.thread_watermarks(
                        "default", "fatal-commit"
                    ).pending_events,
                    0,
                )
                self.assertFalse(governor.flush(timeout=0.01))
                replacement_claims = library.store.claim_outbox_events(
                    "replacement-owner",
                    limit=1,
                    lease_seconds=30.0,
                )
                self.assertEqual(len(replacement_claims), 1)
                self.assertEqual(replacement_claims[0].event_id, "first")
            finally:
                governor.close()
                library.close()

    def test_runtime_close_continues_after_a_component_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            library = LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            )
            runtime = library.runtime
            with mock.patch.object(
                library.store,
                "release_outbox_leases",
                side_effect=OSError("lease release failed"),
            ):
                self.assertFalse(runtime.close())

            self.assertFalse(runtime.scheduler.health()["started"])
            self.assertIn("indexer", runtime.status()["close_errors"])
            self.assertTrue(runtime.close())
            self.assertTrue(_close_within(library))

    def test_owned_indexing_can_finish_while_public_admission_is_closing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            library = LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            )
            try:
                event = library.store.append_thread_event(
                    namespace="default",
                    session_id="closing-index",
                    event_id="event",
                    role="user",
                    content="owned indexing remains admitted",
                    metadata={},
                    importance=0.5,
                    protected=False,
                    token_count=4,
                    record_id="closing-index-record",
                    created_at=1.0,
                )
                claim = library.store.claim_outbox_events(
                    library.runtime.indexer.owner_id,
                    limit=1,
                    lease_seconds=30.0,
                )[0]
                with library._lock:
                    library._lifecycle = "closing"
                try:
                    library.runtime.indexer._index(event, claim)
                finally:
                    with library._lock:
                        library._lifecycle = "open"

                self.assertEqual(
                    library.store.thread_watermarks(
                        "default", "closing-index"
                    ).pending_events,
                    0,
                )
                self.assertIsNotNone(
                    library.store.get("default", "closing-index-record")
                )
            finally:
                library.close()

    def test_concurrent_outbox_scans_claim_only_worker_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            ) as library:
                for index in range(10):
                    library.store.append_thread_event(
                        namespace="default",
                        session_id=f"thread-{index}",
                        event_id="event",
                        role="user",
                        content=f"event {index}",
                        metadata={},
                        importance=0.5,
                        protected=False,
                        token_count=2,
                        record_id=f"record-{index}",
                        created_at=float(index),
                    )
                indexer = OutboxIndexer(library, capacity=2, worker_count=1)
                original_claim = library.store.claim_outbox_events

                def delayed_claim(*args: object, **kwargs: object) -> object:
                    claims = original_claim(*args, **kwargs)
                    time.sleep(0.02)
                    return claims

                with (
                    mock.patch.object(
                        library.store,
                        "claim_outbox_events",
                        side_effect=delayed_claim,
                    ),
                    ThreadPoolExecutor(max_workers=8) as executor,
                ):
                    list(executor.map(lambda _: indexer.scan_outbox(), range(8)))

                self.assertEqual(indexer.queue.queued, 1)
                self.assertEqual(indexer.queue.pending, 1)
                self.assertEqual(indexer.queue.available, 1)

    def test_one_hundred_governors_share_fixed_workers_and_bounded_state(self) -> None:
        settings = RuntimeSettings(
            outbox_workers=2,
            max_active_threads=8,
            thread_idle_ttl_seconds=3600,
        )
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
                runtime_settings=settings,
            ) as library:
                governors = [
                    library.open_context_governor(f"thread-{index}")
                    for index in range(100)
                ]
                for governor in governors:
                    governor.status()

                self.assertEqual(
                    {id(governor._indexer) for governor in governors},
                    {id(library.runtime.indexer)},
                )
                self.assertEqual(
                    library.runtime.thread_states.stats()["active"],
                    8,
                )
                worker_names = [
                    thread.name
                    for thread in threading.enumerate()
                    if thread.name.startswith("library-outbox-")
                ]
                self.assertEqual(len(worker_names), 2)

    def test_outbox_claims_are_exclusive_ordered_and_recoverable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            ) as library:
                store = library.store
                for session_id, event_id, created_at in (
                    ("a", "a-1", 1.0),
                    ("a", "a-2", 2.0),
                    ("b", "b-1", 3.0),
                ):
                    store.append_thread_event(
                        namespace="default",
                        session_id=session_id,
                        event_id=event_id,
                        role="user",
                        content=event_id,
                        metadata={},
                        importance=0.5,
                        protected=False,
                        token_count=1,
                        record_id=f"record-{event_id}",
                        created_at=created_at,
                    )

                first_claims = store.claim_outbox_events(
                    "worker-1",
                    limit=10,
                    lease_seconds=30,
                )
                self.assertEqual(
                    {(claim.session_id, claim.sequence) for claim in first_claims},
                    {("a", 1), ("b", 1)},
                )
                self.assertEqual(
                    store.claim_outbox_events("worker-2", limit=10),
                    [],
                )

                claim_a = next(
                    claim for claim in first_claims if claim.session_id == "a"
                )
                store.mark_thread_event_indexed(
                    claim_a.namespace,
                    claim_a.session_id,
                    claim_a.event_id,
                    indexed_at=time.time(),
                    claim_token=claim_a.claim_token,
                )
                next_claim = store.claim_outbox_events("worker-2", limit=10)[0]
                self.assertEqual((next_claim.session_id, next_claim.sequence), ("a", 2))
                with self.assertRaises(OutboxLeaseLost):
                    store.mark_thread_event_indexed(
                        claim_a.namespace,
                        claim_a.session_id,
                        claim_a.event_id,
                        indexed_at=time.time(),
                        claim_token=claim_a.claim_token,
                    )

                self.assertGreaterEqual(store.release_outbox_leases("worker-1"), 1)
                reclaimed = store.claim_outbox_events("worker-3", limit=10)
                self.assertTrue(any(claim.session_id == "b" for claim in reclaimed))

    def test_same_owner_does_not_replace_its_active_token_after_lease_expiry(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            ) as library:
                library.store.append_thread_event(
                    namespace="default",
                    session_id="thread",
                    event_id="event",
                    role="user",
                    content="same owner fencing",
                    metadata={},
                    importance=0.5,
                    protected=False,
                    token_count=3,
                    record_id="record",
                    created_at=1.0,
                )
                with mock.patch("context_cache.store.time.time", return_value=100.0):
                    claim = library.store.claim_outbox_events(
                        "owner",
                        limit=1,
                        lease_seconds=1.0,
                    )[0]
                with mock.patch("context_cache.store.time.time", return_value=102.0):
                    self.assertEqual(
                        library.store.claim_outbox_events(
                            "owner",
                            limit=1,
                            lease_seconds=1.0,
                        ),
                        [],
                    )
                    library.store.mark_thread_event_indexed(
                        claim.namespace,
                        claim.session_id,
                        claim.event_id,
                        indexed_at=102.0,
                        claim_token=claim.claim_token,
                    )
                self.assertEqual(
                    library.store.thread_watermarks("default", "thread").pending_events,
                    0,
                )

    def test_fatal_indexer_exit_releases_only_its_token_and_reports_degradation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            ) as library:
                library.store.append_thread_event(
                    namespace="default",
                    session_id="fatal",
                    event_id="event",
                    role="user",
                    content="fatal worker",
                    metadata={},
                    importance=0.5,
                    protected=False,
                    token_count=2,
                    record_id="fatal-record",
                    created_at=1.0,
                )
                indexer = OutboxIndexer(
                    library,
                    capacity=1,
                    poll_seconds=0.01,
                    worker_count=1,
                    lease_seconds=30.0,
                )
                try:
                    with (
                        mock.patch.object(
                            indexer,
                            "_index",
                            side_effect=SystemExit("fatal indexer test"),
                        ),
                        mock.patch("threading.excepthook"),
                    ):
                        indexer.start(name="fatal-indexer")
                        worker = indexer._threads[0]
                        worker.join(2)
                    self.assertFalse(worker.is_alive())
                    status = indexer.status()
                    self.assertTrue(status["degraded"])
                    self.assertEqual(status["workers_alive"], 0)
                    self.assertEqual(status["active_claims"], 0)
                    self.assertIn("SystemExit", str(status["last_error"]))
                    reclaimed = library.store.claim_outbox_events(
                        "recovery-owner",
                        limit=1,
                        lease_seconds=30.0,
                    )
                    self.assertEqual(len(reclaimed), 1)
                    self.assertEqual(reclaimed[0].event_id, "event")
                finally:
                    indexer.close()

    def test_terminal_failure_is_visible_and_does_not_block_later_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            ) as library:
                store = library.store
                for sequence in (1, 2):
                    store.append_thread_event(
                        namespace="default",
                        session_id="thread",
                        event_id=f"event-{sequence}",
                        role="user",
                        content=f"event {sequence}",
                        metadata={},
                        importance=0.5,
                        protected=False,
                        token_count=2,
                        record_id=f"record-{sequence}",
                        created_at=float(sequence),
                    )

                first = store.claim_outbox_events("worker", limit=1)[0]
                store.fail_outbox_event(
                    first.namespace,
                    first.session_id,
                    first.event_id,
                    error="unsupported event",
                    retry_after_seconds=60,
                    claim_token=first.claim_token,
                    terminal=True,
                )
                second = store.claim_outbox_events("worker", limit=1)[0]

                self.assertEqual(second.sequence, 2)
                watermarks = store.thread_watermarks("default", "thread")
                self.assertEqual(watermarks.pending_events, 1)
                self.assertEqual(watermarks.failed_events, 1)
                self.assertEqual(watermarks.indexed_through, 0)
                self.assertEqual(store.stats("default")["failed_outbox"], 1)
                self.assertFalse(
                    store.retry_failed_outbox_event(
                        "default",
                        "thread",
                        first.event_id,
                    )
                )

                store.mark_thread_event_indexed(
                    second.namespace,
                    second.session_id,
                    second.event_id,
                    indexed_at=time.time(),
                    claim_token=second.claim_token,
                )
                self.assertTrue(
                    store.retry_failed_outbox_event(
                        "default",
                        "thread",
                        first.event_id,
                    )
                )
                retried = store.claim_outbox_events("worker", limit=1)[0]
                self.assertEqual(retried.sequence, 1)

    def test_indexer_quarantines_a_poison_event_after_the_attempt_limit(self) -> None:
        class FailingEmbedder:
            dimensions = 4

            def embed(self, _texts: list[str]) -> list[list[float]]:
                raise RuntimeError("embedding rejected")

        settings = RuntimeSettings(
            outbox_workers=1,
            outbox_poll_seconds=0.01,
            outbox_max_attempts=1,
        )
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
                embedder=FailingEmbedder(),
                runtime_settings=settings,
            ) as library:
                governor = library.open_context_governor("poison")
                governor.record("user", "cannot embed", event_id="poison-1")

                self.assertFalse(governor.flush(timeout=2))
                watermarks = governor.status()["watermarks"]
                self.assertEqual(watermarks["pending_events"], 0)
                self.assertEqual(watermarks["failed_events"], 1)

    def test_shutdown_releases_a_claim_without_closing_storage_under_a_worker(
        self,
    ) -> None:
        class BlockingEmbedder:
            dimensions = 4

            def __init__(self) -> None:
                self.started = threading.Event()
                self.release = threading.Event()

            def embed(self, texts: list[str]) -> list[list[float]]:
                self.started.set()
                if not self.release.wait(2):
                    raise TimeoutError("blocking embedder was not released")
                return [[0.5, 0.5, 0.5, 0.5] for _ in texts]

        embedder = BlockingEmbedder()
        settings = RuntimeSettings(
            outbox_workers=1,
            outbox_poll_seconds=0.01,
            outbox_shutdown_timeout_seconds=0.05,
        )
        with tempfile.TemporaryDirectory() as directory:
            library = LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
                embedder=embedder,
                runtime_settings=settings,
            )
            worker = None
            try:
                governor = library.open_context_governor("shutdown")
                governor.record("user", "blocked indexing", event_id="blocked")
                self.assertTrue(embedder.started.wait(2))
                worker = library.runtime.indexer._threads[0]

                started = time.monotonic()
                self.assertFalse(library.close())
                self.assertLess(time.monotonic() - started, 1.0)
                self.assertEqual(
                    library.store.thread_watermarks(
                        "default", "shutdown"
                    ).pending_events,
                    1,
                )
                with self.assertRaisesRegex(RuntimeError, "closing"):
                    governor.record("user", "rejected during shutdown")
                with self.assertRaisesRegex(RuntimeError, "closing"):
                    library.runtime.swapper.refresh("shutdown", "rejected")
            finally:
                embedder.release.set()
            assert worker is not None
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertTrue(_close_within(library))

    def test_indexer_shutdown_leaves_an_active_completion_claim_leased(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            library = LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
            )
            library.store.append_thread_event(
                namespace="default",
                session_id="completion",
                event_id="event",
                role="user",
                content="completion in progress",
                metadata={},
                importance=0.5,
                protected=False,
                token_count=3,
                record_id="completion-record",
                created_at=1.0,
            )
            indexer = OutboxIndexer(
                library,
                capacity=1,
                poll_seconds=0.01,
                worker_count=1,
                shutdown_timeout_seconds=0.05,
            )

            def blocked_completion(*_args: object, **_kwargs: object) -> None:
                entered.set()
                release.wait()

            worker = None
            try:
                with (
                    mock.patch.object(
                        library,
                        "_persist_claimed_record",
                        side_effect=blocked_completion,
                    ),
                    mock.patch.object(
                        library.store,
                        "release_outbox_leases",
                        wraps=library.store.release_outbox_leases,
                    ) as release_leases,
                ):
                    indexer.start(name="completion-indexer")
                    self.assertTrue(entered.wait(2))
                    worker = indexer._threads[0]
                    with indexer._state_lock:
                        claim_started = next(iter(indexer._active_claims.values()))
                    with mock.patch(
                        "context_cache.indexing.time.monotonic",
                        return_value=claim_started + indexer.lease_seconds + 1.0,
                    ):
                        status = indexer.status()
                    self.assertTrue(status["degraded"])
                    self.assertEqual(status["active_claims"], 1)
                    self.assertGreater(
                        status["oldest_active_claim_seconds"],
                        indexer.lease_seconds,
                    )
                    self.assertNotIn("event_id", status)
                    self.assertNotIn("session_id", status)

                    started = time.monotonic()
                    self.assertFalse(indexer.close())
                    self.assertLess(time.monotonic() - started, 0.5)
                    release_leases.assert_not_called()

                    release.set()
                    worker.join(2)
                    self.assertFalse(worker.is_alive())
                    self.assertTrue(indexer.close())
                    release_leases.assert_called_once_with(indexer.owner_id)
            finally:
                release.set()
                if worker is not None:
                    worker.join(2)
                indexer.close()
                library.close()

    def test_scheduler_shutdown_keeps_storage_open_until_callback_exit(self) -> None:
        started = threading.Event()
        release = threading.Event()
        storage_accessed = threading.Event()
        errors: list[Exception] = []
        settings = RuntimeSettings(desk_shutdown_timeout_seconds=0.05)
        with tempfile.TemporaryDirectory() as directory:
            library = LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
                runtime_settings=settings,
            )

            def blocked_refresh(_generation: int) -> None:
                started.set()
                release.wait()
                try:
                    library.store.stats("default")
                    storage_accessed.set()
                except Exception as exc:  # pragma: no cover - assertion reports it
                    errors.append(exc)

            library.runtime.scheduler.schedule(
                ThreadKey("default", "periodic"),
                interval_seconds=0.01,
                callback=blocked_refresh,
            )
            self.assertTrue(started.wait(2))
            try:
                self.assertFalse(library.close())
                self.assertEqual(library.stats()["lifecycle"], "closing")
                with self.assertRaisesRegex(RuntimeError, "closing"):
                    library.runtime.swapper.refresh("periodic", "rejected")
            finally:
                release.set()
            for worker in library.runtime.scheduler._workers:
                worker.join(2)
            self.assertTrue(storage_accessed.is_set())
            self.assertEqual(errors, [])
            self.assertTrue(_close_within(library))

    def test_close_waits_for_an_admitted_embedding_operation(self) -> None:
        class BlockingEmbedder:
            dimensions = 4

            def __init__(self) -> None:
                self.started = threading.Event()
                self.release = threading.Event()

            def embed(self, texts: list[str]) -> list[list[float]]:
                self.started.set()
                self.release.wait()
                return [[0.25, 0.25, 0.25, 0.25] for _ in texts]

        embedder = BlockingEmbedder()
        settings = RuntimeSettings(
            outbox_shutdown_timeout_seconds=0.05,
            desk_shutdown_timeout_seconds=0.05,
        )
        with tempfile.TemporaryDirectory() as directory:
            library = LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
                embedder=embedder,
                runtime_settings=settings,
            )
            session = library.open_virtual_session("existing-session")
            records: list[object] = []
            errors: list[Exception] = []

            def shelve() -> None:
                try:
                    records.append(library.shelve("admitted operation"))
                except Exception as exc:
                    errors.append(exc)

            operation = threading.Thread(target=shelve, daemon=True)
            operation.start()
            self.assertTrue(embedder.started.wait(2))
            before = time.monotonic()
            self.assertFalse(library.close())
            self.assertLess(time.monotonic() - before, 0.5)
            with self.assertRaisesRegex(RuntimeError, "closing"):
                session.history()

            embedder.release.set()
            operation.join(2)
            self.assertFalse(operation.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(len(records), 1)
            self.assertTrue(_close_within(library))

    def test_close_drains_admitted_governor_append_before_stopping_indexer(
        self,
    ) -> None:
        entered = threading.Event()
        release = threading.Event()
        settings = RuntimeSettings(
            outbox_shutdown_timeout_seconds=0.05,
            desk_shutdown_timeout_seconds=0.05,
        )
        with tempfile.TemporaryDirectory() as directory:
            library = LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
                runtime_settings=settings,
            )
            governor = library.open_context_governor("append-race")
            original_append = library.store.append_thread_event
            events: list[object] = []
            errors: list[Exception] = []

            def blocked_append(*args: object, **kwargs: object) -> object:
                entered.set()
                release.wait()
                return original_append(*args, **kwargs)

            def record() -> None:
                try:
                    events.append(
                        governor.record(
                            "user",
                            "admitted append",
                            event_id="admitted",
                        )
                    )
                except Exception as exc:
                    errors.append(exc)

            operation = threading.Thread(target=record, daemon=True)
            with mock.patch.object(
                library.store,
                "append_thread_event",
                side_effect=blocked_append,
            ):
                operation.start()
                self.assertTrue(entered.wait(2))
                self.assertFalse(library.close())
                self.assertFalse(library.runtime.indexer._stop.is_set())
                release.set()
                operation.join(2)

            self.assertFalse(operation.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(len(events), 1)
            self.assertIsNotNone(
                library.store.get_thread_event(
                    "default",
                    "append-race",
                    "admitted",
                )
            )
            self.assertTrue(_close_within(library))

    def test_one_scheduler_coordinates_one_hundred_watches(self) -> None:
        settings = RuntimeSettings(
            max_active_desks=128,
            desk_workers=2,
        )
        with tempfile.TemporaryDirectory() as directory:
            with LibraryOfContext(
                Path(directory) / "library.sqlite",
                redis_url="",
                runtime_settings=settings,
            ) as library:
                library.shelve("Shared project context.", book_id="shared")
                for index in range(100):
                    library.runtime.swapper.start_periodic(
                        f"thread-{index}",
                        "shared project context",
                        interval_seconds=60,
                        token_budget=80,
                    )

                self.assertEqual(len(library.runtime.scheduler.status()), 100)
                coordinator_names = [
                    thread.name
                    for thread in threading.enumerate()
                    if thread.name == "library-desk-scheduler"
                ]
                self.assertEqual(coordinator_names, ["library-desk-scheduler"])
                self.assertFalse(
                    any(
                        thread.name.startswith("context-swapper-")
                        for thread in threading.enumerate()
                    )
                )


if __name__ == "__main__":
    unittest.main()
