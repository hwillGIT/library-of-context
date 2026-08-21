# Connect Codex to the Library

The integration profiles connect Codex to local cooperative memory. They use a standard-input-and-output Model Context Protocol (MCP) bridge.

Codex can store useful findings and search them. It can also replace its Library reading desk when the task changes.

This integration does **not** intercept the private model-call boundary in Codex. It does not disable native conversation compaction.

The model-call boundary is the code that constructs and sends a model request. Use a Python or Hypertext Transfer Protocol (HTTP) integration for automatic control.

See [`docs/ADD_TO_YOUR_AGENT.md`](../docs/ADD_TO_YOUR_AGENT.md) for these integrations.

SQLite is a database that stores its data in one local file. A namespace is a name that separates one project's records.

A runtime is one active Library instance and its owned resources. A daemon is a background process that owns shared resources.

A loopback address sends HTTP traffic only inside the local computer.

Choose one runtime topology for each SQLite database:

| Profile | Runtime owner | Applicable condition |
|---|---|---|
| [`codex-config.toml.example`](codex-config.toml.example) | The Codex MCP process opens the Library directly | One local client process owns the project database |
| [`codex-daemon-config.toml.example`](codex-daemon-config.toml.example) | One local daemon owns the Library, and Codex starts a thin MCP bridge | Several local clients share one size-limited runtime and database |

## Direct MCP setup

1. Install the Library in a virtual environment. Follow [`docs/GETTING_STARTED.md`](../docs/GETTING_STARTED.md).
2. Create `.codex/config.toml` in the agent's project.
3. Copy the table from [`codex-config.toml.example`](codex-config.toml.example) into that file.
4. Replace the Python executable placeholder with an absolute path.
5. Replace the database and `cwd` placeholders with absolute paths.
6. Replace the namespace placeholder with a project-specific name.
7. Merge [`AGENTS.library.md`](AGENTS.library.md) into the project's root `AGENTS.md` file.
8. Restart the desktop or integrated development environment (IDE) client.
9. Alternatively, start a new command-line interface (CLI) session.
10. Use `/mcp` in a supported local client or run `codex mcp list`.
11. Ask the agent to call `library_stats`.

`AGENTS.library.md` is a template. Codex does not discover that file automatically.

Configuration cannot add tools to a running chat.

The direct template uses `--no-redis`. It does not require Docker, Redis, or a cloud service.

A cache keeps temporary copies of frequently used data.

Remove this option only after you configure a dedicated local Redis cache.

Do not use the direct profile for a database owned by a running Library daemon.

## Shared daemon setup

Use one daemon when several Codex, IDE, or CLI processes need the same Library.

Start one project-specific daemon from the Library directory. The commands use the line-continuation syntax for each operating system.

Windows PowerShell:

```powershell
<virtual-environment-python> -m library_of_context --no-redis `
  --db <absolute-project-database> --namespace <project-slug> `
  serve --host 127.0.0.1 --port 8765 `
  --auth-token-file <absolute-daemon-token-file>
```

macOS or Linux:

```bash
<virtual-environment-python> -m library_of_context --no-redis \
  --db <absolute-project-database> --namespace <project-slug> \
  serve --host 127.0.0.1 --port 8765 \
  --auth-token-file <absolute-daemon-token-file>
```

Verify the service before you start a client:

```powershell
$daemonToken = (Get-Content -Raw <absolute-daemon-token-file>).Trim()
Invoke-RestMethod http://127.0.0.1:8765/health `
  -Headers @{ Authorization = "Bearer $daemonToken" }
```

```bash
daemon_token="$(tr -d '\r\n' < /absolute/path/to/daemon-token)"
curl --fail --header "Authorization: Bearer ${daemon_token}" \
  http://127.0.0.1:8765/health
