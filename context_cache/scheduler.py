from __future__ import annotations

import queue
import random
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .scopes import ThreadKey

RefreshCallback = Callable[[int], None]


@dataclass(slots=True)
class _ScheduledRefresh:
    key: ThreadKey
    interval_seconds: float
    callback: RefreshCallback
    generation: int
    next_run: float
    last_error: str | None = None
    last_started_at: float | None = None
    last_finished_at: float | None = None


class DeskScheduler:
    """Coordinate periodic desk refreshes with one bounded worker pool."""

    def __init__(
        self,
        *,
        worker_count: int = 2,
        jitter_ratio: float = 0.1,
        max_tasks: int = 256,
        shutdown_timeout_seconds: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if worker_count < 1:
            raise ValueError("worker_count must be positive")
        if not 0.0 <= jitter_ratio <= 0.5:
            raise ValueError("jitter_ratio must be between 0 and 0.5")
        if max_tasks < 1:
            raise ValueError("max_tasks must be positive")
        if shutdown_timeout_seconds <= 0:
            raise ValueError("shutdown_timeout_seconds must be positive")
        self.worker_count = worker_count
        self.jitter_ratio = jitter_ratio
        self.max_tasks = max_tasks
        self.shutdown_timeout_seconds = shutdown_timeout_seconds
        self._clock = clock
        self._condition = threading.Condition(threading.RLock())
        self._tasks: dict[ThreadKey, _ScheduledRefresh] = {}
        self._inflight: set[ThreadKey] = set()
        self._next_generation = 0
        self._stop = False
        self._coordinator: threading.Thread | None = None
        self._work: queue.Queue[tuple[ThreadKey, int, RefreshCallback] | None] = (
            queue.Queue(maxsize=worker_count)
        )
        self._workers: list[threading.Thread] = []
        self._last_fatal_error: str | None = None

    def _delay(self, interval_seconds: float) -> float:
        spread = interval_seconds * self.jitter_ratio
        return max(0.01, interval_seconds + random.uniform(-spread, spread))

    def schedule(
        self,
        key: ThreadKey,
        *,
        interval_seconds: float,
        callback: RefreshCallback,
    ) -> int:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        with self._condition:
            if self._stop:
                raise RuntimeError("desk scheduler is closed")
            if key not in self._tasks and len(self._tasks) >= self.max_tasks:
                raise RuntimeError("periodic desk capacity is exhausted")
            self._next_generation += 1
            generation = self._next_generation
            self._tasks[key] = _ScheduledRefresh(
                key=key,
                interval_seconds=interval_seconds,
                callback=callback,
                generation=generation,
                next_run=self._clock() + self._delay(interval_seconds),
            )
            self._start_locked()
            self._condition.notify_all()
            return generation

    def _start_locked(self) -> None:
        if self._coordinator is not None:
            return
        self._workers = [
            threading.Thread(
                target=self._worker,
                name=f"library-desk-worker-{index}",
                daemon=True,
            )
            for index in range(self.worker_count)
        ]
        for worker in self._workers:
            worker.start()
        self._coordinator = threading.Thread(
            target=self._run,
            name="library-desk-scheduler",
            daemon=True,
        )
        self._coordinator.start()

    def is_current(self, key: ThreadKey, generation: int) -> bool:
        with self._condition:
            task = self._tasks.get(key)
            return task is not None and task.generation == generation

    def stop(self, key: ThreadKey) -> bool:
        with self._condition:
            removed = self._tasks.pop(key, None)
            if removed is not None:
                self._condition.notify_all()
            return removed is not None

    def _run(self) -> None:
        while True:
            with self._condition:
                if self._stop:
                    return
                now = self._clock()
                ready = [
                    task
                    for task in self._tasks.values()
                    if task.next_run <= now and task.key not in self._inflight
                ]
                if ready:
                    available_workers = max(0, self.worker_count - len(self._inflight))
                    for task in ready[:available_workers]:
                        self._inflight.add(task.key)
                        task.last_started_at = now
                        task.next_run = float("inf")
                        self._work.put_nowait(
                            (task.key, task.generation, task.callback)
                        )
                    if available_workers:
                        continue
                    self._condition.wait()
                    continue
                due = [
                    task.next_run
                    for task in self._tasks.values()
                    if task.key not in self._inflight
                ]
                timeout = None if not due else max(0.01, min(due) - now)
                self._condition.wait(timeout)

    def _worker(self) -> None:
        while True:
            item = self._work.get()
            try:
                if item is None:
                    return
                key, generation, callback = item
                with self._condition:
                    stopped = self._stop
                if stopped:
                    with self._condition:
                        self._inflight.discard(key)
                        self._condition.notify_all()
                    return
                self._execute(key, generation, callback)
            finally:
                self._work.task_done()

    def _execute(
        self,
        key: ThreadKey,
        generation: int,
        callback: RefreshCallback,
    ) -> None:
        error: str | None = None
        try:
            callback(generation)
        except Exception as exc:
            error = str(exc)
        except BaseException as exc:
            error = f"{type(exc).__name__}: {exc}"
            with self._condition:
                self._last_fatal_error = error
                self._stop = True
                self._condition.notify_all()
            raise
        finally:
            with self._condition:
                self._inflight.discard(key)
                task = self._tasks.get(key)
                if task is not None and task.generation == generation:
                    task.last_error = error
                    task.last_finished_at = self._clock()
                    task.next_run = task.last_finished_at + self._delay(
                        task.interval_seconds
                    )
                self._condition.notify_all()

    def status(self) -> list[dict[str, object]]:
        with self._condition:
            return [
                {
                    "collection": task.key.collection,
                    "session_id": task.key.session_id,
                    "interval_seconds": task.interval_seconds,
                    "generation": task.generation,
                    "alive": bool(self._coordinator and self._coordinator.is_alive()),
                    "degraded": self._last_fatal_error is not None,
                    "in_flight": task.key in self._inflight,
                    "last_error": task.last_error,
                    "last_started_at": task.last_started_at,
                    "last_finished_at": task.last_finished_at,
                }
                for task in self._tasks.values()
            ]

    def health(self) -> dict[str, object]:
        """Report bounded scheduler worker and fatal-error state."""

        with self._condition:
            started = self._coordinator is not None
            coordinator_alive = bool(
                self._coordinator is not None and self._coordinator.is_alive()
            )
            workers_alive = sum(worker.is_alive() for worker in self._workers)
            fatal_error = self._last_fatal_error
            return {
                "started": started,
                "coordinator_alive": coordinator_alive,
                "worker_count": self.worker_count,
                "workers_alive": workers_alive,
                "degraded": bool(
                    fatal_error is not None
                    or (started and workers_alive < self.worker_count)
                ),
                "last_fatal_error": fatal_error,
            }

    def close(self) -> bool:
        """Stop admission and wait once for coordinator and worker exit."""

        deadline = time.monotonic() + self.shutdown_timeout_seconds
        with self._condition:
            self._stop = True
            self._tasks.clear()
            self._condition.notify_all()
        if (
            self._coordinator is not None
            and self._coordinator is not threading.current_thread()
        ):
            self._coordinator.join(timeout=max(0.0, deadline - time.monotonic()))

        while True:
            try:
                pending = self._work.get_nowait()
            except queue.Empty:
                break
            if pending is not None:
                key, _generation, _callback = pending
                with self._condition:
                    self._inflight.discard(key)
            self._work.task_done()
        for _ in self._workers:
            try:
                self._work.put_nowait(None)
            except queue.Full:
                break
        for worker in self._workers:
            if worker is threading.current_thread():
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            worker.join(timeout=remaining)
        coordinator_alive = bool(
            self._coordinator is not None and self._coordinator.is_alive()
        )
        return not coordinator_alive and not any(
            worker.is_alive() for worker in self._workers
        )
