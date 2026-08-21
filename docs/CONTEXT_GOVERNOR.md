# Context Governor

The context governor controls the boundary around each model call. It replaces transcript growth with reversible semantic paging.

Semantic paging selects protected, recent, and relevant information for one bounded request. The [Glossary](GLOSSARY.md) defines shared technical terms.

A reading desk is the bounded set of retrieved books for one thread and subject. A token is a small text unit that a model processes.

SQLite is the embedded database that stores authoritative Library data in one file.

The caller must let the governor control the request before and after the model call.

## Contract

```text
prepare(user event) -> bounded model envelope
model(envelope.messages) -> response
commit(response) -> durable event
```

`prepare()` stores the user event and its outbox entry in one SQLite transaction. It completes this storage before it constructs the prompt.

This order keeps every omitted event recoverable. An atomic transaction applies all its changes or none.

An outbox is a durable table of work that another component must process.

## Python lifecycle

```python
from library_of_context import LibraryOfContext

with LibraryOfContext("data/library.sqlite", redis_url="") as library:
    with library.open_context_governor(
        "thread-123",
        token_budget=8_000,
        recent_token_budget=2_500,
        protected_token_budget=1_000,
    ) as governor:
        policy = governor.protect(
            "Never mutate production without explicit approval.",
            label="safety-boundary",
        )

        envelope = governor.prepare(
            "Inspect the production deployment configuration.",
            event_id="client-turn-42",
        )

        # result = your_model(input=envelope.messages)
        governor.commit(
            "Inspection completed without changing production.",
            event_id="client-response-42",
        )

        governor.release(policy.event_id)
```

The empty `redis_url` disables the optional Redis key-value cache for this example.

Caller-supplied event identifiers make repeated requests safe. The Library rejects reuse of an event identifier with different content.

## Result from `prepare()`

`prepare()` returns a `GovernedPrompt` object. A watermark identifies the highest sequence that completed a processing stage without a gap.

| Field | Meaning |
|---|---|
| `messages` | Complete bounded input for the next model call |
| `token_count` and `token_budget` | Estimated use of the native context window |
| `recent_event_ids` | Thread events inserted directly |
| `protected_event_ids` | Protected events inserted directly |
| `paged_out_events` | Durable thread events omitted from direct prompt messages |
| `desk` | Retrieved books and reading-desk changes |
| `watermarks` | Durable and searchable sequence boundaries |
| `replaces_compaction` | Indicator that semantic paging controls the request |

The caller sends only `messages` to the model. It must not append the complete transcript.

## Immediate visibility and asynchronous indexing

The user event can remain unindexed when `prepare()` returns. The recent ring inserts the durable event directly into the prompt.

Asynchronous indexing can finish after `prepare()` returns. Synchronous work must finish before the caller receives a result.

An index worker later converts the event into a searchable record. The record remains retrievable after the event leaves the recent ring.

```text
recorded_through = 18
indexed_through  = 16

retrieval searches through 16
recent overlay supplies 17 and 18
```

This behavior gives the thread immediate access to its own new events. The prompt does not wait for the embedding service.

An embedding is a numeric representation of text meaning. Indexing stores data that retrieval can search.

## Protected context

The governor protects system and developer events by default. A caller can also protect a decision, constraint, plan, question, or important tool state.

Protection gives an event priority for direct prompt residence. The `protected_token_budget` still limits the protected set.

Protection cannot exceed the model input limit without notice. An automatic policy must report each omitted protected item as a separate alert.

The default configuration disables automatic protection. Automatic protection can preserve stale, conflicting, or hostile instructions. It can also displace current evidence.

An automatic policy must explain each protected item. It must show the evidence and the condition for release.

Tests must compare that policy with explicit protection. The policy must improve continuity without increasing failures from stale instructions.

`release(event_id)` removes the protection flag. It keeps the durable event and its searchable book.

## Strict freshness

Most calls can use the recent ring while indexing continues. `strict_freshness=True` waits until indexing reaches the thread's recorded watermark.

The call returns a timeout if indexing does not reach that sequence in time.

Use strict freshness for audits and retrieval tests. Do not use it as the normal interactive path.

Strict freshness is a time-bounded wait. It does not guarantee completion during overload.

