# Roadmap and Research Agenda

The project is seeking collaborators for retrieval quality, bounded search work,
tokenization, workstation-daemon design, context policy, branch semantics, privacy, and
selective team memory.

## Near-term sequence

1. Publish reproducible retrieval, prompt, queue, and recovery benchmarks.
2. Add model-aware tokenizers and complete cache/index identity.
3. Introduce a local ANN adapter and bounded hybrid candidate reranking.
4. Consolidate governors into one supervised workstation daemon and fixed workers.
5. Add context-state capsules, branch/fork behavior, and scope routing.
6. Design and test authenticated selective team promotion.

## Questions looking for evidence

- Which context should be protected automatically?
- What benchmark represents agent-thread recall rather than document QA?
- Which local ANN adapter best balances recall, memory, and operational simplicity?
- How should branch context be inherited, superseded, and merged?
- Which memories should be promoted from a private thread to a team catalog?
- Which durable broker best fits a small self-hosted team?
- How should offline deletion and permission revocation work?
- What prompt pressure and staleness signals are understandable to users?

Read the full
[ROADMAP.md](https://github.com/hwillGIT/library-of-context/blob/main/ROADMAP.md) and open
a research-question issue with competing options and a proposed decision experiment.
