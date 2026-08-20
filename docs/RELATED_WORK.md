# Related Work and Design Landscape

This review maps the Library of Context to research and current implementation work
in long-context models, retrieval, prompt compression, agent memory, provider
compaction, and inference-runtime paging. It was reviewed on **20 August 2026**.

The documents have separate roles:

- [Capability Status](STATUS.md) is the authority for what is implemented.
- [Architecture](architecture.md) defines current invariants and boundaries.
- [Why These Improvements?](WHY_THE_ROADMAP.md) records alternatives, costs,
  adoption triggers, and evidence gates.
- [Roadmap](roadmap.md) orders planned and research work.
- This document supplies comparative evidence. Inclusion is not an endorsement or a
  commitment to adopt a method.

## Method

The review uses primary sources: proceedings papers, author preprints, normative API
documentation, and official project repositories. Mutable documentation is marked
with an access date. Performance numbers are not compared across different datasets,
models, or hardware. A missing feature is not inferred merely because one source does
not mention it.

Each section separates two kinds of statement:

- **Source-backed observation:** what the cited work describes or evaluates.
- **Library interpretation:** what that evidence implies for this project. These
  interpretations are design judgments, not claims made by the cited authors.

Recent preprints are useful design evidence but are not treated as established
results. They are labelled accordingly.

## Terms that should not be conflated

| Term | Meaning in this project |
|---|---|
| Native context window | The tokens that a model can process in one request |
| Addressable context | Information that the application can retrieve and place into a later request |
| Semantic paging | Selection of protected, recent, and relevant records for a bounded request |
| Compaction | Replacement of prior request history with a smaller continuation representation |
| Prompt compression | Removal or rewriting of prompt tokens to reduce request size |
| Checkpoint | Saved application, workflow, or conversation state used for resumption |
| Prompt or prefix cache | Reuse of prior model computation for an identical token prefix |
| KV-cache paging | Movement and allocation of inference-time key/value tensors |

The Library manages **addressable context** at the application boundary. It does not
increase the physical model window, modify attention, or manage inference tensors.

![Context-management research landscape](context-management-landscape.png)

*The same memory vocabulary is used for mechanisms at different system boundaries.*

## Current Library baseline

Version 0.3 implements an authoritative SQLite event log and transactional outbox,
bounded recent and work rings, protected context, exact hybrid retrieval, watermarks,
and a `prepare -> model -> commit` lifecycle for text calls. RAM and optional Redis are
disposable accelerators. Ordinary MCP use is cooperative; automatic prompt governance
requires an application or gateway that owns the model-call boundary.

Model-accurate tokenization, local approximate nearest-neighbor search, a shared
workstation daemon, scope routing, automatic summaries, branch semantics, and team
sync are not current baseline features. Their exact status is maintained in
[Capability Status](STATUS.md).

## Model-internal long-context memory

### Recurrent and compressed model state

**Source-backed observation.** Transformer-XL carries hidden state between segments,
while Compressive Transformer retains compressed older activations.[^transformer-xl]
[^compressive-transformer] Recurrent Memory Transformer carries learned memory tokens
between segments.[^rmt] Infini-attention combines local attention with a compressive
memory inside the attention mechanism.[^infini-attention] These methods change model
architecture, training, or inference behavior.

**Library interpretation.** These methods can increase effective sequence reach for a
controlled model. They do not provide a provider-neutral, inspectable store of agent
events. The Library should remain compatible with them, but its durable records,
provenance, deletion, and retrieval policy belong outside the model.

### Sliding windows and streaming inference

**Source-backed observation.** StreamingLLM retains attention-sink tokens and a recent
sliding window to support stable streaming generation beyond the model's training
length.[^streamingllm]

**Library interpretation.** A recent FIFO ring serves a related boundedness objective
at the application level, but the mechanisms are different. A sliding KV cache does
not recover the semantic content of discarded events. Older information still needs
an external source and a selection policy.

