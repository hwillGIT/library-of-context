from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .engine import ContextCache
from .models import ContextRecord, SearchHit, WorkingSet
from .swapper import ContextSwapper

if TYPE_CHECKING:
    from .session import VirtualContextSession


class ReadingDesk(ContextSwapper):
    """Token-bounded active context whose contents are replaced during refresh."""

    def lay_out(
        self,
        subject: str,
        *,
        session_id: str = "default-desk",
        token_budget: int = 4000,
        max_books: int = 12,
        namespace: str | None = None,
        catalog_filters: dict[str, Any] | None = None,
        keep_open: list[str] | None = None,
        leave_shelved: list[str] | None = None,
    ) -> WorkingSet:
        return self.refresh(
            session_id,
            subject,
            token_budget=token_budget,
            top_k=max_books,
            namespace=namespace,
            filters=catalog_filters,
            pinned_record_ids=keep_open,
            exclude_record_ids=leave_shelved,
        )

    def change_subject(
        self,
        subject: str,
        *,
        session_id: str = "default-desk",
        namespace: str | None = None,
    ) -> WorkingSet:
        return self.update_focus(session_id, subject, namespace=namespace)

    def current_books(
        self, *, session_id: str = "default-desk", namespace: str | None = None
    ) -> WorkingSet | None:
        return self.get(session_id, namespace=namespace)


class LibraryOfContext(ContextCache):
    """Public API for context storage, retrieval, reading desks, and governed prompts."""

    def shelve(
        self,
        text: str,
        *,
        book_id: str | None = None,
        collection: str | None = None,
        catalog: dict[str, Any] | None = None,
        source: str = "manual",
        importance: float = 0.5,
        shelf_life_seconds: float | None = None,
    ) -> ContextRecord:
        return self.put(
            text,
            record_id=book_id,
            namespace=collection,
            metadata=catalog,
            source=source,
            importance=importance,
            ttl_seconds=shelf_life_seconds,
        )

    def shelve_document(
        self,
        text: str,
        *,
        source: str,
        collection: str | None = None,
        catalog: dict[str, Any] | None = None,
        importance: float = 0.5,
        chapter_tokens: int = 450,
        overlap_tokens: int = 60,
        replace_edition: bool = False,
    ) -> list[ContextRecord]:
        return self.ingest(
            text,
            source=source,
            namespace=collection,
            metadata=catalog,
            importance=importance,
            chunk_tokens=chapter_tokens,
            overlap_tokens=overlap_tokens,
            replace_source=replace_edition,
        )

    def consult(
        self,
        subject: str,
        *,
        max_books: int = 8,
        collection: str | None = None,
        catalog_filters: dict[str, Any] | None = None,
        minimum_relevance: float = 0.0,
    ) -> list[SearchHit]:
        return self.retrieve(
            subject,
            top_k=max_books,
            namespace=collection,
            filters=catalog_filters,
            minimum_score=minimum_relevance,
        )

    def open_reading_desk(self) -> ReadingDesk:
        return ReadingDesk(self)

    def open_virtual_session(
        self,
        session_id: str,
        *,
        collection: str | None = None,
        token_budget: int = 12000,
        recent_token_budget: int = 4000,
    ) -> "VirtualContextSession":
        from .session import VirtualContextSession

        return VirtualContextSession(
            self,
            session_id,
            collection=collection,
            token_budget=token_budget,
            recent_token_budget=recent_token_budget,
        )
