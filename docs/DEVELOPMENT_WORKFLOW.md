# Contributor QA workflow

This quality assurance (QA) workflow covers changes to stateful behavior. It applies to
identity, scope, promotion, runtime ownership, schema, outbox, and public-interface
changes.

The workflow converts [ADR 0001](adr/0001-thread-scope-and-shared-runtime.md) contracts
into tests and evidence. ADR means architecture decision record. The
[glossary](GLOSSARY.md) defines other shared terms.

Model Context Protocol (MCP), Hypertext Transfer Protocol (HTTP), and command-line
interface (CLI) are public boundaries. An application programming interface (API) is a
defined software interface.

## Change packet

Identify these items before implementation:

- affected ADR acceptance invariants.
- public Python, MCP, HTTP, CLI, and storage contracts.
- failure, privacy, compatibility, and rollback boundaries.
- resource limits and overload behavior.
- an evidence artifact for each claim.

Use the [decision brief template](DECISION_BRIEF_TEMPLATE.md) for a high-impact change.
Such changes affect schema, ordering, visibility, promotion, or public-interface
meaning.

## Contract-first tests

Write an observable contract test before implementation. Use a public boundary when
one exists. Test missing or incorrect behavior. Do not test an internal class
arrangement as a public contract.

Cover each affected layer:

| Contract | Required checks |
|---|---|
| Thread identity | Required session identifiers, invalid values, collection isolation, idempotency, and identifier reuse across threads |
| Scope routing | Positive and negative retrieval for thread, project, and team records through direct lookup, search, pinned books, desks, MCP, and HTTP |
| Promotion | Source authorization, target validation, distinct destination identity, retained source, provenance metadata, and destination revocation |
| Shared runtime | One runtime per owner, fixed workers, serialized thread work, cross-thread progress, and close ownership |
| Durability | Atomic event and outbox append, restart recovery, read-your-own-context behavior, watermarks, and idempotent replay |
| Outbox failure | Exclusive claims, lease expiration, reclaim, progress after terminal failure, visible quarantine, and explicit retry |
| Runtime boundary | Pre-open ownership, loopback transport, bearer token, browser, `Host`, media-type rejection, bounded admission, and resource-free bridges |
| Compatibility | Public payload fields, error classes or status codes, configuration defaults, and supported schema transitions |

Tests must prove denied and allowed behavior. A scope test proves that an owner can
retrieve its thread record. It proves that another thread cannot retrieve or pin that
record.

The test also proves project visibility. It returns no team record for an unauthorized
team identifier.

Implementation unit tests can supplement these contracts. They do not replace protocol
and storage boundary tests.

## Migration fixtures and rollback checks

Create an independent fixture for each supported schema transition. Build it from the
source data definition language or released executable.

Include representative manual and conversation records. Include thread heads and
pending outbox rows. Include ambiguous legacy ownership. Include repeated event
identifiers across eligible threads when the source schema permits them.

If the source schema cannot store repeated cross-thread identifiers, test this behavior
immediately after migration.

Record baseline row counts and stable identifiers. Open the fixture with the target
code. Verify:

1. the declared target schema version.
2. preserved event order, content, record identifiers, and outbox work.
3. project classification for ordinary records.
4. thread classification and owner recovery for conversation records.
5. non-project visibility for ambiguous private records.
6. rebuilt full-text search results under the same scope rules.
7. an empty `PRAGMA foreign_key_check` result.
8. identical results after a second open.
9. rejection of a database with a future schema version.

Use a source-fixture copy for rollback verification. Prove that the source executable
opens the pre-migration backup. Do not give the migrated database to an unsupported
executable.

Cache and full-text search cleanup may discard rebuildable state. It must not delete
events, records, or pending outbox work.

Use synthetic fixture data only. Do not include prompts, credentials, customer content,
or copied production databases.

## Deterministic concurrency tests

Control concurrent order with barriers, events, test clocks, claim tokens, and bounded
executors. Do not use sleep duration as an ordering assertion. Poll with a deadline
only for a public completion contract.

Use fixed seeds for randomized schedules. Record the seed when a test fails. Control
clocks and jitter when timing affects ownership, eviction, or retry.

Set a bounded join timeout for each worker and thread. A deadlock must fail the suite
instead of stopping it indefinitely.

The concurrency matrix includes:

- two handles that use the same `ThreadKey`.
- independent progress for different `ThreadKey` values.
- identical `event_id` values in different threads.
- exclusive outbox claims and ordered claims in each thread.
- lease loss, expiration, release, and reclaim.
- thread-state eviction with active leases and idle entries.
- saturation when all thread-state slots have leases.
- scheduler replacement, cancellation, and closure during an active refresh.
- many governor and watch handles with a fixed worker count.
- terminal failure followed by later work in the same thread and explicit retry of the
  quarantined event.
- two daemon starts for one database, with rejection before the second SQLite
  constructor runs.
- several thin Model Context Protocol bridges sharing one runtime identifier without
  local runtime resources.
- missing and incorrect daemon credentials, browser-origin writes, hostile `Host`
  values, and non-JSON request bodies.
- owner shutdown while handles still exist.

Tests must assert resource counts and registry sizes. Returned data alone is not
sufficient. Valid evidence includes thread names, runtime identities, active entries,
scheduled tasks, and configured capacities.

Each resource assertion must correspond to a public limit.

## Deterministic fault tests

Place fault injection at durable boundaries:

