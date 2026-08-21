# ADR 0001: Thread scope and shared runtime ownership

Status: Accepted

## Context

The Library uses separate models for ordered thread activity and retrievable context.
Both models use a collection boundary. Only thread activity has a session identity and
sequence.

Retrieval needs explicit visibility rules. These rules prevent private thread context
from entering project or team results. The
[glossary](../GLOSSARY.md) defines shared terms.

Governor, desk, and session handles are lightweight process views. Separate workers,
schedulers, or unbounded caches would increase resource use with handle count. Separate
handles could also race within one thread.

This architecture decision record (ADR) defines identity, visibility, promotion,
runtime ownership, and migration as one contract. The
[context governor](../CONTEXT_GOVERNOR.md) and
[architecture](../architecture.md) documents define lifecycle details.

## Terms

| Term | Contract |
|---|---|
| Book | The public name for one retrievable unit. Library interfaces call a `ContextRecord` a book. A book has no separate storage identity. |
| Record | A retrieval object in the `records` table. It contains text, metadata, origin, importance, expiration, scope, and an embedding. |
| Event | A durable ordered item in a governed thread. One SQLite transaction appends the event and its outbox item. |

The `shelve`, `consult`, and desk interfaces call a `ContextRecord` a book. A record can
come from shelving, document ingestion, or event indexing.

An event contains role, content, metadata, protection state, token estimate, and thread
sequence.

An indexed event produces a thread-scoped record with a deterministic identifier. The
event controls thread order and recovery. The record provides its retrieval form.

The durable event reserves the record identifier. Ordinary writes and other events
cannot reuse it. Protection and index-watermark changes do not change event content
identity.

## Thread identity

Every stateful operation uses this identity:

```text
ThreadKey(collection, session_id)
```

`collection` is the public name for the storage `namespace`. Both fields are required.
Neither field can contain control characters. Each field has a 512-character maximum.

Stateful Python, Model Context Protocol (MCP), and Hypertext Transfer Protocol (HTTP)
operations do not provide a default `session_id`.

`ThreadKey` identifies:

- the durable event sequence and watermarks.
- the local recent ring and per-thread operation lock.
- the reading desk and periodic refresh task.
- the scope owner for indexed conversation records.

An `event_id` is idempotent within one `ThreadKey`. Reuse with the same role and content
returns the durable event. Reuse with different content or role fails.

The same `event_id` may exist in another thread. Event-derived record identifiers
include collection, session, and event identity. Records from different threads cannot
alias.

## Visibility scopes

The collection remains the outer storage partition. Each record also has exactly one
visibility scope.

| Scope | Required owner | Retrieval rule |
|---|---|---|
| `thread` | `owner_session_id` | Visible when the selection includes thread scope and the session matches the owner. |
| `project` | No owner | Visible to project retrieval in the same collection. This scope is the manual-shelving default. |
| `team` | `team_id` | Visible when the selection includes team scope and the authorized set contains the team. |

A thread record cannot have a team owner. A team record cannot have a session owner. A
project record has no owner. Validation rejects an invalid combination before storage.

Project scope is also the default for an ordinary `consult` operation.

Retrieval, direct lookup, pinned-book loading, and desk construction use the same
`ScopeSelection`. Query-cache identity includes scopes, session identity, and sorted
team identities. A cache entry serves only its visibility boundary.

The Library treats supplied team identifiers as authorization input. It does not
authenticate a user or prove team membership. A gateway or team service must validate
these claims before a Library call.

## Promotion and provenance

Promotion copies a record into project or team scope. It does not change the source
scope or owner.

The promotion contract is:

1. The target is `project` or `team`. Promotion does not create thread records.
2. A thread source requires its matching `source_session_id`.
3. A team target requires `target_team_id`. A project target rejects a team identifier.
4. The destination has an explicit or deterministic ID that cannot alias a different
   target boundary.
5. The destination metadata names the immediate source record, scope, session owner,
   and team owner through `promoted_from_*` fields.
6. The destination retains source text, hash, label, embedding, importance, and
   expiration. A separate transformation contract can change these fields.

Deleting or revoking a promoted destination does not change the private source.
Deleting the source does not delete a promoted copy.

A cascading-revocation policy must track each origin link. It must delete each
destination explicitly.

## Shared runtime ownership

Each process or daemon creates one lifecycle owner named `LibraryOfContext`. That object
owns one `LibraryRuntime`. All governor, session, desk, MCP, and HTTP handles share that
runtime.

The runtime owns:

| Component | Shared responsibility | Bound |
|---|---|---|
| `OutboxIndexer` | Claims durable indexing work and embeds event records | Fixed worker count and bounded dispatch capacity |
| `ThreadStateRegistry` | Supplies one recent ring and lock per active `ThreadKey` | Entry, idle-time, event, and token limits |
| `DeskScheduler` | Coordinates periodic desk refreshes | One coordinator, fixed worker count, and maximum scheduled tasks |
| `ContextSwapper` | Stores local reading desks | Working-set and idle-time limits |

Operations for one `ThreadKey` use one reentrant lock. Operations for different threads
can run concurrently. A registry lease prevents active-state eviction.

An operation fails with a capacity error when all state slots have leases. The runtime
does not allocate an unbounded entry.

Idle or least-recently-used eviction removes local state only. A later operation rebuilds
the recent ring from SQLite. Retrieval rebuilds the desk.

A full queue or stopped process leaves unfinished work in the durable outbox.

