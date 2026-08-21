from __future__ import annotations

import hashlib
import time
from typing import Literal

from .context_markup import format_library_context
from .embeddings import estimate_tokens
from .library import LibraryOfContext
from .limits import MAX_CONTEXT_TOKENS, MAX_RESULT_BOOKS
from .models import ContextRecord, PromptEnvelope, WorkingSet
from .scopes import ContextScope, ScopeSelection, ThreadKey
from .text_budget import (
    MESSAGE_TRUNCATION_MARKER,
    message_token_count,
    truncate_text,
)

Role = Literal["system", "user", "assistant", "developer", "tool"]


class VirtualContextSession:
    """Build bounded, stateless model requests from disk-backed conversation history.

    The complete history is shelved in the Library. Each prompt contains only a recent
    window plus a freshly retrieved reading desk, so live input does not grow with the
    number of stored turns.
    """

    def __init__(
        self,
        library: LibraryOfContext,
        session_id: str,
        *,
        collection: str | None = None,
        token_budget: int = 12000,
        recent_token_budget: int = 4000,
    ) -> None:
        if not session_id.strip():
            raise ValueError("session_id cannot be empty")
        if token_budget < 256:
            raise ValueError("token_budget must be at least 256")
        if token_budget > MAX_CONTEXT_TOKENS:
            raise ValueError(f"token_budget cannot exceed {MAX_CONTEXT_TOKENS}")
        if recent_token_budget < 64 or recent_token_budget >= token_budget:
            raise ValueError("recent_token_budget must be >= 64 and below token_budget")
        self.library = library
        self.session_id = session_id
        self.collection = library.namespace if collection is None else collection
        self.token_budget = token_budget
        self.recent_token_budget = recent_token_budget
        self.source = f"conversation:{session_id}"
        self._key = ThreadKey(self.collection, self.session_id)
        self._selection = ScopeSelection.for_thread(
            self.session_id,
            include_project=False,
        )
        self.desk = library.open_reading_desk()

    def _record_locked(
        self,
        role: Role,
        content: str,
        *,
        importance: float | None = None,
    ) -> ContextRecord:
        if not content.strip():
            raise ValueError("message content cannot be empty")
        turn = (
            self.library.store.count_source(
                self.collection,
                self.source,
                selection=self._selection,
            )
            + 1
        )
        digest = hashlib.sha256(
            f"{self.collection}\x00{self.session_id}\x00{turn}\x00{role}\x00{content}".encode(
                "utf-8"
            )
        ).hexdigest()[:24]
        if importance is None:
            importance = 0.65 if role in {"developer", "system"} else 0.5
        return self.library.shelve(
            content,
            book_id=digest,
            collection=self.collection,
            catalog={
                "kind": "conversation",
                "session_id": self.session_id,
                "role": role,
                "turn": turn,
            },
            source=self.source,
            importance=importance,
            scope=ContextScope.THREAD,
            owner_session_id=self.session_id,
        )

    def record(
        self,
        role: Role,
        content: str,
        *,
        importance: float | None = None,
    ) -> ContextRecord:
        with self.library._operation():
            with self.library.runtime.thread_states.lease(self._key) as state:
                with state.operation_lock:
                    return self._record_locked(
                        role,
                        content,
                        importance=importance,
                    )

    def history(self, *, limit: int | None = None) -> list[ContextRecord]:
        with self.library._operation():
            return self.library.store.list_source_records(
                self.collection,
                self.source,
                limit=limit,
                selection=self._selection,
            )

    def _recent(self, max_tokens: int) -> list[ContextRecord]:
        history = self.history()
        selected: list[ContextRecord] = []
        used = 0
        for record in reversed(history):
            cost = estimate_tokens(record.text) + 4
            if selected and used + cost > max_tokens:
                break
            selected.append(record)
            used += cost
        return list(reversed(selected))

    def _empty_desk(self, focus: str) -> WorkingSet:
        return WorkingSet(
            session_id=self.session_id,
            namespace=self.collection,
            focus=focus,
            hits=[],
            context="",
            token_count=0,
            token_budget=0,
            refreshed_at=time.time(),
        )

    def _library_block(self, working: WorkingSet) -> str:
        return format_library_context(
            working.context,
            session_id=self.session_id,
            refreshed_at=working.refreshed_at,
            mode="virtual-session",
        )

    def _retrieve_context(
        self,
        *,
        subject: str,
        base_messages: list[dict[str, str]],
        recent_messages: list[dict[str, str]],
        recent: list[ContextRecord],
        max_books: int,
    ) -> WorkingSet:
        wrapper_tokens = estimate_tokens(
            format_library_context(
                "",
                session_id=self.session_id,
                refreshed_at=time.time(),
                mode="virtual-session",
            )
        )
        desk_budget = max(
            0,
            self.token_budget
            - message_token_count(base_messages + recent_messages)
            - wrapper_tokens
            - 8,
        )
        if desk_budget < 8:
            return self._empty_desk(subject)
        return self.desk.refresh(
            self.session_id,
            subject,
            token_budget=desk_budget,
            top_k=max_books,
            namespace=self.collection,
            exclude_record_ids=[record.id for record in recent],
        )

    def _attach_context(
        self,
        *,
        base_messages: list[dict[str, str]],
        recent_messages: list[dict[str, str]],
        working: WorkingSet,
    ) -> list[dict[str, str]]:
        messages = list(base_messages)
        if working.context:
            library_block = self._library_block(working)
            if messages:
                messages[0] = {
                    "role": "system",
                    "content": messages[0]["content"] + "\n\n" + library_block,
                }
            else:
                messages.append({"role": "system", "content": library_block})
        messages.extend(recent_messages)
        return messages

    def _enforce_budget(
        self,
        messages: list[dict[str, str]],
        *,
        system_prompt: str,
        working: WorkingSet,
        has_base_message: bool,
    ) -> int:
        token_count = message_token_count(messages)
        if token_count > self.token_budget and working.context:
            overflow = token_count - self.token_budget
            target = max(0, working.token_count - overflow - 8)
            selected, context = self.desk._pack_hits(
                working.hits,
                token_budget=target,
                top_k=max(1, len(working.hits)),
            )
            working.hits = selected
            working.context = context
            working.token_count = estimate_tokens(context)
            if working.context and has_base_message:
                messages[0] = {
                    "role": "system",
                    "content": system_prompt + "\n\n" + self._library_block(working),
                }
            elif working.context:
                messages[0] = {
                    "role": "system",
                    "content": self._library_block(working),
                }
            elif has_base_message:
                messages[0] = {"role": "system", "content": system_prompt}
            else:
                messages.pop(0)
            token_count = message_token_count(messages)
        if token_count > self.token_budget:
            raise RuntimeError("virtual session failed to enforce the context budget")
        return token_count

    def _build_prompt_locked(
        self,
        *,
        user_message: str | None = None,
        system_prompt: str = "",
        record_user: bool = True,
        max_books: int = 12,
    ) -> PromptEnvelope:
        if not 1 <= max_books <= MAX_RESULT_BOOKS:
            raise ValueError(f"max_books must be between 1 and {MAX_RESULT_BOOKS}")
        if user_message is not None and record_user:
            self._record_locked("user", user_message, importance=None)
        base_messages = (
            [] if not system_prompt else [{"role": "system", "content": system_prompt}]
        )
        base_tokens = message_token_count(base_messages)
        if base_tokens + 64 >= self.token_budget:
            raise ValueError(
                "system_prompt leaves no room for conversation or retrieval"
            )
        recent_budget = min(
            self.recent_token_budget, self.token_budget - base_tokens - 64
        )
        recent = self._recent(recent_budget)
        if not recent and user_message is None:
            raise ValueError("a user_message or existing session history is required")
        subject = user_message or recent[-1].text
        recent_messages: list[dict[str, str]] = []
        recent_used = 0
        for record in recent:
            remaining = max(1, recent_budget - recent_used - 4)
            content = truncate_text(
                record.text,
                remaining,
                marker=MESSAGE_TRUNCATION_MARKER,
            )
            recent_messages.append(
                {
                    "role": str(record.metadata.get("role", "user")),
                    "content": content,
                }
            )
            recent_used += estimate_tokens(content) + 4
        working = self._retrieve_context(
            subject=subject,
            base_messages=base_messages,
            recent_messages=recent_messages,
            recent=recent,
            max_books=max_books,
        )
        messages = self._attach_context(
            base_messages=base_messages,
            recent_messages=recent_messages,
            working=working,
        )
        token_count = self._enforce_budget(
            messages,
            system_prompt=system_prompt,
            working=working,
            has_base_message=bool(base_messages),
        )
        return PromptEnvelope(
            session_id=self.session_id,
            collection=self.collection,
            messages=messages,
            token_count=token_count,
            token_budget=self.token_budget,
            history_books=self.library.store.count_source(
                self.collection,
                self.source,
                selection=self._selection,
            ),
            recent_books=[record.id for record in recent],
            desk=working,
        )

    def build_prompt(
        self,
        *,
        user_message: str | None = None,
        system_prompt: str = "",
        record_user: bool = True,
        max_books: int = 12,
    ) -> PromptEnvelope:
        with self.library._operation():
            with self.library.runtime.thread_states.lease(self._key) as state:
                with state.operation_lock:
                    return self._build_prompt_locked(
                        user_message=user_message,
                        system_prompt=system_prompt,
                        record_user=record_user,
                        max_books=max_books,
                    )

    def record_assistant(
        self, content: str, *, importance: float = 0.5
    ) -> ContextRecord:
        return self.record("assistant", content, importance=importance)

    def close(self) -> None:
        self.desk.close()
