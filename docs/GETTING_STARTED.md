# Getting started

Run the disposable quickstart to verify local persistence, prompt budgeting, and
indexing before connecting an agent. The command uses SQLite and the built-in hashing
embedder. It needs no Redis, Docker, cloud account, model API, or payment.

## What you will verify

The quickstart performs one complete context-governor cycle:

```text
protect a policy
      -> record a user turn
      -> build a bounded model envelope
      -> record an assistant turn
      -> index all three events
      -> remove the disposable database
```

It validates the Library itself. It does not call an AI model.

## 1. Install the project

You need Python 3.11 or newer and Git. Clone the repository:

```bash
git clone https://github.com/hwillGIT/library-of-context.git
cd library-of-context
```

Use the Python executable inside the virtual environment explicitly. This path lets an
agent process use the same environment without inheriting an activated shell.

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

Successful output begins with:

```text
Library of Context quickstart: PASS
  bounded model input: .../700 estimated tokens
  durable events recorded: 3
  events indexed and searchable: 3
  Redis, Docker, and cloud services used: no
  test data retained: no
```

If this command fails, run it again with the same virtual-environment Python and include
the complete error when opening an issue.

## 3. Choose how to connect your agent

| Your agent environment | What the Library can do | Next step |
|---|---|---|
| MCP-capable agent, such as Codex | Cooperative local memory and retrieval | [Add it as an MCP server](ADD_TO_YOUR_AGENT.md#option-a-add-cooperative-memory-to-an-mcp-agent) |
| Python agent or model gateway you can edit | Automatically enforce a bounded text prompt on every call | [Wrap the model-call boundary](ADD_TO_YOUR_AGENT.md#option-b-govern-a-python-text-agent-automatically) |
| JavaScript, Go, Rust, or another local gateway | Automatically govern calls through the loopback HTTP API | [Use the HTTP boundary](ADD_TO_YOUR_AGENT.md#option-c-govern-a-non-python-agent-over-http) |
| Hosted agent with no MCP and no pre/post model hooks | Manual Library operations only; automatic prompt control requires a supported hook | Add a supported hook or use the Library manually |

Read [Add the Library to your agent](ADD_TO_YOUR_AGENT.md) before choosing. In
particular, an MCP tool cannot rewrite the private request that already caused the host
to call it. Automatic prompt control requires a gateway that owns the model call.

## Where integration data goes

The quickstart retains nothing. An integration writes to the SQLite path you
configure, for example `data/my-project.sqlite`. SQLite is authoritative. Process RAM
and optional Redis are accelerators and may be discarded.

Use a separate database or namespace for each project, and a stable, unique session ID
for each agent thread. Do not leave unrelated projects on the shared `default`
collection.

The Library can store prompts, tool results, code, and project facts. Keep its database
private, do not store credentials, and review content before moving it into a shared
project or team scope.

## Redis is optional

Start without Redis. The default 256 MiB process cache plus SQLite is enough to evaluate
the workflow. Add a dedicated local Redis instance only after measurements show that
cross-process hot caching improves your workload. Redis is not the durable event log and
is never required for local prompt construction.
