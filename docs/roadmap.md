# Roadmap and Research Agenda

The project seeks collaborators for retrieval, tokenization, workstation operation,
context policy, branch behavior, privacy, and selective team memory. The
[glossary](GLOSSARY.md) defines shared terms.

The sequence is not a requirement for every deployment. Evidence and correctness work
provides the foundation. Approximate nearest neighbor (ANN) search and daemon operation
address different measured problems.

Context intelligence remains research. The team service remains optional. Read
[Why These Improvements?](WHY_THE_ROADMAP.md) for supporting and opposing cases.

The [related-work landscape](RELATED_WORK.md) provides comparative evidence. An entry
there does not mean adoption or commitment.

## Planned sequence

1. Publish reproducible retrieval, prompt, queue, and recovery benchmarks. Use them to
   establish quality and resource baselines.
2. Add model-aware tokenizers. Complete cache and index identity. These controls protect
   prompt and cache correctness.
3. Add local ANN and bounded hybrid reranking when exact search exceeds a declared
   latency or memory target.
4. Add operating-system supervision, fair scheduling, disk policy, and versioned
   upgrades to the shared daemon when multi-agent workstation operation requires them.
5. Add capsules, branch behavior, and adaptive routing after tests show an unresolved
   continuity or collaboration failure.
6. Design authenticated team promotion for a demonstrated cross-workstation need. Add
   a broker only when direct synchronization fails measured requirements.

## Questions looking for evidence

- Which context should be protected automatically?
- What benchmark represents agent-thread recall instead of document question answering?
- Which local ANN adapter best balances recall, memory, and operational simplicity?
- How should branch context be inherited, superseded, and merged?
- Which memories should be promoted from a private thread to a team catalog?
- Which durable broker best fits a small self-hosted team?
- How should offline deletion and permission revocation work?
- What prompt pressure and staleness signals are understandable to users?

Read the full
[ROADMAP.md](https://github.com/hwillGIT/library-of-context/blob/main/ROADMAP.md). Open a
research issue with competing options and a decision experiment. Use the
[decision brief template](DECISION_BRIEF_TEMPLATE.md) for a substantial proposal.
