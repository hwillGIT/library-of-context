# Technical language rules

The project uses an ASD-STE100-aligned profile for English technical documentation.
[ASD-STE100 Simplified Technical English](https://www.asd-ste100.org/) is a controlled natural language for technical documents.

The official standard has writing rules and a controlled dictionary.
The project uses Issue 9, dated January 15, 2025, as its primary language reference.
The [official PDF](https://www.asd-ste100.org/assets/files/ASD-STE100_ISSUE9.pdf) gives the complete rules and dictionary.

## Project rules

Documentation must obey these rules:

- Use American English spelling.
- Use a short, approved word when it has the correct meaning.
- Use a technical term only when the subject needs it.
- Give each technical term one stable meaning.
- Explain a technical term at its first important use.
- Use the [glossary](GLOSSARY.md) for shared system terms.
- Use the active voice unless the actor is unknown or unimportant.
- Use a maximum of 25 words in a descriptive sentence.
- Use a maximum of 20 words in a procedural sentence.
- Give one instruction in each procedural sentence.
- Do not use contractions.
- Do not use semicolons in prose.
- Use a vertical list for complex information.
- Keep one topic in each paragraph.
- Use no more than six sentences in a paragraph.
- Preserve code identifiers, commands, quoted text, publication titles, and protocol names.

## Technical terms

ASD-STE100 permits technical nouns and technical verbs that a subject field needs.
This project uses software terms such as `SQLite`, `Redis`, `embedding`, and `outbox`.

A technical term must meet these conditions:

1. The glossary or the local text gives a clear definition.
2. The documentation uses the term with one meaning.
3. A simpler word cannot keep the necessary technical meaning.
4. The term does not hide a decision, condition, limit, or failure.

## Dense engineering text

A noun stack is a sequence of nouns that acts as one phrase. Replace a noun stack with
a sentence that names the component and its action.

Concurrency text must identify ownership, operation order, atomic boundaries, locks,
transactions, failure behavior, and resource limits. A concurrency explanation must
not remove a condition that affects correctness.

Use two parts when a reader requests a translation of a supplied passage:

1. `Part 1: Plain-English Translation` gives one or two direct sentences.
2. `Part 2: Key Concepts Explained` gives a bullet for each necessary technical term.

Keep the existing structure when editing a repository document. Put definitions at
first use or in the [glossary](GLOSSARY.md).

## Automated checks

The repository language check finds deterministic rule violations.
It checks sentence length, contractions, semicolons, selected vague terms, and paragraph length.
The check ignores code blocks, commands, link targets, and quoted publication titles.

Automation cannot verify all dictionary meanings or all permitted technical terms.
A reviewer must check technical accuracy, word meaning, active voice, and term consistency.
The official standard remains the primary source for a formal compliance review.

## GitHub technical communication

Pull request summaries use the policy in
`.agents/skills/write-timeless-technical-prose/references/github-technical-communication.yaml`.
The summary states the failure risk, mechanical fix, and guaranteed state in that
order.

The pull request template contains hidden markers for the three parts. These markers
do not appear in rendered text. The summary uses one or two sentences.

The `Key Concepts Explained` section contains three to eight bullets. Each bullet
defines one term and states its runtime impact.

The GitHub Actions job reads the pull request body from `GITHUB_EVENT_PATH`. It checks
trusted policy code from the pull request's base branch. It does not install the pull
request's project code.

The base branch is the repository branch that receives the proposed change.

The job checks structure, length, and placeholder text. It cannot verify whether a
technical claim matches the code. The author and reviewer must verify each claim
against the implementation, tests, and evidence.

Development agents apply the same policy to reviews and commit messages. The automated
job checks only the pull request body.

## Document types

Explanatory documents describe the system in one editorial present.
The changelog contains completed history.
Status and roadmap documents contain capability state and planned sequence.
Migration documents contain version transitions that affect safe operation.
Citations keep publication dates and access dates when those dates identify a source.
