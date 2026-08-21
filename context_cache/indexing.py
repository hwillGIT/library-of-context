from __future__ import annotations

import random
import threading
import time
import uuid
from typing import TYPE_CHECKING

from .models import ContextEvent, OutboxClaim
from .rings import QueueEmpty, UniqueWorkQueue
from .scopes import ContextScope
from .store import OutboxLeaseLost

if TYPE_CHECKING:
    from .engine import ContextCache


class OutboxIndexer:
    """Index durable thread events with one bounded process-owned worker pool."""

    def __init__(
        self,
        library: ContextCache,
        *,
        capacity: int = 1024,
        poll_seconds: float = 0.1,
        worker_count: int = 2,
        lease_seconds: float = 120.0,
        max_attempts: int = 8,
        shutdown_timeout_seconds: float = 2.0,
    ) -> None:
        if worker_count < 1:
            raise ValueError("worker_count must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if shutdown_timeout_seconds <= 0:
            raise ValueError("shutdown_timeout_seconds must be positive")
        self.library = library
        self.poll_seconds = max(0.01, poll_seconds)
        self.worker_count = worker_count
        self.lease_seconds = max(1.0, lease_seconds)
        self.max_attempts = max_attempts
        self.shutdown_timeout_seconds = shutdown_timeout_seconds
        self.queue: UniqueWorkQueue[OutboxClaim] = UniqueWorkQueue(capacity)
        self.owner_id = f"indexer-{uuid.uuid4().hex}"
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._state_lock = threading.RLock()
        self._scan_lock = threading.Lock()
        self._completion_lock = threading.Lock()
        self._last_error: str | None = None
        self._active_claims: dict[str, float] = {}

    def start(self, *, name: str = "library-outbox") -> None:
        with self._state_lock:
            if self._threads:
                return
            self._scan_safely()
            for index in range(self.worker_count):
                thread = threading.Thread(
                    target=self._run,
                    name=f"{name}-{index + 1}",
                    daemon=True,
                )
                self._threads.append(thread)
                thread.start()

    def enqueue(self, namespace: str, event_id: str) -> bool:
        if self._stop.is_set():
            return False
        del namespace, event_id
        return self._scan_safely()

    def _scan_safely(self) -> bool:
        try:
            self.scan_outbox()
            with self._state_lock:
                self._last_error = None
            return True
        except Exception as exc:
            with self._state_lock:
                self._last_error = str(exc)
            self._stop.wait(self.poll_seconds)
            return False

    def scan_outbox(self) -> None:
        with self._scan_lock:
            available = min(
                self.queue.available,
                max(0, self.worker_count - self.queue.pending),
            )
            if available == 0:
                return
            claims = self.library.store.claim_outbox_events(
                self.owner_id,
                limit=available,
                lease_seconds=self.lease_seconds,
            )
            for claim in claims:
                if not self.queue.offer(claim):
                    raise RuntimeError("claimed outbox work exceeds queue capacity")

    def _index(self, event: ContextEvent, claim: OutboxClaim) -> None:
        metadata = dict(event.metadata)
        metadata.update(
            {
                "kind": "conversation",
                "session_id": event.session_id,
                "role": event.role,
                "turn": event.sequence,
                "event_id": event.event_id,
                "protected": event.protected,
            }
        )
        record = self.library._prepare_record(
            event.content,
            record_id=event.record_id,
            namespace=event.namespace,
            metadata=metadata,
            source=f"conversation:{event.session_id}",
            importance=event.importance,
            scope=ContextScope.THREAD,
            owner_session_id=event.session_id,
            team_id=None,
            ttl_seconds=None,
        )
        with self._completion_lock:
            if self._stop.is_set():
                return
            indexed_at = time.time()
            self.library._persist_claimed_record(
                record,
                event,
                claim,
                indexed_at=indexed_at,
            )

    @staticmethod
    def _retry_delay(attempts: int) -> float:
        base = min(30.0, 0.25 * (2 ** min(max(0, attempts), 7)))
        return base + random.uniform(0.0, base * 0.2)

    def _release_fatal_claim(
        self,
        claim: OutboxClaim,
        error: BaseException,
    ) -> None:
        self._stop.set()
        with self._state_lock:
            self._last_error = f"{type(error).__name__}: {error}"
        try:
            self.library.store.release_outbox_claim(
                claim.namespace,
                claim.session_id,
                claim.event_id,
                claim_token=claim.claim_token,
            )
        except Exception:
            pass

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                claim = self.queue.get(timeout=self.poll_seconds)
            except QueueEmpty:
                self._scan_safely()
                continue
            with self._state_lock:
                self._active_claims[claim.claim_token] = time.monotonic()
            try:
                event = self.library.store.get_thread_event(
                    claim.namespace,
                    claim.session_id,
                    claim.event_id,
                )
                if event is None:
                    raise RuntimeError(
                        "claimed outbox event has no durable source event"
                    )
                if event.indexed_at is None:
                    self._index(event, claim)
                else:
                    self.library.store.mark_thread_event_indexed(
                        claim.namespace,
                        claim.session_id,
                        claim.event_id,
                        indexed_at=event.indexed_at or time.time(),
                        claim_token=claim.claim_token,
                    )
                with self._state_lock:
                    self._last_error = None
            except OutboxLeaseLost:
                pass
            except Exception as exc:
                with self._state_lock:
                    self._last_error = str(exc)
                try:
                    self.library.store.fail_outbox_event(
                        claim.namespace,
                        claim.session_id,
                        claim.event_id,
                        error=str(exc),
                        retry_after_seconds=self._retry_delay(claim.attempts),
                        claim_token=claim.claim_token,
                        terminal=claim.attempts + 1 >= self.max_attempts,
                    )
                except OutboxLeaseLost:
                    pass
            except BaseException as exc:
                self._release_fatal_claim(claim, exc)
                raise
            finally:
                with self._state_lock:
                    self._active_claims.pop(claim.claim_token, None)
                self.queue.complete(claim)
                if not self._stop.is_set():
                    self._scan_safely()

    def flush(self, collection: str, session_id: str, *, timeout: float) -> bool:
        target = self.library.store.thread_watermarks(
            collection,
            session_id,
        ).recorded_through
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            if not self._stop.is_set():
                self._scan_safely()
            watermarks = self.library.store.thread_watermarks(collection, session_id)
            if watermarks.indexed_through >= target:
                return True
            if watermarks.failed_events:
                return False
            if time.monotonic() >= deadline or not self.is_alive:
                return False
            self._stop.wait(min(0.02, max(0.0, deadline - time.monotonic())))

    @property
    def is_alive(self) -> bool:
        with self._state_lock:
            return bool(self._threads) and all(
                thread.is_alive() for thread in self._threads
            )

    @property
    def last_error(self) -> str | None:
        with self._state_lock:
            return self._last_error

    def status(self) -> dict[str, int | float | bool | str | None]:
        with self._state_lock:
            now = time.monotonic()
            active_ages = [now - started for started in self._active_claims.values()]
            last_error = self._last_error
        workers_alive = sum(thread.is_alive() for thread in self._threads)
        oldest_active_claim_seconds = max(active_ages, default=0.0)
        return {
            **self.queue.stats(),
            "worker_count": self.worker_count,
            "workers_alive": workers_alive,
            "owner_id": self.owner_id,
            "max_attempts": self.max_attempts,
            "active_claims": len(active_ages),
            "oldest_active_claim_seconds": oldest_active_claim_seconds,
            "degraded": bool(
                last_error is not None
                or (self._threads and workers_alive < self.worker_count)
                or oldest_active_claim_seconds > self.lease_seconds
            ),
            "last_error": last_error,
        }

    def close(self) -> bool:
        """Stop workers and release leases only after every worker exits."""

        self._stop.set()
        deadline = time.monotonic() + self.shutdown_timeout_seconds
        for thread in self._threads:
            if thread is threading.current_thread():
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)
        if any(thread.is_alive() for thread in self._threads):
            return False

        remaining = deadline - time.monotonic()
        acquired = (
            self._completion_lock.acquire(timeout=remaining)
            if remaining > 0
            else self._completion_lock.acquire(blocking=False)
        )
        if not acquired:
            return False
        try:
            self.library.store.release_outbox_leases(self.owner_id)
        finally:
            self._completion_lock.release()
        return True


ContextEventIndexer = OutboxIndexer
