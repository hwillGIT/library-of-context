from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .governor import LibraryContextGovernor
from .limits import MAX_CONTEXT_TOKENS, MAX_RESULT_BOOKS, bounded_integer
from .scopes import ThreadKey
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
        default_session_id: str | None,
        current: LibraryContextGovernor | None,
    ) -> GovernorSettings:
        return cls(
            session_id=_session_id(values, default_session_id),
            collection=str(values.get("collection") or library.namespace),
            token_budget=bounded_integer(
                values.get(
                    "token_budget", 12000 if current is None else current.token_budget
                ),
                name="token_budget",
                minimum=256,
                maximum=MAX_CONTEXT_TOKENS,
            ),
            recent_token_budget=bounded_integer(
                values.get(
                    "recent_token_budget",
                    4000 if current is None else current.recent_token_budget,
                ),
                name="recent_token_budget",
                minimum=64,
                maximum=MAX_CONTEXT_TOKENS,
            ),
            protected_token_budget=bounded_integer(
                values.get(
                    "protected_token_budget",
                    2000 if current is None else current.protected_token_budget,
                ),
                name="protected_token_budget",
                minimum=0,
                maximum=MAX_CONTEXT_TOKENS,
            ),
            max_books=bounded_integer(
                values.get("max_books", 12 if current is None else current.max_books),
                name="max_books",
                minimum=1,
                maximum=MAX_RESULT_BOOKS,
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


def _session_id(
    values: Mapping[str, Any],
    default_session_id: str | None,
) -> str:
    raw = values.get("session_id", default_session_id)
    if raw is None:
        raise ValueError("session_id is required for stateful context operations")
    session_id = str(raw)
    if not session_id.strip():
        raise ValueError("session_id cannot be empty")
    return session_id


class GovernorRegistry:
    """Own context governors keyed by collection and session."""

    def __init__(
        self,
        library: ContextCache,
        *,
        default_session_id: str | None = None,
        max_entries: int = 256,
        idle_ttl_seconds: float = 1800.0,
        clock: Any = time.monotonic,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        if idle_ttl_seconds <= 0:
            raise ValueError("idle_ttl_seconds must be positive")
        self.library = library
        self.default_session_id = default_session_id
        self.max_entries = max_entries
        self.idle_ttl_seconds = idle_ttl_seconds
        self._clock = clock
        self.governors: OrderedDict[tuple[str, str], LibraryContextGovernor] = (
            OrderedDict()
        )
        self._last_access: dict[tuple[str, str], float] = {}
        self._lock = threading.RLock()

    def get(self, values: Mapping[str, Any]) -> LibraryContextGovernor:
        session_id = _session_id(values, self.default_session_id)
        collection = str(values.get("collection") or self.library.namespace)
        ThreadKey(collection, session_id)
        key = (collection, session_id)
        with self._lock:
            self._prune_locked()
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
                self.governors.move_to_end(key)
                self._last_access[key] = self._clock()
                current.close()
                return replacement
            if current is None:
                self._make_room_locked()
                current = self._open(settings)
                self.governors[key] = current
            self.governors.move_to_end(key)
            self._last_access[key] = self._clock()
            return current

    def _prune_locked(self) -> None:
        cutoff = self._clock() - self.idle_ttl_seconds
        expired = [
            key for key, accessed in self._last_access.items() if accessed <= cutoff
        ]
        for key in expired:
            governor = self.governors.pop(key, None)
            self._last_access.pop(key, None)
            if governor is not None:
                governor.close()

    def _make_room_locked(self) -> None:
        while len(self.governors) >= self.max_entries:
            key, governor = self.governors.popitem(last=False)
            self._last_access.pop(key, None)
            governor.close()

    def stats(self, *, collection: str | None = None) -> dict[str, int | float]:
        with self._lock:
            self._prune_locked()
            return {
                "active": sum(
                    1
                    for key in self.governors
                    if collection is None or key[0] == collection
                ),
                "capacity": self.max_entries,
                "idle_ttl_seconds": self.idle_ttl_seconds,
            }

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
            self._last_access.clear()
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
        default_session_id: str | None = None,
        max_entries: int = 256,
        idle_ttl_seconds: float = 1800.0,
        clock: Any = time.monotonic,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        if idle_ttl_seconds <= 0:
            raise ValueError("idle_ttl_seconds must be positive")
        self.library = library
        self.default_session_id = default_session_id
        self.max_entries = max_entries
        self.idle_ttl_seconds = idle_ttl_seconds
        self._clock = clock
        self.sessions: OrderedDict[tuple[str, str], VirtualContextSession] = (
            OrderedDict()
        )
        self._last_access: dict[tuple[str, str], float] = {}
        self._lock = threading.RLock()

    def get(self, values: Mapping[str, Any]) -> VirtualContextSession:
        settings = SessionSettings(
            session_id=_session_id(values, self.default_session_id),
            collection=str(values.get("collection") or self.library.namespace),
            token_budget=bounded_integer(
                values.get("token_budget", 12000),
                name="token_budget",
                minimum=256,
                maximum=MAX_CONTEXT_TOKENS,
            ),
            recent_token_budget=bounded_integer(
                values.get("recent_token_budget", 4000),
                name="recent_token_budget",
                minimum=64,
                maximum=MAX_CONTEXT_TOKENS,
            ),
        )
        key = (settings.collection, settings.session_id)
        ThreadKey(*key)
        with self._lock:
            self._prune_locked()
            current = self.sessions.get(key)
            if current is not None and not settings.matches(current):
                replacement = self._open(settings)
                self.sessions[key] = replacement
                self.sessions.move_to_end(key)
                self._last_access[key] = self._clock()
                current.close()
                return replacement
            if current is None:
                self._make_room_locked()
                current = self._open(settings)
                self.sessions[key] = current
            self.sessions.move_to_end(key)
            self._last_access[key] = self._clock()
            return current

    def _prune_locked(self) -> None:
        cutoff = self._clock() - self.idle_ttl_seconds
        expired = [
            key for key, accessed in self._last_access.items() if accessed <= cutoff
        ]
        for key in expired:
            session = self.sessions.pop(key, None)
            self._last_access.pop(key, None)
            if session is not None:
                session.close()

    def _make_room_locked(self) -> None:
        while len(self.sessions) >= self.max_entries:
            key, session = self.sessions.popitem(last=False)
            self._last_access.pop(key, None)
            session.close()

    def stats(self, *, collection: str | None = None) -> dict[str, int | float]:
        with self._lock:
            self._prune_locked()
            return {
                "active": sum(
                    1
                    for key in self.sessions
                    if collection is None or key[0] == collection
                ),
                "capacity": self.max_entries,
                "idle_ttl_seconds": self.idle_ttl_seconds,
            }

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
            self._last_access.clear()
        for session in active:
            session.close()
