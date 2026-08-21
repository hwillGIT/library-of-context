# Architecture

## Objective

The Library of Context supplements a model's native context window. The native window contains the tokens that a model can process in one request.

The Library stores more addressable context in memory and on disk. Addressable context is information that the application can retrieve for a later request.

A token is a small text unit that a model processes. SQLite is the embedded database that stores the authoritative Library data.

The Library controls two failure modes:

1. **Over-expansion:** A growing transcript fills the native window.
2. **Over-expiration:** Truncation or compression removes useful information before the application can recover it.

The context governor records each managed event. It builds a bounded working set for each model call. The Library does not increase the model's physical input limit.

A reading desk is the bounded set of retrieved books for one thread and subject. A Library runtime owns the shared services for one database.

An outbox is a durable SQLite table of indexing work. The Library stores each event and its outbox item in one atomic operation.

The [Glossary](docs/GLOSSARY.md) defines shared technical terms. [Related Work and Design Landscape](docs/RELATED_WORK.md) provides comparative evidence. [Capability Status](docs/STATUS.md) lists support and limitations.

## Core invariants

An invariant is a condition that the system must always preserve.

1. **Durable before evictable.** The Library commits an event to SQLite before the governor can omit it from a later prompt.
2. **Bounded native context.** A governed envelope stays within its configured token budget under the selected estimator.
3. **Recent writes remain visible.** The bounded recent ring supplies recorded events while indexing is incomplete.
4. **Ring overflow does not lose data.** The bounded work ring stores event identifiers for indexing. A durable SQLite outbox retains excess work.
5. **One replacement desk.** Retrieved Library context replaces the prior block. It does not grow by repeated addition.
6. **SQLite is authoritative.** Random-access memory and the Redis key-value cache are disposable accelerators.
7. **Cloud independence.** Local prompt construction continues without Redis, a team relay, or a network connection.

## Governed turn lifecycle

