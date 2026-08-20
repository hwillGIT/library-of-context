# The Library of Context

<div class="loc-hero" markdown>

**Virtual memory for AI context: durable outside the model, bounded inside it.**

The Library preserves complete agent threads outside the model and semantically pages
only protected, recent, and relevant context into each bounded model request.

[Get started](#quick-start){ .md-button .md-button--primary }
[Explore the governor](CONTEXT_GOVERNOR.md){ .md-button }

</div>

![Library of Context architecture](library-of-context-system.svg)

## The idea

The model's native context is a reading desk. The Library holds the books that do not
currently fit. A context governor records every event before it can expire, keeps
critical and recent state resident, retrieves older relevant books, and replaces the
desk on each call.

```text
prepare(user event) -> bounded messages -> model -> commit(response)
       |                                      |
       +--------- durable SQLite history <----+
```

This is an alternative to making a compacted transcript the only continuation state.
Original events remain recoverable, while the model sees a fresh bounded working set.

## What you get

<div class="grid cards" markdown>

-   :material-shield-lock-outline: **Durable before evictable**

    Every governed event and its outbox entry commit before prompt construction.

-   :material-swap-horizontal: **Semantic paging**

    Protected, recent, and retrieved context replaces transcript accumulation.

-   :material-sync: **Immediate freshness**

    A token-aware recent ring overlays events while asynchronous indexing catches up.

-   :material-database: **Local-first storage**

    SQLite is authoritative; RAM and optional Redis are disposable accelerators.

-   :material-speedometer: **Observable pressure**

    Watermarks, queue occupancy, token pressure, and desk swap deltas are visible.

-   :material-account-group-outline: **Team-aware evolution**

    Selective promotion can add shared knowledge without centralizing every prompt.

</div>

## Quick start

```bash
git clone https://github.com/hwillGIT/library-of-context.git
cd library-of-context
python -m venv .venv
python -m pip install -e .
python -m unittest discover -s tests -v
```

Redis is optional. The core has no runtime dependencies beyond Python 3.11 or newer.

```python
from library_of_context import LibraryOfContext

with LibraryOfContext("data/library.sqlite", redis_url="") as library:
    with library.open_context_governor("thread-1", token_budget=8_000) as context:
        context.protect("Production changes require a canary.")
        request = context.prepare("Diagnose the rollout failure.")

        # result = your_model(input=request.messages)

        context.commit("The health probe used the wrong port.")
```

!!! important
    Send only `request.messages` to the model. Adding the original full transcript again
    defeats the bounded-context invariant.

## Choose your integration

=== "Python"

    Use `LibraryContextGovernor` directly inside an agent or provider gateway.

=== "MCP"

    Run `python -m library_of_context.mcp_server --no-redis` and use the
    `library_context_*` lifecycle tools.

=== "HTTP"

    Run `python -m library_of_context --no-redis serve` and call the loopback
    `/context/prepare` and `/context/commit` endpoints.

## Alpha status

The lifecycle, recovery, and bounded-prompt behavior are implemented and tested. Exact
vector retrieval still scans a namespace, the tokenizer is approximate, and team sync
and ACLs are designs rather than shipped features. See
[Performance and scaling](PERFORMANCE_AND_SCALING.md) and the
[research agenda](roadmap.md).

Contributions that bring evidence, alternatives, failure tests, and privacy review are
especially welcome.
