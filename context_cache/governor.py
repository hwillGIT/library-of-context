from __future__ import annotations

import hashlib
import time
import uuid
from typing import TYPE_CHECKING, Any, Literal

from .embeddings import estimate_tokens
from .indexing import ContextEventIndexer
from .models import ContextEvent, GovernedPrompt
from .prompt_builder import GovernedPromptBuilder, PromptBudget
from .rings import RecentEventRing

if TYPE_CHECKING:
    from .engine import ContextCache


Role = Literal["system", "developer", "user", "assistant", "tool"]
_ROLES = {"system", "developer", "user", "assistant", "tool"}


class LibraryContextGovernor:
    """Own the durable record -> bounded prompt -> durable response lifecycle.

    The model receives a bounded prompt assembled for every call. Full thread events stay
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
        self._recent = RecentEventRing(
            max_events=recent_ring_events,
            max_tokens=max(recent_token_budget * 2, 256),
        )
        for event in self.library.store.list_thread_events(
            self.collection, self.session_id, limit=recent_ring_events
        ):
            self._recent.append(event)
        self._indexer = ContextEventIndexer(
            library,
            capacity=work_ring_capacity,
            poll_seconds=self.worker_poll_seconds,
        )
        self._prompt_builder = GovernedPromptBuilder(
            library,
            self.desk,
            self._recent,
            session_id=self.session_id,
            collection=self.collection,
            budget=PromptBudget(
                total_tokens=self.token_budget,
                recent_tokens=self.recent_token_budget,
                protected_tokens=self.protected_token_budget,
                max_books=self.max_books,
            ),
        )
        self._last_prompt_tokens = 0
        self._last_prompt_at: float | None = None
        if start_worker:
            self._indexer.start(
                name=f"library-governor-{session_id}",
            )

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
        self._indexer.enqueue(event.namespace, event.event_id)
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
        envelope = self._prompt_builder.build(
            focus=focus,
            system_prompt=system_prompt,
        )
        self._last_prompt_tokens = envelope.token_count
        self._last_prompt_at = time.time()
        return envelope

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

        return self._indexer.flush(
            self.collection,
            self.session_id,
            timeout=timeout,
        )

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
            "work_ring": self._indexer.status(),
            "worker_alive": self._indexer.is_alive,
            "last_error": self._indexer.last_error,
            "last_prompt_tokens": self._last_prompt_tokens,
            "last_prompt_at": self._last_prompt_at,
        }

    def close(self) -> None:
        """Stop the indexing worker and close the reading-desk scheduler."""

        self._indexer.close()
        self.desk.close()

    def __enter__(self) -> "LibraryContextGovernor":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
