from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .indexing import OutboxIndexer
from .scheduler import DeskScheduler
from .swapper import ContextSwapper
from .thread_state import ThreadStateRegistry

if TYPE_CHECKING:
    from .engine import ContextCache


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    """Bounded process-level worker and desk resources."""

    outbox_workers: int = 2
    outbox_capacity: int = 1024
    outbox_poll_seconds: float = 0.1
    outbox_lease_seconds: float = 120.0
    outbox_max_attempts: int = 8
    outbox_shutdown_timeout_seconds: float = 2.0
    desk_workers: int = 2
    desk_jitter_ratio: float = 0.1
    desk_shutdown_timeout_seconds: float = 2.0
    max_active_threads: int = 256
    thread_idle_ttl_seconds: float = 1800.0
    recent_ring_events: int = 256
    recent_ring_tokens: int = 8192
    max_active_desks: int = 256
    desk_idle_ttl_seconds: float = 1800.0
    http_max_connections: int = 32
    http_read_timeout_seconds: float = 15.0
    http_shutdown_timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        if self.outbox_workers < 1 or self.desk_workers < 1:
            raise ValueError("runtime worker counts must be positive")
        if self.outbox_capacity < 1:
            raise ValueError("outbox_capacity must be positive")
        if (
            self.outbox_poll_seconds <= 0
            or self.outbox_lease_seconds <= 0
            or self.outbox_shutdown_timeout_seconds <= 0
        ):
            raise ValueError("outbox timing values must be positive")
        if self.outbox_max_attempts < 1:
            raise ValueError("outbox_max_attempts must be positive")
        if self.max_active_threads < 1 or self.thread_idle_ttl_seconds <= 0:
            raise ValueError("thread registry limits must be positive")
        if self.max_active_desks < 1 or self.desk_idle_ttl_seconds <= 0:
            raise ValueError("desk registry limits must be positive")
        if self.desk_shutdown_timeout_seconds <= 0:
            raise ValueError("desk shutdown timeout must be positive")
        if (
            self.http_max_connections < 1
            or self.http_read_timeout_seconds <= 0
            or self.http_shutdown_timeout_seconds <= 0
        ):
            raise ValueError("HTTP runtime limits must be positive")


class LibraryRuntime:
    """Own the bounded indexing and desk services for one Library process."""

    def __init__(
        self,
        cache: ContextCache,
        *,
        settings: RuntimeSettings | None = None,
    ) -> None:
        self.cache = cache
        self.settings = settings or RuntimeSettings()
        self.scheduler = DeskScheduler(
            worker_count=self.settings.desk_workers,
            jitter_ratio=self.settings.desk_jitter_ratio,
            max_tasks=self.settings.max_active_desks,
            shutdown_timeout_seconds=self.settings.desk_shutdown_timeout_seconds,
        )
        self.swapper = ContextSwapper(
            cache,
            scheduler=self.scheduler,
            max_working_sets=self.settings.max_active_desks,
            working_set_ttl_seconds=self.settings.desk_idle_ttl_seconds,
        )
        self.thread_states = ThreadStateRegistry(
            cache,
            max_entries=self.settings.max_active_threads,
            idle_ttl_seconds=self.settings.thread_idle_ttl_seconds,
            recent_events=self.settings.recent_ring_events,
            recent_tokens=self.settings.recent_ring_tokens,
        )
        self.indexer = OutboxIndexer(
            cache,
            capacity=self.settings.outbox_capacity,
            poll_seconds=self.settings.outbox_poll_seconds,
            worker_count=self.settings.outbox_workers,
            lease_seconds=self.settings.outbox_lease_seconds,
            max_attempts=self.settings.outbox_max_attempts,
            shutdown_timeout_seconds=self.settings.outbox_shutdown_timeout_seconds,
        )
        self._closed = False
        self._shutdown_complete = False
        self._close_errors: dict[str, str] = {}
        self._lock = threading.RLock()

    def start_indexer(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("Library runtime is closed")
            self.indexer.start()

    def status(self, *, collection: str | None = None) -> dict[str, Any]:
        periodic_desks = [
            desk
            for desk in self.scheduler.status()
            if collection is None or desk["collection"] == collection
        ]
        return {
            "indexer": self.indexer.status(),
            "desk_scheduler": self.scheduler.health(),
            "periodic_desks": periodic_desks,
            "thread_states": self.thread_states.stats(collection=collection),
            "close_errors": dict(self._close_errors),
            "settings": {
                "outbox_workers": self.settings.outbox_workers,
                "outbox_capacity": self.settings.outbox_capacity,
                "outbox_max_attempts": self.settings.outbox_max_attempts,
                "outbox_shutdown_timeout_seconds": (
                    self.settings.outbox_shutdown_timeout_seconds
                ),
                "desk_workers": self.settings.desk_workers,
                "desk_shutdown_timeout_seconds": (
                    self.settings.desk_shutdown_timeout_seconds
                ),
                "max_active_threads": self.settings.max_active_threads,
                "max_active_desks": self.settings.max_active_desks,
                "http_max_connections": self.settings.http_max_connections,
                "http_read_timeout_seconds": self.settings.http_read_timeout_seconds,
                "http_shutdown_timeout_seconds": (
                    self.settings.http_shutdown_timeout_seconds
                ),
            },
        }

    def close(self) -> bool:
        with self._lock:
            if self._shutdown_complete:
                return True
            self._closed = True
            self._close_errors.clear()

        def attempt(name: str, callback: Any, *, expects_bool: bool = False) -> bool:
            try:
                result = callback()
            except Exception as exc:
                with self._lock:
                    self._close_errors[name] = f"{type(exc).__name__}: {exc}"
                return False
            if expects_bool and not bool(result):
                return False
            return True

        indexer_drained = attempt(
            "indexer",
            self.indexer.close,
            expects_bool=True,
        )
        swapper_closed = attempt("swapper", self.swapper.close)
        scheduler_drained = attempt(
            "scheduler",
            self.scheduler.close,
            expects_bool=True,
        )
        thread_states_closed = attempt("thread_states", self.thread_states.close)
        drained = all(
            (
                indexer_drained,
                swapper_closed,
                scheduler_drained,
                thread_states_closed,
            )
        )
        if drained:
            with self._lock:
                self._shutdown_complete = True
        return drained
