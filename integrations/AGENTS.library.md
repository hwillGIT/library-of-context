# Library of Context workflow for a cooperative MCP agent

Use these instructions only when the `library_of_context` MCP server is available.

- At the start of a substantial task, call `library_desk_refresh` using a concise summary
  of the current user objective as `subject`. Choose a unique, stable `session_id` for
  this agent thread and reuse it for every desk call in the thread.
- When the objective materially changes, refresh again. Treat the returned `context` as
  a replacement for the earlier desk, never as content to append indefinitely.
- For a long-running task with a stable subject, use `library_desk_watch`; stop the watch
  when the task ends. A watch refreshes stored desk state; it does not push new context
  into the prompt. Call `library_desk_get` to read the latest snapshot.
- Shelve concise, durable decisions, constraints, discoveries, and user-approved memories.
  Do not shelve routine chatter, credentials, tokens, secrets, or raw sensitive material.
- `swapped_out` means a book left the active desk; it remains stored until it expires or
  is explicitly discarded.
- Do not store credentials, secrets, or raw sensitive content. Keep each project in its
  configured namespace or database; never reuse the default project boundary casually.
- This MCP workflow adds cooperative recall. It does not intercept the host's model
  request or replace its native compaction. Automatic context governance requires a
  Python or HTTP gateway that owns every model call.
