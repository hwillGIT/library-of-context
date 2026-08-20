# Performance and Scaling

Unless identified as observed measurements, the figures below are proposed targets.
Record hardware, Python version, embedding model, cardinality, query selectivity,
concurrency, and cache state with every result.

## Diagnostic measurements

### Computed-key lexical-join query

An FTS query that joins every match to a computed record key produced the following
results on one Windows/Python
3.13 workstation with the dependency-free 384-dimensional hashing embedder and Redis
disabled:

| Records | Query shape | Observed latency |
|---:|---|---:|
| 1,000 | broad/common lexical terms | about 1.35 seconds |
| 2,500 | broad/common lexical terms | about 8.6 seconds |
| 2,500 | no lexical match | about 91 milliseconds |
| 2,500 | about 20% lexical match | about 1.76 seconds |

`EXPLAIN QUERY PLAN` showed scans of both the FTS and record tables plus a temporary
B-tree. These measurements characterize the computed-key lexical-join query and do not
represent retrieval with bounded FTS record IDs.

### Bounded-candidate query

A diagnostic spot check on 2026-08-20 used disposable databases, Python 3.13,
the 384-dimensional hashing embedder, Redis disabled, and one thread. Representative
cold common-term queries were about 27 ms at 1,000 records, 93 ms at 3,000, and 330 ms
at 10,000. The bounded query returns FTS record IDs directly without the temporary
record-table join. These measurements expose the exact vector scan as the visible growth
path. They are diagnostic results, not a versioned benchmark or a performance guarantee
for other hardware.

## Exact-vector bottleneck

Cold vector retrieval:

1. loads every live record in a namespace;
2. deserializes every vector into Python floats;
3. exact-scores every vector;
4. sorts all eligible hits before taking `top_k`.

The work and transient memory therefore grow with namespace cardinality. RAM and Redis
help direct book reads and exact repeated queries, but do not accelerate a novel cold
catalog query.

The context governor fixes a different latency problem: durable append can be
acknowledged before embedding/index completion, and the recent overlay prevents fresh
events from disappearing. It does not make the exact vector scan sublinear.

## Provisional performance gates

These values are proposed acceptance targets, not observed results or performance
guarantees.

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

Benchmark execution uses three tiers: a small deterministic smoke suite for routine
validation, scheduled or nightly scale and fault suites, and a release suite that covers
the declared workload profiles. Raw results and workload definitions must be versioned
so comparisons are meaningful.

## Conditional scaling mechanisms

### Candidate generation and ANN

A vector-index interface can support local adapters such as sqlite-vector extensions,
HNSW libraries, and embedded vector stores. Scalable hybrid retrieval unions a bounded
vector candidate set with a bounded FTS candidate set, applies authorization before
hydration, and exact-reranks only tens or hundreds of candidates.

**Constraint:** exact vector scoring grows in latency and transient memory with namespace
size. **Trade-off:** ANN loses some recall, adds native packaging and index recovery, and
can be slower than exact scoring for a small catalog. **Adoption criterion:** the exact
reference crosses the declared cold-query or memory SLO on target hardware, with quality,
rebuild, deletion, filter, and cross-platform evidence available.

Bounded FTS output does not mean the FTS engine performs constant internal work; broad
posting lists and FTS update/delete maintenance are selectivity- and schema-dependent.

Questions:

- Which adapter has the best operational fit for a zero-service local install?
- What recall is lost at practical HNSW memory settings?
- Can one index safely support private thread, personal, project, and team scopes?
- How should embedding-model migrations coexist with old index versions?

### Token accounting and packing

The dependency-free default estimates one token per four characters but is not model
exact. Model-specific integrations require tokenizer adapters, hard byte and token
ceilings, and coverage for CJK, code, very long unbroken input, tool payloads, and mixed
modalities.

**Constraint:** a model-token overrun violates the bounded-context contract.
**Trade-off:** providers and model revisions use different tokenizers, and a provider
dependency would weaken the dependency-free local core. **Adoption criterion:** a
model-specific integration selects its tokenizer or adversarial tests expose estimator
drift; a conservative fallback is available and derived counts are versioned.

Questions:

- Should the durable event store persist token counts per model/tokenizer version?
- How should the governor divide a changing budget among protected, recent, and
  retrieved context?
- When is truncation preferable to a derived summary?

### Worker topology

For workstation-scale workloads, one supervised daemon, one scheduler, and fixed worker
pools replace per-governor indexing threads. Partitioning by `(project_id, thread_id)`
preserves thread order while allowing independent threads to run concurrently.
Embedding batches use total-token limits, and local HTTP connections are pooled.

**Constraint:** shared ownership bounds threads, duplicate work, connections, and
aggregate cache memory. **Trade-off:** a daemon adds IPC, supervision, upgrade, local
security, and a workstation failure boundary; batching adds queue delay and partial-
failure policy. **Adoption criterion:** measured duplicate work, contention, queue age,
or memory breaks the declared multi-agent profile. A solo large catalog may need ANN
without a daemon; many agents over a small catalog may need the daemon without ANN.

Questions:

- Which events deserve a high-priority lane?
- How should focus-change events be coalesced and stale completions discarded?
- What backpressure response should interactive and bulk callers receive?

### Cache correctness

Cache identities must include ranking weights, recency policy, embedding provider/model
and revision, tokenizer/ranker version, authorization fingerprint, and index snapshot.
Thread and project snapshot epochs provide finer invalidation than one namespace-wide
generation. Cache candidate IDs and scores rather than duplicating full vectors and
text.

**Constraint:** an incomplete identity can return wrongly ranked, stale, or unauthorized
data. **Trade-off:** unnecessary identity dimensions fragment the cache, increase
storage, and reduce reuse. **Adoption criterion:** canonicalize inputs that affect
ranking, visibility, or permission, and measure hit rate and key growth when those
dimensions become configurable.

See [Why These Improvements?](WHY_THE_ROADMAP.md) for alternatives, costs, and adoption
criteria.

## Benchmark evidence requirements

A reproducible performance claim includes:

1. the benchmark code and synthetic-data generator;
2. exact environment and dependency versions;
3. raw machine-readable results;
4. plots or compact tables;
5. retrieval-quality deltas, not latency alone;
6. failure and recovery behavior;
7. a comparison against the exact reference scorer.

A retrieval optimization requires a quality baseline in addition to latency
measurements.
