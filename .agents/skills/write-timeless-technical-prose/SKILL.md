---
name: write-timeless-technical-prose
description: Edit technical documentation, READMEs, architecture pages, examples, API or CLI help, docstrings, code comments, pull request summaries, review notes, commit messages, and GitHub Actions text into timeless, non-historical prose. Translate dense engineering jargon and concurrency descriptions into plain, active English. Use when a user supplies technical text for translation or when engineering prose needs defined terms and direct explanations. Apply ASD-STE100-aligned Simplified Technical English while preserving necessary status, migration, compatibility, citation, and runtime time semantics.
---

# Write Timeless, Non-Historical Technical Prose

Write the artifact as a coherent description of the system, not as a record of the
conversation or editing process that produced it.
The result must read as one coherent editorial present.

## Establish the document's job

Classify the material before editing:

- Use explanatory documents for mechanisms, interfaces, constraints, rationale, and
  failure behavior.
- Use `CHANGELOG` for completed change history.
- Use status and roadmap documents for capability state, sequence, and planned work.
- Use migration and compatibility sections for version transitions that affect safe
  operation.
- Use citations for publication and access dates.

Keep time-relative language only when time, version, or runtime state is part of that
job.

## Write in one editorial present

1. State the mechanism or contract directly.
2. State the reason only when it explains behavior, a constraint, or a trade-off.
3. Remove narration about the prompt, requester, author, drafting process, review
   process, or sequence of edits.
4. Remove release-relative words such as "now," "currently," "previously," "recently,"
   "new," "updated," and "latest" when the sentence remains true without them.
5. Remove temporal provenance. Do not tell readers when content entered the artifact.
   State the complete structure and behavior directly.
6. Move necessary history to a changelog, status page, ADR, migration note, or labeled
   benchmark record.
7. Preserve precise runtime terms such as "current request," "recent ring," "new
   snapshot," and "previous desk" when they distinguish live states in an algorithm.

Do not use phrases that show placement or edit history. Examples include "the section
added below," "the new module," and "after the refactor." Name the component and state
its contract. Use sequence words only for a technical order. Examples include a
procedure, runtime transition, dependency, migration, or evaluation protocol.

## Replace process commentary with technical content

Prefer:

- `FTS returns a bounded candidate set.`
- `The gateway sends only the governed envelope.`
- `The class separates transport from request dispatch.`
- `A daemon is justified when duplicate workers exceed the declared resource limit.`

Remove statements about the writing request or the edit process. State the resulting
behavior and component boundary. Compare designs only when a migration or compatibility
rule needs the comparison.

Do not preserve the sequence of discovery merely because it explains how the author
arrived at the final structure. Preserve the final structure and the operational
rationale.

## Use standard technical English

- Prefer concrete nouns and active verbs.
- Name the component, operation, condition, and effect.
- Define specialized terms before using them as shorthand.
- Treat "updated," "latest," and "improved" as claims that require a reference point.
  In explanatory prose, state the resulting behavior. In status or migration material,
  identify the version or date. Use comparative language only with a named baseline and
  evidence.
- Replace promotional superlatives, claims of frictionless operation, and vague verbs
  with measurable behavior or omit them.
- Avoid broad claims about quality, capacity, reliability, or release readiness. State
  the comparison, workload, evidence, and boundary.
- Keep sentences compact, but retain qualifications that affect correctness.

## Translate dense engineering text

Work as a principal technical writer and concurrency specialist.

A noun stack is a sequence of nouns that acts as one phrase. For example, `worker lease
recovery policy` is a noun stack.

1. Identify the component that performs each action.
2. Rewrite passive noun stacks as subject-verb-object sentences.
3. Replace formal abstractions with concrete components, operations, conditions, and
   results.
4. Keep every condition and qualification that affects correctness.
5. Explain each necessary technical term in plain English.

For concurrency text, preserve these facts:

