from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .governor import LibraryContextGovernor
from .session import VirtualContextSession

if TYPE_CHECKING:
    from .engine import ContextCache
    from .library import LibraryOfContext


@dataclass(frozen=True, slots=True)
class GovernorSettings:
    session_id: str
    collection: str
    token_budget: int
    recent_token_budget: int
    protected_token_budget: int
    max_books: int

    @classmethod
    def resolve(
        cls,
        values: Mapping[str, Any],
        *,
        library: ContextCache,
        default_session_id: str,
        current: LibraryContextGovernor | None,
    ) -> GovernorSettings:
        return cls(
            session_id=str(values.get("session_id", default_session_id)),
            collection=str(values.get("collection") or library.namespace),
            token_budget=int(
                values.get(
                    "token_budget",
                    12000 if current is None else current.token_budget,
                )
            ),
            recent_token_budget=int(
                values.get(
                    "recent_token_budget",
                    4000 if current is None else current.recent_token_budget,
                )
            ),
            protected_token_budget=int(
                values.get(
                    "protected_token_budget",
                    2000 if current is None else current.protected_token_budget,
                )
            ),
            max_books=int(
                values.get("max_books", 12 if current is None else current.max_books)
            ),
        )

    def matches(self, governor: LibraryContextGovernor) -> bool:
        return (
            governor.session_id == self.session_id
            and governor.collection == self.collection
            and governor.token_budget == self.token_budget
            and governor.recent_token_budget == self.recent_token_budget
            and governor.protected_token_budget == self.protected_token_budget
            and governor.max_books == self.max_books
        )


class GovernorRegistry:
    """Own context governors keyed by collection and session."""

    def __init__(
        self,
        library: ContextCache,
        *,
        default_session_id: str,
    ) -> None:
        self.library = library
        self.default_session_id = default_session_id
        self.governors: dict[tuple[str, str], LibraryContextGovernor] = {}
        self._lock = threading.RLock()

    def get(self, values: Mapping[str, Any]) -> LibraryContextGovernor:
        session_id = str(values.get("session_id", self.default_session_id))
        collection = str(values.get("collection") or self.library.namespace)
        key = (collection, session_id)
        with self._lock:
            current = self.governors.get(key)
            settings = GovernorSettings.resolve(
                values,
                library=self.library,
                default_session_id=self.default_session_id,
                current=current,
            )
            if current is not None and not settings.matches(current):
                replacement = self._open(settings)
                self.governors[key] = replacement
                current.close()
                return replacement
            if current is None:
                current = self._open(settings)
                self.governors[key] = current
            return current

    def _open(self, settings: GovernorSettings) -> LibraryContextGovernor:
        return self.library.open_context_governor(
            settings.session_id,
            collection=settings.collection,
            token_budget=settings.token_budget,
            recent_token_budget=settings.recent_token_budget,
            protected_token_budget=settings.protected_token_budget,
            max_books=settings.max_books,
        )

    def close(self) -> None:
        with self._lock:
            active = list(self.governors.values())
            self.governors.clear()
        for governor in active:
            governor.close()


@dataclass(frozen=True, slots=True)
class SessionSettings:
    session_id: str
    collection: str
    token_budget: int
    recent_token_budget: int

    def matches(self, session: VirtualContextSession) -> bool:
        return (
            session.session_id == self.session_id
            and session.collection == self.collection
            and session.token_budget == self.token_budget
            and session.recent_token_budget == self.recent_token_budget
        )


class VirtualSessionRegistry:
    """Own virtual sessions keyed by collection and session."""

    def __init__(
        self,
        library: LibraryOfContext,
        *,
        default_session_id: str,
    ) -> None:
        self.library = library
        self.default_session_id = default_session_id
        self.sessions: dict[tuple[str, str], VirtualContextSession] = {}
        self._lock = threading.RLock()

    def get(self, values: Mapping[str, Any]) -> VirtualContextSession:
        settings = SessionSettings(
            session_id=str(values.get("session_id", self.default_session_id)),
            collection=str(values.get("collection") or self.library.namespace),
            token_budget=int(values.get("token_budget", 12000)),
            recent_token_budget=int(values.get("recent_token_budget", 4000)),
        )
        key = (settings.collection, settings.session_id)
        with self._lock:
            current = self.sessions.get(key)
            if current is not None and not settings.matches(current):
                replacement = self._open(settings)
                self.sessions[key] = replacement
                current.close()
                return replacement
            if current is None:
                current = self._open(settings)
                self.sessions[key] = current
            return current

    def _open(self, settings: SessionSettings) -> VirtualContextSession:
        return self.library.open_virtual_session(
            settings.session_id,
            collection=settings.collection,
            token_budget=settings.token_budget,
            recent_token_budget=settings.recent_token_budget,
        )

    def close(self) -> None:
        with self._lock:
            active = list(self.sessions.values())
            self.sessions.clear()
        for session in active:
            session.close()
