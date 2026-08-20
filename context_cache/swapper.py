from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .embeddings import estimate_tokens
from .engine import ContextCache
from .models import SearchHit, WorkingSet


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
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    last_error: str | None = None


class ContextSwapper:
    """Maintains a token-bounded reading desk on demand or on a timer."""

    def __init__(self, cache: ContextCache) -> None:
        self.cache = cache
        self._working_sets: dict[tuple[str, str], WorkingSet] = {}
        self._tasks: dict[tuple[str, str], _PeriodicTask] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _key(namespace: str, session_id: str) -> tuple[str, str]:
        return namespace, session_id

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
    ) -> WorkingSet:
        if token_budget <= 0:
            raise ValueError("token_budget must be positive")
        ns = self.cache.namespace if namespace is None else namespace
        previous = self.get(session_id, namespace=ns)
        hits = self.cache.retrieve(
            focus,
            top_k=max(top_k, top_k * 3),
            namespace=ns,
            filters=filters,
        )
        excluded = set(exclude_record_ids or [])
        if excluded:
            hits = [hit for hit in hits if hit.record.id not in excluded]
        by_id = {hit.record.id: hit for hit in hits}
        ordered: list[SearchHit] = []
        for record_id in pinned_record_ids or []:
            hit = by_id.pop(record_id, None)
            if hit is None:
                record = self.cache.get(record_id, namespace=ns)
                if record is not None:
                    hit = SearchHit(record, 1.0, 1.0, 0.0, 1.0, 1.0)
            if hit is not None:
                ordered.append(hit)
        ordered.extend(hit for hit in hits if hit.record.id in by_id)

        selected: list[SearchHit] = []
        sections: list[str] = []
        used = 0
        for hit in ordered:
            header = (
                f"[library-book id={hit.record.id} source={hit.record.source} "
                f"relevance={hit.score:.3f}]"
            )
            header_tokens = estimate_tokens(header) + 1
            remaining = token_budget - used - header_tokens
            if remaining <= 0:
                break
            text = hit.record.text
            text_tokens = estimate_tokens(text)
            if text_tokens > remaining:
                # Character truncation follows the same 4 chars/token estimator.
                text = text[: max(0, remaining * 4)].rstrip()
                if not text:
                    continue
                text += " …"
                text_tokens = estimate_tokens(text)
            sections.append(f"{header}\n{text}")
            selected.append(hit)
            used += header_tokens + text_tokens
            if len(selected) >= top_k:
                break

        context = "\n\n".join(sections)
        # Enforce the same estimator used by callers, including inter-section spacing.
        if estimate_tokens(context) > token_budget:
            context = context[: token_budget * 4]
        previous_ids = (
            [] if previous is None else [hit.record.id for hit in previous.hits]
        )
        selected_ids = [hit.record.id for hit in selected]
        previous_id_set = set(previous_ids)
        selected_id_set = set(selected_ids)
        working_set = WorkingSet(
            session_id=session_id,
            namespace=ns,
            focus=focus,
            hits=selected,
            context=context,
            token_count=estimate_tokens(context),
            token_budget=token_budget,
            refreshed_at=time.time(),
            swapped_in=[
                record_id
                for record_id in selected_ids
                if record_id not in previous_id_set
            ],
            swapped_out=[
                record_id
                for record_id in previous_ids
                if record_id not in selected_id_set
            ],
            retained=[
                record_id for record_id in selected_ids if record_id in previous_id_set
            ],
        )
        with self._lock:
            self._working_sets[self._key(ns, session_id)] = working_set
        if self.cache.redis is not None:
            self.cache.redis.put_working_set(working_set)
        return working_set

    def get(
        self, session_id: str, *, namespace: str | None = None
    ) -> WorkingSet | None:
        ns = self.cache.namespace if namespace is None else namespace
        with self._lock:
            working_set = self._working_sets.get(self._key(ns, session_id))
        if working_set is None and self.cache.redis is not None:
            working_set = self.cache.redis.get_working_set(ns, session_id)
            if working_set is not None:
                with self._lock:
                    self._working_sets[self._key(ns, session_id)] = working_set
        return working_set

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
    ) -> WorkingSet:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        ns = self.cache.namespace if namespace is None else namespace
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
        )

        def run() -> None:
            while not task.stop_event.wait(task.interval_seconds):
                try:
                    self.refresh(
                        task.session_id,
                        task.focus,
                        token_budget=task.token_budget,
                        top_k=task.top_k,
                        namespace=task.namespace,
                        filters=task.filters,
                        pinned_record_ids=task.pinned_record_ids,
                        exclude_record_ids=task.exclude_record_ids,
                    )
                    task.last_error = None
                except Exception as exc:  # keep the periodic service alive
                    task.last_error = str(exc)

        initial = self.refresh(
            session_id,
            focus,
            token_budget=token_budget,
            top_k=top_k,
            namespace=ns,
            filters=filters,
            pinned_record_ids=pinned_record_ids,
            exclude_record_ids=exclude_record_ids,
        )
        task.thread = threading.Thread(
            target=run, name=f"context-swapper-{session_id}", daemon=True
        )
        with self._lock:
            self._tasks[self._key(ns, session_id)] = task
        task.thread.start()
        return initial

    def update_focus(
        self, session_id: str, focus: str, *, namespace: str | None = None
    ) -> WorkingSet:
        ns = self.cache.namespace if namespace is None else namespace
        with self._lock:
            task = self._tasks.get(self._key(ns, session_id))
        if task is None:
            return self.refresh(session_id, focus, namespace=ns)
        task.focus = focus
        return self.refresh(
            session_id,
            focus,
            token_budget=task.token_budget,
            top_k=task.top_k,
            namespace=ns,
            filters=task.filters,
            pinned_record_ids=task.pinned_record_ids,
            exclude_record_ids=task.exclude_record_ids,
        )

    def stop_periodic(self, session_id: str, *, namespace: str | None = None) -> bool:
        ns = self.cache.namespace if namespace is None else namespace
        with self._lock:
            task = self._tasks.pop(self._key(ns, session_id), None)
        if task is None:
            return False
        task.stop_event.set()
        if task.thread is not None and task.thread is not threading.current_thread():
            task.thread.join(timeout=min(2.0, task.interval_seconds + 0.1))
        return True

    def status(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "session_id": task.session_id,
                    "namespace": task.namespace,
                    "focus": task.focus,
                    "interval_seconds": task.interval_seconds,
                    "alive": bool(task.thread and task.thread.is_alive()),
                    "last_error": task.last_error,
                }
                for task in self._tasks.values()
            ]

    def close(self) -> None:
        with self._lock:
            tasks = list(self._tasks.values())
            self._tasks.clear()
        for task in tasks:
            task.stop_event.set()
        for task in tasks:
            if task.thread is not None:
                task.thread.join(timeout=min(2.0, task.interval_seconds + 0.1))
