---
name: write-timeless-technical-prose
description: Edit technical documentation, READMEs, architecture pages, examples, API or CLI help, docstrings, and code comments into timeless, non-historical prose written in one coherent editorial present. Use when writing or reviewing repository prose, removing prompt-evident or author-process commentary, eliminating revision history from explanatory text, or enforcing plain standard technical English while preserving necessary status, migration, compatibility, citation, and runtime time semantics.
---

# Write Timeless, Non-Historical Technical Prose

Write the artifact as a coherent description of the system, not as a record of the
conversation or editing process that produced it.

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
5. Remove temporal provenance: do not tell readers that a section, feature, class,
   module, example, or explanation was added before or after another part of the
   artifact. State the complete structure and behavior directly.
6. Move necessary history to a changelog, status page, ADR, migration note, or labeled
   benchmark record.
7. Preserve precise runtime terms such as "current request," "recent ring," "new
   snapshot," and "previous desk" when they distinguish live states in an algorithm.

Do not use placement or edit-history phrases such as "the section added below," "the
earlier explanation," "the new module," "now also supports," or "after the refactor."
Name the section or component and state its contract. Sequence words such as "first,"
"then," "before," and "after" are appropriate only when they describe a procedure,
runtime transition, dependency, migration, evaluation protocol, or other technical
ordering.

## Replace process commentary with technical content

Prefer:

- `FTS returns a bounded candidate set.`
- `The gateway sends only the governed envelope.`
- `The class separates transport from request dispatch.`
- `A daemon is justified when duplicate workers exceed the declared resource limit.`

Remove or relocate statements that report a recent fix instead of the resulting
behavior, acknowledge the request instead of explaining the subject, describe a
refactoring instead of the resulting boundary, or compare an old and new design without
an operationally relevant migration constraint.

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
- Avoid claims such as "better," "scalable," "robust," or "production-ready" unless the
  document states the comparison, workload, evidence, and boundary.
- Keep sentences compact, but retain qualifications that affect correctness.

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
