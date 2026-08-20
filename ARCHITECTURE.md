# Architecture

## Objective

The Library of Context supplements a model's native context window with external,
persistent, semantically paged memory. It manages two failure modes:

1. **over-expansion:** an accumulating transcript consumes the model window;
2. **over-expiration:** old information is truncated or compressed before it is safely
   recoverable.

The system preserves complete context outside the model and constructs a fresh bounded
working set for every governed call. It increases addressable context, not the model's
physical input limit.

## Core invariants

1. **Durable before evictable.** An event is committed to SQLite before the governor may
   omit it from a future prompt.
2. **Bounded native context.** A governed envelope never exceeds its configured token
   budget under the project's estimator.
3. **Recent writes remain visible.** Recorded but unindexed events are overlaid from the
   recent ring so asynchronous indexing cannot make a new turn disappear.
4. **Ring overflow is not data loss.** The work ring carries only event identifiers. A
   durable SQLite outbox retains work that does not fit.
5. **One replacement desk.** Retrieved Library context replaces the prior block instead
   of being appended indefinitely.
6. **SQLite is authoritative.** RAM and Redis are disposable accelerators.
7. **Cloud independence.** A local prompt must remain possible when Redis, a team relay,
   or the network is unavailable.

## Governed turn lifecycle

```text
                   synchronous durability boundary
user event ───────> thread_events + context_outbox ───────> ACK
                           │                         \
                           │                          └─> bounded work ring
                           ▼                                  │
                  token-aware recent ring                    ▼
                           │                           embed and index
                           ├──────────────┐                   │
                           ▼              ▼                   ▼
protected context ─> context governor <── catalog <── SQLite / RAM / Redis
                           │
                           ▼
                 bounded replacement envelope
                           │
                           ▼
                       model call
                           │
                           ▼
                 commit assistant/tool event
```

`LibraryContextGovernor.prepare()` performs the durable user append first and then
assembles the model request. The caller invokes its model with `envelope.messages` and
records the result with `commit()`. The model API therefore receives a stateless bounded
request rather than an ever-growing transcript.

### Watermarks

Every thread reports:

- `recorded_through`: highest durable sequence;
- `embedded_through`: highest contiguous embedded sequence;
- `indexed_through`: highest contiguous searchable sequence;
- `team_synced_through`: reserved for acknowledged selective team promotion;
- `pending_events`: durable events waiting in the local outbox.

The current local worker embeds and indexes in one step, so the embedded and indexed
watermarks advance together. Separate stages can split them later without changing the
public contract.

## Prompt assembly

The governor constructs:

```text
protected native instructions
+ selected protected thread events
+ token-bounded recent thread events
+ one retrieved <library-context replacement="true"> block
```

The current user turn is stored in full. If it alone exceeds the recent-context budget,
the prompt receives a marked truncated view while the complete event remains on disk.
Protected context is durable and remains eligible until explicitly released. Retrieved
books exclude events already present in the recent/protected selection.

The resulting `GovernedPrompt` reports selected event identifiers, paged-out event
count, token pressure, the reading-desk swap delta, and visibility watermarks.

## Memory and work hierarchy

| Tier | Structure | Policy | Correctness role |
|---|---|---|---|
| Immediate thread overlay | Token-aware FIFO ring | Event and token bounds | Fresh visibility |
| Process hot tier | Byte-estimated LRU | Record/query budgets | Disposable |
| Shared local hot tier | Redis | TTL and LFU | Disposable |
| Durable library | SQLite WAL | Disk/quota policy | Authoritative |
| Model-visible desk | Prompt messages | Hard token budget | Rebuilt per turn |

Two rings have different semantics:

- The **recent ring** preserves thread order and supplies not-yet-indexed events.
- The **work ring** is a bounded queue of durable event IDs for asynchronous indexing.

The work ring is not a broker and is not durable. The transactional SQLite outbox is
the recovery mechanism. On restart, a scanner rehydrates pending identifiers into the
ring. Index writes use stable record IDs, so replay is idempotent.

