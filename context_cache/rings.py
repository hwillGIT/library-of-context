from __future__ import annotations

import queue
import threading
from collections import deque
from typing import Generic, TypeVar

from .models import ContextEvent

T = TypeVar("T")


class RecentEventRing:
    """Ordered recent events bounded by count and estimated tokens."""

    def __init__(self, *, max_events: int, max_tokens: int) -> None:
        self.max_events = max(1, max_events)
        self.max_tokens = max(64, max_tokens)
        self._events: deque[ContextEvent] = deque()
        self._ids: set[str] = set()
        self._tokens = 0
        self._lock = threading.RLock()

    def append(self, event: ContextEvent) -> None:
        with self._lock:
            if event.event_id in self._ids:
                return
            self._events.append(event)
            self._ids.add(event.event_id)
            self._tokens += event.token_count + 4
            while len(self._events) > self.max_events or (
                len(self._events) > 1 and self._tokens > self.max_tokens
            ):
                removed = self._events.popleft()
                self._ids.discard(removed.event_id)
                self._tokens -= removed.token_count + 4

    def snapshot(self) -> list[ContextEvent]:
        with self._lock:
            return list(self._events)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "events": len(self._events),
                "estimated_tokens": self._tokens,
                "event_capacity": self.max_events,
                "token_capacity": self.max_tokens,
            }


class UniqueWorkQueue(Generic[T]):
    """Bounded FIFO queue that rejects duplicate pending keys."""

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._queue: queue.Queue[T] = queue.Queue(maxsize=capacity)
        self._pending: set[T] = set()
        self._lock = threading.RLock()

    def offer(self, item: T) -> bool:
        with self._lock:
            if item in self._pending:
                return True
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                return False
            self._pending.add(item)
            return True

    def get(self, timeout: float) -> T:
        return self._queue.get(timeout=timeout)

    def complete(self, item: T) -> None:
        with self._lock:
            self._pending.discard(item)
        self._queue.task_done()

    @property
    def queued(self) -> int:
        return self._queue.qsize()

    @property
    def available(self) -> int:
        return max(0, self.capacity - self.queued)

    def stats(self) -> dict[str, int | float]:
        queued = self.queued
        return {
            "queued": queued,
            "capacity": self.capacity,
            "occupancy": queued / self.capacity,
        }


QueueEmpty = queue.Empty
