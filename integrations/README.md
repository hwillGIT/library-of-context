# Add the Library to an existing Codex agent

This profile adds local, cooperative memory to Codex through a standard input/output
Model Context Protocol (MCP) server. Codex can shelve durable findings, search them, and
replace its Library reading desk when the task changes.

It does **not** intercept Codex's private model-call boundary or disable native
compaction. Use the Python or HTTP integration in
[`docs/ADD_TO_YOUR_AGENT.md`](../docs/ADD_TO_YOUR_AGENT.md) when you control that
boundary and need automatic enforcement.

## Project-scoped setup

1. Install the Library in its own virtual environment as described in
   [`docs/GETTING_STARTED.md`](../docs/GETTING_STARTED.md).
2. In the project where your agent works, create `.codex/config.toml`.
3. Copy the table from [`codex-config.toml.example`](codex-config.toml.example) into
   that file.
4. Replace the Python executable, database, `cwd`, and namespace placeholders with
   absolute paths and a project-specific slug.
5. Merge the contents of [`AGENTS.library.md`](AGENTS.library.md) into the target
   project's root `AGENTS.md`. The filename `AGENTS.library.md` is a template; Codex
   does not discover that filename automatically.
6. Restart the desktop or IDE client, or start a new CLI session. Configuration cannot
   add tools to a chat that is already running.
7. Use `/mcp` in a supported local client or `codex mcp list`, then ask the agent to
   call `library_stats`.

The template uses `--no-redis`, so the first run needs no Docker, Redis, or cloud
service. Remove that option after configuring a dedicated local Redis cache.

## Keep projects and threads separate

The example uses a project-specific database and namespace. For every agent thread, the
instructions tell the agent to choose a unique, stable `session_id`.
Reusing `default` or `codex` across unrelated work can mix retrieved context.

A global MCP entry in `~/.codex/config.toml` is convenient, but it also creates a global
memory boundary unless every client routes projects to separate databases or
namespaces. Project-scoped configuration is the safer first-run default.

## What the cooperative tools do

| Tool group | Behavior |
|---|---|
| `library_shelve*` | Save concise, approved knowledge locally |
| `library_consult` | Search off-desk books without changing the desk |
| `library_desk_refresh` / `library_desk_get` | Replace or read the bounded desk |
| `library_desk_watch` / `library_desk_stop` | Refresh backend desk state periodically |
| `library_stats` | Inspect the local storage tiers |

`library_desk_watch` does not inject updates into a running prompt. The agent must call
`library_desk_get` to read the refreshed snapshot.

The MCP server also exposes `library_context_*` tools for custom gateways. The normal
Codex profile does not allowlist them because Codex cannot use an MCP result to rewrite
the model request that already caused the tool call.

Official reference: <https://learn.chatgpt.com/docs/extend/mcp>