Other threads can use the shared worker pool. Atomic claims preserve order within one thread. The scheduler does not reserve capacity or provide weighted fairness.

A stronger freshness service level needs measured admission limits and a priority policy. A service level defines a measurable operational target.

## Work-ring overflow and recovery

The bounded work ring contains leased event references. A lease grants temporary ownership of work.

If the ring is full, event storage still succeeds. The outbox row already holds the pending work on disk.

The process-owned indexer scans the outbox after each task and during idle polling.

After a restart, the system performs these operations:

1. The thread registry restores recent events from `thread_events`.
2. The shared indexer claims eligible outbox rows in one atomic database operation.
3. The indexer queues claims until the ring reaches its capacity.
4. Stable record identifiers make repeated processing safe.
5. Watermarks advance only after record storage succeeds.

Each claim contains an owner, an expiration time, and a unique token. The database rejects completion from a worker with an obsolete token.

Workers in one runtime share a process-owner identity. They do not reclaim that owner's expired claim.

The worker with the active token can complete after the listed expiration. A replacement process can reclaim the work after expiration.

A stalled service call requires an operation time limit and supervised process restart. Indexer status reports active-claim age and service degradation.

Retries use increasing delays with random variation. The indexer quarantines an event after its configured attempt limit.

Quarantine isolates failed work. It exposes the event in governor status and permits later events to continue.

The Python governor can return a quarantined event to the queue with `retry_failed(event_id)`.

## Runtime ownership

One `LibraryOfContext` instance owns a shared runtime. All governors from that instance share one indexer, scheduler, thread registry, and desk registry.

A governor is a lightweight view of `ThreadKey(collection, session_id)`. The collection identifies a project namespace. The session identifier identifies one chat thread.

A chat does not allocate a worker or scheduler. Active thread rings and desks have count, token, and idle-time limits.

The runtime reconstructs evicted state from SQLite.

Separate embedded Model Context Protocol processes own separate runtimes. Model Context Protocol (MCP) defines an interface for agent tools and resources.

Thin MCP bridges can connect several clients to one loopback daemon with `--daemon-url`. A thin bridge forwards requests without opening the database.

Every route requires the daemon's bearer token. A bearer token is a secret value that grants access to its holder.

The token authenticates a trusted local client. It does not identify a user or prove team membership. Operators must not expose the service beyond loopback.

## Model Context Protocol operations

The standard-input and standard-output MCP server exposes these tools:

- `library_context_prepare`
- `library_context_commit`
- `library_context_protect`
- `library_context_release`
- `library_context_status`
- `library_context_flush`

These tools expose the governor lifecycle to an external gateway. They do not control a host's internal context manager.

A normal MCP agent can shelve records and recall a reading desk. Calling `prepare` during a model turn cannot reduce that same turn.

Automatic enforcement requires a client that owns the next model call. That client must send only the returned messages.

## Hypertext Transfer Protocol operations

Hypertext Transfer Protocol (HTTP) endpoints provide the same operations:

- `/context/prepare`
- `/context/commit`
- `/context/protect`
- `/context/release`
- `/context/flush`
- `/context/status/{session}`

Lower-level record, query, and desk endpoints use the same thread, project, and trusted team routes as the Python interface.

The gateway supplies team identifiers as routing values. The Library does not verify team membership.

The local server requires one shared bearer credential. It validates the loopback `Host` value and rejects browser-origin traffic.

The server accepts write bodies only in JavaScript Object Notation format. It does not provide Transport Layer Security or user authorization.

Do not bind or proxy the server to a shared network. A shared service requires user authentication, authorization, encryption, and privacy controls.

## Integration checklist

A conforming gateway must:

- use one stable collection and session identifier for each thread
- call `prepare()` before every model request
- send only the returned messages
- call `commit()` for assistant results and important tool results
- use stable event identifiers when a caller can retry a request
- keep the owning `LibraryOfContext` active for the process or daemon lifetime
- report watermark lag and worker errors
- retain provider compaction only as an emergency fallback
- test process failure between event storage, model call, result storage, and index completion

See [Why These Improvements?](WHY_THE_ROADMAP.md) for policy options, costs, and adoption criteria.
