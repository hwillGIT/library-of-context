# The Library of Context

![The Library of Context virtual-memory architecture](library-of-context-system.svg)

The Library of Context makes a model’s usable history larger without making every
model request larger. Complete conversations, documents, decisions, and tool results
live outside the prompt in a local library. A retrieval-and-paging layer selects only
the books relevant to the current subject and places them on a fixed-size “reading
desk.” When the subject changes, the desk is replaced.

## The metaphor

| In the library | In the implementation |
|---|---|
| Book | A chunk of text with provenance, metadata, importance, and an embedding |
| Catalog | Hybrid semantic and full-text retrieval |
| Shelves | SQLite on disk, the durable source of truth |
| Nearby stacks | Local Redis, shared hot records and query results |
| Librarian cart | A byte-bounded LRU in the current Python process |
| Reading desk | The token-bounded context block visible to the model |
| Returning a book | Swapping an irrelevant chunk out of the next prompt |

“Offline” means outside the model’s live context window. It does not mean inaccessible:
the catalog can retrieve an old book whenever the new request makes it relevant again.

## The invariant that prevents context growth

For each session there is exactly one current desk snapshot:

```text
new desk = pinned books + highest-ranked books that fit the token budget

swapped in  = new desk − old desk
swapped out = old desk − new desk
retained    = new desk ∩ old desk
```

The caller replaces its earlier `<library-context>` block with the new block. It never
appends every successive retrieval. `LibraryContextGovernor` enforces the stronger
form: it durably appends each event, overlays recent unindexed events, and emits a fresh,
bounded, stateless `messages` array for every model call.

## The context governor

```text
prepare(user turn)
  ├─ commit thread event + outbox atomically
  ├─ add event to the recent ring
  ├─ page protected, recent, and retrieved context into a bounded envelope
  └─ return messages for the model

commit(model or tool result)
  ├─ commit another durable event
  └─ queue asynchronous embedding and indexing
```

The recent ring solves the freshness gap: a turn remains visible immediately even if
its embedding is still queued. If the work ring fills or the process stops, the durable
outbox preserves the work for recovery.

## One request, step by step

1. **Record:** a new event and indexing outbox item are committed to SQLite before it
   can leave the native prompt.
2. **Consult:** the current subject becomes a catalog query. The engine combines vector
   similarity, SQLite FTS rank, explicit importance, and recency.
3. **Pack:** pinned books go first. Ranked books fill the remaining token budget. An
   oversized final book is truncated to the exact remaining allowance.
4. **Swap:** the engine compares the new selection with the previous desk and reports
   `swapped_in`, `swapped_out`, and `retained`.
5. **Read:** only protected state, the bounded recent ring, and the replacement desk are
   sent to the model. The rest stays locally addressable.
6. **Index:** a bounded work ring asynchronously embeds durable events; overflow remains
   safely queued in the SQLite outbox.

Paging can happen on request, immediately after a focus change, or periodically for a
long-running agent.

## Memory hierarchy

| Tier | Purpose | Capacity policy | Correctness role |
|---|---|---|---|
| L1 process RAM | Fastest record and query reuse | Byte-bounded LRU | Disposable |
| L2 local Redis | Shared hot set, TTLs, working desks, generations | 4 GiB default, `allkeys-lfu` | Disposable |
| L3 SQLite | Text, vectors, metadata, provenance, FTS | Disk capacity | Authoritative |
| Reading desk | Model-visible working set | Hard token budget | Replaced per focus |

Redis handles cache housekeeping but never owns unique data. If Redis is stopped,
evicted, or restarted, the engine falls through to SQLite and lazily warms the hot tier
again.

## Hybrid RAG ranking

The default score is intentionally inspectable:

```text
0.60 × normalized cosine similarity
+ 0.25 × reciprocal FTS rank
+ 0.10 × explicit importance
+ 0.05 × exponential recency
```

Metadata filters can restrict retrieval to a project, user, agent, source, or security
boundary. The included hashing embedder is private and dependency-free; an Ollama
adapter is included for stronger local semantic embeddings.

## Free local Redis on Windows—no Docker

This workstation uses Redis 7 inside Ubuntu WSL. Installation and cache policy are
automated by:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-local-redis.ps1
```

The script installs the Ubuntu package, enables the service, limits it to 4 GiB,
selects LFU eviction, and disables Redis persistence because SQLite owns durability.
Verify both Redis and the Library:

```powershell
wsl -d Ubuntu -- redis-cli ping
python -m library_of_context doctor
```

No Docker subscription or cloud account is required. A managed Redis endpoint can be
used later by changing `LIBRARY_OF_CONTEXT_REDIS_URL`; private context would then cross
the local-machine boundary and should be protected with an authenticated TLS proxy or a
client that supports `rediss://`.

## Codex integration

The dependency-free STDIO MCP server exposes three groups of tools:

- shelving and catalog search;
- immediate or periodic reading-desk replacement;
- governed `prepare → model → commit` prompt construction and message recording.

Copy `integrations/codex-config.toml.example` into a trusted project’s
`.codex/config.toml`, adjust its absolute `cwd`, and restart the local Codex client. The
server’s initialization instructions explicitly say to refresh at task start or focus
change and to replace—not append—the prior desk.

The direct loop is:

```text
prepare and durably record → build bounded prompt → call model
          ▲                                      │
          └──────── commit response ◀────────────┘
```

## What the system does—and does not—expand

It expands **addressable context** into RAM and disk. It does not alter the physical
context-window limit of a hosted model. Retrieval quality still matters: a book that is
poorly chunked, mislabeled, or unrelated to the query may stay on the shelf. The hard
budget protects prompt size even when retrieval returns too much.

For implementation details, see [Architecture](architecture.md). For setup and API
examples, see the [documentation home](index.md).
