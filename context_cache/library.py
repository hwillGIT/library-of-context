from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from .engine import ContextCache
from .models import ContextRecord, SearchHit, WorkingSet
from .scopes import ContextScope
from .swapper import ContextSwapper

if TYPE_CHECKING:
    from .session import VirtualContextSession


class ReadingDesk:
    """Token-bounded active context whose contents are replaced during refresh."""

    def __init__(self, swapper: ContextSwapper) -> None:
        self._swapper = swapper

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
    ) -> WorkingSet:
        return self._swapper.refresh(
            session_id,
            focus,
            token_budget=token_budget,
            top_k=top_k,
            namespace=namespace,
            filters=filters,
            pinned_record_ids=pinned_record_ids,
            exclude_record_ids=exclude_record_ids,
            team_ids=team_ids,
        )

    def get(
        self,
        session_id: str,
        *,
        namespace: str | None = None,
    ) -> WorkingSet | None:
        return self._swapper.get(session_id, namespace=namespace)

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
        return self._swapper.start_periodic(
            session_id,
            focus,
            interval_seconds=interval_seconds,
            token_budget=token_budget,
            top_k=top_k,
            namespace=namespace,
            filters=filters,
            pinned_record_ids=pinned_record_ids,
            exclude_record_ids=exclude_record_ids,
            team_ids=team_ids,
        )

    def update_focus(
        self,
        session_id: str,
        focus: str,
        *,
        namespace: str | None = None,
    ) -> WorkingSet:
        return self._swapper.update_focus(session_id, focus, namespace=namespace)

    def stop_periodic(
        self,
        session_id: str,
        *,
        namespace: str | None = None,
    ) -> bool:
        return self._swapper.stop_periodic(session_id, namespace=namespace)

    def status(self, *, namespace: str | None = None) -> list[dict[str, Any]]:
        return self._swapper.status(namespace=namespace)

    def close(self) -> None:
        """Release this desk handle without stopping the shared scheduler."""

    def lay_out(
        self,
        subject: str,
        *,
        session_id: str,
        token_budget: int = 4000,
        max_books: int = 12,
        namespace: str | None = None,
        catalog_filters: dict[str, Any] | None = None,
        keep_open: list[str] | None = None,
        leave_shelved: list[str] | None = None,
        team_ids: tuple[str, ...] = (),
    ) -> WorkingSet:
        return self._swapper.refresh(
            session_id,
            subject,
            token_budget=token_budget,
            top_k=max_books,
            namespace=namespace,
            filters=catalog_filters,
            pinned_record_ids=keep_open,
            exclude_record_ids=leave_shelved,
            team_ids=team_ids,
        )

    def change_subject(
        self,
        subject: str,
        *,
        session_id: str,
        namespace: str | None = None,
    ) -> WorkingSet:
        return self._swapper.update_focus(session_id, subject, namespace=namespace)

    def current_books(
        self, *, session_id: str, namespace: str | None = None
    ) -> WorkingSet | None:
        return self._swapper.get(session_id, namespace=namespace)


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
        scope: ContextScope | str = ContextScope.PROJECT,
        owner_session_id: str | None = None,
        team_id: str | None = None,
    ) -> ContextRecord:
        return self.put(
            text,
            record_id=book_id,
            namespace=collection,
            metadata=catalog,
            source=source,
            importance=importance,
            ttl_seconds=shelf_life_seconds,
            scope=scope,
            owner_session_id=owner_session_id,
            team_id=team_id,
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
        scope: ContextScope | str = ContextScope.PROJECT,
        owner_session_id: str | None = None,
        team_id: str | None = None,
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
            scope=scope,
            owner_session_id=owner_session_id,
            team_id=team_id,
        )

    def consult(
        self,
        subject: str,
        *,
        max_books: int = 8,
        collection: str | None = None,
        catalog_filters: dict[str, Any] | None = None,
        minimum_relevance: float = 0.0,
        team_ids: tuple[str, ...] = (),
    ) -> list[SearchHit]:
        return self.retrieve(
            subject,
            top_k=max_books,
            namespace=collection,
            filters=catalog_filters,
            minimum_score=minimum_relevance,
            scopes=(ContextScope.PROJECT, ContextScope.TEAM)
            if team_ids
            else (ContextScope.PROJECT,),
            team_ids=team_ids,
        )

    def promote_book(
        self,
        book_id: str,
        *,
        target_scope: ContextScope | str,
        collection: str | None = None,
        source_session_id: str | None = None,
        promoted_book_id: str | None = None,
        target_team_id: str | None = None,
    ) -> ContextRecord:
        return self.promote(
            book_id,
            target_scope=target_scope,
            namespace=collection,
            source_session_id=source_session_id,
            promoted_record_id=promoted_book_id,
            target_team_id=target_team_id,
        )

    def open_reading_desk(self) -> ReadingDesk:
        with self._operation():
            return ReadingDesk(self.runtime.swapper)

    def open_virtual_session(
        self,
        session_id: str,
        *,
        collection: str | None = None,
        token_budget: int = 12000,
        recent_token_budget: int = 4000,
    ) -> "VirtualContextSession":
        from .session import VirtualContextSession

        with self._operation():
            return VirtualContextSession(
                self,
                session_id,
                collection=collection,
                token_budget=token_budget,
                recent_token_budget=recent_token_budget,
            )