- who owns the state, lock, lease, task, or resource
- which operations must occur in order
- which operation is atomic
- which lock or transaction protects the operation
- what happens after contention, timeout, interruption, or failure
- which queue, memory, worker, time, or retry limit applies

Use this transformation pattern:

```text
Dense: Expired-lease recovery coordination prevents duplicate claim execution.
Plain: The coordinator checks an expired lease before another worker claims the task.
       This check prevents two workers from running the task.
```

### Output format for a supplied passage

When a user supplies a passage for translation, use exactly these sections:

```markdown
## Part 1: Plain-English Translation

One or two direct sentences that state the mechanism.

## Part 2: Key Concepts Explained

- **Term:** A plain explanation of the term and its function in the mechanism.
```

When editing a repository document, keep its existing structure. Put the direct
translation in the main text. Define key concepts at first use or in the glossary.

### GitHub technical communication

Read
[references/github-technical-communication.yaml](references/github-technical-communication.yaml)
for pull request summaries, reviews, commit messages, and GitHub Actions status text.

State the failure risk or conflict first. State the mechanical fix second. State the
guaranteed result at method completion third.

Use the GitHub policy output format instead of the general passage format. Write one or
two direct sentences. Then add `**Key Concepts Explained**` and at least three bullets.

A pull request body keeps the hidden risk, fix, and state markers from the repository
template. The markers enforce the required order without changing the rendered text.

The GitHub Actions check validates the markers, headings, sentence limits, and bullet
structure. It does not decide whether a claim is technically true. A development agent
and a reviewer must compare each claim with the implementation, tests, and evidence.

The automated check reads only the pull request body. Apply the same language policy to
review text and commit messages during authoring and review.

Preserve the implemented mechanism. Do not describe a lock as a transaction. Do not
describe a transaction as a lock. Name SQLite, Redis, queues, workers, and scopes when
they affect the invariant.

## Apply the ASD-STE100 software profile

Read [references/asd-ste100-software.yaml](references/asd-ste100-software.yaml) when
the user requests ASD-STE100, Simplified Technical English, controlled vocabulary, or
jargon removal.

Use these rules for software documentation:

- Use American English spelling.
- Use the active voice unless the actor is unknown or unimportant.
- Use no more than 25 words in a descriptive sentence.
- Use no more than 20 words in a procedural sentence.
- Give one instruction in each procedural sentence.
- Do not use contractions or semicolons in prose.
- Use a vertical list when one sentence would contain complex information.
- Keep one topic in each paragraph. Use no more than six sentences.
- Explain a specialized term at its first important use.
- Use one term for one concept. Do not use synonyms only for variety.
- Preserve code identifiers, commands, protocol names, quoted text, and citations.

ASD-STE100 permits technical nouns and technical verbs. Treat required software terms
as project terminology. Define each term and use it with one stable meaning.

Do not claim formal ASD-STE100 compliance from an automated rewrite. The controlled
dictionary, technical-term approval, and intended meaning need qualified human review.

## Treat comments and docstrings as contracts

Keep a comment or docstring when it explains an invariant, non-obvious algorithm,
unit, ownership rule, side effect, error condition, concurrency constraint, or public
interface. Remove commentary about renaming, moving, refactoring, prior implementations,
or satisfying a request unless compatibility or migration behavior depends on it.

Comments and docstrings must not record when code was introduced or where an
explanation appeared during editing. Describe the invariant or interface as a single
coherent contract.

Do not restate code in prose. Explain why the code must behave that way when the reason
is not evident from names and types.

## Review the result

Check the complete artifact, not only changed lines:

- The text reads as if one author wrote it at one time.
- A reader can understand the system without knowing the prompt or edit history.
- No phrase reveals when one explanatory passage or component entered the artifact
  relative to another.
- Status, roadmap, migration, compatibility, and citation dates remain where required.
- Runtime uses of time-relative words still describe real state transitions.
- Rationale describes technical consequences rather than author preference.
- Terminology and modality (`must`, `should`, `may`) are consistent.
- No promotional filler or unsupported comparative claim remains.
