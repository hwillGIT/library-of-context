from __future__ import annotations

import queue
import threading
from collections import deque
from copy import deepcopy
from dataclasses import replace
from typing import Generic, TypeVar

from .embeddings import estimate_tokens
from .models import ContextEvent
from .text_budget import EVENT_TRUNCATION_MARKER, truncate_text

T = TypeVar("T")


class RecentEventRing:
    """Ordered recent events bounded by count and estimated tokens."""

    def __init__(self, *, max_events: int, max_tokens: int) -> None:
        self.max_events = max(1, max_events)
        self.max_tokens = max(64, max_tokens)
        self._events: deque[ContextEvent] = deque()
        self._ids: set[str] = set()
        self._tokens = 0
        self._highest_sequence = 0
        self._lock = threading.RLock()

    def append(self, event: ContextEvent) -> None:
        with self._lock:
            if event.event_id in self._ids or event.sequence <= self._highest_sequence:
                return
            projected = replace(event, metadata=deepcopy(event.metadata))
            if event.token_count + 4 > self.max_tokens:
                content = truncate_text(
                    event.content,
                    self.max_tokens - 4,
                    marker=EVENT_TRUNCATION_MARKER,
                )
                projected = replace(
                    projected,
                    content=content,
                    token_count=estimate_tokens(content),
                )
            self._events.append(projected)
            self._ids.add(projected.event_id)
            self._highest_sequence = event.sequence
            self._tokens += projected.token_count + 4
            while len(self._events) > self.max_events or self._tokens > self.max_tokens:
                removed = self._events.popleft()
                self._ids.discard(removed.event_id)
                self._tokens -= removed.token_count + 4

    def snapshot(self) -> list[ContextEvent]:
        with self._lock:
            return [
                replace(event, metadata=deepcopy(event.metadata))
                for event in self._events
            ]

    def set_protected(self, event_id: str, protected: bool) -> bool:
        """Update the process-local projection of a durable event."""

        with self._lock:
            for index, event in enumerate(self._events):
                if event.event_id != event_id:
                    continue
                self._events[index] = replace(event, protected=protected)
                return True
        return False

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
        with self._lock:
            return max(0, self.capacity - len(self._pending))

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._pending)

    def stats(self) -> dict[str, int | float]:
        queued = self.queued
        pending = self.pending
        return {
            "queued": queued,
            "pending": pending,
            "capacity": self.capacity,
            "occupancy": pending / self.capacity,
        }


QueueEmpty = queue.Empty
