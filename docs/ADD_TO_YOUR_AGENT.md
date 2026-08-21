# Add the Library to your agent

Model Context Protocol (MCP) lets an agent call external tools. Use cooperative MCP integration when the host provides tools but hides its model-call boundary.

The model-call boundary is the code that constructs and sends a model request. Use a Python or Hypertext Transfer Protocol (HTTP) wrapper when you control this boundary.

A loopback address sends HTTP traffic only inside the local computer. JavaScript Object Notation (JSON) is the message format for the HTTP routes.

## Choose the integration boundary

| Integration | Applicable host | Behavior | Controls the next model request? |
|---|---|---|---|
| Cooperative MCP | An MCP agent | The agent stores, searches, and refreshes a size-limited reading desk when it chooses | No |
| Python model-call wrapper | A text agent or gateway that you control | The wrapper stores, limits, retrieves, sends, and commits every turn | Yes |
| Loopback HTTP wrapper | A non-Python gateway that you control | The wrapper uses the same process through local JSON routes | Yes |
| Undocumented native host hook | A closed host with no supported interception point | Unsupported | No |

**Automatic** means that your application routes every model turn through the Library. Package installation does not intercept another process.

SQLite is the Library's required database and stores its data in one local file. A context event is one ordered message, instruction, or tool result.

## Identify every thread explicitly

Stateful operations use `ThreadKey(collection, session_id)`. The collection separates project storage.

The session identifier separates threads inside that collection. The Library has no implicit default session.

Use the stable thread identifier that the host stores. Reuse it for every desk or governor call in that thread.

Do not use a display title that can change. Do not reuse `default`, `codex`, or another shared label for unrelated threads.

A different session identifier starts a separate recent ring, desk, event sequence, and status position. The collection can remain the same.

The Codex MCP profile cannot read or store the internal Codex thread identifier. Pass a stable host identifier without changes when one is available.

Otherwise, create an opaque identifier such as `<project-slug>:<random-uuid>`. Create it before the first desk call.

In this placeholder, `uuid` means universally unique identifier. It is a random value that is not expected to repeat.

Reuse this identifier for the life of the chat. The native chat must retain the value.

Loss of the value creates a separate desk identity. A host or gateway adapter is necessary for guaranteed identity binding.

## Option A: add cooperative memory to an MCP agent

Use this mode when the agent supports a local standard-input-and-output MCP server. Use it only when you do not control the model-call boundary.

This repository provides a Codex example.

### Codex setup

#### Direct MCP process

Use the direct profile when one Codex MCP process owns the project database.