Closing a governor, desk, or session does not stop shared services. Closing
`LibraryOfContext` stops its shared components once. These components include the
indexer, scheduler, swapper, registry, Redis client, and SQLite connection.

Request handlers must not create a Library owner for each request or thread.

Owner shutdown stops admission. It drains accepted operations and workers. It closes
SQLite before releasing the database lock. A failed close remains retryable during the
`closing` state.

Each embedded MCP process owns a runtime. A workstation daemon can serve thin MCP
bridges from one runtime. Thin bridges open no SQLite, Redis, embedder, or worker
resources.

Embedded and daemon owners must acquire the database lock before SQLite initialization.
Only one runtime may own a database.

The daemon HTTP interface requires a bearer credential from an owner-readable file. It
rejects browser-origin traffic and non-loopback `Host` values. It does not provide
Transport Layer Security, user identity, or team authorization. It is a loopback
network service and is not suitable for shared networks.

## Migration boundary

SQLite schema version 6 defines record scopes, thread event identity, leased claims,
and terminal quarantine. Opening an older supported database performs these operations:

1. Add `scope`, `owner_session_id`, and `team_id` to records.
2. Classify records marked as conversations, or sourced as
   `conversation:<session_id>`, as thread scope.
3. Assign an unresolvable migration owner to a conversation record whose session cannot
   be recovered. This assignment prevents project visibility.
4. Keep other legacy records in project scope.
5. Replace event and outbox uniqueness with
   `(collection, session_id, event_id)` and preserve row contents and sequence values.
6. Add outbox lease ownership, expiry, and claim-token columns.
7. Add terminal timestamp and error columns for quarantined work.
8. Rebuild full-text search when its columns do not include scope ownership.

Stop other writers before migration. Verify the backup. Complete migration before the
process accepts requests.

Reopening the database is idempotent. The executable rejects a database with an
unsupported future schema.

## Rollback boundary

Random-access memory, Redis entries, reading desks, and full-text search data are
rebuildable. Rollback may discard them.

Redis keys include a versioned random runtime identity. A restarted runtime begins with
an empty Redis view. It ignores another runtime's data.

Thread events, records, and outbox rows are authoritative. Caches must not reconstruct
them.

An executable that supports schema 6 can reverse application behavior without reversing
the schema. An older executable requires the verified pre-migration backup. Do not run
an older writer against the migrated database.

A promotion rollback deletes the destination and clears retrieval caches. It does not
relabel or delete the source.

A runtime rollback stops the owner. Start one replacement after prior workers release
their leases or those leases expire.

## Consequences

- Stateful callers must supply a stable collection and session identifier.
- Each retrieval and cache key includes scope selection.
- This routing data prevents cache reuse across threads or teams.
- Promotion consumes additional storage because it preserves the source and destination.
- Governor and desk handles do not own workers. Handle count does not determine worker
  count.
- Bounded registries can reject work during sustained saturation.
- Callers can retry after lease release. Operators can increase capacity from measured
  demand.
- SQLite provides local correctness. Redis remains a disposable accelerator.

## Rejected alternatives

### Use `session_id` as the complete thread key

The same session name can occur in different collections. This option merges event
sequences, desks, watermarks, and recent rings across storage boundaries.

### Supply a default session for missing identity

A default combines unrelated callers. It makes private-context exposure look like valid
retrieval.

### Treat every indexed record as project context

Conversation events would become visible to unrelated threads as soon as indexing
completed.

### Change a record's scope in place

In-place widening removes the private source boundary. It also removes evidence for
promotion review or revocation.

### Give each handle its own workers and scheduler

Worker and timer counts would increase with active handles. Same-thread handles would
also maintain different recent rings and locks.

### Keep active thread and desk state in unbounded dictionaries

Memory use would increase with each observed thread and watch. Abandoned entries would
remain.

### Use Redis as the authoritative shared runtime

Redis eviction or absence would become a correctness failure. Local operation would
depend on a service that SQLite does not require.

## Acceptance invariants

The decision requires all of these invariants:

1. Every stateful operation resolves a valid `ThreadKey`. No stateful interface supplies
   a fallback session identifier.
2. `ThreadKey` isolates event sequence and idempotency. Different threads may use the
   same `event_id`.
3. Event append and outbox append commit atomically before prompt construction.
   Event-derived record identity is reserved by the source event.
4. Thread, project, and team ownership combinations are validated before persistence.
5. Retrieval, direct lookup, pinning, desks, and query caches enforce one scope
   selection.
6. Promotion preserves the source, records provenance, and cannot alias a different
   destination boundary.
7. All handles from one Library share one indexer, scheduler, swapper, and registry.
8. Worker counts, scheduled desks, active thread states, recent rings, and working sets
   remain within configured bounds under load.
9. Per-thread operations serialize without imposing a process-wide operation lock.
10. Outbox claims are exclusive and preserve thread order. A replacement owner may
    reclaim an expired claim. Workers under one live owner do not steal claims.
11. A terminal indexing failure is visible, does not block later work, and returns to
    the queue only through an explicit retry.
12. Every runtime claims database ownership before SQLite initialization. A second owner
    fails without opening SQLite. A thin MCP bridge opens no local runtime resources.
13. Migration preserves authoritative rows, is idempotent, passes SQLite foreign-key
    checks, and rejects unsupported future schemas.
14. Rollback never asks an older executable to write a schema it does not support.

The [contributor quality workflow](../DEVELOPMENT_WORKFLOW.md) defines required tests,
reviews, gates, and evidence.
