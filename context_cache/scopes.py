from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class ContextScope(StrEnum):
    """Visibility boundary assigned to an indexed context record."""

    THREAD = "thread"
    PROJECT = "project"
    TEAM = "team"


def validate_identifier(name: str, value: str) -> str:
    """Validate one externally supplied identity component."""

    if not value.strip():
        raise ValueError(f"{name} cannot be empty")
    if len(value) > 512:
        raise ValueError(f"{name} cannot exceed 512 characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{name} cannot contain control characters")
    return value


@dataclass(frozen=True, slots=True)
class ThreadKey:
    """Stable identity for one agent thread inside a collection."""

    collection: str
    session_id: str

    def __post_init__(self) -> None:
        for name, value in (
            ("collection", self.collection),
            ("session_id", self.session_id),
        ):
            validate_identifier(name, value)


@dataclass(frozen=True, slots=True)
class ScopeSelection:
    """Authorized record scopes for one retrieval operation."""

    scopes: tuple[ContextScope, ...]
    session_id: str | None = None
    team_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.scopes:
            raise ValueError("at least one context scope is required")
        if len(set(self.scopes)) != len(self.scopes):
            raise ValueError("context scopes cannot contain duplicates")
        if ContextScope.THREAD in self.scopes:
            if self.session_id is None:
                raise ValueError("thread scope requires a non-empty session_id")
            validate_identifier("session_id", self.session_id)
        if ContextScope.TEAM in self.scopes and not self.team_ids:
            raise ValueError("team scope requires at least one authorized team_id")
        for team_id in self.team_ids:
            validate_identifier("team_id", team_id)

    @classmethod
    def resolve(
        cls,
        scopes: Iterable[ContextScope | str] | None = None,
        *,
        session_id: str | None = None,
        team_ids: Iterable[str] = (),
    ) -> ScopeSelection:
        selected = (
            (ContextScope.PROJECT,)
            if scopes is None
            else tuple(ContextScope(scope) for scope in scopes)
        )
        return cls(
            selected,
            session_id=session_id,
            team_ids=frozenset(str(team_id) for team_id in team_ids),
        )

    @classmethod
    def for_thread(
        cls,
        session_id: str,
        *,
        include_project: bool = True,
        team_ids: Iterable[str] = (),
    ) -> ScopeSelection:
        scopes = [ContextScope.THREAD]
        if include_project:
            scopes.append(ContextScope.PROJECT)
        authorized_teams = frozenset(str(team_id) for team_id in team_ids)
        if authorized_teams:
            scopes.append(ContextScope.TEAM)
        return cls(
            tuple(scopes),
            session_id=session_id,
            team_ids=authorized_teams,
        )

    def cache_identity(self) -> dict[str, object]:
        return {
            "scopes": [scope.value for scope in self.scopes],
            "session_id": (
                self.session_id if ContextScope.THREAD in self.scopes else None
            ),
            "team_ids": sorted(self.team_ids),
        }

    def allows(
        self,
        scope: ContextScope | str,
        owner_session_id: str | None,
        team_id: str | None = None,
    ) -> bool:
        resolved = ContextScope(scope)
        if resolved not in self.scopes:
            return False
        if resolved is ContextScope.THREAD:
            return owner_session_id == self.session_id
        if resolved is ContextScope.TEAM:
            return team_id in self.team_ids
        return True


def validate_record_scope(
    scope: ContextScope | str,
    owner_session_id: str | None,
    team_id: str | None = None,
) -> tuple[ContextScope, str | None, str | None]:
    """Validate a record's visibility boundary and owner."""

    resolved = ContextScope(scope)
    owner = None if owner_session_id is None else str(owner_session_id)
    team = None if team_id is None else str(team_id)
    if resolved is ContextScope.THREAD:
        if not owner:
            raise ValueError("thread-scoped records require owner_session_id")
        validate_identifier("owner_session_id", owner)
        if team:
            raise ValueError("thread-scoped records cannot have team_id")
        return resolved, owner, None
    if resolved is ContextScope.TEAM:
        if not team:
            raise ValueError("team-scoped records require team_id")
        validate_identifier("team_id", team)
        if owner:
            raise ValueError("team-scoped records cannot have owner_session_id")
        return resolved, None, team
    if owner or team:
        raise ValueError("project records cannot have an owner")
    return resolved, None, None
