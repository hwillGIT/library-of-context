# Library of Context workflow for a cooperative Model Context Protocol (MCP) agent

Use these instructions only when the `library_of_context` Model Context Protocol (MCP) server is available.

A namespace is a project name that separates stored records. An opaque identifier is a random value that does not contain user information.

1. Call `library_desk_refresh` at the start of a substantial task.
2. Use a short summary of the user objective as `subject`.
3. Choose a unique and stable `session_id` for the chat.
4. Use the stable thread identifier from the host when one is available.
5. Otherwise, create an opaque identifier in the form `<project-slug>:<random-uuid>`.
6. Reuse that identifier for every desk call in the chat.
7. Do not reuse the identifier for another chat.

In this form, `uuid` means universally unique identifier. It is a random value that is not expected to repeat.

Refresh the desk when the user objective changes. The returned `context` replaces the prior desk. Do not append each desk to prior desks.

Use `library_desk_watch` for a long task that has a stable subject. Stop the watch when the task ends.

A watch refreshes stored desk state. It does not add context to the prompt. Call `library_desk_get` to read the stored snapshot.

Shelve short decisions, constraints, findings, and user-approved project memories that remain useful. Do not shelve routine conversation or sensitive data.

Shelved books are visible to every thread in the configured collection. A `session_id` does not make a book private to one chat.

Do not store credentials, access tokens, secrets, or unprocessed sensitive content. Keep each project in its configured namespace or database.

`swapped_out` means that a book left the active desk. The book remains stored until it expires or a caller discards it.

This MCP workflow lets the agent retrieve approved stored facts. It does not intercept the model request from the host.

It does not replace native compaction, which shortens the transcript that the host manages.

Automatic context governance controls which stored information enters each model request. It requires a Python or Hypertext Transfer Protocol (HTTP) gateway.

That gateway must control every model call.
