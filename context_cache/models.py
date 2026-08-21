from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .scopes import ContextScope, validate_record_scope


@dataclass(frozen=True, slots=True)
class ContextRecord:
    id: str
    namespace: str
    text: str
    embedding: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = "manual"
    importance: float = 0.5
    token_count: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    accessed_at: float = 0.0
    expires_at: float | None = None
    content_hash: str = ""
    scope: ContextScope = ContextScope.PROJECT
    owner_session_id: str | None = None
    team_id: str | None = None

    def __post_init__(self) -> None:
        scope, owner_session_id, team_id = validate_record_scope(
            self.scope,
            self.owner_session_id,
            self.team_id,
        )
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "owner_session_id", owner_session_id)
        object.__setattr__(self, "team_id", team_id)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ContextRecord":
        return cls(
            id=str(value["id"]),
            namespace=str(value["namespace"]),
            text=str(value["text"]),
            embedding=[float(item) for item in value.get("embedding", [])],
            metadata=dict(value.get("metadata", {})),
            source=str(value.get("source", "manual")),
            importance=float(value.get("importance", 0.5)),
            token_count=int(value.get("token_count", 0)),
            created_at=float(value.get("created_at", 0.0)),
            updated_at=float(value.get("updated_at", 0.0)),
            accessed_at=float(value.get("accessed_at", 0.0)),
            expires_at=(
                None if value.get("expires_at") is None else float(value["expires_at"])
            ),
            content_hash=str(value.get("content_hash", "")),
            scope=ContextScope(value.get("scope", ContextScope.PROJECT)),
            owner_session_id=(
                None
                if value.get("owner_session_id") is None
                else str(value["owner_session_id"])
            ),
            team_id=(None if value.get("team_id") is None else str(value["team_id"])),
        )


@dataclass(slots=True)
class SearchHit:
    record: ContextRecord
    score: float
    vector_score: float
    lexical_score: float
    importance_score: float
    recency_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "record": self.record.to_dict(),
            "score": self.score,
            "vector_score": self.vector_score,
            "lexical_score": self.lexical_score,
            "importance_score": self.importance_score,
            "recency_score": self.recency_score,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SearchHit":
        return cls(
            record=ContextRecord.from_dict(value["record"]),
            score=float(value["score"]),
            vector_score=float(value.get("vector_score", 0.0)),
            lexical_score=float(value.get("lexical_score", 0.0)),
            importance_score=float(value.get("importance_score", 0.0)),
            recency_score=float(value.get("recency_score", 0.0)),
        )


@dataclass(slots=True)
class WorkingSet:
    session_id: str
    namespace: str
    focus: str
    hits: list[SearchHit]
    context: str
    token_count: int
    token_budget: int
    refreshed_at: float
    swapped_in: list[str] = field(default_factory=list)
    swapped_out: list[str] = field(default_factory=list)
    retained: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "namespace": self.namespace,
            "focus": self.focus,
            "hits": [hit.to_dict() for hit in self.hits],
            "context": self.context,
            "token_count": self.token_count,
            "token_budget": self.token_budget,
            "refreshed_at": self.refreshed_at,
            "swapped_in": self.swapped_in,
            "swapped_out": self.swapped_out,
            "retained": self.retained,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "WorkingSet":
        return cls(
            session_id=str(value["session_id"]),
            namespace=str(value["namespace"]),
            focus=str(value["focus"]),
            hits=[SearchHit.from_dict(item) for item in value.get("hits", [])],
            context=str(value.get("context", "")),
            token_count=int(value.get("token_count", 0)),
            token_budget=int(value.get("token_budget", 0)),
            refreshed_at=float(value.get("refreshed_at", 0.0)),
            swapped_in=[str(item) for item in value.get("swapped_in", [])],
            swapped_out=[str(item) for item in value.get("swapped_out", [])],
            retained=[str(item) for item in value.get("retained", [])],
        )


@dataclass(slots=True)
class PromptEnvelope:
    session_id: str
    collection: str
    messages: list[dict[str, str]]
    token_count: int
    token_budget: int
    history_books: int
    recent_books: list[str]
    desk: WorkingSet

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "collection": self.collection,
            "messages": self.messages,
            "token_count": self.token_count,
            "token_budget": self.token_budget,
            "history_books": self.history_books,
            "recent_books": self.recent_books,
            "desk": self.desk.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ContextEvent:
    """One durable event in a governed agent thread."""

    event_id: str
    namespace: str
    session_id: str
    sequence: int
    role: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    importance: float = 0.5
    protected: bool = False
    token_count: int = 0
    record_id: str = ""
    created_at: float = 0.0
    indexed_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ContextEvent":
        return cls(
            event_id=str(value["event_id"]),
            namespace=str(value["namespace"]),
            session_id=str(value["session_id"]),
            sequence=int(value["sequence"]),
            role=str(value["role"]),
            content=str(value["content"]),
            metadata=dict(value.get("metadata", {})),
            importance=float(value.get("importance", 0.5)),
            protected=bool(value.get("protected", False)),
            token_count=int(value.get("token_count", 0)),
            record_id=str(value.get("record_id", "")),
            created_at=float(value.get("created_at", 0.0)),
            indexed_at=(
                None if value.get("indexed_at") is None else float(value["indexed_at"])
            ),
        )


@dataclass(slots=True)
class ContextWatermarks:
    """Durable visibility boundaries for a governed thread."""

    recorded_through: int = 0
    embedded_through: int = 0
    indexed_through: int = 0
    team_synced_through: int = 0
    pending_events: int = 0
    failed_events: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OutboxClaim:
    """Leased indexing work selected from the durable outbox."""

    namespace: str
    event_id: str
    session_id: str
    sequence: int
    attempts: int
    claim_token: str


@dataclass(slots=True)
class GovernedPrompt:
    """A complete bounded model request assembled by the context governor."""

    session_id: str
    collection: str
    messages: list[dict[str, str]]
    token_count: int
    token_budget: int
    event_count: int
    recent_event_ids: list[str]
    protected_event_ids: list[str]
    desk: WorkingSet
    watermarks: ContextWatermarks
    paged_out_events: int = 0
    native_context_pressure: float = 0.0
    context_mode: str = "semantic-paging"
    replaces_compaction: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "collection": self.collection,
            "messages": self.messages,
            "token_count": self.token_count,
            "token_budget": self.token_budget,
            "event_count": self.event_count,
            "recent_event_ids": self.recent_event_ids,
            "protected_event_ids": self.protected_event_ids,
            "desk": self.desk.to_dict(),
            "watermarks": self.watermarks.to_dict(),
            "paged_out_events": self.paged_out_events,
            "native_context_pressure": self.native_context_pressure,
            "context_mode": self.context_mode,
            "replaces_compaction": self.replaces_compaction,
        }
