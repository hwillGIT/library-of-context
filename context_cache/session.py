from __future__ import annotations

import hashlib
from typing import Literal

from .embeddings import estimate_tokens
from .library import LibraryOfContext
from .models import ContextRecord, PromptEnvelope

Role = Literal["system", "user", "assistant", "developer", "tool"]


class VirtualContextSession:
    """Build stateless, bounded model requests over an unbounded disk conversation.

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
        if recent_token_budget < 64 or recent_token_budget >= token_budget:
            raise ValueError("recent_token_budget must be >= 64 and below token_budget")
        self.library = library
        self.session_id = session_id
        self.collection = library.namespace if collection is None else collection
        self.token_budget = token_budget
        self.recent_token_budget = recent_token_budget
        self.source = f"conversation:{session_id}"
        self.desk = library.open_reading_desk()

    def record(
        self,
        role: Role,
        content: str,
        *,
        importance: float | None = None,
    ) -> ContextRecord:
        if not content.strip():
            raise ValueError("message content cannot be empty")
        turn = self.library.source_count(self.source, namespace=self.collection) + 1
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
        )

    def history(self, *, limit: int | None = None) -> list[ContextRecord]:
        return self.library.source_records(
            self.source, namespace=self.collection, limit=limit
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

    @staticmethod
    def _message_tokens(messages: list[dict[str, str]]) -> int:
        return sum(estimate_tokens(message["content"]) + 4 for message in messages)

    @staticmethod
    def _truncate(text: str, token_budget: int) -> str:
        if estimate_tokens(text) <= token_budget:
            return text
        marker = " … [full message remains in the Library]"
        marker_tokens = estimate_tokens(marker)
        if token_budget <= marker_tokens:
            return text[: max(0, token_budget * 4)]
        return text[: (token_budget - marker_tokens) * 4].rstrip() + marker

    def build_prompt(
        self,
        *,
        user_message: str | None = None,
        system_prompt: str = "",
        record_user: bool = True,
        max_books: int = 12,
    ) -> PromptEnvelope:
        if user_message is not None and record_user:
            self.record("user", user_message)
        base_messages = (
            [] if not system_prompt else [{"role": "system", "content": system_prompt}]
        )
        base_tokens = self._message_tokens(base_messages)
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
            content = self._truncate(record.text, remaining)
            recent_messages.append(
                {
                    "role": str(record.metadata.get("role", "user")),
                    "content": content,
                }
            )
            recent_used += estimate_tokens(content) + 4
        reserved = self._message_tokens(base_messages + recent_messages) + 24
        desk_budget = max(64, self.token_budget - reserved)
        working = self.desk.refresh(
            self.session_id,
            subject,
            token_budget=desk_budget,
            top_k=max_books,
            namespace=self.collection,
            exclude_record_ids=[record.id for record in recent],
        )
        library_block = (
            '<library-context replacement="true" '
            f'session="{self.session_id}" refreshed_at="{working.refreshed_at}">\n'
            f"{working.context}\n</library-context>"
        )
        messages = list(base_messages)
        if working.context:
            if messages:
                messages[0] = {
                    "role": "system",
                    "content": messages[0]["content"] + "\n\n" + library_block,
                }
            else:
                messages.append({"role": "system", "content": library_block})
        messages.extend(recent_messages)
        token_count = self._message_tokens(messages)
        if token_count > self.token_budget and working.context:
            overflow = token_count - self.token_budget
            trim_chars = overflow * 4 + 8
            trimmed = working.context[
                : max(0, len(working.context) - trim_chars)
            ].rstrip()
            working.context = trimmed
            working.token_count = estimate_tokens(trimmed)
            library_block = (
                '<library-context replacement="true" '
                f'session="{self.session_id}" refreshed_at="{working.refreshed_at}">\n'
                f"{trimmed}\n</library-context>"
            )
            if base_messages:
                messages[0] = {
                    "role": "system",
                    "content": system_prompt + "\n\n" + library_block,
                }
            else:
                messages[0] = {"role": "system", "content": library_block}
            token_count = self._message_tokens(messages)
        return PromptEnvelope(
            session_id=self.session_id,
            collection=self.collection,
            messages=messages,
            token_count=token_count,
            token_budget=self.token_budget,
            history_books=self.library.source_count(
                self.source, namespace=self.collection
            ),
            recent_books=[record.id for record in recent],
            desk=working,
        )

    def record_assistant(
        self, content: str, *, importance: float = 0.5
    ) -> ContextRecord:
        return self.record("assistant", content, importance=importance)

    def close(self) -> None:
        self.desk.close()
