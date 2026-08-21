from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from contextlib import nullcontext
from dataclasses import dataclass
from functools import wraps
from typing import TYPE_CHECKING, Any, TypeVar, cast

from .context_markup import BOOK_TRUNCATION_MARKER, format_library_book
from .embeddings import estimate_tokens
from .limits import MAX_CONTEXT_TOKENS, MAX_RESULT_BOOKS
from .models import SearchHit, WorkingSet
from .scheduler import DeskScheduler
from .scopes import ContextScope, ThreadKey

if TYPE_CHECKING:
    from .engine import ContextCache

SwapperResult = TypeVar("SwapperResult")


def _open_cache_operation(
    method: Callable[..., SwapperResult],
) -> Callable[..., SwapperResult]:
    @wraps(method)
    def guarded(self: "ContextSwapper", *args: Any, **kwargs: Any) -> SwapperResult:
        operation = getattr(self.cache, "_operation", None)
        lease = nullcontext() if operation is None else operation()
        with lease:
            return method(self, *args, **kwargs)

    return cast(Callable[..., SwapperResult], guarded)


def _status_cache_operation(
    method: Callable[..., SwapperResult],
) -> Callable[..., SwapperResult]:
    @wraps(method)
    def guarded(self: "ContextSwapper", *args: Any, **kwargs: Any) -> SwapperResult:
        operation = getattr(self.cache, "_operation", None)
        lease = nullcontext() if operation is None else operation(allow_closing=True)
        with lease:
            return method(self, *args, **kwargs)

    return cast(Callable[..., SwapperResult], guarded)


@dataclass(slots=True)
class _PeriodicTask:
    session_id: str
    focus: str
    interval_seconds: float
    token_budget: int
    top_k: int
    namespace: str
    filters: dict[str, Any] | None
    pinned_record_ids: list[str]
    exclude_record_ids: list[str]
    team_ids: tuple[str, ...]