## Retrieval and external memory

### Retrieval-augmented generation

**Source-backed observation.** Retrieval-Augmented Generation combines a parametric
generator with an external dense document index.[^rag] RETRO retrieves neighboring
chunks from a large corpus through a model architecture trained to use them.[^retro]
Self-RAG trains a model to decide when to retrieve and to assess retrieved evidence.[^self-rag]
RAPTOR builds a hierarchy of clustered source text and abstractive summaries for
retrieval at several levels.[^raptor]

**Library interpretation.** These works establish retrieval as a way to keep useful
information outside the active request. Agent threads add requirements that document
retrieval alone does not settle: immediate visibility of new events, ordering,
supersession, protected instructions, idempotent replay, and strict prompt budgets.
Hierarchical summaries may be useful navigation records, but their source events must
remain addressable.

### Long-term model memory

**Source-backed observation.** Memorizing Transformers use approximate nearest-neighbor
lookup over stored key/value pairs from prior batches.[^memorizing-transformers]
LongMem separates a frozen language-model backbone from a trained memory side network
that retrieves older context.[^longmem]

**Library interpretation.** Both works support separating an active model from a
larger memory. Their learned or latent representations are not suitable as the sole
authoritative record for this project. Embeddings and indexes should remain versioned,
rebuildable derivatives of durable text and metadata.

## Agent memory and active context management

### Explicit memory tiers and reflective memory

**Source-backed observation.** Generative Agents retrieve observations using recency,
importance, and relevance, then create higher-level reflections for planning.[^generative-agents]
MemoryBank combines stored conversation, summarization, retrieval, and a heuristic
forgetting and reinforcement policy.[^memorybank] MemGPT presents an operating-system
analogy with tiered memory, interrupts, and model-directed movement between working
and external context.[^memgpt]

**Library interpretation.** MemGPT is the nearest research precedent for the Library's
virtual-memory metaphor. The Library differs in emphasis: an event is durable before
it becomes evictable; the recent and work rings are explicitly bounded; and summaries
are derived records rather than the only surviving continuation state. Reflection and
forgetting policies remain research topics because they can preserve errors or remove
details that later become important.

### Temporal and graph memory

**Source-backed observation.** The Zep paper and its Graphiti implementation represent
episodes, entities, and temporally qualified relationships in an incrementally updated
knowledge graph.[^zep-paper][^graphiti] Mem0 extracts and consolidates selected memories
and supports vector and graph-backed retrieval through its documented memory
pipeline.[^mem0-paper][^mem0-docs]

**Library interpretation.** Temporal graphs may improve retrieval of changing facts,
relationships, and supersession. Extracted facts are still derived state. They should
retain provenance to original records and should not silently replace the thread log.
Graph storage also adds schema, consistency, and operational costs that need measured
benefit before adoption.

### Recent active-context research

Several current proposals treat memory management as an agent action or learned
policy:

- **Sculptor** gives an agent tools for context fragmentation, summarization, hiding,
  restoration, and search.[^sculptor]
- **Memory-R1** applies reinforcement learning to memory construction and use.[^memory-r1]
- **Agentic Context Management** lets an agent offload selected context to external
  memory and retrieve it later.[^acm]
- **Context Folding** branches into a subtask and replaces the completed branch with a
  concise result; its OpenReview record lists acceptance at ICML 2026.[^context-folding]
- **MemOS** proposes a memory operating-system abstraction spanning plaintext,
  activation, and parametric memory.[^memos]

These are recent papers or preprints. Their policies and evaluations should be
reproduced before they influence a default Library policy.

**Library interpretation.** Explicit operations such as protect, release, retrieve,
and derive fit the Library's control surface. A learned policy must not control
durability, authorization, or irreversible deletion. It may propose a working set or
summary if the result remains explainable, bounded, and linked to source records.

## Prompt compression and compaction

### Query-aware and learned compression

