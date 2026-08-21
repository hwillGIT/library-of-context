from __future__ import annotations

import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Iterator

from .rings import RecentEventRing
from .scopes import ThreadKey

if TYPE_CHECKING:
    from .engine import ContextCache


class ThreadCapacityError(RuntimeError):
    """Raised when every bounded thread-state slot is in use."""


@dataclass(slots=True)
class ThreadState:
    """Process-local state shared by handles for one durable thread."""

    key: ThreadKey
    recent: RecentEventRing
    operation_lock: threading.RLock = field(default_factory=threading.RLock)


@dataclass(slots=True)
class _ThreadEntry:
    state: ThreadState
    last_access: float
    leases: int = 0


class ThreadStateRegistry:
    """Bound active thread state by idle TTL and least-recently-used eviction."""

    def __init__(
        self,
        cache: ContextCache,
        *,
        max_entries: int = 256,
        idle_ttl_seconds: float = 1800.0,
        recent_events: int = 256,
        recent_tokens: int = 8192,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        if idle_ttl_seconds <= 0:
            raise ValueError("idle_ttl_seconds must be positive")
        self.cache = cache
        self.max_entries = max_entries
        self.idle_ttl_seconds = idle_ttl_seconds
        self.recent_events = max(1, recent_events)
        self.recent_tokens = max(64, recent_tokens)
        self._clock = clock
        self._entries: OrderedDict[ThreadKey, _ThreadEntry] = OrderedDict()
        self._lock = threading.RLock()

    def _create(self, key: ThreadKey) -> ThreadState:
        recent = RecentEventRing(
            max_events=self.recent_events,
            max_tokens=self.recent_tokens,
        )
        for event in self.cache.store.list_thread_events(
            key.collection,
            key.session_id,
            limit=self.recent_events,
        ):
            recent.append(event)
        return ThreadState(key=key, recent=recent)

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self.idle_ttl_seconds
        expired = [
            key
            for key, entry in self._entries.items()
            if entry.leases == 0 and entry.last_access <= cutoff
        ]
        for key in expired:
            self._entries.pop(key, None)

    def _make_room_locked(self) -> None:
        if len(self._entries) < self.max_entries:
            return
        for key, entry in list(self._entries.items()):
            if entry.leases == 0:
                self._entries.pop(key)
                return
        raise ThreadCapacityError("all thread-state slots are in use")

    @contextmanager
    def lease(self, key: ThreadKey) -> Iterator[ThreadState]:
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            entry = self._entries.get(key)
            if entry is None:
                self._make_room_locked()
                entry = _ThreadEntry(self._create(key), last_access=now)
                self._entries[key] = entry
            entry.leases += 1
            entry.last_access = now
            self._entries.move_to_end(key)
        try:
            yield entry.state
        finally:
            with self._lock:
                current = self._entries.get(key)
                if current is not None:
                    current.leases = max(0, current.leases - 1)
                    current.last_access = self._clock()
                    self._entries.move_to_end(key)

    def stats(self, *, collection: str | None = None) -> dict[str, int | float]:
        with self._lock:
            self._prune_locked(self._clock())
            entries = [
                entry
                for key, entry in self._entries.items()
                if collection is None or key.collection == collection
            ]
            return {
                "active": len(entries),
                "leased": sum(entry.leases for entry in entries),
                "capacity": self.max_entries,
                "idle_ttl_seconds": self.idle_ttl_seconds,
                "recent_event_capacity": self.recent_events,
                "recent_token_capacity": self.recent_tokens,
            }

    def close(self) -> None:
        with self._lock:
            self._entries.clear()
