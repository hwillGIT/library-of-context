# Python API Reference

The public API is available from `library_of_context` and re-exported from
`context_cache`. The generated reference below is built directly from source with
mkdocstrings.

## Context governor

::: context_cache.governor.LibraryContextGovernor
    options:
      members:
        - record
        - prepare
        - commit
        - protect
        - release
        - build_prompt
        - flush
        - status
        - close
      inherited_members: false

## Library facade

::: context_cache.library.LibraryOfContext
    options:
      members:
        - shelve
        - shelve_document
        - consult
        - open_reading_desk
        - open_virtual_session
        - open_context_governor
      inherited_members: false

## Reading desk

::: context_cache.library.ReadingDesk
    options:
      members:
        - lay_out
        - change_subject
        - current_books
      inherited_members: false

## Core models

::: context_cache.models.ContextEvent

::: context_cache.models.ContextWatermarks

::: context_cache.models.GovernedPrompt

::: context_cache.models.ContextRecord

::: context_cache.models.SearchHit

::: context_cache.models.WorkingSet
