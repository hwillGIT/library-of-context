# Shared-runtime contract evidence

The [contract manifest](shared-runtime-contracts.json) maps each acceptance invariant
in [ADR 0001](../adr/0001-thread-scope-and-shared-runtime.md) to tests and observable
boundaries. The manifest is the stable review index. Test runners and continuous
integration (CI) jobs provide platform results.

The manifest names tests when a public boundary exists. Public boundaries include the
Python application programming interface (API) and storage. They also include Model
Context Protocol (MCP), Hypertext Transfer Protocol (HTTP), and command-line interfaces.

Concurrency entries identify controlled operation order, bounded resources, or injected
faults. Security entries cover denied and allowed access. The
[glossary](../GLOSSARY.md) defines shared terms.

Run the complete contract set with:

```bash
python -m unittest discover -s tests -v
```

The [contributor quality workflow](../DEVELOPMENT_WORKFLOW.md) defines platforms,
migration fixtures, specialist reviews, and release gates. A CI result applies to one
commit. It does not replace the manifest or denied-access tests.

## Scope matrix

The scope contracts apply one selection rule to direct lookup, hybrid search, pinning,
reading desks, MCP, and HTTP.

| Record boundary | Caller route | Expected result |
|---|---|---|
| Thread A | Thread A with thread scope | Allow |
| Thread A | Thread B with thread scope | Deny |
| Thread A | Project-only route | Deny |
| Project | Thread A or Thread B with project scope | Allow |
| Team Red | Route with Team Red selected | Allow |
| Team Red | Route with Team Blue or no team selected | Deny |

Promotion creates a separate destination record. It retains the private source and
records its origin. Team identifiers are trusted routing inputs. They do not prove team
membership.

## Migration matrix

The independent schema-2 fixture contains ordinary records, owned conversation records,
an ambiguous conversation record, thread events, and pending outbox work.

| Transition | Durable change | Acceptance evidence |
|---|---|---|
| 2 → 3 | Record scope, thread owner, and team columns | Classification, row count, stable identifiers, and denied-visibility checks |
| 3 → 4 | Thread-qualified event and outbox identity | Pending work, repeated identifiers across threads, and foreign-key checks |
| 4 → 5 | Outbox owner, expiry, and claim token | Interrupted-stage rollback and resume |
| 5 → 6 | Terminal quarantine fields | Interrupted-stage rollback and resume |

The test copies the source fixture before migration. A source-version reader opens the
copy. It rejects the migrated database. This result proves that rollback uses the
backup instead of an unsupported downgrade.

The test reopens the target to prove idempotence. The target rejects a future schema.

## Concurrency and recovery matrix

| Boundary | Controlled schedule or fault | Required outcome |
|---|---|---|
| Same record ID | Concurrent writes with a barrier | One visibility boundary wins, and all stores agree |
| Same thread | Concurrent handles | Operations serialize in durable sequence order |
| Different threads | Concurrent indexing | Independent progress within the fixed worker pool |
| Outbox lease | Stale and replacement claim tokens | Only the live token completes or fails the event |
| Poison event | Repeated indexing failure | Quarantine is visible, later work proceeds, and retry is explicit |
| Reading desk | Slow older refresh and fast newer refresh | The older completion cannot replace the newer desk |
| Scheduler | Start, stop, replacement, fatal callback, and 10,000-key churn | Bounded state, stale-work rejection, and degraded status |
| Shutdown | Admitted work, blocked workers, and close faults | Admission closes, work drains, and ownership persists through SQLite closure |
| Daemon ownership | Two processes open one database | The second owner fails before SQLite initialization |

Tests use barriers, events, controlled clocks, and claim tokens. Sleep duration does not
define the required order.

## Compatibility matrix

Standard input/output (STDIO) carries direct MCP messages. JavaScript Object Notation
(JSON) defines HTTP request data. Windows Subsystem for Linux (WSL) hosts the optional
Redis service.

| Surface | Contract |
|---|---|
| Python 3.11 and 3.13 | CI runs the complete suite on Windows and Linux |
| Embedded Python | One runtime owns one database and all handles share its workers |
| Direct STDIO MCP | One process owns one database, and stateful calls require a stable session identifier |
| Daemon-backed MCP | Thin bridges own no storage or workers, and they provide a project default |
| Loopback HTTP | Bearer authentication, JSON media type, trusted `Host`, and browser-origin rejection |
| SQLite | Schema 2 migrates to schema 6, and future schemas fail closed |
| Redis | Optional hot cache, authenticated WSL service, and cold restart keys |

The workflow file defines operating-system and Python-version CI jobs. Put each job URL
and commit result in the pull request. These values identify one exact source tree.

## Review boundaries

- Security review covers scope routing, daemon credentials, browser rejection,
  owner-only files, and untrusted retrieved text.
- Storage review covers the staged schema, source fixture, rollback copy, foreign keys,
  record identity, outbox claims, and database ownership.
- Concurrency review covers controlled operation order, bounded resources, scheduler
  replacement, stale publication, shutdown, and terminal recovery.
- API review covers Python, MCP, HTTP, command-line, configuration, error, and
  payload contracts.
- Retrieval review uses exact vector scoring as the reference implementation.
- Retrieval review does not justify an approximate nearest neighbor backend.
- Documentation review requires spelling, editorial-contract, local-link, and strict
  MkDocs gates.

Resource tests establish worker, queue, registry, desk-count, token, and time-to-live
bounds. They do not establish a byte limit for arbitrary metadata or embeddings.

[Performance and Scaling](../PERFORMANCE_AND_SCALING.md) defines required latency,
resident-memory, and retrieval-quality evidence.
