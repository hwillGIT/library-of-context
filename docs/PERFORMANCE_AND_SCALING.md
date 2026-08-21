# Performance and Scaling

The [Glossary](GLOSSARY.md) defines shared technical terms. This page separates observed measurements from proposed targets.

An embedding is a list of numbers that represents text meaning. Redis is an optional in-memory key-value cache.

Hybrid retrieval combines full-text search with vector search. An outbox is a durable table of indexing work.

SQLite is the embedded database that stores authoritative Library data in one file.

Every result must identify its test environment. Record the hardware, Python version, embedding model, record count, query selectivity, concurrency, and cache state.

Record count is the number of searchable records. Query selectivity is the percentage of records that satisfy a filter or search term.

## Diagnostic measurements

### Computed-key lexical-join query

Full-text search (FTS) finds records that contain matching terms. One query joined every FTS match to a calculated record key.

Tests used one Windows workstation with Python 3.13. The test configuration disabled Redis. A dependency-free hashing embedder produced 384-value vectors.

A hashing embedder converts text into a fixed numeric vector without a learned model.

| Records | Query shape | Observed latency |
|---:|---|---:|
| 1,000 | Broad, common lexical terms | About 1.35 seconds |
| 2,500 | Broad, common lexical terms | About 8.6 seconds |
| 2,500 | No lexical match | About 91 milliseconds |
| 2,500 | About 20% lexical match | About 1.76 seconds |

`EXPLAIN QUERY PLAN` reported scans of both search tables. It also reported a temporary balanced-tree structure for sorting or lookup.

These measurements apply only to the computed-key join. They do not describe retrieval that returns bounded FTS record identifiers.

### Bounded-candidate query

A diagnostic test ran on 20 August 2026. It used disposable databases, Python 3.13, one thread, the 384-value hashing embedder, and no Redis.

Representative cold queries used common terms. A cold query starts without reusable cache data.

Observed latency was about 27 milliseconds for 1,000 records. It was 93 milliseconds for 3,000 records and 330 milliseconds for 10,000 records.

The bounded query returns FTS record identifiers directly. It does not create the temporary join with the record table.

These results show the cost of exact vector scanning. They are diagnostic observations, not a performance guarantee for other hardware.

## Exact-vector bottleneck

Cold vector retrieval performs these operations:

1. It loads every live record in a namespace.
2. It converts every stored vector into Python floating-point values.
3. It calculates an exact score for every vector.
4. It sorts all eligible results before it selects `top_k` results.

Work and temporary memory grow with the number of records. Memory and Redis caches help direct reads and repeated identical queries.

Those caches do not accelerate a new cold query across the catalog.

The context governor addresses a different delay. It can acknowledge durable storage before embedding and indexing finish.

The recent ring keeps new events visible during that delay. The governor does not make exact vector search use sublinear work.

Sublinear work grows more slowly than the number of searchable records.

## Provisional performance gates

This section proposes acceptance targets. The values are not observed results or guarantees.

Percentile notation describes a latency boundary. For example, p95 means that 95 percent of measured operations finish within the listed time.

| Capability | Proposed target |
|---|---|
| Local durable append | p95 at or below 20 ms, with p99 at or below 50 ms |
| Acknowledged event loss after process failure | Zero |
| Index visibility | p95 at or below 2 s, with p99 at or below 10 s under the declared load |
| Cached reading-desk snapshot read | At or below 20 ms, with maximum snapshot age reported |
| Warm governed prompt build | p95 at or below 250 ms |
| New-focus prompt build with 100,000 chunks | p95 at or below 500 ms |
| Cold hybrid retrieval with 100,000 chunks | p95 at or below 250 ms, with p99 at or below 750 ms |
| Focus event to active desk | p95 at or below 1 s, with p99 at or below 3 s |
| Ring occupancy | Below 80 percent for 99 percent of minutes |
| Continuous ring saturation | Below the declared overload period, with spill and rejection counts reported |
| Oldest connected outbox item | p99 at or below 5 s |
| Recovery drain | At least twice the declared peak event-production rate |
| Prompt budget violations | Zero |
| Authorization leakage in team mode | Zero |
| Approximate-index recall at 12 results | At least 0.90 against exact retrieval |

A warm operation can reuse cache data. A focus event changes the topic that the reading desk should support.

Recall measures how many relevant exact-search results the approximate index returns. Recall at 12 evaluates the first 12 results.

Every latency target needs a corpus size, concurrency level, hardware profile, and retrieval-quality score.

Tests must treat zero acknowledged-event loss as an invariant. Tests must treat zero authorization leakage as an invariant.

Ring occupancy alone does not describe overload. Report the longest saturation period, oldest work age, spill count, retry count, rejection count, and drain rate.

## Required benchmark matrix

- Records: `10²`, `10³`, `10⁴`, `10⁵`, and `10⁶`
- Vector dimensions: 384, 768, and 1,536 values
- FTS selectivity: 0, 1, 20, and 80 percent
- Metadata and authorization-filter selectivity
- Thread length: `10²` through `10⁵` events
- Active sessions: 1, 4, 16, and 64
- Cache states: warm, cold, invalidated, Redis disabled, and Redis unavailable
- Focus changes and repeated or new queries
- Embedding states: fast, slow, unavailable, and malformed response
- Lifecycle failures: process stop, write-ahead-log recovery, full disk, outbox replay, and obsolete worker
- Team failures: offline node, delayed relay, duplicate delivery, and permission revocation

Report median, p95, and p99 latency. Also report throughput, peak resident set size, storage input and output, queue age, and cache hit rate.

