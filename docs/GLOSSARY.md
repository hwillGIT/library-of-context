# Glossary

This glossary gives one meaning to each technical term in the Library documentation.
The first use of a term in a document also gives a short explanation when necessary.

## Context and model terms

| Term | Meaning in this project |
|---|---|
| Agent | Software that uses a model, tools, and stored state to do a task. |
| Artificial intelligence (AI) | Software that does tasks that usually need human reasoning or language skills. |
| Compaction | A process that replaces older conversation text with a shorter form. The shorter form can omit details. |
| Context | Information that the model can use for one request. Context can include instructions, messages, and retrieved text. |
| Context window | The maximum number of tokens that a model can accept for one request. |
| Model | Software that reads an input and produces an output. This project usually uses a language model. |
| Prompt | The complete input that an application sends to a model. |
| Token | A small unit of text that a model reads. A token can be a word, part of a word, or punctuation. |
| Token budget | The maximum number of tokens that one part of a prompt can use. |

## Library terms

| Term | Meaning in this project |
|---|---|
| Backing store | Durable storage that keeps data when the process stops. SQLite is the Library backing store. |
| Book | The public name for one context record. A book is not a second database entity. |
| Catalog | The stored set of records and their descriptive data. |
| Collection | A named project boundary in one Library database. The code also uses `namespace` for this value. |
| Context event | One ordered message or item in a governed thread. SQLite keeps the complete event. |
| Context record | One indexed unit that retrieval can find. The public documentation calls this unit a book. |
| Governor | A component that records events and builds a bounded prompt for each model call. |
| Library runtime | The process-owned services for one database. These services include workers, caches, and reading desks. |
| Protected event | An event that remains eligible for prompt assembly until a caller releases it. A token budget still limits the prompt. |
| Reading desk | The bounded set of retrieved books for one thread and subject. The model sees the desk context, not the full catalog. |
| Recent ring | An ordered in-memory list of recent thread events. Count and token limits bound this list. |
| Semantic paging | Selection of protected, recent, and relevant context for one bounded prompt. The complete source stays in durable storage. |
| Session ID | A stable identifier that an application gives to one chat thread. |
| Thread | One ordered chat or agent work stream. |
| Thread key | The pair `ThreadKey(collection, session_id)`. This pair identifies one thread inside one collection. |
| Working set | The context that is ready for immediate use. A reading desk is a retrieval working set. |

## Storage and retrieval terms

| Term | Meaning in this project |
|---|---|
| Approximate nearest-neighbor search (ANN) | A vector search method that reduces work by returning likely matches. It can omit a relevant match. |
| Cache | Disposable storage that gives faster access to data that another source can restore. |
| Chunk | One part of a larger document. The Library indexes chunks as separate records. |
| Embedding | A list of numbers that represents the meaning of text for vector search. |
| Embedder | A component that changes text into an embedding. |
| Full-text search (FTS) | Search that finds records from the words in their text. SQLite FTS5 supplies this function. |
| Hybrid retrieval | Search that combines full-text scores and vector scores. |
| Index | Data that helps a search find records without reading all source text in sequence. |
| Least-frequently-used policy (LFU) | A cache rule that removes items with the lowest use count. |
| Least-recently-used policy (LRU) | A cache rule that removes items that have not been used for the longest time. |
| Metadata | Structured descriptive data about an event or record. Metadata is not the record text. |
| Redis | An optional in-memory data service. The Library uses Redis only as a disposable local cache. |
| Retrieval | The process that finds stored records for a subject or query. |
| Retrieval-augmented generation (RAG) | A design that gives retrieved source text to a model before the model produces an answer. |
| SQLite | The embedded database that stores the authoritative Library data in one file. |
| Vector search | Search that compares embeddings to find text with a related meaning. |
| Write-ahead log (WAL) | A SQLite file that records database changes before SQLite adds them to the main database file. |

## Runtime and recovery terms

| Term | Meaning in this project |
|---|---|
| Atomic operation | An operation that completes all of its durable changes or completes none of them. |
| Broker | A service that stores or routes messages between producers and consumers. Redis cache is not the Library broker. |
| Daemon | A long-running local process that owns one Library database and serves local clients. |
| Durable | Able to remain after a process stops or a cache loses its data. |
| Idempotent operation | An operation that has the same durable result when a caller repeats it with the same identity and content. |
| Lease | A time-limited claim that gives one runtime owner the right to process an outbox event. |
| Outbox | A SQLite table of durable indexing work. An event and its outbox item enter SQLite in one atomic operation. |
| Quarantine | A terminal state for work that failed too many times. An operator can inspect and retry the work. |
| Ring buffer | A bounded in-memory queue or ordered list. SQLite remains the recovery source when the work ring is full. |
| Watermark | A sequence number that shows the last event that reached a processing stage. |
| Worker | A runtime task that processes queued work. The Library uses a fixed number of indexing workers. |

## Interface and security terms

| Term | Meaning in this project |
|---|---|
| Application programming interface (API) | A defined set of operations that software can call. |
| Authentication | A check that verifies the identity or credential of a caller. |
| Authorization | A decision that permits or refuses an operation for an identified caller. |
| Command-line interface (CLI) | A program interface that accepts commands in a terminal. |
| Hypertext Transfer Protocol (HTTP) | The request and response protocol that the local daemon uses. |
| Model Context Protocol (MCP) | A protocol that lets an agent host call external tools and read their results. |
| Scope | A record visibility rule. The Library has thread, project, and trusted team scopes. |
| Standard input and output (STDIO) | Process streams that an MCP host uses to communicate with a local tool server. |
| Transport | The method that moves requests and responses between a client and a service. |
| Transport Layer Security (TLS) | A protocol that encrypts and authenticates network connections. The loopback daemon does not supply TLS. |

## Measurement terms

| Term | Meaning in this project |
|---|---|
| Non-functional requirement (NFR) | A measurable quality requirement, such as latency, capacity, or recovery time. |
| Recovery point objective (RPO) | The maximum amount of committed data that a failure is permitted to lose. |
| Recovery time objective (RTO) | The maximum time that recovery is permitted to take. |
| Resident set size (RSS) | The amount of physical memory that an operating system assigns to a process. |
| Service-level objective (SLO) | A measurable target for service behavior during a stated workload and time period. |
