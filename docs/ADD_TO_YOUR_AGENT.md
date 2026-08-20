# Add the Library to your agent

Use cooperative MCP integration when the host controls model requests. Use the Python or
HTTP wrapper when the application controls every model call.

## Choose the integration boundary

| Integration | Works with | Behavior | Controls the next model request? |
|---|---|---|---|
| Cooperative MCP | An installed MCP-capable agent | The agent shelves, consults, and refreshes a bounded reading desk when it chooses | No |
| Python model-call wrapper | A text agent or gateway whose source you control | Every turn is recorded, bounded, retrieved, sent, and committed | Yes |
| Loopback HTTP wrapper | A non-Python agent or gateway whose source you control | Same lifecycle over local JSON endpoints | Yes |
| Undocumented native host hook | A closed host with no supported interception point | Unsupported | No |

“Automatic” means that your application routes **every** model turn through the Library.
Package installation does not intercept another process.

## Option A: add cooperative memory to an MCP agent

Use this mode when the agent already supports a local standard input/output MCP server
but you do not own its internal model-call boundary. Codex is the documented example in
this repository.

### Codex setup

1. Finish the [local installation](GETTING_STARTED.md).
2. In the repository where the agent works, create `.codex/config.toml`.
3. Copy
   [`integrations/codex-config.toml.example`](https://github.com/hwillGIT/library-of-context/blob/main/integrations/codex-config.toml.example)
   into that file without removing any other server tables.
4. Replace every placeholder. Use the absolute virtual-environment Python executable,
   a project-specific SQLite file, a project-specific namespace, and the absolute
   Library checkout as `cwd`.
5. Merge
   [`integrations/AGENTS.library.md`](https://github.com/hwillGIT/library-of-context/blob/main/integrations/AGENTS.library.md)
   into the **target project's** root `AGENTS.md`. The template filename is not
   discovered by Codex on its own.
6. Restart the desktop or IDE client, or start a new CLI session. Use `/mcp` or
   `codex mcp list`, then ask the agent to call `library_stats`.

The template starts with `--no-redis` and the normal 256 MiB cache default. No Docker or
cloud service is involved.

### What the MCP agent can do

The agent can:

- shelve concise decisions, constraints, findings, and approved memories;
- search the durable Library with `library_consult`;
- replace its Library reading desk at task start or when focus changes;
- keep a backend desk refreshed and read its latest snapshot; and
- show local cache and storage status.

The agent must choose a unique, stable `session_id` for each thread and reuse it for the
thread's desk calls. The project configuration supplies the separate database and
namespace boundary.

This mode does not shrink the host's native transcript. Tool outputs are still part of
the host-managed conversation. `library_desk_watch` updates stored desk state but cannot
push content into a prompt; the agent must call `library_desk_get` to read it.

### Other MCP-capable hosts

Use the same server process when the host supports local standard input/output MCP:

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

Each host has its own configuration schema and restart behavior. Enable the cooperative
tools listed in the Codex template. Do not assume that enabling the governor tools lets
a host rewrite a request that is already in progress.

## Option B: govern a Python text agent automatically

Use `GovernedTextAgent` when you control a stateless text model call. It performs the
required boundary in one method:

```text
durable prepare(user) -> call your model with only bounded messages -> durable commit(reply)
```

```python
from library_of_context import GovernedTextAgent, LibraryOfContext


def call_my_model(messages: list[dict[str, str]]) -> str:
    # Replace this call with the target model SDK.
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

Keep the `LibraryOfContext`, governor, and adapter alive for the agent thread's process
lifetime. Use a stable, unique `turn_id` when a caller may retry. The Library derives
separate user and assistant event IDs from it.

The callback must send exactly the messages it receives. Disable framework transcript
memory and provider-managed continuation, such as a previous-response or conversation
identifier. Adding either source of history defeats the bounded-context guarantee.

`GovernedTextAgent` accepts text messages and text responses only. A tool-calling,
streaming, attachment, or multimodal agent needs a custom serialization adapter.
Commit consequential tool results with role `tool` through the underlying governor, and
preserve the provider's structured identifiers in your own typed integration.

## Option C: govern a non-Python agent over HTTP

Run one local service beside the agent. The API binds to loopback and has no
authentication, TLS, or cross-origin policy, so do not expose it to another machine.

=== "Windows PowerShell"

    ```powershell
    .\.venv\Scripts\python.exe -m library_of_context --no-redis `
      --db data/my-project.sqlite --namespace my-project `
      serve --host 127.0.0.1 --port 8765
    ```

=== "macOS or Linux"

    ```bash
    .venv/bin/python -m library_of_context --no-redis \
      --db data/my-project.sqlite --namespace my-project \
      serve --host 127.0.0.1 --port 8765
    ```

Wire the following shape around each stateless model call. This JavaScript example uses
only the standard `fetch` interface:

```javascript
const base = "http://127.0.0.1:8765";

async function governedTurn(sessionId, userMessage, turnId) {
  const prepared = await fetch(`${base}/context/prepare`, {
    method: "POST",
    headers: {"content-type": "application/json"},
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

  // Replace this call with the target model SDK. Pass no other history.
  const reply = await callYourStatelessTextModel(prepared.messages);

  await fetch(`${base}/context/commit`, {
    method: "POST",
    headers: {"content-type": "application/json"},
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

Check `GET /health` before accepting traffic. Use
`GET /context/status/{session}?collection=my-project` to inspect watermarks and queue
pressure. `POST /context/flush` is useful at audit or shutdown boundaries; an interactive
turn normally relies on the recent ring while indexing completes asynchronously.

## Is this a custom compact command?

No. The automatic mode replaces the need for transcript compaction at a model gateway:
each request is assembled afresh from protected state, recent turns, and retrieved
books. Original events remain in SQLite.

A custom `/compact` command would run after a transcript had already grown and would
still depend on the host accepting replacement state. The Library instead intervenes
before and after every model call. A host-specific compact command could be added later
only when that host publishes a safe hook.

## Add it to an agent that is already running

You can configure an agent application you already have, but you cannot hot-inject a new
MCP tool inventory into a model call or chat already in progress. Save the configuration,
restart or reconnect the agent client, and begin a new session.

The Library does not import prior conversation history automatically. If you control the
application, you may review and record selected prior turns through the Python governor.
For a cooperative MCP agent, shelve a concise, non-sensitive handoff instead of copying
an entire raw transcript without review.

## Integration invariants

An automatic adapter is correct only when all of the following remain true:

1. The user or tool event is durably prepared before the model call.
2. The model receives only the returned `messages`, never an appended full transcript.
3. The framework does not add hidden history or provider conversation continuation.
4. The assistant response and consequential tool results are committed afterward.
5. The project database or namespace and thread session ID are stable and isolated.
6. The final envelope is checked with the target model's tokenizer when enforcing a
   provider token limit.

The built-in text adapter enforces rules 1–5. Model-accurate tokenizers, structured
multimodal events, and native host compaction hooks remain planned work; see
[Capability status](STATUS.md).