Resident set size is the physical memory that the operating system assigns to a process. Throughput is the number of completed operations per time unit.

Report Redis request counts and bytes. Report recall, normalized discounted cumulative gain, and answer grounding.

Normalized discounted cumulative gain measures ranking quality. It gives more credit when relevant results appear near the top.

Benchmark execution has three tiers:

1. A small deterministic smoke suite supports routine validation.
2. Scheduled scale and failure suites test larger workloads.
3. A release suite covers every declared workload profile.

Store raw results and workload definitions with version identifiers. These records make comparisons reproducible.

## Conditional scaling mechanisms

### Candidate generation and approximate vector search

An approximate nearest-neighbor (ANN) index searches likely vector matches without scoring every vector. A vector-index interface can support several local implementations.

Possible implementations include SQLite vector extensions, hierarchical navigable small world libraries, and embedded vector stores.

Hierarchical navigable small world is a graph algorithm for approximate vector search.

A bounded hybrid search combines vector candidates with FTS candidates. It applies authorization before loading text. It calculates exact scores for only the combined candidates.

**Constraint:** Exact vector search needs more time and temporary memory as the namespace grows.

**Trade-off:** ANN can miss relevant records. It also adds native software packages, index recovery, and configuration.

ANN can cost more than exact search for a small catalog.

**Adoption criterion:** Adopt ANN when exact search exceeds the declared cold-query or memory target on target hardware.

Tests must cover quality, rebuild, deletion, filters, and supported operating systems.

Bounded FTS output does not make internal FTS work constant. Broad posting lists can still require significant work.

A posting list identifies records that contain one indexed term. FTS maintenance cost also depends on the schema and update pattern.

Questions:

- Which adapter has the lowest installation and operating cost for a local system?
- How much recall does each practical graph-memory setting lose?
- Can one index safely separate thread, personal, project, and team records?
- How should old and new embedding-model versions share a migration period?

### Token accounting and packing

The dependency-free estimator assumes one token for every four characters. It does not match every model tokenizer.

A tokenizer converts input text into the token units that a model processes. Model integrations need model-specific tokenizer adapters.

They also need byte and token limits. Tests must cover Chinese, Japanese, and Korean text, source code, long unbroken input, tool data, and mixed media.

**Constraint:** A token overrun violates the bounded-context contract.

**Trade-off:** Providers and model revisions use different tokenizers. A provider dependency also weakens the dependency-free local core.

**Adoption criterion:** Select a model-specific tokenizer when an integration names a model. Also select one when adversarial tests show unacceptable estimator error.

The system must retain a conservative fallback. It must record the version that produced each derived token count.

Questions:

- Should each durable event store token counts for every tokenizer version?
- How should the governor divide a changing budget among protected, recent, and retrieved context?
- When should the governor use truncation instead of a derived summary?

### Worker topology

One Library runtime owns a fixed worker pool, one desk scheduler, bounded thread state, and bounded desk snapshots.

Atomic claims preserve order within `(collection, session_id)`. Independent threads can continue concurrently.

Thin Model Context Protocol bridges can share one runtime through the loopback daemon. Model Context Protocol defines an interface for agent tools and resources.

The Hypertext Transfer Protocol server limits accepted requests. Hypertext Transfer Protocol carries requests between local clients and the daemon.

Weighted fairness, operating-system supervision, disk policy, embedding batches, and connection pools need separate implementation and evidence.

Weighted fairness gives selected work classes a declared share of service. A connection pool reuses a bounded set of open connections.

Registry counts and prompt-token limits do not impose a strict memory limit. Event metadata and selected desk results retain complete Python values.

Capacity tests must report peak resident set size and payload distributions. Entry counts alone are insufficient.

**Constraint:** Shared ownership limits duplicate workers, connections, and cache memory.

**Trade-off:** A daemon adds communication cost, supervision, upgrades, local security, and one workstation failure point.

Batching can improve throughput but adds queue delay. It also needs rules for partial failures.

**Adoption criterion:** Add a daemon when measured duplicate work, contention, queue age, or memory exceeds the declared multi-agent profile.

A single large catalog can need ANN without a daemon. Several agents over a small catalog can need a daemon without ANN.

Questions:

- Which events require a high-priority work lane?
- How should the scheduler combine repeated focus changes and discard obsolete results?
- Which overload response should interactive and bulk callers receive?

### Cache correctness

A cache identity contains every input that can change the cached result. It must include ranking weights, recency policy, and embedding version.

It must also include tokenizer version, ranker version, authorization fingerprint, and index snapshot.

An authorization fingerprint is a stable value that represents the caller's effective permissions. A snapshot identifies one index state.

Thread and project snapshot counters permit narrow invalidation. Invalidation marks cached data as unusable after its source changes.

Cache candidate identifiers and scores. Do not duplicate complete vectors and record text in query-cache entries.

**Constraint:** An incomplete cache identity can return obsolete, incorrectly ranked, or unauthorized data.

**Trade-off:** Unnecessary identity fields create more cache entries and reduce reuse.

**Adoption criterion:** Normalize every input that affects ranking, visibility, or permission. Measure cache hit rate and key growth when configuration changes.

See [Why These Improvements?](WHY_THE_ROADMAP.md) for options, costs, and adoption criteria.

## Benchmark evidence requirements

A reproducible performance claim includes:

1. Benchmark code and a synthetic-data generator.
2. Exact environment and dependency versions.
3. Raw machine-readable results.
4. Plots or compact tables.
5. Retrieval-quality changes in addition to latency.
6. Failure and recovery behavior.
7. Comparison with the exact reference scorer.

Every retrieval optimization needs a quality baseline and latency measurements.