class ContextSwapper:
    """Maintains a token-bounded reading desk on demand or on a timer."""

    def __init__(
        self,
        cache: ContextCache,
        *,
        scheduler: DeskScheduler | None = None,
        max_working_sets: int = 256,
        working_set_ttl_seconds: float = 1800.0,
    ) -> None:
        if max_working_sets < 1:
            raise ValueError("max_working_sets must be positive")
        if working_set_ttl_seconds <= 0:
            raise ValueError("working_set_ttl_seconds must be positive")
        self.cache = cache
        self.max_working_sets = max_working_sets
        self.working_set_ttl_seconds = working_set_ttl_seconds
        self._working_sets: OrderedDict[tuple[str, str], WorkingSet] = OrderedDict()
        self._working_set_access: dict[tuple[str, str], float] = {}
        self._refresh_generations: OrderedDict[tuple[str, str], int] = OrderedDict()
        self._refresh_leases: dict[tuple[str, str], int] = {}
        self._next_refresh_generation = 0
        self._tasks: dict[tuple[str, str], _PeriodicTask] = {}
        self._periodic_locks = tuple(threading.RLock() for _ in range(64))
        self._lock = threading.RLock()
        self._scheduler = scheduler or DeskScheduler()
        self._owns_scheduler = scheduler is None

    @staticmethod
    def _key(namespace: str, session_id: str) -> tuple[str, str]:
        return namespace, session_id

    def _periodic_lock(self, namespace: str, session_id: str) -> threading.RLock:
        slot = hash((namespace, session_id)) % len(self._periodic_locks)
        return self._periodic_locks[slot]

    @staticmethod
    def _copy_working_set(working_set: WorkingSet) -> WorkingSet:
        return WorkingSet.from_dict(working_set.to_dict())

    @staticmethod
    def _contains_expired_record(working_set: WorkingSet, now: float) -> bool:
        return any(
            hit.record.expires_at is not None and hit.record.expires_at <= now
            for hit in working_set.hits
        )

    def _prune_working_sets_locked(self) -> None:
        cutoff = time.monotonic() - self.working_set_ttl_seconds
        expired = [
            key
            for key, accessed in self._working_set_access.items()
            if accessed <= cutoff
        ]
        for key in expired:
            self._working_sets.pop(key, None)
            self._working_set_access.pop(key, None)
            if self._refresh_leases.get(key, 0) == 0 and key not in self._tasks:
                self._refresh_generations.pop(key, None)

    def _begin_refresh_locked(self, key: tuple[str, str]) -> int:
        self._prune_working_sets_locked()
        if key not in self._refresh_generations:
            while len(self._refresh_generations) >= self.max_working_sets:
                removable = next(
                    (
                        candidate
                        for candidate in self._refresh_generations
                        if self._refresh_leases.get(candidate, 0) == 0
                        and candidate not in self._tasks
                    ),
                    None,
                )
                if removable is None:
                    raise RuntimeError("reading-desk refresh capacity is exhausted")
                self._refresh_generations.pop(removable, None)
                self._working_sets.pop(removable, None)
                self._working_set_access.pop(removable, None)
        self._next_refresh_generation += 1
        generation = self._next_refresh_generation
        self._refresh_generations[key] = generation
        self._refresh_generations.move_to_end(key)
        self._refresh_leases[key] = self._refresh_leases.get(key, 0) + 1
        return generation

    def _finish_refresh_locked(self, key: tuple[str, str]) -> None:
        leases = max(0, self._refresh_leases.get(key, 0) - 1)
        if leases:
            self._refresh_leases[key] = leases
        else:
            self._refresh_leases.pop(key, None)

    def _invalidate_refresh_locked(self, key: tuple[str, str]) -> None:
        self._next_refresh_generation += 1
        self._refresh_generations[key] = self._next_refresh_generation
        self._refresh_generations.move_to_end(key)

    def _store_working_set_locked(
        self,
        key: tuple[str, str],
        working_set: WorkingSet,
    ) -> None:
        self._prune_working_sets_locked()
        if key not in self._working_sets:
            while len(self._working_sets) >= self.max_working_sets:
                removed_key, _ = self._working_sets.popitem(last=False)
                self._working_set_access.pop(removed_key, None)
                if (
                    self._refresh_leases.get(removed_key, 0) == 0
                    and removed_key not in self._tasks
                ):
                    self._refresh_generations.pop(removed_key, None)
        self._working_sets[key] = self._copy_working_set(working_set)
        self._working_sets.move_to_end(key)
        self._working_set_access[key] = time.monotonic()

    def _retrieve_and_order_hits(
        self,
        focus: str,
        *,
        session_id: str,
        namespace: str,
        top_k: int,
        filters: dict[str, Any] | None,
        pinned_record_ids: list[str] | None,
        exclude_record_ids: list[str] | None,
        team_ids: tuple[str, ...],
    ) -> list[SearchHit]:
        scopes = [ContextScope.THREAD, ContextScope.PROJECT]
        if team_ids:
            scopes.append(ContextScope.TEAM)
        hits = self.cache.retrieve(
            focus,
            top_k=min(MAX_RESULT_BOOKS, max(top_k, top_k * 3)),
            namespace=namespace,
            filters=filters,
            scopes=scopes,
            session_id=session_id,
            team_ids=team_ids,
        )
        excluded = set(exclude_record_ids or [])
        if excluded:
            hits = [hit for hit in hits if hit.record.id not in excluded]

        by_id = {hit.record.id: hit for hit in hits}
        ordered: list[SearchHit] = []
        for record_id in pinned_record_ids or []:
            hit = by_id.pop(record_id, None)
            if hit is None:
                record = self.cache.get(
                    record_id,
                    namespace=namespace,
                    scopes=scopes,
                    session_id=session_id,
                    team_ids=team_ids,
                )
                if record is not None:
                    hit = SearchHit(record, 1.0, 1.0, 0.0, 1.0, 1.0)
            if hit is not None:
                ordered.append(hit)
        ordered.extend(hit for hit in hits if hit.record.id in by_id)
        return ordered

    @staticmethod
    def _pack_hits(
        ordered: list[SearchHit], *, token_budget: int, top_k: int
    ) -> tuple[list[SearchHit], str]:
        selected: list[SearchHit] = []
        sections: list[str] = []
        for hit in ordered:
            section = format_library_book(
                record_id=hit.record.id,
                source=hit.record.source,
                relevance=hit.score,
                text=hit.record.text,
            )
            candidate = "\n\n".join([*sections, section])
            if estimate_tokens(candidate) <= token_budget:
                sections.append(section)
                selected.append(hit)
                if len(selected) >= top_k:
                    break
                continue

            low = 1
            high = len(hit.record.text)
            fitted: str | None = None
            while low <= high:
                midpoint = (low + high) // 2
                section = format_library_book(
                    record_id=hit.record.id,
                    source=hit.record.source,
                    relevance=hit.score,
                    text=hit.record.text[:midpoint] + BOOK_TRUNCATION_MARKER,
                )
                candidate = "\n\n".join([*sections, section])
                if estimate_tokens(candidate) <= token_budget:
                    fitted = section
                    low = midpoint + 1
                else:
                    high = midpoint - 1
            if fitted is None:
                break
            sections.append(fitted)
            selected.append(hit)
            break

        context = "\n\n".join(sections)
        return selected, context

    @staticmethod
    def _swap_diff(
        previous: WorkingSet | None, selected: list[SearchHit]
    ) -> tuple[list[str], list[str], list[str]]:
        previous_ids = (
            [] if previous is None else [hit.record.id for hit in previous.hits]
        )
        selected_ids = [hit.record.id for hit in selected]
        previous_id_set = set(previous_ids)
        selected_id_set = set(selected_ids)
        swapped_in = [
            record_id for record_id in selected_ids if record_id not in previous_id_set
        ]
        swapped_out = [
            record_id for record_id in previous_ids if record_id not in selected_id_set
        ]
        retained = [
            record_id for record_id in selected_ids if record_id in previous_id_set
        ]
        return swapped_in, swapped_out, retained

    @_open_cache_operation
    def refresh(
        self,
        session_id: str,
        focus: str,
        *,
        token_budget: int = 4000,
        top_k: int = 12,
        namespace: str | None = None,
        filters: dict[str, Any] | None = None,
        pinned_record_ids: list[str] | None = None,
        exclude_record_ids: list[str] | None = None,
        team_ids: Iterable[str] = (),
        _publish_if: Any = None,
    ) -> WorkingSet:
        if token_budget <= 0:
            raise ValueError("token_budget must be positive")
        if token_budget > MAX_CONTEXT_TOKENS:
            raise ValueError(f"token_budget cannot exceed {MAX_CONTEXT_TOKENS}")
        if not 1 <= top_k <= MAX_RESULT_BOOKS:
            raise ValueError(f"top_k must be between 1 and {MAX_RESULT_BOOKS}")
        ns = self.cache.namespace if namespace is None else namespace
        key = self._key(ns, session_id)
        with self._lock:
            generation = self._begin_refresh_locked(key)
        try:
            previous = self.get(session_id, namespace=ns)
            ordered = self._retrieve_and_order_hits(
                focus,
                session_id=session_id,
                namespace=ns,
                top_k=top_k,
                filters=filters,
                pinned_record_ids=pinned_record_ids,
                exclude_record_ids=exclude_record_ids,
                team_ids=tuple(team_ids),
            )
            selected, context = self._pack_hits(
                ordered,
                token_budget=token_budget,
                top_k=top_k,
            )
            swapped_in, swapped_out, retained = self._swap_diff(previous, selected)
            working_set = WorkingSet(
                session_id=session_id,
                namespace=ns,
                focus=focus,
                hits=selected,
                context=context,
                token_count=estimate_tokens(context),
                token_budget=token_budget,
                refreshed_at=time.time(),
                swapped_in=swapped_in,
                swapped_out=swapped_out,
                retained=retained,
            )
            permitted = _publish_if is None or bool(_publish_if())
            with self._lock:
                is_latest = self._refresh_generations.get(key) == generation
                if is_latest and permitted:
                    self._store_working_set_locked(
                        key,
                        working_set,
                    )
                    if self.cache.redis is not None:
                        self.cache.redis.put_working_set(working_set)
            return self._copy_working_set(working_set)
        finally:
            with self._lock:
                self._finish_refresh_locked(key)

    @_open_cache_operation
    def get(
        self, session_id: str, *, namespace: str | None = None
    ) -> WorkingSet | None:
        ns = self.cache.namespace if namespace is None else namespace
        key = self._key(ns, session_id)
        with self._lock:
            self._prune_working_sets_locked()
            working_set = self._working_sets.get(key)
            if working_set is not None and self._contains_expired_record(
                working_set, time.time()
            ):
                self._working_sets.pop(key, None)
                self._working_set_access.pop(key, None)
                working_set = None
            if working_set is not None:
                self._working_sets.move_to_end(key)
                self._working_set_access[key] = time.monotonic()
        if working_set is None and self.cache.redis is not None:
            fetched = self.cache.redis.get_working_set(ns, session_id)
            if fetched is not None and self._contains_expired_record(
                fetched, time.time()
            ):
                fetched = None
            with self._lock:
                current = self._working_sets.get(key)
                if current is not None and self._contains_expired_record(
                    current, time.time()
                ):
                    self._working_sets.pop(key, None)
                    self._working_set_access.pop(key, None)
                    current = None
                if current is not None and (
                    fetched is None or current.refreshed_at >= fetched.refreshed_at
                ):
                    working_set = current
                elif fetched is not None:
                    self._store_working_set_locked(key, fetched)
                    working_set = fetched
        return None if working_set is None else self._copy_working_set(working_set)

    @_open_cache_operation
    def start_periodic(
        self,
        session_id: str,
        focus: str,
        *,
        interval_seconds: float = 30.0,
        token_budget: int = 4000,
        top_k: int = 12,
        namespace: str | None = None,
        filters: dict[str, Any] | None = None,
        pinned_record_ids: list[str] | None = None,
        exclude_record_ids: list[str] | None = None,
        team_ids: Iterable[str] = (),
    ) -> WorkingSet:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        ns = self.cache.namespace if namespace is None else namespace
        key = ThreadKey(ns, session_id)
        with self._periodic_lock(ns, session_id):
            self.stop_periodic(session_id, namespace=ns)
            task = _PeriodicTask(
                session_id=session_id,
                focus=focus,
                interval_seconds=interval_seconds,
                token_budget=token_budget,
                top_k=top_k,
                namespace=ns,
                filters=filters,
                pinned_record_ids=list(pinned_record_ids or []),
                exclude_record_ids=list(exclude_record_ids or []),
                team_ids=tuple(team_ids),
            )

            initial = self.refresh(
                session_id,
                focus,
                token_budget=token_budget,
                top_k=top_k,
                namespace=ns,
                filters=filters,
                pinned_record_ids=pinned_record_ids,
                exclude_record_ids=exclude_record_ids,
                team_ids=team_ids,
            )
            with self._lock:
                self._tasks[self._key(ns, session_id)] = task

            def run(generation: int) -> None:
                self.refresh(
                    task.session_id,
                    task.focus,
                    token_budget=task.token_budget,
                    top_k=task.top_k,
                    namespace=task.namespace,
                    filters=task.filters,
                    pinned_record_ids=task.pinned_record_ids,
                    exclude_record_ids=task.exclude_record_ids,
                    team_ids=task.team_ids,
                    _publish_if=lambda: self._scheduler.is_current(key, generation),
                )

            try:
                self._scheduler.schedule(
                    key,
                    interval_seconds=interval_seconds,
                    callback=run,
                )
            except Exception:
                with self._lock:
                    if self._tasks.get(self._key(ns, session_id)) is task:
                        self._tasks.pop(self._key(ns, session_id), None)
                raise
            return initial

    @_open_cache_operation
    def update_focus(
        self, session_id: str, focus: str, *, namespace: str | None = None
    ) -> WorkingSet:
        ns = self.cache.namespace if namespace is None else namespace
        with self._periodic_lock(ns, session_id):
            with self._lock:
                task = self._tasks.get(self._key(ns, session_id))
                if task is not None:
                    task.focus = focus
                    self._invalidate_refresh_locked(self._key(ns, session_id))
            if task is None:
                return self.refresh(session_id, focus, namespace=ns)
            key = ThreadKey(ns, session_id)

            def run(generation: int) -> None:
                self.refresh(
                    task.session_id,
                    task.focus,
                    token_budget=task.token_budget,
                    top_k=task.top_k,
                    namespace=task.namespace,
                    filters=task.filters,
                    pinned_record_ids=task.pinned_record_ids,
                    exclude_record_ids=task.exclude_record_ids,
                    team_ids=task.team_ids,
                    _publish_if=lambda: self._scheduler.is_current(key, generation),
                )

            self._scheduler.schedule(
                key,
                interval_seconds=task.interval_seconds,
                callback=run,
            )
            return self.refresh(
                session_id,
                focus,
                token_budget=task.token_budget,
                top_k=task.top_k,
                namespace=ns,
                filters=task.filters,
                pinned_record_ids=task.pinned_record_ids,
                exclude_record_ids=task.exclude_record_ids,
                team_ids=task.team_ids,
            )

    @_open_cache_operation
    def stop_periodic(self, session_id: str, *, namespace: str | None = None) -> bool:
        ns = self.cache.namespace if namespace is None else namespace
        with self._periodic_lock(ns, session_id):
            with self._lock:
                local_key = self._key(ns, session_id)
                task = self._tasks.pop(local_key, None)
                if task is not None:
                    self._invalidate_refresh_locked(local_key)
            scheduler_stopped = self._scheduler.stop(ThreadKey(ns, session_id))
            return task is not None or scheduler_stopped

    @_status_cache_operation
    def status(self, *, namespace: str | None = None) -> list[dict[str, Any]]:
        scheduler_status = {
            (item["collection"], item["session_id"]): item
            for item in self._scheduler.status()
        }
        with self._lock:
            return [
                {
                    **scheduler_status.get(
                        (task.namespace, task.session_id),
                        {
                            "collection": task.namespace,
                            "session_id": task.session_id,
                        },
                    ),
                    "namespace": task.namespace,
                    "focus": task.focus,
                    "interval_seconds": task.interval_seconds,
                }
                for task in self._tasks.values()
                if namespace is None or task.namespace == namespace
            ]

    def close(self) -> None:
        with self._lock:
            keys = [
                ThreadKey(task.namespace, task.session_id)
                for task in self._tasks.values()
            ]
            self._tasks.clear()
        for key in keys:
            self._scheduler.stop(key)
        if self._owns_scheduler:
            self._scheduler.close()
        with self._lock:
            self._working_sets.clear()
            self._working_set_access.clear()
            self._refresh_generations.clear()
            self._refresh_leases.clear()