**Source-backed observation.** LLMLingua removes lower-value prompt tokens under a
budget.[^llmlingua] LongLLMLingua adds query-aware compression and document
reordering.[^longllmlingua] Gist tokens and AutoCompressors encode prompts or previous
segments into compact learned representations.[^gist-tokens][^autocompressors]
RECOMP trains extractive and abstractive compressors for retrieved documents.[^recomp]

**Library interpretation.** Compression can be a final packing operation after
retrieval. It is often query-specific or lossy, so it cannot replace the durable event
store. Exact instructions, identifiers, decisions, and cited evidence require explicit
protection or a lossless representation. Any compressor needs task-quality tests and a
model-specific token budget.

### Provider-native continuation management

**Source-backed observation.** The OpenAI Responses API provides a compact operation
that returns a compacted response representation for later continuation.[^openai-compact]
Anthropic documents server-side compaction and context editing that can summarize
older conversation or clear selected tool and thinking blocks.[^anthropic-compaction]
[^anthropic-context-editing] These are mutable product interfaces; the cited pages
were accessed on 20 August 2026.

**Library interpretation.** Provider compaction can reduce the active provider history
when the application uses that provider's continuation protocol. It is not the same as
an application-owned, inspectable, semantically searchable backing store. The Library
can coexist with provider compaction, but a gateway must define which layer owns
history and must avoid sending both a governed envelope and an accumulated transcript.
The project has no native hook that replaces a closed host's internal compaction.

## Current implementation frameworks

The following sources describe current project behavior; they do not provide
independent comparative performance evidence. Documentation and repositories were
accessed on 20 August 2026.

