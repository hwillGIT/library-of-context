# Performance and Scaling

This document separates measured behavior, current limitations, proposed targets, and
open benchmark questions. Performance claims should include hardware, Python version,
embedding model, cardinality, query selectivity, concurrency, and cache state.

## Baseline audit

The first prototype used an FTS query that joined every FTS match to a computed record
key. On one Windows/Python 3.13 workstation with the dependency-free 384-dimensional
hashing embedder and Redis disabled, representative cold retrievals were:

| Records | Query shape | Observed latency |
|---:|---|---:|
| 1,000 | broad/common lexical terms | about 1.35 seconds |
| 2,500 | broad/common lexical terms | about 8.6 seconds |
| 2,500 | no lexical match | about 91 milliseconds |
| 2,500 | about 20% lexical match | about 1.76 seconds |

`EXPLAIN QUERY PLAN` showed scans of both the FTS and record tables plus a temporary
B-tree. The current implementation removes that join and returns bounded FTS record IDs
directly. These baseline numbers must not be presented as current post-fix results; a
repeatable benchmark harness is still needed.

## Current bottleneck

Cold vector retrieval still:

1. loads every live record in a namespace;
2. deserializes every vector into Python floats;
3. exact-scores every vector;
4. sorts all eligible hits before taking `top_k`.

The work and transient memory therefore grow with namespace cardinality. RAM and Redis
help direct book reads and exact repeated queries, but do not accelerate a novel cold
catalog query.

The context governor fixes a different latency problem: durable append can be
acknowledged before embedding/index completion, and the recent overlay prevents fresh
events from disappearing. It does not make the current vector scan sublinear.

## Provisional performance gates

These are design targets for contributors to challenge and refine, not current claims.

| Capability | Proposed target |
|---|---|
| Local durable append | p95 ≤ 20 ms; p99 ≤ 50 ms |
| Acknowledged event loss after process crash | zero |
| Index visibility | p95 ≤ 2 s; p99 ≤ 10 s under declared load |
| Stale reading-desk return | ≤ 20 ms |
| Warm governed prompt build | p95 ≤ 250 ms |
| Novel-focus prompt build at 100k chunks | p95 ≤ 500 ms |
| Cold hybrid retrieval at 100k chunks | p95 ≤ 250 ms; p99 ≤ 750 ms |
| Focus event to current desk | p95 ≤ 1 s; p99 ≤ 3 s |
| Ring occupancy | below 80% for 99% of minutes |
| Connected outbox age | p99 ≤ 5 s |
| Prompt budget violations | zero |
| ACL leakage in team mode | zero |
| ANN recall@12 versus exact reference | ≥ 0.90 |

Every latency target needs an accompanying corpus size, concurrency, hardware profile,
and retrieval-quality score.

## Required benchmark matrix

- Records: `10²`, `10³`, `10⁴`, `10⁵`, and `10⁶`.
- Embedding dimensions: 384, 768, and 1,536.
- FTS selectivity: 0%, 1%, 20%, and 80%.
- Metadata/ACL filter selectivity.
- Thread length: `10²` through `10⁵` events.
- Active sessions: 1, 4, 16, and 64.
- Cache states: warm, cold, invalidated, Redis disabled, Redis unavailable.
- Focus churn and repeated versus novel queries.
- Embedding states: fast, slow, unavailable, malformed response.
- Lifecycle faults: process stop, WAL recovery, disk full, outbox replay, stale worker.
- Team faults: offline node, delayed relay, duplicate delivery, permission revocation.

Report p50/p95/p99 latency, throughput, peak RSS, database bytes read/written, Redis
round trips and bytes, queue age, cache hit rate, recall, NDCG, and answer grounding.

## High-value implementation work

### Candidate generation and ANN

Introduce a vector-index interface and compare local adapters such as sqlite-vector
extensions, HNSW libraries, and embedded vector stores. Hybrid retrieval should union a
bounded vector candidate set and bounded FTS candidate set, apply authorization before
hydration, and exact-rerank only tens or hundreds of candidates.

Questions:

- Which adapter has the best operational fit for a zero-service local install?
- What recall is lost at practical HNSW memory settings?
- Can one index safely support private thread, personal, project, and team scopes?
- How should embedding-model migrations coexist with old index versions?

### Token accounting and packing

The current four-characters-per-token estimate is dependency-free but not model exact.
Add tokenizer adapters, hard byte/token ceilings, and coverage for CJK, code, very long
unbroken input, tool payloads, and mixed modalities.

Questions:

- Should the durable event store persist token counts per model/tokenizer version?
- How should the governor divide a changing budget among protected, recent, and
  retrieved context?
- When is truncation preferable to a derived summary?

### Worker topology

Move from one indexing thread per governor toward one supervised workstation daemon,
one scheduler, and fixed worker pools. Partition by `(project_id, thread_id)` to preserve
thread order while allowing independent threads to run concurrently. Batch embeddings
by total tokens and pool local HTTP connections.

Questions:

- Which events deserve a high-priority lane?
- How should focus-change events be coalesced and stale completions discarded?
- What backpressure response should interactive and bulk callers receive?

### Cache correctness

Cache identities must include ranking weights, recency policy, embedding provider/model
and revision, tokenizer/ranker version, authorization fingerprint, and index snapshot.
Namespace-wide generation invalidation should evolve into thread/project snapshot
epochs. Cache candidate IDs and scores rather than duplicating full vectors and text.

## Reproducibility contribution format

A performance pull request should include:

1. the benchmark code and synthetic-data generator;
2. exact environment and dependency versions;
3. raw machine-readable results;
4. plots or compact tables;
5. retrieval-quality deltas, not latency alone;
6. failure and recovery behavior;
7. a comparison against the existing exact scorer.

Optimization without a quality baseline is not sufficient for retrieval changes.