1. Complete the [local installation](GETTING_STARTED.md).
2. Create `.codex/config.toml` in the agent's project.
3. Copy [`integrations/codex-config.toml.example`](https://github.com/hwillGIT/library-of-context/blob/main/integrations/codex-config.toml.example) into that file.
4. Keep all other server tables in the file.
5. Replace the Python placeholder with the absolute virtual-environment Python path.
6. Replace the database placeholder with a project-specific SQLite path.
7. Replace the namespace placeholder with a project-specific name.
8. Replace `cwd` with the absolute Library directory.
9. Merge [`integrations/AGENTS.library.md`](https://github.com/hwillGIT/library-of-context/blob/main/integrations/AGENTS.library.md) into the project's root `AGENTS.md` file.
10. Restart the desktop or integrated development environment (IDE) client.
11. Alternatively, start a new command-line interface (CLI) session.
12. Use `/mcp` or run `codex mcp list`.
13. Ask the agent to call `library_stats`.

Codex does not discover the `AGENTS.library.md` template by itself.

The template uses `--no-redis` and the default 256-mebibyte process cache. A cache keeps temporary copies of frequently used data.

It does not use Docker or a cloud service.

Do not use this profile for a database owned by a running Library daemon.

#### Shared daemon and thin MCP bridges

Use a shared daemon when several Codex, IDE, or CLI processes need one Library runtime. A daemon is a background process that owns shared resources.

The runtime contains a size-limited worker pool, thread-state registry, desk scheduler, and SQLite database connection.

A worker pool runs a limited number of background tasks. A registry maps thread identifiers to active thread state.

Start one daemon from the Library checkout:

=== "Windows PowerShell"

    ```powershell
    .\.venv\Scripts\python.exe -m library_of_context --no-redis `
      --db data/my-project.sqlite --namespace my-project `
      serve --host 127.0.0.1 --port 8765 `
      --auth-token-file data/my-project.sqlite.daemon-token
    ```

=== "macOS or Linux"

    ```bash
    .venv/bin/python -m library_of_context --no-redis \
      --db data/my-project.sqlite --namespace my-project \
      serve --host 127.0.0.1 --port 8765 \
      --auth-token-file data/my-project.sqlite.daemon-token
    ```

Read the token file. Send its value as `Authorization: Bearer <token>` to `http://127.0.0.1:8765/health`.

Copy [`integrations/codex-daemon-config.toml.example`](https://github.com/hwillGIT/library-of-context/blob/main/integrations/codex-daemon-config.toml.example) into the project's `.codex/config.toml` file.

Replace the Python executable and `cwd` placeholders. Set `--namespace` to the project's collection.

Merge the agent instructions. Restart the client.

Codex starts a thin standard-input-and-output MCP bridge with `--daemon-url`. The bridge verifies the daemon communication rules and MCP message format.

The bridge forwards tool calls. It does not open a database, embedder, indexer, or scheduler.

An embedder converts text into numbers for similarity searches. An indexer makes stored text searchable.

All bridges report the same daemon runtime identifier. Closing one bridge leaves the daemon and other bridges running.

The bridge adds its configured collection to a call that omits `collection`. An explicit tool argument has priority.

The default request deadline is 120 seconds. Configure it with `--daemon-timeout-seconds`.

Set the MCP host's `tool_timeout_sec` to the same or a greater value.

A timeout has an unknown result. The daemon can complete the operation after the bridge stops its wait.

You can retry read-only calls. Retry `library_shelve_document` only with the same collection, source, text, and options.

Use the same caller-supplied `event_id` when you retry a governed write. Do not automatically retry a write without a stable identity.

The daemon rejects HTTP request bodies larger than 10 mebibytes. Document ingestion completes within one request and cannot resume an interrupted upload.

Embedding can exceed the deadline before a document reaches the size limit. Split a large input into independently named sources.

Make each source small enough to finish before the configured deadline.

Run exactly one daemon owner for each database. Do not start a second daemon for that database.

Do not connect a direct MCP profile or an in-process writer to that database. Every route requires the daemon bearer token.

A bearer token is a secret value that grants access to its holder. The daemon rejects requests that originate in a browser.

The token authenticates a trusted local client. It does not identify an individual user or team member.

The service has no Transport Layer Security (TLS). Do not expose it to another computer.

### What the MCP agent can do

The agent can do these operations:

- Store short decisions, constraints, findings, and approved memories.
- Search the stored Library with `library_consult`.
- Replace the reading desk when a task starts or its focus changes.
- Refresh a stored desk and read its most recent snapshot.
- Show local cache and storage status.

The agent must select a unique and stable `session_id` for each thread. It must reuse that value for the thread's desk calls.

The configured collection and session identifier form the `ThreadKey`.

`library_shelve` and `library_shelve_document` create books that are visible to the project by default. Every thread in the collection can retrieve these books.

The session identifier isolates desk state, watch state, and governed thread records. It does not make default MCP books private.

A trusted custom gateway can provide `scope`, `owner_session_id`, `team_id`, or authorized `team_ids`. These values control local visibility.

The Library does not verify team membership. Store only reviewed project knowledge.

Use a separate collection or database for content that other project chats must not retrieve.

This mode does not reduce the transcript that the host manages. Tool outputs remain part of the host conversation.

`library_desk_watch` refreshes stored desk state. It cannot add content to a prompt.

The agent must call `library_desk_get` to read the refreshed state.

### Other MCP-capable hosts

Use the same direct server process when the host supports a local standard-input-and-output MCP server:

```text
command: <absolute path to the virtual-environment Python>
arguments:
  - -m
  - library_of_context.mcp_server
  - --no-redis
  - --db
  - <absolute project-specific SQLite path>
  - --namespace
  - <project slug>
working directory: <absolute Library checkout>
```

Each host has a different configuration format and restart process. Enable the cooperative tools from the Codex template.

Governor tools do not let a host change a request that is already in progress.

For a shared runtime, start the same MCP module with `--daemon-url`, `--daemon-token-file`, and a project-specific `--namespace`.

Do not give the bridge `--db`, embedder, or Redis options. The daemon owns these resources.

The namespace supplies a default route for forwarded calls. The bridge does not own the namespace.

## Option B: govern a Python text agent automatically

Use `GovernedTextAgent` when you control a stateless text model call. A stateless call does not use provider-managed conversation history.

The adapter performs this process in one method:

A model counts text in units called tokens. The governor uses token budgets to limit the request size.

```text
store user -> call your model with only size-limited messages -> store reply
```

```python
from library_of_context import GovernedTextAgent, LibraryOfContext


def call_my_model(messages: list[dict[str, str]]) -> str:
    # Replace this call with the target model client.
    # Do not add another transcript or provider conversation identifier.
    return my_model_client.generate(messages=messages)


with LibraryOfContext("data/my-project.sqlite", redis_url="") as library:
    with library.open_context_governor(
        "agent-thread-42",
        collection="my-project",
        token_budget=8_000,
        recent_token_budget=2_500,
        protected_token_budget=1_000,
    ) as context:
        agent = GovernedTextAgent(
            context,
            call_my_model,
            system_prompt="Use evidence from the governed project context.",
        )

        reply = agent.turn(
            "Diagnose the failed rollout.",
            turn_id="request-0001",
        )
        print(reply)
```

Keep the `LibraryOfContext` owner active for the process lifetime. Reuse the governor and adapter for the agent thread when practical.

Use a stable and unique `turn_id` when a caller can retry. The Library derives separate user and assistant event identifiers from it.

The callback must send exactly the messages that it receives. Disable transcript memory in the agent framework.

Disable provider-managed continuation, such as a prior-response or conversation identifier. Either history source can make the request exceed the configured context limit.

`GovernedTextAgent` accepts only text messages and text responses. Other agents require a custom conversion adapter.

This requirement applies to tool calls, streams, attachments, and content with multiple media types. Commit important tool results through the governor with role `tool`.

Preserve structured provider identifiers in your typed integration.

## Option C: govern a non-Python agent over HTTP

Run one local daemon beside the agent. The daemon owns the Library runtime and database.

The same process can serve HTTP gateways and thin MCP bridges. The application programming interface (API) listens only on the local loopback address.

It requires one shared bearer token and rejects requests that originate in a browser. It has no TLS or individual user authorization.

Do not expose the daemon to another computer. Do not run another owner for its database.

=== "Windows PowerShell"

    ```powershell
    .\.venv\Scripts\python.exe -m library_of_context --no-redis `
      --db data/my-project.sqlite --namespace my-project `
      serve --host 127.0.0.1 --port 8765 `
      --auth-token-file data/my-project.sqlite.daemon-token
    ```

=== "macOS or Linux"

    ```bash
    .venv/bin/python -m library_of_context --no-redis \
      --db data/my-project.sqlite --namespace my-project \
      serve --host 127.0.0.1 --port 8765 \
      --auth-token-file data/my-project.sqlite.daemon-token
    ```

Use the following process for each stateless model call. This JavaScript example uses only the standard `fetch` interface:

```javascript
const base = "http://127.0.0.1:8765";
const token = process.env.LIBRARY_OF_CONTEXT_DAEMON_TOKEN;
if (!token) throw new Error("Library daemon token is required");
const headers = {
  "content-type": "application/json",
  "authorization": `Bearer ${token}`,
};

async function governedTurn(sessionId, userMessage, turnId) {
  const prepared = await fetch(`${base}/context/prepare`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      session_id: sessionId,
      collection: "my-project",
      user_message: userMessage,
      event_id: `${turnId}:user`,
      token_budget: 8000,
      recent_token_budget: 2500,
      protected_token_budget: 1000,
    }),
  }).then((response) => response.json());

  // Replace this call with the target model client. Pass no other history.
  const reply = await callYourStatelessTextModel(prepared.messages);

  await fetch(`${base}/context/commit`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      session_id: sessionId,
      collection: "my-project",
      content: reply,
      event_id: `${turnId}:assistant`,
    }),
  });
  return reply;
}
```

Load the environment value from the daemon token file. Do not write the value to a log.

Check `GET /health` with the same `Authorization` header before you accept traffic. Use `GET /context/status/{session}?collection=my-project` to inspect status positions and queue pressure.

A queue is an ordered list of tasks that wait for processing.

Use `POST /context/flush` at an audit or shutdown boundary. An interactive turn uses the recent ring while a background worker completes indexing.

The lower-level HTTP routes use the same visibility fields as the Python API. A `POST /records` or `POST /ingest` body can set visibility fields.

These fields are `scope`, `owner_session_id`, and `team_id`. A `POST /query` body can set `scopes`, `session_id`, and `team_ids`.

A reading-desk request uses its `session_id` for thread visibility. It can include `team_ids`.

Record deletion uses project visibility by default. Provide repeated `scope` and `team_id` query parameters to delete from another authorized scope.

Also provide `session_id` for that deletion. The bearer token authenticates the local daemon client.

The caller must verify the user's identity and authorize access to every supplied team identifier.

## Is this a custom compact command?

No. Automatic mode constructs each request from protected state, recent turns, and retrieved books.

This process can replace transcript compaction at a model gateway. The Library stores the original events in SQLite.

A custom `/compact` command operates on a transcript that has already grown. It also requires the host to accept a replacement state.

The Library operates before and after every governed model call. A host-specific compact command is a separate integration.

That integration requires a documented host interface for replacement state.

## Add it to an agent that is already running

You can configure an installed agent application. You cannot add a new MCP tool list to a model call that is in progress.

You also cannot add it to a running chat. Save the configuration.

Restart or reconnect the agent client. Start a new session.

The Library does not import prior conversation history automatically. If you control the application, review selected prior turns.

Record approved turns through the Python governor. For a cooperative MCP agent, store a short handoff that contains no sensitive data.

Do not copy a complete unreviewed transcript.

## Integration invariants

An automatic adapter must meet all these conditions:

1. Store the user or tool event before the model call.
2. Send only the returned `messages` to the model.
3. Do not append a complete transcript.
4. Prevent the framework from adding hidden history.
5. Prevent the provider from continuing another conversation.
6. Commit the assistant response after the model call.
7. Commit important tool results after their calls.
8. Keep the project database or namespace stable and separate.
9. Keep the thread session identifier stable and separate.
10. Check the final request with the tokenizer for the target model.

A tokenizer divides text into the input units that a model counts.

The built-in text adapter enforces conditions 1 through 9. Condition 10 depends on the tokenizer for the target model.

Structured events, multiple media types, and native compaction require host-specific adapters. See [Capability status](STATUS.md).

The [glossary](GLOSSARY.md) defines shared Library terms.
