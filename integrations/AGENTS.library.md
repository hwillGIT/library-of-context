# Library of Context workflow

Use these instructions only when the `library_of_context` MCP server is available.

- At the start of a substantial task, call `library_desk_refresh` using a concise summary
  of the current user objective as `subject`.
- When the objective materially changes, refresh again. Treat the returned `context` as
  a replacement for the earlier desk, never as content to append indefinitely.
- For a long-running task with a stable subject, use `library_desk_watch`; stop the watch
  when the task ends.
- Shelve concise, durable decisions, constraints, discoveries, and user-approved memories.
  Do not shelve routine chatter, credentials, tokens, secrets, or raw sensitive material.
- `swapped_out` means a book left the active desk; it remains safely stored in the Library.
- External stateless model gateways should call `library_prompt_build` for each turn and
  `library_message_record` for the resulting assistant message. Send only the returned
  `messages` array to the model, not the full historical transcript.
- Gateways that want semantic paging to replace transcript compaction should instead
  call `library_context_prepare` before every model request and
  `library_context_commit` after every assistant or consequential tool result.
- Use `library_context_protect` for active instructions, decisions, plans, and unresolved
  state that must remain resident. Release it when it is no longer active.
- Inspect `library_context_status` when indexing freshness or queue pressure matters.