## Durable schema

`thread_heads` allocates a monotonic sequence per `(namespace, session_id)`.
`thread_events` stores role, content, metadata, protection state, token estimate,
record ID, timestamps, and index visibility. `context_outbox` is written in the same
SQLite transaction as its event.

Indexed books remain in `records`, with text, metadata, provenance, importance,
timestamps, content hash, and float32 vectors. `records_fts` provides lexical candidate
IDs without joining every match against the complete record table.

## Retrieval and reading-desk paging

Current ranking combines:

```text
0.60 × normalized cosine similarity
+ 0.25 × reciprocal FTS rank
+ 0.10 × explicit importance
+ 0.05 × exponential recency
```

Pinned books are considered first. Other books fill the remaining budget in relevance
order. `WorkingSet` exposes:

```text
swapped_in  = new desk - previous desk
swapped_out = previous desk - new desk
retained    = new desk ∩ previous desk
```

The FTS path is bounded by candidate count. The portable vector path still exact-scores
all live records in a namespace. An ANN adapter is required for consistently bounded
retrieval work at large cardinalities.

## Process and concurrency model

The current implementation can run in-process, through the local HTTP server, or as a
STDIO MCP process. Each governor has one bounded worker ring and one indexing thread.
SQLite access is serialized through the store connection lock. This is correct for a
small local prototype but is not the final workstation-scale process model.

The intended next topology is one supervised Library daemon per workstation, with thin
MCP/HTTP/named-pipe bridges. A fixed worker pool would partition events by
`(project_id, thread_id)`, preserve order inside a thread, and process independent
threads concurrently.

## Failure behavior

| Failure | Behavior |
|---|---|
| Process stops after event append | Event and outbox survive; the next worker replays it |
| Work ring is full | Event stays pending in the outbox; no acknowledged context is lost |
| Embedder is unavailable | Recent overlay preserves visibility; outbox retries later |
| Redis is unavailable | RAM/SQLite continue; Redis is lazily retried |
| RAM/Redis evicts data | Read falls through to SQLite |
| Strict freshness times out | Caller receives a timeout instead of false freshness |
| Team/cloud plane is unavailable | Local prompt construction continues |

SQLite currently uses WAL with `synchronous=NORMAL`. A deployment requiring power-loss
RPO 0 must select and test a stronger fsync policy.

## Local-first team evolution

Raw thread events remain private and local by default. A future promotion compiler may
turn selected decisions, facts, evidence, summaries, and artifacts into approved team
knowledge cards. Those cards can move asynchronously through a durable acknowledged
stream into an ACL-aware project catalog.

Redis Pub/Sub is suitable only as a wake-up or invalidation hint because it does not
provide durable replay. The current persistence-disabled Redis cache must never be
treated as the team broker. Redis Streams, NATS JetStream, or another durable system are
possible team-plane choices, while SQLite outbox/inbox records remain the local truth.

See [Team Architecture](docs/TEAM_ARCHITECTURE.md).

## Security and privacy boundary

The defaults are local-only: SQLite stays on disk, Redis is loopback-oriented, and no
hosted embedding provider is required. The HTTP service is unauthenticated and must not
be exposed directly to another machine.

A team deployment needs authenticated device identity, per-project authorization,
encryption in transit and at rest, retention/deletion policies, provenance, audit logs,
and authorization filters enforced before candidate retrieval and hydration.

## Known limitations

- Exact vector scoring performs work proportional to namespace size.
- The token estimator is approximate rather than model-specific.
- Embedding/index workers are single-threaded per governor and do not batch requests.
- Multiple governors can observe the same shared outbox and may perform harmless
  duplicate idempotent work.
- There is no team sync, ACL, branch merge, or knowledge-promotion implementation yet.
- The MCP integration cannot replace an undocumented host-internal compaction hook.

These limitations are public work items, not hidden assumptions. See
[Performance and Scaling](docs/PERFORMANCE_AND_SCALING.md) and [Roadmap](ROADMAP.md).
