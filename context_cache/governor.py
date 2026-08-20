from __future__ import annotations

import hashlib
import queue
import threading
import time
import uuid
from collections import deque
from typing import TYPE_CHECKING, Any, Literal

from .embeddings import estimate_tokens
from .models import ContextEvent, GovernedPrompt, WorkingSet

if TYPE_CHECKING:
    from .engine import ContextCache


Role = Literal["system", "developer", "user", "assistant", "tool"]
_ROLES = {"system", "developer", "user", "assistant", "tool"}


class _RecentEventRing:
    """A FIFO thread overlay bounded by both event count and estimated tokens."""

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


class LibraryContextGovernor:
    """Own the durable record -> bounded prompt -> durable response lifecycle.

    The model receives a newly assembled prompt on every call. Full thread events stay
    in SQLite, recent events remain immediately visible through an in-memory overlay,
    and a bounded work ring indexes durable outbox events asynchronously.
    """

    def __init__(
        self,
        library: "ContextCache",
        session_id: str,
        *,
        collection: str | None = None,
        token_budget: int = 12000,
        recent_token_budget: int = 4000,
        protected_token_budget: int = 2000,
        max_books: int = 12,
        recent_ring_events: int = 256,
        work_ring_capacity: int = 1024,
        worker_poll_seconds: float = 0.1,
        start_worker: bool = True,
    ) -> None:
        if not session_id.strip():
            raise ValueError("session_id cannot be empty")
        if token_budget < 256:
            raise ValueError("token_budget must be at least 256")
        if recent_token_budget < 64 or recent_token_budget >= token_budget:
            raise ValueError("recent_token_budget must be >= 64 and below token_budget")
        if protected_token_budget < 0 or protected_token_budget >= token_budget:
            raise ValueError("protected_token_budget must be below token_budget")
        if max_books < 1:
            raise ValueError("max_books must be positive")
        if work_ring_capacity < 1:
            raise ValueError("work_ring_capacity must be positive")
        self.library = library
        self.session_id = session_id
        self.collection = library.namespace if collection is None else collection
        self.token_budget = token_budget
        self.recent_token_budget = recent_token_budget
        self.protected_token_budget = protected_token_budget
        self.max_books = max_books
        self.worker_poll_seconds = max(0.01, worker_poll_seconds)
        from .swapper import ContextSwapper

        self.desk = ContextSwapper(library)
        self._recent = _RecentEventRing(
            max_events=recent_ring_events,
            max_tokens=max(recent_token_budget * 2, 256),
        )
        for event in self.library.store.list_thread_events(
            self.collection, self.session_id, limit=recent_ring_events
        ):
            self._recent.append(event)
        self._work: queue.Queue[tuple[str, str]] = queue.Queue(
            maxsize=work_ring_capacity
        )
        self._queued: set[tuple[str, str]] = set()
        self._queue_lock = threading.RLock()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._last_error: str | None = None
        self._last_prompt_tokens = 0
        self._last_prompt_at: float | None = None
        self._scan_outbox()
        if start_worker:
            self._worker = threading.Thread(
                target=self._worker_loop,
                name=f"library-governor-{session_id}",
                daemon=True,
            )
            self._worker.start()

    @staticmethod
    def _message_tokens(messages: list[dict[str, str]]) -> int:
        return sum(estimate_tokens(message["content"]) + 4 for message in messages)

    @staticmethod
    def _truncate(text: str, token_budget: int) -> str:
        if token_budget <= 0:
            return ""
        if estimate_tokens(text) <= token_budget:
            return text
        marker = " … [full event remains in the Library]"
        marker_tokens = estimate_tokens(marker)
        if token_budget <= marker_tokens:
            return text[: token_budget * 4]
        return text[: (token_budget - marker_tokens) * 4].rstrip() + marker

    def _offer(self, namespace: str, event_id: str) -> bool:
        key = (namespace, event_id)
        with self._queue_lock:
            if key in self._queued:
                return True
            try:
                self._work.put_nowait(key)
            except queue.Full:
                return False
            self._queued.add(key)
            return True

    def _scan_outbox(self) -> None:
        available = max(1, self._work.maxsize - self._work.qsize())
        for namespace, event_id in self.library.store.pending_outbox_event_ids(
            limit=available
        ):
            if not self._offer(namespace, event_id):
                break

    def _index_event(self, event: ContextEvent) -> None:
        catalog = dict(event.metadata)
        catalog.update(
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
            metadata=catalog,
            source=f"conversation:{event.session_id}",
            importance=event.importance,
        )
        indexed_at = time.time()
        self.library.store.mark_thread_event_indexed(
            event.namespace, event.event_id, indexed_at=indexed_at
        )
        event.indexed_at = indexed_at

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                namespace, event_id = self._work.get(timeout=self.worker_poll_seconds)
            except queue.Empty:
                self._scan_outbox()
                continue
            key = (namespace, event_id)
            try:
                event = self.library.store.get_thread_event(namespace, event_id)
                if event is not None and event.indexed_at is None:
                    self._index_event(event)
                self._last_error = None
            except Exception as exc:  # keep durable work retryable
                self._last_error = str(exc)
                self.library.store.fail_outbox_event(
                    namespace,
                    event_id,
                    error=str(exc),
                    retry_after_seconds=0.5,
                )
            finally:
                with self._queue_lock:
                    self._queued.discard(key)
                self._work.task_done()
                self._scan_outbox()

    def record(
        self,
        role: Role,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        importance: float | None = None,
        protected: bool | None = None,
        event_id: str | None = None,
    ) -> ContextEvent:
        """Durably append one thread event and queue it for asynchronous indexing.

        System and developer roles are protected by default. A caller-supplied
        ``event_id`` makes retry idempotent; reusing it with different content fails.
        """

        if role not in _ROLES:
            raise ValueError(f"unsupported role: {role}")
        if not content.strip():
            raise ValueError("content cannot be empty")
        if importance is None:
            importance = 0.85 if role in {"system", "developer"} else 0.5
        if not 0.0 <= importance <= 1.0:
            raise ValueError("importance must be between 0 and 1")
        if protected is None:
            protected = role in {"system", "developer"}
        event_id = event_id or uuid.uuid4().hex
        record_id = hashlib.sha256(
            f"{self.collection}\x00{self.session_id}\x00{event_id}".encode("utf-8")
        ).hexdigest()[:24]
        event = self.library.store.append_thread_event(
            namespace=self.collection,
            session_id=self.session_id,
            event_id=event_id,
            role=role,
            content=content,
            metadata=dict(metadata or {}),
            importance=importance,
            protected=protected,
            token_count=estimate_tokens(content),
            record_id=record_id,
            created_at=time.time(),
        )
        self._recent.append(event)
        self._offer(event.namespace, event.event_id)
        return event

    def commit(
        self,
        content: str,
        *,
        role: Role = "assistant",
        metadata: dict[str, Any] | None = None,
        importance: float | None = None,
        protected: bool | None = None,
        event_id: str | None = None,
    ) -> ContextEvent:
        """Record the model or tool result after a governed model call."""

        return self.record(
            role,
            content,
            metadata=metadata,
            importance=importance,
            protected=protected,
            event_id=event_id,
        )

    def protect(
        self,
        content: str,
        *,
        role: Role = "developer",
        label: str | None = None,
        importance: float = 1.0,
        event_id: str | None = None,
    ) -> ContextEvent:
        """Append critical context that remains eligible until explicitly released."""

        metadata = {} if label is None else {"label": label}
        return self.record(
            role,
            content,
            metadata=metadata,
            importance=importance,
            protected=True,
            event_id=event_id,
        )

    def release(self, event_id: str) -> bool:
        """Remove protection without deleting the durable event or indexed record."""

        return self.library.store.set_thread_event_protected(
            self.collection, self.session_id, event_id, False
        )

    def _select_tail(
        self,
        events: list[ContextEvent],
        token_budget: int,
        *,
        excluded: set[str] | None = None,
    ) -> tuple[list[tuple[ContextEvent, str]], int]:
        excluded = excluded or set()
        selected: list[tuple[ContextEvent, str]] = []
        used = 0
        for event in reversed(events):
            if event.event_id in excluded:
                continue
            available = token_budget - used - 4
            if available <= 0:
                break
            if event.token_count > available:
                if selected:
                    break
                content = self._truncate(event.content, available)
            else:
                content = event.content
            cost = estimate_tokens(content) + 4
            if used + cost > token_budget:
                break
            selected.append((event, content))
            used += cost
        selected.reverse()
        return selected, used

    @staticmethod
    def _empty_desk(session_id: str, namespace: str, focus: str) -> WorkingSet:
        return WorkingSet(
            session_id=session_id,
            namespace=namespace,
            focus=focus,
            hits=[],
            context="",
            token_count=0,
            token_budget=0,
            refreshed_at=time.time(),
        )

    def build_prompt(
        self,
        *,
        focus: str | None = None,
        system_prompt: str = "",
        strict_freshness: bool = False,
    ) -> GovernedPrompt:
        """Build a bounded envelope from protected, recent, and retrieved context.

        Set ``strict_freshness`` only when the caller must wait for asynchronous index
        visibility. Normal interactive calls use the recent overlay instead.
        """

        if strict_freshness and not self.flush(timeout=10.0):
            raise TimeoutError("context index did not reach the recorded watermark")
        recent_events = self._recent.snapshot()
        if not recent_events:
            recent_events = self.library.store.list_thread_events(
                self.collection, self.session_id, limit=256
            )
        if not recent_events:
            raise ValueError("the governed thread has no events")

        base_messages = (
            [] if not system_prompt else [{"role": "system", "content": system_prompt}]
        )
        base_tokens = self._message_tokens(base_messages)
        if base_tokens + 64 >= self.token_budget:
            raise ValueError("system_prompt leaves no room for governed context")

        available = self.token_budget - base_tokens - 32
        protected_reserve = min(
            self.protected_token_budget,
            max(0, available - min(self.recent_token_budget, max(64, available // 2))),
        )
        recent_cap = min(self.recent_token_budget, available - protected_reserve)
        selected_recent, recent_tokens = self._select_tail(recent_events, recent_cap)
        recent_ids = {event.event_id for event, _ in selected_recent}

        protected_events = self.library.store.list_thread_events(
            self.collection,
            self.session_id,
            limit=256,
            protected_only=True,
        )
        protected_cap = min(self.protected_token_budget, available - recent_tokens)
        selected_protected, _ = self._select_tail(
            protected_events, protected_cap, excluded=recent_ids
        )

        event_messages = [
            {"role": event.role, "content": content}
            for event, content in selected_protected + selected_recent
        ]
        messages = list(base_messages) + event_messages
        selected_message_tokens = self._message_tokens(messages)
        subject = focus
        if subject is None:
            subject = next(
                (
                    event.content
                    for event in reversed(recent_events)
                    if event.role == "user"
                ),
                recent_events[-1].content,
            )

        library_wrapper = (
            '<library-context replacement="true" mode="semantic-paging" '
            f'session="{self.session_id}">\n\n</library-context>'
        )
        wrapper_tokens = estimate_tokens(library_wrapper) + 4
        desk_budget = max(
            0, self.token_budget - selected_message_tokens - wrapper_tokens
        )
        excluded_record_ids = [
            event.record_id
            for event, _ in selected_protected + selected_recent
            if event.record_id
        ]
        if subject.strip() and desk_budget >= 8:
            working = self.desk.refresh(
                self.session_id,
                subject,
                token_budget=desk_budget,
                top_k=self.max_books,
                namespace=self.collection,
                exclude_record_ids=excluded_record_ids,
            )
        else:
            working = self._empty_desk(self.session_id, self.collection, subject)

        if working.context:
            library_block = (
                '<library-context replacement="true" mode="semantic-paging" '
                f'session="{self.session_id}" '
                f'refreshed_at="{working.refreshed_at}">\n'
                f"{working.context}\n</library-context>"
            )
            if base_messages:
                messages[0] = {
                    "role": "system",
                    "content": system_prompt + "\n\n" + library_block,
                }
            else:
                messages.insert(0, {"role": "system", "content": library_block})

        token_count = self._message_tokens(messages)
        if token_count > self.token_budget and working.context:
            overflow = token_count - self.token_budget
            target = max(0, estimate_tokens(working.context) - overflow - 2)
            working.context = self._truncate(working.context, target)
            working.token_count = estimate_tokens(working.context)
            if working.context:
                library_block = (
                    '<library-context replacement="true" mode="semantic-paging" '
                    f'session="{self.session_id}" '
                    f'refreshed_at="{working.refreshed_at}">\n'
                    f"{working.context}\n</library-context>"
                )
                if base_messages:
                    messages[0] = {
                        "role": "system",
                        "content": system_prompt + "\n\n" + library_block,
                    }
                else:
                    messages[0] = {"role": "system", "content": library_block}
            token_count = self._message_tokens(messages)
        if token_count > self.token_budget:
            raise RuntimeError("governor failed to enforce the native context budget")

        watermarks = self.library.store.thread_watermarks(
            self.collection, self.session_id
        )
        event_count = watermarks.recorded_through
        selected_ids = recent_ids | {event.event_id for event, _ in selected_protected}
        active_protected_ids = [event.event_id for event, _ in selected_protected]
        active_protected_ids.extend(
            event.event_id
            for event, _ in selected_recent
            if event.protected and event.event_id not in active_protected_ids
        )
        self._last_prompt_tokens = token_count
        self._last_prompt_at = time.time()
        return GovernedPrompt(
            session_id=self.session_id,
            collection=self.collection,
            messages=messages,
            token_count=token_count,
            token_budget=self.token_budget,
            event_count=event_count,
            recent_event_ids=[event.event_id for event, _ in selected_recent],
            protected_event_ids=active_protected_ids,
            desk=working,
            watermarks=watermarks,
            paged_out_events=max(0, event_count - len(selected_ids)),
            native_context_pressure=token_count / self.token_budget,
        )

    def prepare(
        self,
        user_message: str,
        *,
        focus: str | None = None,
        system_prompt: str = "",
        metadata: dict[str, Any] | None = None,
        importance: float = 0.5,
        protected: bool = False,
        event_id: str | None = None,
        strict_freshness: bool = False,
    ) -> GovernedPrompt:
        """Durably record a user turn, then construct its bounded model request."""

        self.record(
            "user",
            user_message,
            metadata=metadata,
            importance=importance,
            protected=protected,
            event_id=event_id,
        )
        return self.build_prompt(
            focus=focus or user_message,
            system_prompt=system_prompt,
            strict_freshness=strict_freshness,
        )

    def flush(self, *, timeout: float = 10.0) -> bool:
        """Wait until the thread's outbox has reached its indexed watermark."""

        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            self._scan_outbox()
            watermarks = self.library.store.thread_watermarks(
                self.collection, self.session_id
            )
            if watermarks.pending_events == 0:
                return True
            if time.monotonic() >= deadline:
                return False
            if self._worker is None or not self._worker.is_alive():
                return False
            self._stop.wait(min(0.02, max(0.0, deadline - time.monotonic())))

    def status(self) -> dict[str, Any]:
        """Return visibility watermarks, ring pressure, and worker health."""

        watermarks = self.library.store.thread_watermarks(
            self.collection, self.session_id
        )
        return {
            "session_id": self.session_id,
            "collection": self.collection,
            "context_mode": "semantic-paging",
            "replaces_compaction": True,
            "watermarks": watermarks.to_dict(),
            "recent_ring": self._recent.stats(),
            "work_ring": {
                "queued": self._work.qsize(),
                "capacity": self._work.maxsize,
                "occupancy": self._work.qsize() / self._work.maxsize,
            },
            "worker_alive": bool(self._worker and self._worker.is_alive()),
            "last_error": self._last_error,
            "last_prompt_tokens": self._last_prompt_tokens,
            "last_prompt_at": self._last_prompt_at,
        }

    def close(self) -> None:
        """Stop the indexing worker and close the reading-desk scheduler."""

        self._stop.set()
        if self._worker is not None and self._worker is not threading.current_thread():
            self._worker.join(timeout=2.0)
        self.desk.close()

    def __enter__(self) -> "LibraryContextGovernor":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
