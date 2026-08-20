# Codex integration

The Library exposes a local STDIO Model Context Protocol server. It gives Codex native
tools for shelving, retrieval, on-demand desk replacement, periodic desk refresh, and
bounded stateless prompt construction.

Official OpenAI documentation says the ChatGPT desktop app, Codex CLI, and IDE extension
support local STDIO MCP servers and share configuration on the same Codex host. A trusted
project can scope the configuration in `.codex/config.toml`.

## Project-scoped setup

1. Copy the table from `codex-config.toml.example` into `.codex/config.toml`.
2. Replace `cwd` with this repository's absolute path.
3. Start local Redis if desired. On Windows, run
   `powershell -ExecutionPolicy Bypass -File .\scripts\install-local-redis.ps1`; Docker
   is not required.
4. Restart the ChatGPT desktop app or Codex client so it initializes the new server.
5. Use `/mcp` in a supported local client, or `codex mcp list`, to verify the server.

The server itself can be exercised without configuring Codex:

```powershell
python -m library_of_context.mcp_server --no-redis
```

It speaks newline-delimited JSON-RPC over standard input/output; normal users should let
Codex start it from the MCP configuration.

## Tool groups

- Library: `library_shelve`, `library_shelve_document`, `library_consult`, `library_stats`
- Reading desk: `library_desk_refresh`, `library_desk_get`, `library_desk_watch`,
  `library_desk_stop`
- Stateless model gateway: `library_prompt_build`, `library_message_record`
- Context governor: `library_context_prepare`, `library_context_commit`,
  `library_context_protect`, `library_context_release`, `library_context_status`,
  `library_context_flush`

The MCP initialization response includes the core lifecycle guidance, so Codex knows to
refresh at task start or focus change and to replace—never append—the earlier desk.

For a gateway that controls every model call, prefer the governor protocol:

```text
library_context_prepare(user turn)
        -> send only returned messages to the model
        -> library_context_commit(assistant/tool result)
```

This prevents transcript growth from the first governed turn. In a normal Codex thread,
MCP is cooperative: the tools can preserve and retrieve context, but the server cannot
replace an undocumented host-internal compaction hook.

Adding a server to configuration does not inject it into an already-running chat. A new
local session/client restart is needed before its tools can appear.

Official reference: <https://learn.chatgpt.com/docs/extend/mcp>
