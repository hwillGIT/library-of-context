# The Library of Context

![The Library of Context virtual-memory architecture](library-of-context-system.svg)

The Library of Context stores information outside an artificial intelligence model's prompt. A prompt is the input that the model processes.

A model counts prompt text in units called tokens.

The stored information can include conversations, documents, decisions, and tool results. The Library can retrieve this information when it becomes relevant.

The Library selects a size-limited set for the current subject. It places this set on a fixed-size reading desk.

The reading desk represents the context that the model can read. The Library replaces the desk when the subject changes.

Metadata describes a stored record, such as its source or document type. An embedding is a numeric text representation that supports similarity searches.

In this guide, **durable** means that SQLite retains the data after a process restart.

## The metaphor

| In the library | In the implementation |
|---|---|
| Book | Public name for one searchable `ContextRecord`. Agent views contain short excerpts but omit numeric embeddings and complete metadata |
| Catalog | Stored set of searchable records and their descriptive data |
| Shelves | SQLite on disk, the durable source of truth |
| Nearby stacks | Optional local Redis cache for frequently used records and search results |
| Librarian cart | A byte-limited least-recently-used cache in the Python process |
| Reading desk | The prompt context block with a fixed token limit |
| Returning a book | Swapping an irrelevant chunk out of the next prompt |

The term **offline** means outside the model's active context. The content remains available to the Library.

Catalog search can retrieve an older book when a request makes that book relevant.

## The invariant that prevents context growth

For each `ThreadKey(collection, session_id)`, the Library keeps one desk snapshot. A snapshot records the selected books at one point in runtime.

```text
new desk = pinned books + highest-ranked books that fit the token budget

swapped in  = new desk − old desk
swapped out = old desk − new desk
retained    = new desk ∩ old desk
```

The caller replaces the prior `<library-context>` block with the new block. The caller must not append each retrieved block to prior blocks.

`LibraryContextGovernor` stores each event before the model can lose it. It includes recent events that a search worker has not indexed.

The governor creates a new size-limited `messages` array for each model call. The array does not depend on a provider-managed conversation.

## The context governor

```text
prepare(user turn)
  ├─ store the thread event and pending task in one database operation
  ├─ add event to the recent ring
  ├─ put protected, recent, and retrieved context in a size-limited request
  └─ return messages for the model

commit(model or tool result)
  ├─ commit another durable event
  └─ queue embedding and indexing for a background worker
```

The recent ring is an ordered and size-limited memory area. It keeps a turn visible while the embedding task waits in a queue.

A queue is an ordered list of tasks that wait for processing.

The durable outbox is a SQLite table that stores pending indexing tasks.

If the work ring becomes full or the process stops, the outbox retains the pending task.

## One request, step by step

1. **Record:** Store the event and its pending indexing task in SQLite.
2. **Consult:** Use the current subject as a catalog query.
3. **Rank:** Combine numeric text similarity, full-text search rank, assigned importance, and age.
4. **Pack:** Add pinned books to the desk.
5. **Pack:** Add the highest-ranked books that fit the remaining token limit.
6. **Trim:** Shorten the final book when it exceeds the estimated remaining token capacity.
7. **Swap:** Compare the selected books with the prior desk.
8. **Report:** Return `swapped_in`, `swapped_out`, and `retained` book lists.
9. **Read:** Send protected state, recent events, and the replacement desk to the model.
10. **Index:** Process durable events in a size-limited background work ring.

The SQLite outbox retains work that does not fit in the ring.

Paging replaces one model-visible context block with another block. It can occur on request, after a focus change, or at set intervals.

## Memory hierarchy

Random-access memory (RAM) stores active process data. A cache keeps temporary copies of frequently used data.

A runtime is one active Library instance and its owned resources. SQLite keeps the required durable copy.