```text
                   synchronous durability boundary
user event ───────> thread_events + context_outbox ───────> acknowledgment
                           │                         \
                           │                          └─> bounded work ring
                           ▼                                  │
                  token-aware recent ring                    ▼
                           │                           embed and index
                           ├──────────────┐                   │
                           ▼              ▼                   ▼
protected context ─> context governor <── catalog <── SQLite / memory / Redis
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

`LibraryContextGovernor.prepare()` stores the user event before it assembles the model request. The caller sends `envelope.messages` to the model. The caller records the result with `commit()`.

The model application programming interface receives one bounded request. It does not receive a growing transcript. An application programming interface defines how software components exchange requests and responses.

Synchronous storage finishes before the caller receives an acknowledgment. Asynchronous index work can finish later. An atomic transaction applies all its changes or none.

### Watermarks

A watermark identifies the highest sequence that completed a processing stage without a gap. Each thread reports these watermarks:

- `recorded_through`: highest durable sequence
- `embedded_through`: highest contiguous embedded sequence
- `indexed_through`: highest contiguous searchable sequence
- `team_synced_through`: reserved for acknowledged team promotion
- `pending_events`: durable events that await local indexing

The local worker embeds and indexes an event in one operation. Therefore, the embedded and indexed watermarks advance together.

The public contract permits separate stages. An implementation can advance each watermark independently.

Each event reserves its derived record identifier in SQLite. A normal record update cannot use an identifier that `thread_events` references.

Event storage rejects an identifier that another record or event owns. Claim completion verifies event identity and projection provenance.

Projection provenance records the source event of a derived record. The same transaction stores the record and advances the watermark.

## Prompt assembly

The governor constructs this message sequence:

```text
protected native instructions
+ selected protected thread events
+ token-bounded recent thread events
+ one retrieved <library-context replacement="true"> block
```

The Library stores the current user turn in full. An oversized turn can exceed the recent-context budget.

In that case, the prompt receives a marked, shortened view. The complete event remains on disk.

Protected context remains eligible until a caller releases it. Retrieved books exclude events that the protected or recent selection already contains.

The resulting `GovernedPrompt` reports several facts. It reports selected event identifiers, paged-out event count, token pressure, reading-desk changes, and visibility watermarks.

## Memory and work hierarchy

| Tier | Structure | Policy | Correctness role |
|---|---|---|---|
| Immediate thread overlay | Token-aware first-in, first-out ring | Event and token limits, with a marked memory projection for oversized content | Fresh visibility |
| Process memory cache | Least-recently-used cache with estimated item sizes | Record and query budgets | Disposable acceleration |
| Runtime Redis cache | Redis key-value cache | Versioned runtime key space, expiration time, and least-frequently-used eviction | Disposable acceleration |
| Durable library | SQLite with a write-ahead log | Disk and quota policy | Authoritative storage |
| Model-visible desk | Prompt messages | Hard token budget | Rebuilt for each turn |

First-in, first-out order removes the oldest item first. Least-recently-used eviction removes the item that the process accessed least recently.

Least-frequently-used eviction removes items with the fewest accesses. A write-ahead log records changes before SQLite applies them to the database file.

The **recent ring** preserves thread order. It also supplies events that indexing has not processed.

The **work ring** is a bounded queue. It carries durable event identifiers to asynchronous index workers.

The work ring is not a message broker. A message broker stores and delivers messages between independent components.

The transactional SQLite outbox provides recovery. A scanner restores pending identifiers to the ring after a restart.

Index writes use stable record identifiers. Therefore, replaying the same work has the same effect as one successful write.

## Durable schema

`thread_heads` assigns an increasing sequence to each `(namespace, session_id)` pair. A namespace separates one collection of records from another.

`thread_events` stores role, content, metadata, protection state, token estimate, record identifier, timestamps, and index visibility. `context_outbox` stores indexing work in the same transaction.

`records` stores each retrieval unit. A record contains text, metadata, provenance, importance, timestamps, a content hash, visibility ownership, and 32-bit floating-point vectors.

A **book** is the public Library view of one `ContextRecord`. The database does not store a separate book entity.

`records_fts` provides full-text search (FTS) candidate identifiers. FTS finds records that contain matching words without scanning every record in application code.

Each record has one scope:

- A `thread` record has an `owner_session_id`.
- A `project` record has no thread owner.
- A `team` record has a `team_id`.

Direct lookup, full-text search, vector retrieval, pinning, desk packing, and query caching use the same scope selection.

Promotion copies a record into a broader scope. It records the source and does not change the source record's scope.

## Retrieval and reading-desk paging

The default ranker combines four scores:

```text
0.60 × normalized cosine similarity
+ 0.25 × reciprocal FTS rank
+ 0.10 × explicit importance
+ 0.05 × exponential recency
```

Cosine similarity compares the direction of two numeric vectors. Reciprocal rank gives more weight to records near the top of full-text search results.

The exponential recency score decreases at a fixed proportional rate as a record ages.

The ranker considers pinned books first. Other books fill the remaining token budget in relevance order.

`WorkingSet` reports changes to the reading desk:

```text
swapped_in  = new desk - previous desk
swapped_out = previous desk - new desk
retained    = new desk ∩ previous desk
```

The Library limits full-text search output and later record loading by candidate count. SQLite can still inspect a large internal posting list for a broad term.

The portable vector path scores every live record in a namespace. Work therefore grows with the number of live records.

A large catalog can require an approximate nearest-neighbor adapter. This adapter searches likely vector matches without scoring every vector.

Adoption requires a declared latency or memory target. Tests must also measure retrieval quality, deletion, recovery, filtering, and platform support.

## Process and concurrency model

One `LibraryOfContext` instance owns one `LibraryRuntime`. All governors, sessions, desks, Model Context Protocol handles, and Hypertext Transfer Protocol handles share that runtime.

Model Context Protocol defines an interface for agent tools and resources. Hypertext Transfer Protocol carries requests between local clients and the daemon.

The runtime owns these shared components:

- one fixed `OutboxIndexer` worker pool and bounded dispatch ring
- one `DeskScheduler` coordinator with a fixed refresh pool
- one thread-state registry with expiration and least-recently-used eviction
- one bounded `ContextSwapper` desk registry
- one SQLite store, random-access memory cache, and optional Redis client

Every stateful operation uses `ThreadKey(collection, session_id)`. The collection identifies a project namespace. The session identifier identifies one chat thread.

Per-thread state contains an ordered recent ring and a reentrant operation lock. A reentrant lock permits the same execution thread to acquire the lock again.

A chat does not create a copy of the runtime. Operations for one thread run in order. Independent threads can run concurrently.

The registry removes idle state and reconstructs it from SQLite. The runtime returns an explicit error when the registry reaches its capacity.

The recent ring limits event count and content tokens. The desk registry limits entry count and idle time.

These limits do not guarantee a fixed resident set size. Resident set size is the physical memory that the operating system assigns to a process.

Event metadata and selected `SearchHit` objects retain complete Python values. The byte-estimated cache limits only record and query entries.

A deployment with a strict memory limit needs more quotas. It must limit metadata, embeddings, and desk bytes. Tests must compare those quotas with measured process memory.

The outbox indexer claims work atomically in SQLite. Each claim includes a token, lease, process owner, and thread sequence.

A lease grants temporary ownership of work. Workers retry failures with increasing delays and random variation.

The indexer quarantines an event after its configured attempt limit. Quarantine isolates failed work so that later events can continue.

Workers for one process owner do not reclaim an expired claim from that owner. A replacement owner can reclaim the claim after the lease expires.

This ownership rule prevents two workers from repeatedly replacing each other's claims. A stalled service call still needs an operation time limit and supervised restart.

The SQLite outbox is authoritative. The memory ring only accelerates dispatch.

The supported topology permits one process owner for each database. Other local agents use thin bridges to the daemon.

Every runtime owner acquires the database lock before it opens SQLite. Embedded, Model Context Protocol, Hypertext Transfer Protocol, and daemon owners use the same lock.

The compatibility argument `exclusive_database_owner` does not disable the lock. Only one runtime can own a database at one time.

Shutdown stops public admission. It drains accepted operations and owned workers. It closes SQLite before it releases the database lock.

An incomplete close leaves the lifecycle in `closing`. A later close attempt continues the shutdown. The runtime does not release ownership around an open connection.

`GovernedTextAgent` can enforce a fresh bounded request because it owns the model call. The Hypertext Transfer Protocol boundary can enforce the same rule.

A normal Model Context Protocol host calls tools during an existing turn. Therefore, it cannot use a tool result to reduce that same turn.

Its safe mode uses cooperative shelving and reading-desk recall. Automatic enforcement requires a client that owns the next model request.

Embedded standard-input and standard-output mode creates one runtime for each server process. Daemon mode places one runtime behind a loopback service.

Loopback communication stays on the same computer. Thin bridges use `--daemon-url` and do not open SQLite, Redis, an embedder, or worker pools.

The daemon holds the database lock from before SQLite initialization through shutdown. It rejects non-loopback addresses, limits requests, and applies read time limits.

Every route requires a bearer token from an owner-readable file. A bearer token is a secret value that grants access to its holder.

The transport rejects browser origins, cross-site requests, non-loopback `Host` values, and write requests that are not JavaScript Object Notation.

The service does not provide Transport Layer Security, user identity, or team authorization. Operators must use it only with trusted local processes.

### Code organization

| Modules | Responsibility |
|---|---|
| `library`, `engine` | Public storage interface, cache tiers, persistence, and retrieval coordination |
| `governor`, `prompt_builder` | Governed-turn lifecycle and bounded request assembly |
| `runtime`, `thread_state`, `scheduler` | Shared process ownership, bounded thread state, and desk scheduling |
| `indexing`, `rings`, `text_budget` | Outbox claims, bounded queues, recent events, and text budgeting |
| `store`, `ram`, `redis_hot` | SQLite authority and disposable cache implementations |
| `swapper`, `resource_registry` | Reading-desk state and thread-safe session ownership |
| `agent` | Stateless text-model call boundary |
| `http_app`, `server`, `client`, `process_lock` | Local routes, bounded transport, thin clients, and daemon ownership |
| `mcp_schema`, `mcp_views`, `mcp_service`, `mcp_server` | Model Context Protocol contracts, serialization, tool execution, and request input and output |
| `cli_parser`, `cli_config`, `cli_commands`, `quickstart` | Command-line definitions, runtime construction, command execution, and diagnostics |

## Failure behavior

| Failure | Behavior |
|---|---|
| Process stops after event storage | The event and outbox remain. A later worker replays the work. |
| Work ring is full | The event remains in the outbox. The Library does not lose acknowledged context. |
| Embedder is unavailable | The recent ring preserves visibility. The outbox retries the work. |
| Event reaches its attempt limit | Quarantine exposes the failure in status and allows later events to continue. |
| Redis is unavailable | Reads continue through memory and SQLite. The runtime retries Redis later. |
| Memory or Redis evicts data | The read uses SQLite. |
| Old Redis keys remain after restart | A separate runtime key space starts empty. Old values expire and remain unused. |
| Strict freshness reaches its time limit | The caller receives a timeout. The Library does not report false freshness. |
| Team or cloud service is unavailable | Local prompt construction continues. |

SQLite uses write-ahead logging with `synchronous=NORMAL`. This setting can lose recent committed changes during some power failures.

A deployment that requires a recovery point objective of zero must select and test stronger disk synchronization. A zero objective permits no acknowledged data loss.

## Local-first team boundary

Raw thread events remain private and local by default. `promote_book()` copies an explicitly selected thread record into project or team scope.

The copy retains its source and provenance. The Library trusts a supplied team identifier as routing data. The identifier does not prove membership.

An access-control-list-aware service can enforce team permissions. Access-control lists define which identities can access each resource. That service remains outside the local trust boundary.

Redis publish-and-subscribe messages can provide wake-up or invalidation hints. Publish-and-subscribe sends each message to listening consumers but does not provide durable replay.

The local Redis cache disables persistence. Therefore, it must not serve as the team message broker.

Redis Streams and NATS JetStream are possible durable broker choices. SQLite outbox and inbox records remain each local node's recovery source.

See [Team Architecture](docs/TEAM_ARCHITECTURE.md).

## Security and privacy boundary

The default configuration uses local resources. SQLite remains on disk, Redis uses loopback, and embedding does not require a hosted provider.

A bearer token authenticates daemon clients on one workstation. It does not provide network, user, or team security. The service has no Transport Layer Security.

Operators must not expose the service directly to another computer.

A team deployment needs several controls. It needs device identity, project authorization, encryption, retention rules, deletion rules, provenance, and audit logs.

The retrieval system must apply authorization filters before it selects candidates or loads record text.

## Known limitations

- Exact vector scoring performs work proportional to namespace size.
- The default token estimator is approximate and is not specific to one model.
- The runtime does not combine several embedding calls into one batch.
- Virtual-context sessions embed records synchronously. The context governor uses the durable event and outbox path.
- The loopback daemon uses one shared bearer credential. It does not identify individual users.
- Team identifiers do not implement an access-control list or prove identity.
- The local implementation does not provide team synchronization, team authorization, branch merging, or a reviewed promotion workflow.
- The Model Context Protocol integration cannot replace an undocumented host compaction function.

See [Performance and Scaling](docs/PERFORMANCE_AND_SCALING.md), [Why These Improvements?](docs/WHY_THE_ROADMAP.md), and [Roadmap](ROADMAP.md). These documents describe alternatives, evidence needs, and adoption criteria.
