# Roadmap and Research Agenda

The project is seeking collaborators for retrieval quality, bounded search work,
tokenization, workstation-daemon design, context policy, branch semantics, privacy, and
selective team memory.

The sequence is not a mandate for every deployment. Evidence and correctness work is
foundational; ANN and a daemon respond to different measured pressures; context
intelligence is research; and the team plane is optional. The full rationale and
counterarguments are in [Why These Improvements?](WHY_THE_ROADMAP.md).

## Near-term sequence

1. Publish reproducible retrieval, prompt, queue, and recovery benchmarks so later
   changes have a quality and resource baseline.
2. Add model-aware tokenizers and complete cache/index identity because these protect
   prompt and cache correctness.
3. Introduce local ANN and bounded hybrid reranking only when exact search crosses the
   declared latency or memory target.
4. Consolidate governors into a supervised daemon only when local processes materially
   duplicate work, memory, or contention.
5. Add capsules, branch behavior, and scope routing only when evaluations demonstrate
   continuity or collaboration failures that basic paging does not solve.
6. Design authenticated selective team promotion only for a real cross-workstation
   knowledge need; earn a broker or cloud plane through measured requirements.

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
Use the [decision brief template](DECISION_BRIEF_TEMPLATE.md) for a substantial proposal.
