# Performance and Scaling

Unless identified as observed measurements, the figures below are proposed targets.
Record hardware, Python version, embedding model, cardinality, query selectivity,
concurrency, and cache state with every result.

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

A post-fix diagnostic spot check on 2026-08-20 used disposable databases, Python 3.13,
the 384-dimensional hashing embedder, Redis disabled, and one thread. Representative
cold common-term queries were about 27 ms at 1,000 records, 93 ms at 3,000, and 330 ms
at 10,000. These measurements are consistent with removal of the pathological lexical
join and show the exact vector scan as the visible growth path. They are not a versioned
benchmark result or a performance guarantee for other hardware.

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
| Cached reading-desk snapshot read | ≤ 20 ms, with maximum snapshot age reported |
| Warm governed prompt build | p95 ≤ 250 ms |
| Novel-focus prompt build at 100k chunks | p95 ≤ 500 ms |
| Cold hybrid retrieval at 100k chunks | p95 ≤ 250 ms; p99 ≤ 750 ms |
| Focus event to current desk | p95 ≤ 1 s; p99 ≤ 3 s |
| Ring occupancy | below 80% for 99% of minutes |
| Continuous ring saturation | below the declared overload window; spill and rejection counts reported |
| Connected outbox age | p99 ≤ 5 s |
| Recovery drain | at least 2× the declared peak event-production rate |
| Prompt budget violations | zero |
| ACL leakage in team mode | zero |
| ANN recall@12 versus exact reference | ≥ 0.90 |

Every latency target needs an accompanying corpus size, concurrency, hardware profile,
and retrieval-quality score.

Zero acknowledged-event loss and zero authorization leakage are invariants tested with
fault and adversarial cases, not percentile SLOs. Ring occupancy alone is insufficient:
report maximum continuous saturation, oldest work age, spill/retry/rejection counts, and
drain rate.

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

Do not run the full Cartesian matrix on every pull request. Use three tiers: a small
deterministic PR smoke suite, scheduled or nightly scale and fault suites, and a release
suite that covers the declared workload profiles. Raw results and workload definitions
must be versioned so comparisons remain meaningful.

## High-value implementation work

### Candidate generation and ANN

Introduce a vector-index interface and compare local adapters such as sqlite-vector
extensions, HNSW libraries, and embedded vector stores. Hybrid retrieval should union a
bounded vector candidate set and bounded FTS candidate set, apply authorization before
hydration, and exact-rerank only tens or hundreds of candidates.

**Why:** the exact vector path grows in latency and transient memory with the namespace.
**Why not automatically:** ANN loses some recall, adds native packaging and index
recovery, and can be slower than exact scoring for a small catalog. **Adopt when:** the
exact reference crosses the declared cold-query or memory SLO on target hardware, then
require quality, rebuild, deletion, filter, and cross-platform evidence.

Bounded FTS output does not mean the FTS engine performs constant internal work; broad
posting lists and FTS update/delete maintenance remain selectivity- and schema-dependent.

Questions:

- Which adapter has the best operational fit for a zero-service local install?
- What recall is lost at practical HNSW memory settings?
- Can one index safely support private thread, personal, project, and team scopes?
- How should embedding-model migrations coexist with old index versions?

### Token accounting and packing

The current four-characters-per-token estimate is dependency-free but not model exact.
Add tokenizer adapters, hard byte/token ceilings, and coverage for CJK, code, very long
unbroken input, tool payloads, and mixed modalities.

**Why:** a real model-token overrun violates the bounded-context contract. **Why not one
mandatory tokenizer:** providers and model revisions differ, and a provider dependency
would weaken the zero-dependency local core. **Adopt when:** a production integration
selects a model or adversarial tests expose estimator drift; preserve a conservative
fallback and version derived counts.

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

**Why:** shared ownership bounds threads, duplicate work, connections, and aggregate
cache memory. **Why not automatically:** a daemon adds IPC, supervision, upgrade, local
security, and a workstation failure boundary; batching adds queue delay and partial-
failure policy. **Adopt when:** measured duplicate work, contention, queue age, or memory
breaks the declared multi-agent profile. A solo large catalog may need ANN without a
daemon; many agents over a small catalog may need the daemon without ANN.

Questions:

- Which events deserve a high-priority lane?
- How should focus-change events be coalesced and stale completions discarded?
- What backpressure response should interactive and bulk callers receive?

### Cache correctness

Cache identities must include ranking weights, recency policy, embedding provider/model
and revision, tokenizer/ranker version, authorization fingerprint, and index snapshot.
Namespace-wide generation invalidation should evolve into thread/project snapshot
epochs. Cache candidate IDs and scores rather than duplicating full vectors and text.

**Why:** an incomplete identity can return wrongly ranked, stale, or unauthorized data.
**Why not include every runtime value:** unnecessary dimensions fragment the cache,
increase storage, and reduce reuse. **Adopt when:** correctness dimensions become
configurable; canonicalize only inputs that change ranking, visibility, or permission,
and measure hit rate and key growth after the change.

For the full dialectic, alternatives, and adoption triggers, see
[Why These Improvements?](WHY_THE_ROADMAP.md).

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
