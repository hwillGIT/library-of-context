# The Library of Context

<div class="loc-hero" markdown>

**Virtual memory for artificial intelligence context: stored outside the model and size-limited inside it.**

Artificial intelligence (AI) models can process only a limited amount of text in each request. A model counts text in units called tokens.

The Library stores complete governed events outside the model. An event is one message, instruction, tool result, or other ordered item.

It selects protected, recent, and relevant information for each size-limited request.

[Get started](GETTING_STARTED.md){ .md-button .md-button--primary }
[Add it to your agent](ADD_TO_YOUR_AGENT.md){ .md-button }

</div>

![Library of Context architecture](library-of-context-system.svg)

## The idea

The model context is like a reading desk with limited space. The Library holds other books outside this active working set.

A context governor controls what enters each model request. It records each governed event before the event can leave the active context.

The governor keeps critical and recent information available. It retrieves older relevant books and replaces the desk for each call.

```text
prepare(user event) -> size-limited messages -> model -> commit(response)
       |                                      |
       +--------- stored SQLite history <----+
```

Compaction creates a shorter representation of a conversation. The Library does not make that representation the only continuation state.

The Library retains the original events. The model receives a new size-limited working set for each governed call.

The [related-work landscape](RELATED_WORK.md) compares this design with other context-management methods. These methods include retrieval, compaction, checkpoints, agent memory, and long model contexts.

## What you get

<div class="grid cards" markdown>

-   :material-shield-lock-outline: **Store before removal**

    The Library stores each governed event before it constructs the prompt. It also stores the event's pending indexing task.

-   :material-swap-horizontal: **Meaning-based selection**

    The Library replaces transcript growth with protected, recent, and retrieved context.

-   :material-sync: **Recent events remain available**

    A recent ring is an ordered, size-limited memory area. It keeps events available while a background worker indexes them.

-   :material-database: **Local storage**

    SQLite keeps the required data and stores it in one local file. Random-access memory (RAM) and optional Redis contain temporary caches.

    A cache keeps copies of frequently used data. The Library can reconstruct cache data from SQLite.

-   :material-speedometer: **Runtime status**

    Status data shows completed event positions, queue use, prompt-size pressure, and desk changes.

-   :material-account-group-outline: **Selective team memory**

    The team design separates private thread context from approved shared knowledge. [Capability status](STATUS.md) defines the support limits.

</div>

## Quick start

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m library_of_context quickstart
```

Redis is optional. This temporary self-test uses no Docker, cloud service, model application programming interface (API), or retained data.

See [Getting started](GETTING_STARTED.md) for macOS and Linux commands.

```python
from library_of_context import LibraryOfContext

with LibraryOfContext("data/library.sqlite", redis_url="") as library:
    with library.open_context_governor("thread-1", token_budget=8_000) as context:
        context.protect("Production changes require a canary.")
        request = context.prepare("Diagnose the rollout failure.")

        # Send only these messages to the model.
        # result = your_model(input=request.messages)

        context.commit("The health probe used the wrong port.")
```

!!! important
    Send only `request.messages` to the model. Do not also send the complete original transcript.

    This rule keeps the model request within its configured size limit.

## Add it to an agent you already run

| Integration | Behavior |
|---|---|
| Cooperative Model Context Protocol (MCP) | Provide local storage, retrieval, and a replaceable Library reading desk |
| Python text-agent wrapper | Automatic size-limited context for every stateless model call |
| Loopback Hypertext Transfer Protocol (HTTP) wrapper | Provide the same controlled process for non-Python gateways |

An MCP tool cannot change the host request that invoked the tool. Automatic context governance requires control of the model-call boundary.

The model-call boundary is the code that constructs and sends a model request.

A loopback address sends HTTP traffic only inside the local computer. A gateway sends requests between an agent and a model.

The [agent integration guide](ADD_TO_YOUR_AGENT.md) provides configuration examples. It also explains project and thread separation.

The [glossary](GLOSSARY.md) defines shared terms.

## Limits and evidence

Vector retrieval compares the numeric text representation for every live record in a namespace. A namespace separates records for a project.

The default token estimator gives an approximate prompt size. [Capability status](STATUS.md) identifies supported, experimental, planned, and unsupported behavior.

A benchmark is a repeatable measurement under a defined workload.

The project requires evidence before it adopts an architectural extension. See [Performance and scaling](PERFORMANCE_AND_SCALING.md) for measurements and benchmark questions.

See the [research agenda](roadmap.md) for planned work. See [Why these improvements?](WHY_THE_ROADMAP.md) for alternatives and adoption conditions.

Contributions can provide measurements, alternatives, failure tests, or privacy reviews.