| Project | Documented context or memory mechanism | Relationship to this project |
|---|---|---|
| [Letta](https://github.com/letta-ai/skills/blob/main/letta/letta-api-client/memory-architecture.md) | In-context core blocks plus out-of-context archival and conversation search; agent messages can be compacted | Similar tiered-memory model; useful reference for explicit memory operations and agent-facing controls |
| [LangGraph](https://docs.langchain.com/oss/python/langgraph/persistence) and [LangChain memory](https://docs.langchain.com/oss/python/concepts/memory) | Thread checkpoints and namespaced cross-thread stores; trimming and summarization are application patterns | A possible orchestration and persistence integration surface; context ownership and paging policy remain application-defined |
| [LlamaIndex Memory](https://github.com/run-llama/llama_index/blob/main/docs/src/content/docs/framework/module_guides/deploying/agents/memory.mdx) | Token-bounded FIFO short-term memory can flush older messages into configurable long-term memory blocks | A concrete reference for short-term to long-term movement and budgeted retrieval |
| [Microsoft Agent Framework](https://learn.microsoft.com/en-us/agent-framework/journey/adding-context-providers) | Sessions and context providers can inject state before and after invocation; separate APIs provide compaction and workflow checkpoints | A current host integration target for a Library context provider; not itself evidence for a semantic paging policy |
| [OpenAI Agents SDK](https://github.com/openai/openai-agents-python/blob/main/docs/sessions/index.md) | Session backends persist conversation history; a Responses compaction session can rewrite stored continuation state | A session and compaction integration reference; semantic retrieval needs an additional policy and index |
| [Graphiti](https://github.com/getzep/graphiti) | Incremental temporal knowledge graph with semantic, lexical, and graph retrieval | A candidate derived temporal index; it does not replace the authoritative event log |
| [Mem0](https://github.com/mem0ai/mem0/blob/main/docs/core-concepts/how-it-works.mdx) | Extracts, updates, stores, and searches selected memories across documented scopes | A reference for memory extraction and consolidation; the caller still decides how results enter the prompt |
| [AutoGen](https://github.com/microsoft/autogen) | Memory interfaces inject retrieved content and model-context classes truncate by message or token count | Official repository is in maintenance mode and directs new users to Microsoft Agent Framework |
| [Semantic Kernel](https://learn.microsoft.com/en-us/semantic-kernel/concepts/ai-services/chat-completion/chat-history) | Chat-history reducers truncate or summarize history; vector stores are a separate facility | A reference for reducers and adapter boundaries; current repository guidance also points agent work to Microsoft Agent Framework |

The comparison does not imply that the Library must wrap every framework. A maintained
adapter needs an owner, a stable pre-request and post-response lifecycle, retry and
stream semantics, and conformance tests proving that the host does not append an
additional transcript.

## Inference-runtime paging and caching

**Source-backed observation.** PagedAttention applies operating-system virtual-memory
ideas to allocation and sharing of inference-time KV-cache blocks.[^pagedattention]
Prefix and prompt caches reuse computation for repeated token prefixes; they do not
select facts from a durable agent history.

**Library interpretation.** PagedAttention and Library semantic paging share a
metaphor, not a storage layer. Runtime KV pages are model activations and may be
discarded. Library books are application records with provenance and lifecycle rules.
The two mechanisms can operate together without one replacing the other.

## Evaluation evidence

Long context must be evaluated as both a retrieval problem and a reading problem.

| Reference | What it measures | Use for this project |
|---|---|---|
| [Lost in the Middle](https://doi.org/10.1162/tacl_a_00638) | Position effects when relevant information appears at different places in long input | Test packing order and avoid treating nominal window length as effective recall |
| [RULER](https://openreview.net/forum?id=kIoBbc76Sy) | Synthetic retrieval, tracing, aggregation, and distractor tasks across context lengths | A controlled reading test; its authors caution that it is not a substitute for realistic tasks |
| [LongBench](https://aclanthology.org/2024.acl-long.172/) | Long-context tasks in English and Chinese across QA, summarization, code, and synthetic categories | Broad model-side regression coverage, but not an operational agent-memory test |
| [LoCoMo](https://aclanthology.org/2024.acl-long.747/) | Multi-session conversational QA, temporal and causal reasoning, and summarization | Test thread recall and ordering; the dataset contains ten base conversations and should not be the only evaluation |
| [LongMemEval](https://openreview.net/forum?id=pZiyCaVuti) | Information extraction, multi-session reasoning, temporal reasoning, updates, and abstention | Separate indexing, retrieval, and answer-reading failures; supplement it with systems tests |

The Library also needs tests that these benchmarks do not cover: crash recovery,
idempotent replay, work-ring overflow, concurrent writers, access control, deletion,
stale indexes, token-bound enforcement, and local operation during dependency failure.

## Comparison by system boundary

| Approach | Primary state being managed | Typical intervention point | Does not by itself provide |
|---|---|---|---|
| Longer or recurrent model | Hidden state, activations, or a larger token sequence | Model architecture or inference runtime | Application-owned provenance and lifecycle |
| KV-cache paging | Inference-time key/value tensors | Model server | Semantic retrieval from durable records |
| Prompt or prefix caching | Reusable computation for an identical prefix | Model provider or runtime | New relevance selection or durable memory |
| Prompt compression | A smaller form of selected input | Before a model request | A lossless authoritative history |
| Conversation compaction | A smaller continuation state | Provider or agent session | Independently addressable original events unless the application retains them |
| Checkpointing | Serializable workflow or thread state | Agent runtime | Relevance ranking and bounded evidence packing |
| Document RAG | Retrieved corpus passages | Before a model request | Agent-event ordering, protected state, and read-your-own-write behavior |
| Library semantic paging | Durable events plus derived indexes and policy-selected records | Application-owned model-call boundary | A larger native window or automatic control of a closed host |

## Design implications

![Research synthesis for the Library of Context](research-synthesis.png)

*Durable originals remain authoritative; retrieval structures and model working sets
remain replaceable.*

### Adopted in the current architecture

- Keep original events authoritative and treat summaries, embeddings, and graphs as
  rebuildable derivatives.
- Combine a bounded recent FIFO ring with retrieval of older records.
- Make selected instructions and decisions explicitly protectable.
- Separate durable outbox recovery from bounded ring dispatch.
- Report recorded and indexed watermarks so freshness is observable.
- Keep provider, Redis, and team services outside the local prompt requirement.

### Planned or scale-triggered

- Add model-accurate tokenizers before advertising a hard provider-token bound.
- Add bounded vector candidates or approximate nearest-neighbor search only after
  exact retrieval crosses a measured latency or memory threshold.
- Evaluate hierarchical summaries and temporal indexes as derived views with source
  links, versioning, and contradiction tests.
- Build adapters only where a framework exposes a stable model-call boundary.

### Deliberately excluded from the authority boundary

- Learned summaries, latent vectors, or graph facts as the only surviving record.
- Redis, a vector index, or a remote service as the sole source of truth.
- A learned policy with authority to delete durable records or bypass access control.
- Claims that MCP can replace undocumented native compaction behavior.
- Cloud availability as a prerequisite for a local governed prompt.

## Open evidence gaps

The related work narrows several repository questions but does not decide them:

1. Which agent-thread corpus measures retrieval, supersession, contradictions, and
   genuine misses without exposing private production traces?
2. When do derived capsules improve continuity compared with direct source retrieval,
   and how should their errors be shown to users?
3. Which local vector index preserves sufficient recall under metadata and future ACL
   filters on Windows, macOS, and Linux?
4. Which intervention adapters can preserve structured tool calls, streaming aborts,
   retries, and branches without sending duplicate history?
5. How should provider compaction and Library paging compose when both are enabled?
6. What policy protects critical state without allowing protected-context growth to
   defeat the bounded envelope?

Contributions should connect a proposed change to a named workload, baseline, simpler
alternative, failure mode, and acceptance evidence. See the
[decision brief template](DECISION_BRIEF_TEMPLATE.md).

## Primary references

[^transformer-xl]: Z. Dai et al., [“Transformer-XL: Attentive Language Models Beyond a Fixed-Length Context”](https://aclanthology.org/P19-1285/), ACL 2019.

[^compressive-transformer]: J. Rae et al., [“Compressive Transformers for Long-Range Sequence Modelling”](https://openreview.net/forum?id=SylKikSYDH), ICLR 2020.

[^rmt]: A. Bulatov, Y. Kuratov, and M. Burtsev, [“Recurrent Memory Transformer”](https://arxiv.org/abs/2207.06881), NeurIPS 2022.

[^infini-attention]: T. Munkhdalai et al., [“Leave No Context Behind: Efficient Infinite Context Transformers with Infini-attention”](https://arxiv.org/abs/2404.07143), 2024.

[^streamingllm]: G. Xiao et al., [“Efficient Streaming Language Models with Attention Sinks”](https://openreview.net/forum?id=NG7sS51zVF), ICLR 2024.

[^rag]: P. Lewis et al., [“Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks”](https://proceedings.neurips.cc/paper/2020/hash/6b493230205f780e1bc26945df7481e5-Abstract.html), NeurIPS 2020.

[^retro]: S. Borgeaud et al., [“Improving Language Models by Retrieving from Trillions of Tokens”](https://proceedings.mlr.press/v162/borgeaud22a.html), ICML 2022.

[^self-rag]: A. Asai et al., [“Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection”](https://openreview.net/forum?id=hSyW5go0v8), ICLR 2024.

[^raptor]: P. Sarthi et al., [“RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval”](https://openreview.net/forum?id=GN921JHCRw), ICLR 2024.

[^memorizing-transformers]: Y. Wu et al., [“Memorizing Transformers”](https://arxiv.org/abs/2203.08913), ICLR 2022.

[^longmem]: W. Wang et al., [“Augmenting Language Models with Long-Term Memory”](https://arxiv.org/abs/2306.07174), NeurIPS 2023.

[^generative-agents]: J. Park et al., [“Generative Agents: Interactive Simulacra of Human Behavior”](https://doi.org/10.1145/3586183.3606763), UIST 2023.

[^memorybank]: W. Zhong et al., [“MemoryBank: Enhancing Large Language Models with Long-Term Memory”](https://arxiv.org/abs/2305.10250), AAAI 2024.

[^memgpt]: C. Packer et al., [“MemGPT: Towards LLMs as Operating Systems”](https://arxiv.org/abs/2310.08560), 2023.

[^zep-paper]: P. Rasmussen et al., [“Zep: A Temporal Knowledge Graph Architecture for Agent Memory”](https://arxiv.org/abs/2501.13956), 2025.

[^graphiti]: Zep AI, [Graphiti official repository](https://github.com/getzep/graphiti), accessed 20 August 2026.

[^mem0-paper]: T. Chhikara et al., [“Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory”](https://arxiv.org/abs/2504.19413), 2025.

[^mem0-docs]: Mem0, [“How it works”](https://github.com/mem0ai/mem0/blob/main/docs/core-concepts/how-it-works.mdx), accessed 20 August 2026.

[^sculptor]: M. Li, L. H. Xu, Q. Tan, T. Cao, and Y. Liu, [“Sculptor: Empowering LLMs with Cognitive Agency via Active Context Management”](https://arxiv.org/abs/2508.04664), preprint, 2025.

[^memory-r1]: S. Yan et al., [“Memory-R1: Enhancing Large Language Model Agents to Manage and Utilize Memories via Reinforcement Learning”](https://arxiv.org/abs/2508.19828), preprint, 2025.

[^acm]: X. Li et al., [“ACM: Agentic Context Management for Long Horizon Tasks”](https://arxiv.org/abs/2607.23809), preprint, 2026.

[^context-folding]: W. Sun et al., [“Scaling Long-Horizon Agent via Context Folding”](https://openreview.net/forum?id=lNRgWoGfYg), ICML 2026 OpenReview record.

[^memos]: MemTensor, [“MemOS: An Operating System for Memory-Augmented Generation”](https://arxiv.org/abs/2505.22101), preprint, 2025.

[^llmlingua]: H. Jiang et al., [“LLMLingua: Compressing Prompts for Accelerated Inference of Large Language Models”](https://doi.org/10.18653/v1/2023.emnlp-main.825), EMNLP 2023.

[^longllmlingua]: H. Jiang et al., [“LongLLMLingua: Accelerating and Enhancing LLMs in Long Context Scenarios via Prompt Compression”](https://doi.org/10.18653/v1/2024.acl-long.91), ACL 2024.

[^gist-tokens]: J. Mu, X. Li, and N. Goodman, [“Learning to Compress Prompts with Gist Tokens”](https://proceedings.neurips.cc/paper_files/paper/2023/hash/3d77c6dcc7f143aa2154e7f4d5e22d68-Abstract-Conference.html), NeurIPS 2023.

[^autocompressors]: A. Chevalier et al., [“Adapting Language Models to Compress Contexts”](https://doi.org/10.18653/v1/2023.emnlp-main.232), EMNLP 2023.

[^recomp]: F. Xu et al., [“RECOMP: Improving Retrieval-Augmented LMs with Compression and Selective Augmentation”](https://arxiv.org/abs/2310.04408), 2023.

[^openai-compact]: OpenAI, [Responses compact API reference](https://developers.openai.com/api/reference/java/resources/responses/methods/compact), accessed 20 August 2026.

[^anthropic-compaction]: Anthropic, [Compaction](https://platform.claude.com/docs/en/build-with-claude/compaction), accessed 20 August 2026.

[^anthropic-context-editing]: Anthropic, [Context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing), accessed 20 August 2026.

[^pagedattention]: W. Kwon et al., [“Efficient Memory Management for Large Language Model Serving with PagedAttention”](https://doi.org/10.1145/3600006.3613165), SOSP 2023.
