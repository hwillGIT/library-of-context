# Repository instructions

## Technical prose

Every development agent must read and apply
[the timeless technical prose skill](.agents/skills/write-timeless-technical-prose/SKILL.md)
before it creates, edits, reviews, or summarizes documentation. This requirement also
applies to examples, help text, docstrings, and code comments.

Write explanatory material in one editorial present. Describe behavior, interfaces,
constraints, failure modes, and operational reasons. State the complete contract
directly.

Do not narrate the prompt, requester, author choices, drafting process, refactoring
process, or edit sequence. Do not state when explanatory content entered the repository
relative to other content.

Use active voice and American spelling. Limit descriptive sentences to 25 words.
Limit procedural sentences to 20 words. Put one instruction in each procedural
sentence. Do not use contractions or semicolons. Limit each paragraph to six
sentences and one topic.

Define a specialized term or acronym at its first use. Use plain technical English.
Replace vague or promotional language with the mechanism, condition, and effect.

For dense engineering or concurrency text, name the component that performs each
action. Replace noun stacks with subject-verb-object sentences. Preserve ownership,
operation order, atomic boundaries, locks, transactions, failure behavior, and resource
limits.

When a user supplies a passage for translation, structure the response in two parts:

1. `Part 1: Plain-English Translation` contains one or two direct sentences.
2. `Part 2: Key Concepts Explained` contains bullets that define the necessary terms.

When editing a repository document, keep its document structure. Put the direct
translation in the main text. Define key concepts at first use or in the glossary.

For pull request summaries, reviews, commit messages, and GitHub Actions text, read
`.agents/skills/write-timeless-technical-prose/references/github-technical-communication.yaml`.
State the failure risk, mechanical fix, and guaranteed state in that order. Preserve
the exact lock, transaction, data-store, ordering, and isolation guarantees.

Use one or two summary sentences and the `Key Concepts Explained` section. Give at
least three bullets. Each bullet must define one term and state its runtime impact.

Keep the hidden risk, fix, and state markers in the pull request template. Automation
checks the structure. The author and reviewer must verify every technical claim against
the implementation and evidence.

Do not make reliability or capacity claims without evidence and a stated baseline. Do
not label explanatory content as updated, latest, or improved. State the resulting
behavior or measured comparison.

Follow the [glossary](docs/GLOSSARY.md) and
[skill profile](.agents/skills/write-timeless-technical-prose/SKILL.md). Preserve code
identifiers and quoted text exactly.

Put completed history in `CHANGELOG.md`. Put capability state in `docs/STATUS.md`.
Put future sequence in `ROADMAP.md`. Put version transitions in migration or
compatibility sections. Put publication and access dates in citations.

Preserve time-relative words when they identify runtime state. Examples include the
current request, recent ring, new snapshot, and previous desk.

Use sequence words only for technical order. Technical order includes procedures,
runtime transitions, dependencies, migrations, and evaluation protocols.