```

Configure Codex:

1. Copy the table from [`codex-daemon-config.toml.example`](codex-daemon-config.toml.example) into the project's `.codex/config.toml` file.
2. Replace the Python executable and `cwd` placeholders.
3. Replace the token-file placeholder with the file passed to `serve`.
4. Replace the namespace placeholder with the project namespace.
5. Change the loopback address only when the daemon uses a different local port.
6. Merge [`AGENTS.library.md`](AGENTS.library.md) into the project's root `AGENTS.md` file.
7. Restart the desktop or IDE client.
8. Alternatively, start a new CLI session.
9. Use `/mcp` or run `codex mcp list`.
10. Call `library_stats`.

Each Codex client starts a small standard-input-and-output bridge. The bridge verifies the daemon communication rules and MCP message format.

It forwards tool calls. It does not open SQLite, an embedding model, a worker pool, or a desk scheduler.

Closing one bridge does not stop the daemon or other bridges. The bridge supplies `--namespace` when a tool call omits `collection`.

An explicit tool argument has priority. This rule lets one daemon route project bridges to separate collections.

The default bridge request deadline is 120 seconds. The Codex profile gives each MCP tool the same deadline.

`--daemon-timeout-seconds` controls the bridge deadline. Set the host tool deadline to the same or a greater value.

Increase both deadlines only when measurements show that an operation needs more time.

A timeout has an unknown result. The daemon can complete a write after the bridge stops its wait.

You can retry read-only tools. Retry `library_shelve_document` only with the same collection, source, text, and options.

Retry a governed write with the same caller-supplied `event_id`. Do not automatically retry a write that has no stable identity.

The loopback server accepts request bodies up to 10 mebibytes. Document ingestion completes within one request and cannot resume an interrupted upload.

Embedding many chapters can exceed the deadline even when the request body fits. Split a large document into independently named sources.

Make each source small enough to finish before the configured deadline.

Run exactly one daemon owner for each database. Do not connect another database writer while the daemon runs.

These writers include a direct MCP profile, another daemon, and an in-process Library. The daemon accepts only local loopback HTTP requests.

Every route requires a bearer token. A bearer token is a secret value that grants access to its holder.

The daemon rejects requests from a browser. The token identifies a trusted local client, not a user or team member.

The service has no Transport Layer Security (TLS). It does not support remote or multi-host use.

## Keep projects and threads separate

The examples use a database and namespace for one project. A namespace separates one project's records from other records.

Stateful operations use `ThreadKey(collection, session_id)`. Select a unique and stable `session_id` for each agent thread.

Reuse that value for all desk and context calls in the thread. The Library has no implicit default session.

Do not reuse `default`, `codex`, or a display title for unrelated work. A shared value can mix retrieved context.

The Codex MCP profile cannot read or store the internal Codex thread identifier. Use a stable host identifier when one is available.

Otherwise, create an opaque value such as `<project-slug>:<random-uuid>`. Create it before the first desk call.

Retain this value in the native chat. Loss of this value starts a separate desk identity.

A host adapter must inject a stored thread identifier to keep the binding through host restarts or compaction.

A global MCP entry in `~/.codex/config.toml` can create a global memory boundary. Separate all projects with databases or namespaces.

A project-specific configuration limits the default memory boundary to one project.

## What the cooperative tools do

| Tool group | Behavior |
|---|---|
| `library_shelve*` | Store short, approved knowledge locally |
| `library_consult` | Search stored books without changing the desk |
| `library_desk_refresh` / `library_desk_get` | Replace or read the size-limited desk |
| `library_desk_watch` / `library_desk_stop` | Refresh stored desk state at set intervals |
| `library_stats` | Inspect the local storage levels |

`library_desk_watch` does not add data to a running prompt. The agent must call `library_desk_get` to read the refreshed snapshot.

Search and desk results contain size-limited excerpts and small book references. Use the desk `context` field as the retrieval block for a prompt.

Complete book text, metadata, and embeddings remain outside the MCP result. An embedding is a numeric text representation that supports similarity searches.

Context commit and protect tools return size-limited acknowledgements. They do not repeat the submitted event content.

Storage tools create project-visible books by default. Every thread in the configured collection can retrieve these books.

A `session_id` isolates desk state, watch state, and governed thread records. It does not make default MCP books private.

A trusted custom gateway can provide explicit thread or team scope fields. A `team_id` controls routing but does not prove team membership.

Store only reviewed project knowledge. Use a separate collection or database when you need a stricter boundary.

Both MCP profiles provide `library_context_*` tools for custom gateways. The Codex templates do not permit these tools.

Codex cannot use an MCP result to change the model request that caused the tool call.

The [glossary](../docs/GLOSSARY.md) defines shared Library terms.

Official reference: <https://learn.chatgpt.com/docs/extend/mcp>
