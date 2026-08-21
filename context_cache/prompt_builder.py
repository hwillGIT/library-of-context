from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .context_markup import format_library_context
from .embeddings import estimate_tokens
from .models import ContextEvent, GovernedPrompt, WorkingSet
from .text_budget import (
    EVENT_TRUNCATION_MARKER,
    message_token_count,
    select_event_tail,
    truncate_text,
)

if TYPE_CHECKING:
    from .engine import ContextCache
    from .rings import RecentEventRing
    from .swapper import ContextSwapper


@dataclass(frozen=True, slots=True)
class PromptBudget:
    total_tokens: int
    recent_tokens: int
    protected_tokens: int
    max_books: int


def _select_descending_events(
    events: Iterable[ContextEvent],
    token_budget: int,
    *,
    excluded: set[str] | None = None,
) -> tuple[list[tuple[ContextEvent, str]], int]:
    """Select a bounded newest-first stream and return chronological messages."""

    excluded_ids = excluded or set()
    selected: list[tuple[ContextEvent, str]] = []
    used = 0
    for event in events:
        if event.event_id in excluded_ids:
            continue
        available = token_budget - used - 4
        if available <= 0:
            break
        if event.token_count > available:
            if selected:
                break
            content = truncate_text(
                event.content,
                available,
                marker=EVENT_TRUNCATION_MARKER,
            )
        else:
            content = event.content
        cost = estimate_tokens(content) + 4
        if used + cost > token_budget:
            break
        selected.append((event, content))
        used += cost
    selected.reverse()
    return selected, used


class GovernedPromptBuilder:
    """Assemble one bounded model request from durable and active context."""

    def __init__(
        self,
        library: ContextCache,
        desk: ContextSwapper,
        recent: RecentEventRing,
        *,
        session_id: str,
        collection: str,
        budget: PromptBudget,
    ) -> None:
        self.library = library
        self.desk = desk
        self.recent = recent
        self.session_id = session_id
        self.collection = collection
        self.budget = budget

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

    def build(
        self,
        *,
        focus: str | None = None,
        system_prompt: str = "",
    ) -> GovernedPrompt:
        recent_events = self.recent.snapshot()
        if not recent_events:
            recent_events = self.library.store.list_thread_events(
                self.collection,
                self.session_id,
                limit=256,
            )
        if not recent_events:
            raise ValueError("the governed thread has no events")

        base_messages = (
            [] if not system_prompt else [{"role": "system", "content": system_prompt}]
        )
        base_tokens = message_token_count(base_messages)
        if base_tokens + 64 >= self.budget.total_tokens:
            raise ValueError("system_prompt leaves no room for governed context")

        available = self.budget.total_tokens - base_tokens - 32
        protected_reserve = min(
            self.budget.protected_tokens,
            max(
                0,
                available - min(self.budget.recent_tokens, max(64, available // 2)),
            ),
        )
        recent_cap = min(self.budget.recent_tokens, available - protected_reserve)
        selected_recent, recent_tokens = select_event_tail(recent_events, recent_cap)
        recent_ids = {event.event_id for event, _ in selected_recent}

        protected_cap = min(self.budget.protected_tokens, available - recent_tokens)
        selected_protected, _ = _select_descending_events(
            self.library.store.iter_thread_events_descending(
                self.collection,
                self.session_id,
                protected_only=True,
            ),
            protected_cap,
            excluded=recent_ids,
        )

        messages = list(base_messages)
        messages.extend(
            {"role": event.role, "content": content}
            for event, content in selected_protected + selected_recent
        )
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
        working = self._retrieve_context(
            subject=subject,
            messages=messages,
            selected_events=selected_protected + selected_recent,
        )
        messages = self._attach_context(
            messages,
            system_prompt=system_prompt,
            working=working,
        )
        token_count = self._enforce_budget(
            messages,
            system_prompt=system_prompt,
            working=working,
            has_base_message=bool(base_messages),
        )

        watermarks = self.library.store.thread_watermarks(
            self.collection,
            self.session_id,
        )
        selected_ids = recent_ids | {event.event_id for event, _ in selected_protected}
        protected_ids = [event.event_id for event, _ in selected_protected]
        protected_ids.extend(
            event.event_id
            for event, _ in selected_recent
            if event.protected and event.event_id not in protected_ids
        )
        return GovernedPrompt(
            session_id=self.session_id,
            collection=self.collection,
            messages=messages,
            token_count=token_count,
            token_budget=self.budget.total_tokens,
            event_count=watermarks.recorded_through,
            recent_event_ids=[event.event_id for event, _ in selected_recent],
            protected_event_ids=protected_ids,
            desk=working,
            watermarks=watermarks,
            paged_out_events=max(0, watermarks.recorded_through - len(selected_ids)),
            native_context_pressure=token_count / self.budget.total_tokens,
        )

    def _retrieve_context(
        self,
        *,
        subject: str,
        messages: list[dict[str, str]],
        selected_events: list[tuple[ContextEvent, str]],
    ) -> WorkingSet:
        wrapper = format_library_context(
            "",
            session_id=self.session_id,
            refreshed_at=time.time(),
            mode="semantic-paging",
        )
        desk_budget = max(
            0,
            self.budget.total_tokens
            - message_token_count(messages)
            - estimate_tokens(wrapper)
            - 4,
        )
        excluded_record_ids = [
            event.record_id for event, _ in selected_events if event.record_id
        ]
        if subject.strip() and desk_budget >= 8:
            return self.desk.refresh(
                self.session_id,
                subject,
                token_budget=desk_budget,
                top_k=self.budget.max_books,
                namespace=self.collection,
                exclude_record_ids=excluded_record_ids,
            )
        return self._empty_desk(self.session_id, self.collection, subject)

    def _library_block(self, working: WorkingSet) -> str:
        return format_library_context(
            working.context,
            session_id=self.session_id,
            refreshed_at=working.refreshed_at,
            mode="semantic-paging",
        )

    def _attach_context(
        self,
        messages: list[dict[str, str]],
        *,
        system_prompt: str,
        working: WorkingSet,
    ) -> list[dict[str, str]]:
        if not working.context:
            return messages
        library_block = self._library_block(working)
        if system_prompt:
            messages[0] = {
                "role": "system",
                "content": system_prompt + "\n\n" + library_block,
            }
        else:
            messages.insert(0, {"role": "system", "content": library_block})
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
        if token_count > self.budget.total_tokens and working.context:
            overflow = token_count - self.budget.total_tokens
            target = max(0, estimate_tokens(working.context) - overflow - 8)
            selected, context = self.desk._pack_hits(
                working.hits,
                token_budget=target,
                top_k=max(1, len(working.hits)),
            )
            working.hits = selected
            working.context = context
            working.token_count = estimate_tokens(working.context)
            if working.context:
                library_block = self._library_block(working)
                if has_base_message:
                    messages[0] = {
                        "role": "system",
                        "content": system_prompt + "\n\n" + library_block,
                    }
                else:
                    messages[0] = {"role": "system", "content": library_block}
            elif has_base_message:
                messages[0] = {"role": "system", "content": system_prompt}
            else:
                messages.pop(0)
            token_count = message_token_count(messages)
        if token_count > self.budget.total_tokens:
            raise RuntimeError("governor failed to enforce the context budget")
        return token_count