| Tier | Purpose | Capacity policy | Correctness role |
|---|---|---|---|
| Level 1 process RAM | Reuse records and results inside the process | Byte-limited least-recently-used cache | Disposable |
| Level 2 local Redis | Cache frequently used data and reading desks | One-gibibyte default, `allkeys-lfu` policy | Disposable |
| Level 3 SQLite | Store text, numeric vectors, metadata, origin data, and search indexes | Available disk capacity | Required stored copy |
| Reading desk | Model-visible working set | Hard token budget | Replaced per focus |

Redis manages temporary cached data but does not own unique data. If Redis stops or removes data, the Library reads the data from SQLite.

The Library adds frequently used data to Redis as requests use that data. Each runtime uses a random, versioned Redis keyspace.

A keyspace is a set of Redis keys that belong to one runtime. A process restart creates an empty keyspace.

The new runtime ignores data from another runtime.

## Combined retrieval ranking

Retrieval-augmented generation (RAG) adds retrieved information to a model request. The Library ranks retrieval results with this fixed calculation.

Cosine similarity measures direction agreement between two numeric text representations. Reciprocal rank gives more weight to results near the start of a list.

The recency term gives more weight to a recent record. The effect decreases exponentially as the record becomes older.

```text
0.60 × normalized cosine similarity
+ 0.25 × reciprocal full-text-search rank
+ 0.10 × explicit importance
+ 0.05 × exponential recency
```

Metadata filters select catalog fields, such as a source or document type. They do not authorize access.

The Library applies thread, project, and team visibility scopes before it loads complete records. A scope defines which records a request can retrieve.

The Library treats supplied team identifiers as routing data. It does not verify team membership.

The hashing embedder runs locally and does not use an external service. The optional Ollama adapter creates embeddings with a local model.

## Free local Redis on Windows without Docker

Windows Subsystem for Linux (WSL) runs a Linux environment on Windows. The installer creates an authenticated Redis 7 service inside Ubuntu WSL.

The service name is `library-of-context-redis`. It listens on local loopback port 6380.

A loopback address sends traffic only inside the local computer.

The installer does not change the default Redis service in Ubuntu.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-local-redis.ps1
```

The script prints a `LIBRARY_OF_CONTEXT_REDIS_URL` environment assignment that contains the generated password. Run this assignment in the Library's PowerShell session.

The service uses a one-gibibyte least-frequently-used cache by default. It disables Redis persistence because SQLite owns the durable data.

Set the printed address. Then verify the Library:

```powershell
python -m library_of_context doctor
```

This configuration does not require Docker or a cloud account. Set `LIBRARY_OF_CONTEXT_REDIS_URL` to use a managed Redis service.

A managed service sends private context outside the local computer. The Library client accepts `redis://` connections.

Place a remote connection inside an authenticated Transport Layer Security tunnel or secure proxy.

## Agent integration

The standard-input-and-output Model Context Protocol (MCP) server provides Library, desk, and governor tool groups. It does not add a package dependency.

Select the integration according to who controls the model call:

- A normal MCP agent uses storage, catalog search, and desk replacement as cooperative memory. It does not control growth of the host transcript.
- A custom gateway uses `prepare → model → commit`. It sends only the returned messages as the complete request.

The Codex template permits only the cooperative tools. Copy it to `.codex/config.toml` in a trusted project.

Select a database and namespace for that project. Merge the supplied instructions into the target `AGENTS.md` file.

Restart the local client. See [Add the Library to your agent](ADD_TO_YOUR_AGENT.md).

The automatic gateway loop is:

```text
prepare and store event → build size-limited prompt → call model
          ▲                                      │
          └──────── commit response ◀────────────┘
```

## What the system does—and does not—expand

The Library expands **addressable context** into RAM and disk. Addressable context is stored information that the Library can retrieve.

The Library does not change the context limit of a hosted model. Retrieval quality determines which stored information the Library selects.

A poorly divided, incorrectly labeled, or unrelated book can remain on the shelf. The fixed token limit controls prompt size when retrieval returns excess content.

For implementation details, see [Architecture](architecture.md). For setup and application programming interface examples, see the [documentation home](index.md).

The [glossary](GLOSSARY.md) defines shared Library terms.
