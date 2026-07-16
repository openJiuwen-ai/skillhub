# Refactor Complete

## Result

The repository refactor is complete.

Current runtime code lives only in these canonical packages:

- `indexing/`: offline tree, catalog, embedding, and BM25 index construction
- `retrieval/`: online retrieval algorithms and APIs
- `orchestration/`: orchestrator loop, routing, runtime, and retrieval adapter
- `demo/`: web demo, CLI tools, and benchmark utilities
- `training/`: dataset generation, training, and offline evaluation
- `models/`: shared data contracts
- `shared/`: shared infrastructure utilities
- root [__init__.py](./__init__.py): SDK entrypoints for external imports
- `demo/config.py`: demo-only runtime configuration

Repository support directories remain intentionally separate from the SDK:

- `data/`: assets and generated artifacts
- `tests/`: verification only
- `scripts/`: local tooling

Removed during the refactor:

- legacy package roots from the pre-canonical layout
- retrieval dead branches such as `hybrid/fusion`
- demo compatibility shims and broken top-level wrapper scripts
- stale runtime audit output and committed cache directories

## Final Structure

```text
repo/
  demo/
    benchmark/
    cli/
    web/
  docs/
  indexing/
    bm25/
    catalog/
    embedding/
    io/
    tree/
    workflows/
  models/
    cid/
    indexing/
    retrieval/
  orchestration/
    engine/
    llm/
    nodes/
    retrieval_adapter/
    routing/
    runtime/
    utils/
    vllm/
  retrieval/
    io/
    lexical/
    merge/
    protocols/
    semantic/
    service/
    tree/
  shared/
  tests/
  training/
```

## Architectural Rules

Dependency direction:

- `models` is shared and does not depend on upper-layer runtime packages
- `shared` contains generic utilities only
- non-demo packages do not read repository-level runtime API config
- demo-only runtime configuration lives under `demo/config.py`
- `indexing` does not import `retrieval.service`
- `retrieval` does not import orchestration core runtime
- `orchestration` depends on `retrieval` only through canonical retrieval modules
- `demo` and `training` consume canonical packages; they do not define duplicate core logic

## What Was Finished

### 1. Canonical ownership

- indexing logic moved under `indexing/`
- retrieval logic moved under `retrieval/`
- orchestration logic moved under `orchestration/`
- shared CID/retrieval/indexing contracts consolidated under `models/`

### 2. Legacy removal

- old compatibility shells were deleted instead of being kept indefinitely
- repository imports now resolve directly to canonical packages
- demo root no longer contains obsolete wrapper scripts for removed module paths

### 3. Package cleanup

- package entrypoints were made lighter to reduce eager imports and optional-dependency failures
- `shared/rich_compat.py` centralizes the optional `rich` fallback
- generated reports and artifacts are kept under repository data directories rather than under canonical SDK packages

### 4. Documentation cleanup

- repository docs now describe the current structure rather than the migration process
- canonical docs exist for indexing and retrieval
- the top-level architecture note was rewritten from the old pre-refactor version

## Verification

Final verification after cleanup:

- no source or docs import any removed legacy package roots
- no demo code depends on deleted compatibility shims
- key regression suite passed: `99` tests
- in-memory compile check passed for the entire Python tree

## Maintenance Rule

All new implementation work should stay inside canonical packages only. If a future change introduces duplicate ownership or compatibility wrappers again, that should be treated as architectural regression rather than accepted as normal drift.
