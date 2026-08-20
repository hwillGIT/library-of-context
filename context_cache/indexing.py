from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from .models import ContextEvent
from .rings import QueueEmpty, UniqueWorkQueue

if TYPE_CHECKING:
    from .engine import ContextCache

EventKey = tuple[str, str]


class ContextEventIndexer:
    """Index durable context events with a bounded in-process work queue."""

    def __init__(
        self,
        library: ContextCache,
        *,
        capacity: int,
        poll_seconds: float,
    ) -> None:
        self.library = library
        self.poll_seconds = max(0.01, poll_seconds)
        self.queue: UniqueWorkQueue[EventKey] = UniqueWorkQueue(capacity)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_error: str | None = None
        self.scan_outbox()

    def start(self, *, name: str) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name=name,
            daemon=True,
        )
        self._thread.start()

    def enqueue(self, namespace: str, event_id: str) -> bool:
        return self.queue.offer((namespace, event_id))

    def scan_outbox(self) -> None:
        if self.queue.available == 0:
            return
        for namespace, event_id in self.library.store.pending_outbox_event_ids(
            limit=self.queue.available
        ):
            if not self.enqueue(namespace, event_id):
                break

    def _index(self, event: ContextEvent) -> None:
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
        self.library.put(
            event.content,
            record_id=event.record_id,
            namespace=event.namespace,
            metadata=metadata,
            source=f"conversation:{event.session_id}",
            importance=event.importance,
        )
        indexed_at = time.time()
        self.library.store.mark_thread_event_indexed(
            event.namespace,
            event.event_id,
            indexed_at=indexed_at,
        )
        event.indexed_at = indexed_at

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                key = self.queue.get(timeout=self.poll_seconds)
            except QueueEmpty:
                self.scan_outbox()
                continue
            namespace, event_id = key
            try:
                event = self.library.store.get_thread_event(namespace, event_id)
                if event is not None and event.indexed_at is None:
                    self._index(event)
                self._last_error = None
            except Exception as exc:
                self._last_error = str(exc)
                self.library.store.fail_outbox_event(
                    namespace,
                    event_id,
                    error=str(exc),
                    retry_after_seconds=0.5,
                )
            finally:
                self.queue.complete(key)
                self.scan_outbox()

    def flush(self, collection: str, session_id: str, *, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            self.scan_outbox()
            watermarks = self.library.store.thread_watermarks(collection, session_id)
            if watermarks.pending_events == 0:
                return True
            if time.monotonic() >= deadline or not self.is_alive:
                return False
            self._stop.wait(min(0.02, max(0.0, deadline - time.monotonic())))

    @property
    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def status(self) -> dict[str, int | float]:
        return self.queue.stats()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
