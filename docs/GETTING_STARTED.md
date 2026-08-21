# Getting started

Run the temporary quickstart before you connect an agent. It verifies local storage, prompt size control, and search indexing.

An index organizes stored content for search. The command uses SQLite, a database that stores its data in one local file.

It also uses the built-in hashing embedder.

An embedder converts text into numbers that represent text features. The quickstart does not use Redis, Docker, or a cloud account.

It does not use a model application programming interface (API).

## What you will verify

The quickstart performs one complete context-governor cycle. A context governor controls which stored information enters each model request.

A context event is one stored message, instruction, tool result, or other ordered item. A model token is one counted unit of text.

```text
protect a policy
      -> record a user turn
      -> build a size-limited model request
      -> record an assistant turn
      -> index all three events
      -> remove the disposable database
```

The quickstart validates the Library. It does not call an artificial intelligence (AI) model.

## 1. Install the project

Install Python 3.11 or a newer version. Install Git. Clone the repository:

```bash
git clone https://github.com/hwillGIT/library-of-context.git
cd library-of-context
```

Use the Python executable in the virtual environment. A virtual environment keeps this project's Python packages separate from other projects.

The explicit path lets an agent use the environment without an activated command shell.

=== "Windows PowerShell"

    ```powershell
    py -3.11 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -e .
    ```

=== "macOS or Linux"

    ```bash
    python3 -m venv .venv
    .venv/bin/python -m pip install -e .
    ```

## 2. Run the disposable quickstart

=== "Windows PowerShell"

    ```powershell
    .\.venv\Scripts\python.exe -m library_of_context quickstart
    ```

=== "macOS or Linux"

    ```bash
    .venv/bin/python -m library_of_context quickstart
    ```

Successful output starts with:

```text
Library of Context quickstart: PASS
  bounded model input: .../700 estimated tokens
  durable events recorded: 3
  events indexed and searchable: 3
  Redis, Docker, and cloud services used: no
  test data retained: no
```

In this output, **bounded** means size-limited. **Durable** means that SQLite retains the event after a process restart.

If the command fails, run it with the same virtual-environment Python. Include the complete error in the issue report.

## 3. Choose how to connect your agent

| Agent environment | Library behavior | Next step |
|---|---|---|
| Model Context Protocol (MCP) agent, such as Codex | Provide cooperative local storage and retrieval | [Add it as an MCP server](ADD_TO_YOUR_AGENT.md#option-a-add-cooperative-memory-to-an-mcp-agent) |
| Python agent or model gateway you can edit | Enforce a size-limited text prompt on every call | [Wrap the model-call boundary](ADD_TO_YOUR_AGENT.md#option-b-govern-a-python-text-agent-automatically) |
| JavaScript, Go, Rust, or another local gateway | Govern calls through the loopback Hypertext Transfer Protocol (HTTP) API | [Use the HTTP boundary](ADD_TO_YOUR_AGENT.md#option-c-govern-a-non-python-agent-over-http) |
| Hosted agent without MCP or model-call hooks | Provide manual Library operations only | Add a supported hook or use the Library manually |

Read [Add the Library to your agent](ADD_TO_YOUR_AGENT.md) before you select an integration. An MCP tool cannot change the request that caused its call.

Automatic prompt control requires a gateway that controls the model call. A gateway is software that sends requests between an agent and a model.

## Where integration data goes

The quickstart does not retain data. An integration writes data to the configured SQLite path, such as `data/my-project.sqlite`.

SQLite keeps the required stored copy. Random-access memory (RAM) and optional Redis caches can lose their data without data loss from SQLite.

A cache keeps a temporary copy of frequently used data.

Use a separate database or namespace for each project. A namespace is a name that separates one project's records from other records.

Use a stable and unique session identifier for each agent thread. Do not put unrelated projects in the shared `default` collection.

One chat maps to `ThreadKey(collection, session_id)`. Reuse this pair for every turn in that chat.

Assign a different `session_id` to each different chat. A chat does not create a database, Redis instance, cache, index worker, or scheduler.

Each chat uses size-limited thread state in RAM. The Library can remove this state from RAM and reconstruct it from SQLite.

One embedded Library process owns one shared runtime. A runtime contains caches, worker pools, schedulers, and storage connections.

Each embedded MCP server process creates one runtime. Use thin MCP bridges when several agents must share one runtime.

The bridges connect to one loopback daemon. A daemon is a background process that owns the shared runtime.

The [agent integration guide](ADD_TO_YOUR_AGENT.md) explains both configurations.

Every runtime takes the database owner lock before it opens SQLite. This lock permits only one runtime to own the database.

Two embedded processes cannot share one database directly. An embedded process cannot open a database that a daemon owns.

Use one daemon and thin clients for access from multiple processes.

The daemon accepts only loopback traffic. A loopback address sends traffic only inside the local computer.

The daemon requires one bearer token in a file that only the owner can read.

A bearer token is a secret value that grants access to its holder. The daemon rejects requests that originate in a browser.

The token identifies a local client. It does not identify an individual user or team member.

The service has no Transport Layer Security (TLS). Do not expose or forward its port.

The Library can store prompts, tool results, code, and project facts. Keep the database private.

Do not store credentials. Review content before you move it to a shared project or team scope.

## Redis is optional

Start without Redis. The default process cache has a 256-mebibyte capacity and works with SQLite to evaluate the workflow.

Add a dedicated local Redis instance only when measurements show a benefit for your workload. Redis manages temporary cached data.

Each Library runtime uses a random, versioned Redis keyspace. A keyspace is the set of cache keys that belong to one runtime.

Each runtime starts with an empty cache after a process restart. Independent runtimes do not share Redis cache entries.

SQLite stores the authoritative event log. Redis does not store an authoritative event log.

The Library does not require Redis to construct a local prompt.