- before and after event insertion.
- before and after outbox insertion.
- after an outbox claim but before record storage.
- after record storage but before indexed status.
- during lease renewal or stale-claim completion.
- while SQLite is busy or storage is unavailable.
- while Redis is absent, empty, or restarted.
- when stale Redis data remains after a runtime restart.
- while the dispatch queue and thread-state registry are full.
- during process-owner shutdown.
- when one event repeatedly fails until quarantine while a later event remains
  indexable.
- when an operator retries a quarantined event during another active claim.
- before daemon lock acquisition and before SQLite initialization in a competing
  process.

For each fault, assert acknowledged state, authoritative rows, retry eligibility,
watermarks, duplicate behavior, and visible prompt content. Recovery must not drop an
acknowledged event. It must not expose a record outside its scope.

## Specialist reviews

Request each applicable specialist review. Record the reviewer and decision in the pull
request.

| Trigger | Specialist review | Required evidence |
|---|---|---|
| Thread keys, scopes, promotion, or team routing | Security and privacy | Access matrix, negative record-loading tests, provenance and revocation behavior |
| SQLite schema, transactions, outbox, or rollback | Storage and migration | Source fixture, migration report, foreign-key check, backup and rollback procedure |
| Locks, leases, workers, queues, or schedulers | Concurrency and runtime | Controlled interleavings, fault matrix, deadlock timeout, worker and memory bounds |
| Python, MCP, HTTP, CLI, or configuration surface | API compatibility | Baseline and target payloads, errors, and version boundary |
| Ranking, filtering, embedding, or cache identity | Retrieval evaluation | Fixed corpus, quality metrics, latency and resource measurements |
| Explanatory prose or public examples | Technical documentation | Local-link check, strict site build, spelling check, and editorial-style test |
| Pull request summary, review, commit, or Actions text | Technical communication | Risk, fix, state order, term definitions, runtime impact, and evidence review |

One reviewer may cover multiple specialties. Record that reviewer's applicable
experience and evidence. Review approval does not override a failed automated gate.

## Local verification

Run contract tests during development. Run all repository checks before you request
review.

```bash
python -m unittest -v tests.test_identity_and_scope
python -m unittest -v tests.test_schema_migrations
python -m unittest -v tests.test_shared_runtime
python -m compileall -q context_cache library_of_context
python -m unittest discover -s tests -v
ruff check context_cache library_of_context tests examples
ruff format --check context_cache library_of_context tests examples
codespell .
mkdocs build --strict
```

The default suite runs without Redis. Run the optional Redis integration test after a
cache change. Use a dedicated disposable instance. Never use a shared or production
Redis database.

## CI gates

A pull request is eligible for merge only when all applicable gates pass:

- static analysis, formatting, spelling, and editorial checks pass.
- the trusted-base technical-summary job validates the pull request body structure.
- an author and reviewer verify each summary claim against implementation evidence.
- the complete suite passes on Python 3.11 and 3.13 on Windows and Linux.
- quickstart and governed-loop smoke tests pass without optional services.
- the strict MkDocs build resolves each local link and generated interface reference.
- each affected ADR invariant has a named contract or fault test.
- daemon tests prove pre-open exclusivity and resource-free thin bridges when the
  daemon boundary changes.
- outbox tests prove terminal quarantine, later-event progress, and explicit retry when
  retry behavior changes.
- each required specialist review is complete.
- migration, compatibility, security, and performance evidence is attached when
  applicable.

### GitHub repository rule

A GitHub ruleset is a repository setting that controls which changes can enter a
branch. Protect `main` with these requirements:

- require a pull request before merge.
- require approval from a code owner.
- require the `Technical summary structure` status check.
- require the repository test and documentation checks.

`CODEOWNERS` identifies the required reviewers for policy files. GitHub does not enforce
those reviewers until the branch ruleset requires code-owner approval.

A schema release requires a source-version fixture and an idempotent migration test. It
also requires future-schema rejection, foreign-key checks, and an executable rollback
procedure.

A scope release requires a denied-access matrix across all loading paths. A runtime
release requires measured worker, queue, registry, and desk limits under declared load.

Run candidate smoke tests against an empty database. Run them against each supported
migration fixture copy. Do not release with an unresolved durability, privacy,
migration, or data-loss failure.

## Evidence artifacts

Keep evidence reproducible and reviewable. Attach or link each applicable artifact from
the pull request.

The [shared-runtime contract manifest](evidence/shared-runtime-contracts.json) indexes
ADR 0001 evidence. Update the manifest when a change affects an invariant. Update its
tests in the same change.

| Artifact | Minimum contents |
|---|---|
| Contract manifest | ADR invariant, test name, public boundary, and expected failure mode |
| Scope matrix | Caller thread, selected scopes, team IDs, record owner, operation, and allow/deny result |
| Migration report | Source and target schema, fixture digest, row counts, stable IDs, classification results, FTS result, and foreign-key result |
| Concurrency report | Test seed, controlled schedule, fault point, worker counts, registry maxima, timeouts, and outcome |
| Performance report | Commit, command, corpus, hardware, samples, median and 95th-percentile latency, peak memory, cache state, and retrieval quality |
| Compatibility matrix | Python and OS versions, transport payloads, configuration, schema versions, and rollback support |
| CI record | Workflow address, commit hash, job results, and retained logs or machine-readable output |
| Review record | Triggered specialty, reviewer, decision, conditions, and resolved findings |

Provide machine-readable data for results with multiple cases or measurements. Use
JavaScript Object Notation (JSON) or comma-separated values (CSV). Identify the command,
data version, and commit hash.

Remove secrets and private context before publication.

## Completion criterion

Work is complete when implementation, contracts, migration, rollback, documentation,
gates, reviews, and evidence describe the same behavior. An unsupported claim keeps the
change in review. An untested denied-access path also keeps it in review.
