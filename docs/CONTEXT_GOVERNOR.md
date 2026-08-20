# Context Governor

The context governor is the integration layer that replaces transcript growth with
reversible semantic paging. It must own the boundary immediately before and after each
model call.

## Contract

```text
prepare(user event) -> bounded model envelope
model(envelope.messages) -> response
commit(response) -> durable event
```

`prepare()` commits the user event and its outbox entry in one SQLite transaction before
constructing a prompt. This ordering prevents an event from leaving active context
before it is recoverable.

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

Caller-supplied event IDs provide idempotency across network retries. Reusing an event ID
with different content is rejected.

## What `prepare()` returns

`GovernedPrompt` contains:

| Field | Meaning |
|---|---|
| `messages` | Complete bounded input for the next model call |
| `token_count` / `token_budget` | Estimated pressure on the native window |
| `recent_event_ids` | Thread events inserted directly |
| `protected_event_ids` | Protected events currently resident |
| `paged_out_events` | Durable thread events omitted from direct prompt messages |
| `desk` | Retrieved books and swap delta |
| `watermarks` | Durable and searchable visibility boundaries |
| `replaces_compaction` | Signals semantic-paging mode |

The caller sends only `messages`. It must not append the original full transcript again.

## Immediate visibility and asynchronous indexing

The user event acknowledged by `prepare()` may not be embedded yet. That is safe because
the recent ring overlays the durable event directly into the prompt. When the worker
indexes it, the record becomes retrievable after it ages out of the ring.

```text
recorded_through = 18
indexed_through  = 16

retrieval searches through 16
recent overlay supplies 17 and 18
```

The prompt therefore has read-your-own-context semantics without waiting for an
embedding service.

## Protected context

System and developer events are protected by default. Callers can explicitly protect a
decision, constraint, active plan, unresolved question, or important tool state.

Protection means “eligible for direct residence before ordinary retrieval.” It is still
subject to `protected_token_budget`; protection cannot silently break the model's hard
input limit. A future policy layer should report omitted protected items as a distinct
alert when the protected set exceeds its budget.

Automatic protection is disabled by default because it could preserve stale,
conflicting, or injected instructions and crowd out current evidence. Any future policy
must explain why an item was protected, show its evidence and release condition, and
demonstrate improved continuity without increasing stale-instruction failures.

`release(event_id)` removes the protection flag but retains the durable event and its
searchable book.

## Strict freshness

Most calls should use the recent overlay and accept asynchronous index visibility.
`strict_freshness=True` waits until the thread's index reaches its recorded watermark.
It fails with a timeout if that boundary is not reached. Use it for explicit audit or
retrieval tests, not as the default interactive path.

This is a deadline-bounded wait, not an unconditional guarantee under overload. The
current per-governor workers can encounter unrelated global outbox work. A multi-agent
daemon needs atomic claims, thread partitions, and priority policy before it can offer a
stronger freshness service level.

## Work-ring overflow and recovery

The bounded work ring contains only `(namespace, event_id)` references. If it is full,
the append still succeeds because the outbox row is already durable. The worker scans
the outbox after every task and during idle polling.

After a restart:

1. the governor restores recent events from `thread_events`;
2. it scans all unprocessed outbox rows;
3. it requeues identifiers up to the ring capacity;
4. stable record IDs make replay idempotent;
5. watermarks advance only after the index record is written.

Stable IDs make duplicate work safe for correctness, but duplicate embedding and writes
still cost resources. Multiple workers therefore require atomic claim/lease/reclaim,
retry classification, jitter, and poison-event quarantine before the outbox is treated
as a workstation-scale queue.

## MCP operations

The STDIO MCP server exposes:

- `library_context_prepare`
- `library_context_commit`
- `library_context_protect`
- `library_context_release`
- `library_context_status`
- `library_context_flush`

These tools make the lifecycle available to an external gateway. They do not grant an
MCP server control over a host's internal context manager. A normal MCP agent can use
the Library and desk tools cooperatively, but calling `prepare` inside an already active
model turn cannot bound that same turn. Automatic enforcement requires a client that
owns the next model call and sends only the returned messages.

## HTTP operations

Equivalent loopback endpoints use `/context/prepare`, `/context/commit`,
`/context/protect`, `/context/release`, `/context/flush`, and
`/context/status/{session}`.

The local development server has no authentication. Do not bind or proxy it to a shared
network without adding authentication, authorization, request limits, TLS, and privacy
policy enforcement.

## Integration checklist

A correct gateway must:

- use a stable namespace and session ID;
- call `prepare()` before each model request;
- send only the returned messages;
- call `commit()` for assistant and consequential tool results;
- use stable event IDs when calls may be retried;
- close or reuse governors rather than creating one per message;
- expose watermark lag and worker errors;
- retain native provider compaction only as an emergency fallback;
- test crash recovery between append, model call, commit, and index completion.

The rationale, costs, and adoption triggers for these future policies are documented in
[Why These Improvements?](WHY_THE_ROADMAP.md).
